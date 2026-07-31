from __future__ import annotations

import csv
import json
import logging
import re
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import Config
from remote import SSHFailure, run_subprocess
from utils import report_log_dir, wait_statkit_stopped


# ------------------------------------------------------------------------------
# Cleanup scope
#
# Output files are named after the app tag, and one output_dir is shared by every
# app of a matrix configuration, so cleanup is scoped to a single app's files.
# Deleting the whole directory would destroy siblings that already succeeded, and
# would unlink globus-gridftp-debug.log while the daemon still holds it open.
_APP_TAGS: frozenset[str] = frozenset({
    "iperf_gst", "iperf_base", "rsync_gst", "rsync_base",
    "globus_gtr", "mini_gst", "mini_base",
})

# Untagged per-test files. rsync.py only; iperf.py, apsmini.py and gtransfer.py
# name everything after the app tag.
_EXTRA_PATTERNS: dict[str, tuple[str, ...]] = {
    "rsync_base": ("rsync_ssh*",),      # rsync.py:240, 252, 258, encrypt==1 path
}

# At least 3 literal characters and exactly one '*'. Rejects "*", "*.log", "?",
# "/", ".." and anything an empty or None app tag could produce.
_SAFE_PATTERN = re.compile(r"\A[A-Za-z0-9_.-]{3,}\*[A-Za-z0-9_.-]*\Z")

# Files left in place on purpose: config-level, or held open by a running daemon.
_KEPT = ("gridftp-stream.log", "gridftp-audit.log", "gridftp-single.log",
         "globus-gridftp-debug.log")

_OUTCOME_OK = "ok"
_OUTCOME_FAILED = "failed"
_OUTCOME_UNREACHABLE = "failed-unreachable"
_OUTCOME_CONFIG = "failed-config"
_OUTCOME_MONITOR = "failed-monitor"
_OUTCOME_CLEANUP = "failed-cleanup"

_LEDGER_FIELDS = (
    "timestamp", "lease", "test", "idx", "app", "numa", "block", "parallel",
    "arg", "splice", "encrypt", "run", "attempts", "outcome", "error",
)


def _cleanup_patterns(app: str) -> tuple[str, ...]:
    if app not in _APP_TAGS:
        raise ValueError(f"RETRY: refusing cleanup for unknown app tag {app!r}")
    patterns = (f"{app}*",) + _EXTRA_PATTERNS.get(app, ())
    for pattern in patterns:
        if not _SAFE_PATTERN.match(pattern):
            raise ValueError(f"RETRY: refusing unsafe cleanup pattern {pattern!r}")
    return patterns


def _cleanup_cmd(app: str, out_dir: str) -> str:
    patterns = _cleanup_patterns(app)
    name_expr = " -o ".join(f"-name {shlex.quote(p)}" for p in patterns)
    return (
        f'd={shlex.quote(out_dir)}; '
        f'case "$d" in /*) ;; *) echo "RETRY: refusing non-absolute dir: $d" >&2; exit 1;; esac; '
        f'case "$d" in */B*/P*/[ST]*/[AE]*/R*) ;; '
        f'*) echo "RETRY: refusing unexpected dir: $d" >&2; exit 1;; esac; '
        f'[ -d "$d" ] || exit 0; '
        f'find "$d" -maxdepth 1 -type f \\( {name_expr} \\) -print -delete'
    )


def _hosts_for(cfg: Config, app: str) -> list[str]:
    # statkit runs on all four remote hosts for every app; only globus transfer
    # writes into output_dir on the control machine (gtransfer.py:79, 118).
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    if app == "globus_gtr" and cfg.localhost not in hosts:
        hosts.append(cfg.localhost)
    return hosts


