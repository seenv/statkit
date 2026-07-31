from __future__ import annotations

import logging
import shlex
import subprocess
import time
from typing import Optional


# run subprocess via ssh
def _ssh_base(host: str) -> list[str]:
    return ["ssh", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10", "-o", "ControlMaster=no", "-o", "TCPKeepAlive=yes", host, "bash", "-lc"]


class SSHFailure(RuntimeError):
    """SSH transport failure: rc 255 with a transport-level stderr needle.

    Raised instead of a plain RuntimeError when is_ssh_failure() holds, so the
    retry layer can tell "the host is gone" apart from "the command failed".
    Note rc 255 alone is not enough: on the host == localhost path _build_argv
    runs bash -lc locally, where 255 is the local command's own exit code.
    """


def is_ssh_failure(cp: subprocess.CompletedProcess[str]) -> bool:
    if cp.returncode != 255:
        return False
    stderr = cp.stderr or ""
    needles = [
        "Connection closed by remote host",
        "Connection timed out",
        "Network is unreachable",
        "stdio forwarding failed",
    ]
    return any(x in stderr for x in needles)


def _env_wrap(cmd: str, env: Optional[str], discard: bool = False) -> str:
    prefix = (
        "set -euo pipefail >/dev/null 2>&1; set -x; "
        if not discard
        else "set -euo pipefail; set -x; "
    )
    if env:
        return f"{prefix} . {env} > /dev/null 2>&1 && {cmd}"
    return f"{prefix} {cmd}"

def _build_argv(host: str, env: Optional[str], cmd: str, localhost: str) -> list[str]:
    wrapped = _env_wrap(cmd, env)
    if host == localhost:
        return ["bash", "-lc", wrapped]
    return _ssh_base(host) + [wrapped]


def run_subprocess(host: str, env: Optional[str], cmd: str, *, 
                   localhost: str, check: bool = True, timeout: Optional[int] = None,
                    retries: int = 100, sleep: int = 5) -> subprocess.CompletedProcess[str]:

    argv = _build_argv(host, env, cmd, localhost=localhost)
    total_attempts = retries + 1
    last_err: Optional[RuntimeError] = None

    logging.debug("RUN: %s %s", host.upper(), cmd)

    for attempt in range(1, total_attempts + 1):
        cp = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)

        if cp.returncode == 0:
            return cp

        retryable = is_ssh_failure(cp)
        err_cls = SSHFailure if retryable else RuntimeError
        err = err_cls(
            f"Command failed on host={host}\n"
            f"ARGV: {argv}\n"
            f"RC={cp.returncode}\n"
            f"STDOUT:\n{cp.stdout}\n"
            f"STDERR:\n{cp.stderr}"
        )
        last_err = err
        if not check:
            return cp

        if retryable and attempt < total_attempts:
            logging.warning(
                "SSH command failed on %s (attempt %d/%d), retrying in %.1fs",
                host,
                attempt,
                total_attempts,
                sleep,
            )
            time.sleep(sleep)
            continue
        raise err

    assert last_err is not None
    raise last_err


def popen_subprocess(host: str, env: Optional[str], cmd: str, *, localhost: str) -> subprocess.Popen[str]:
    argv = _build_argv(host, env, cmd,  localhost=localhost)
    logging.debug("POPEN: %s", argv)
    return subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    #make it stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, for server processes with no communicate