# ------------------------------------------------------------------------------
# Failure classification
def _root_exception(exc: BaseException) -> BaseException:
    current, seen = exc, {id(exc)}
    while True:
        nxt = current.__cause__ or current.__context__
        if nxt is None or id(nxt) in seen:
            return current
        seen.add(id(nxt))
        current = nxt


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Every exception reachable through __cause__ or __context__.

    Teardown in the run_* finally blocks is unguarded, so a teardown failure can
    replace the original exception. Python links that through __context__, not
    __cause__, so both edges have to be followed to find the real failure.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        for nxt in (current.__cause__, current.__context__):
            if nxt is not None:
                stack.append(nxt)
    return chain


def _classify(exc: BaseException) -> tuple[BaseException, bool, str]:
    """Return (root exception, retryable, reason not to retry)."""
    root = _root_exception(exc)
    if any(isinstance(e, SSHFailure) for e in _exception_chain(exc)):
        # remote.py exhausted its own SSH retries: treat the host as down.
        return root, False, "host unreachable"
    if isinstance(root, ValueError):
        return root, False, "config error"
    return root, True, ""


# ------------------------------------------------------------------------------
# Accounting
class RetryLedger:
    """Append-as-you-go CSV on the control machine, one row per test."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict[str, Any]] = []

    def record(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.path.exists()
            with self.path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_LEDGER_FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow({k: row.get(k, "") for k in _LEDGER_FIELDS})
        except Exception:
            logging.exception("RETRY: Failed appending to the retry ledger %s", self.path)


def ledger_for(cfg: Config) -> RetryLedger:
    path = Path(cfg.retry_ledger) if cfg.retry_ledger else report_log_dir(cfg) / "retries.csv"
    return RetryLedger(path)


def log_summary(ledger: RetryLedger) -> None:
    notable = [r for r in ledger.rows if int(r["attempts"]) > 1 or r["outcome"] != _OUTCOME_OK]
    logging.info("")
    if not notable:
        logging.info("RETRY: All %d test(s) succeeded on the first attempt", len(ledger.rows))
        return
    logging.info("RETRY: %d of %d test(s) needed retries or failed. Ledger: %s",
                 len(notable), len(ledger.rows), ledger.path)
    for row in notable:
        logging.info(
            "RETRY:   idx=%s app=%s attempts=%s outcome=%s | "
            "numa=%s block=%s parallel=%s arg=%s splice=%s encrypt=%s run=%s",
            row["idx"], row["app"], row["attempts"], row["outcome"],
            row["numa"], row["block"], row["parallel"], row["arg"],
            row["splice"], row["encrypt"], row["run"],
        )


def _write_marker(cfg: Config, app: str, out_dir: str, payload: dict[str, Any]) -> None:
    """Best-effort {app}-attempts.json beside the test's output.

    Written only when a test was retried or finally failed, so no marker means a
    clean first-attempt success. It matches {app}*, so a later attempt's cleanup
    removes an earlier marker and only the final one survives.
    """
    body = json.dumps(payload, indent=2, sort_keys=True)
    for host in _hosts_for(cfg, app):
        try:
            run_subprocess(
                host, None,
                f"mkdir -p {shlex.quote(out_dir)} && "
                f"cat > {shlex.quote(out_dir)}/{shlex.quote(app)}-attempts.json "
                f"<<'STATKIT_MARKER_EOF'\n{body}\nSTATKIT_MARKER_EOF\n",
                localhost=cfg.localhost,
                check=False,
            )
        except Exception:
            logging.exception("RETRY: Failed writing the attempt marker on %s", host.upper())


# ------------------------------------------------------------------------------
# Cleanup + retry
def _cleanup_attempt(cfg: Config, app: str, prefix: str, out_dir: str) -> bool:
    """Delete this app's output for the failed attempt. False means do not retry."""
    if not cfg.is_test and not wait_statkit_stopped(cfg):
        logging.error(
            "%s: A statkit monitor outlived teardown; abandoning the retry so it "
            "cannot rewrite the files being deleted", prefix,
        )
        return False

    cmd = _cleanup_cmd(app, out_dir)
    for host in _hosts_for(cfg, app):
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost, check=False)
        if cp.returncode != 0:
            logging.warning(
                "%s: Cleanup on %s returned %d: %s",
                prefix, host.upper(), cp.returncode, (cp.stderr or "").strip(),
            )
            continue
        removed = [ln for ln in (cp.stdout or "").splitlines() if ln.strip()]
        logging.info("%s: Removed %d partial %s file(s) on %s",
                     prefix, len(removed), app, host.upper())
        for path in removed:
            logging.debug("%s: Removed %s", prefix, path)
    logging.info("%s: Kept the config-level logs (%s); the GridFTP debug log will "
                 "hold output from every attempt", prefix, ", ".join(_KEPT))
    return True


def run_with_retry(
    cfg: Config, *, app: str, prefix: str, output_dir: str, context: str,
    ledger: RetryLedger, fields: dict[str, Any], fn: Callable[[], None],
) -> int:
    """Run fn(), cleaning up and retrying on failure. Returns attempts used.

    Teardown in fn's own finally block always completes before the exception
    reaches here, so tunnels and daemons are down before anything is deleted.
    On the final attempt nothing is deleted: the partial output is left for a
    post-mortem and the error is re-raised with the attempt count.
    """
    if app not in _APP_TAGS:
        raise ValueError(f"RETRY: refusing to run unknown app tag {app!r}")

    attempts = max(1, cfg.retry_attempts)

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            logging.info("")
            logging.info("%s: Retry attempt %d / %d: %s", prefix, attempt, attempts, context)
        try:
            fn()
        except Exception as exc:
            root, retryable, reason = _classify(exc)
            logging.exception("%s: Attempt %d / %d failed: %s", prefix, attempt, attempts, context)

            if retryable and attempt < attempts:
                # Cleanup must never replace the failure it is cleaning up after.
                try:
                    if _cleanup_attempt(cfg, app, prefix, output_dir):
                        if cfg.retry_delay > 0:
                            logging.info("%s: Waiting %ds before attempt %d / %d",
                                         prefix, cfg.retry_delay, attempt + 1, attempts)
                            time.sleep(cfg.retry_delay)
                        continue
                    reason, outcome = "monitor survived teardown", _OUTCOME_MONITOR
                except Exception:
                    logging.exception("%s: Cleanup before the retry failed", prefix)
                    reason, outcome = "cleanup failed", _OUTCOME_CLEANUP
            elif reason == "host unreachable":
                outcome = _OUTCOME_UNREACHABLE
            elif reason == "config error":
                outcome = _OUTCOME_CONFIG
            else:
                outcome = _OUTCOME_FAILED

            message = f"{prefix}: Failed after {attempt} attempt(s): {exc}"
            if root is not exc:
                message += f" (root: {type(root).__name__}: {root})"
            if reason:
                message += f" (not retried: {reason})"

            _finish(cfg, ledger, fields, app, output_dir, context,
                    attempt, outcome, f"{type(root).__name__}: {root}")
            raise RuntimeError(message) from exc
        else:
            if attempt > 1:
                logging.info("%s: Succeeded on attempt %d / %d: %s",
                             prefix, attempt, attempts, context)
            _finish(cfg, ledger, fields, app, output_dir, context,
                    attempt, _OUTCOME_OK, "")
            return attempt

    raise AssertionError("RETRY: unreachable")


def _finish(
    cfg: Config, ledger: RetryLedger, fields: dict[str, Any], app: str,
    output_dir: str, context: str, attempts: int, outcome: str, error: str,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    flat = " ".join(error.split())[:500]
    ledger.record({
        **fields,
        "timestamp": timestamp,
        "app": app,
        "attempts": attempts,
        "outcome": outcome,
        "error": flat,
    })
    if attempts > 1 or outcome != _OUTCOME_OK:
        _write_marker(cfg, app, output_dir, {
            "app": app,
            "attempts": attempts,
            "outcome": outcome,
            "context": context,
            "timestamp": timestamp,
            "error": flat,
            "note": (
                "Reports from earlier attempts were deleted before each retry. "
                "The GridFTP debug log is not app-scoped and may hold output "
                "from every attempt."
            ),
        })
