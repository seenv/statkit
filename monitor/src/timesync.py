from __future__ import annotations

import ctypes
import ctypes.util
import re
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

from .util import CsvLogger

# One row per monitor sample in <app>-<resource>_timesync.csv, with the same
# ts_wall_s / ts_mono_s as that monitor's row, so each resource file can be
# joined with the clock-sync state at the moment it was sampled.
#
# Sources:
#   1. `timedatectl timesync-status` (systemd-timesyncd), or `chronyc tracking`
#      if timedatectl reports nothing. These values come from the LAST NTP poll
#      (every 32 s .. 34 min), so they repeat between polls; packet_count
#      increases when a new poll happened.
#   2. The kernel's clock state (adjtimex, read-only, no root needed, no
#      subprocess): live every sample. timesyncd/chrony update it when they
#      correct the clock; kernel_maxerror_s grows between corrections.
#
# Sign convention: offset_s = reference time - local time, as measured at the
# last poll (NTP convention). The daemon then corrects the clock by that
# amount, so it is the size of the last correction, not the current error.
# root_distance_s is the daemon's bound on the error relative to true time;
# kernel_esterror_s / kernel_maxerror_s are the kernel's current estimates.

TIMESYNC_FIELDS = [
    "ts_wall_s",
    "ts_mono_s",
    "source",            # timedatectl | chronyc | none
    "query_ms",          # how long the command took
    "server_name",
    "server_address",
    "poll_interval_s",
    "leap",
    "version",
    "stratum",
    "reference",
    "precision_s",
    "root_distance_s",
    "offset_s",
    "delay_s",
    "jitter_s",
    "packet_count",
    "frequency_ppm",
    "kernel_time_state",  # 0=OK 1=INS 2=DEL 3=OOP 4=WAIT 5=ERROR (unsynchronized)
    "kernel_synced",
    "kernel_maxerror_s",
    "kernel_esterror_s",
    "kernel_offset_s",
    "kernel_freq_ppm",
]

# ---------------------------------------------------------------- parsing ----

_DUR_UNITS = {
    "ns": 1e-9, "us": 1e-6, "µs": 1e-6, "μs": 1e-6, "ms": 1e-3,
    "s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0,
}
_DUR_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(ns|us|µs|μs|ms|min|s|h|d)\b")


def _parse_duration_s(text: str) -> Optional[float]:
    """'34min 8s (min: 32s; ...)' -> 2048.0, '-656us' -> -0.000656"""
    text = text.split("(", 1)[0]
    parts = _DUR_RE.findall(text)
    if not parts:
        return None
    sign = -1.0 if text.strip().startswith("-") else 1.0
    total = sum(abs(float(v)) * _DUR_UNITS[u] for v, u in parts)
    return sign * total


def _parse_float(text: str) -> Optional[float]:
    m = re.search(r"[+-]?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else None


def parse_timedatectl(out: str) -> Dict[str, Any]:
    kv: Dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    if not kv:
        return {}

    r: Dict[str, Any] = {"source": "timedatectl"}
    srv = kv.get("Server", "")
    m = re.match(r"(\S+)\s*\((.*)\)", srv)
    if m:
        r["server_address"], r["server_name"] = m.group(1), m.group(2)
    elif srv:
        r["server_address"] = srv
    if "Poll interval" in kv:
        r["poll_interval_s"] = _parse_duration_s(kv["Poll interval"])
    if "Leap" in kv:
        r["leap"] = kv["Leap"]
    for k, col in (("Version", "version"), ("Stratum", "stratum"), ("Packet count", "packet_count")):
        if k in kv:
            v = _parse_float(kv[k])
            r[col] = int(v) if v is not None else None
    if "Reference" in kv:
        r["reference"] = kv["Reference"]
    for k, col in (("Precision", "precision_s"), ("Root distance", "root_distance_s"),
                   ("Offset", "offset_s"), ("Delay", "delay_s"), ("Jitter", "jitter_s")):
        if k in kv:
            r[col] = _parse_duration_s(kv[k])
    if "Frequency" in kv:
        r["frequency_ppm"] = _parse_float(kv["Frequency"])
    return r


def parse_chronyc(out: str) -> Dict[str, Any]:
    """`chronyc tracking`. Not available on your nodes; parsed for completeness."""
    kv: Dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    if "Reference ID" not in kv:
        return {}

    r: Dict[str, Any] = {"source": "chronyc"}
    m = re.match(r"(\S+)\s*\((.*)\)", kv.get("Reference ID", ""))
    if m:
        r["reference"], r["server_name"] = m.group(1), m.group(2)
    if "Stratum" in kv:
        v = _parse_float(kv["Stratum"])
        r["stratum"] = int(v) if v is not None else None
    if "Last offset" in kv:
        # chrony: positive = local clock was ahead. Negated to the NTP
        # convention used above (reference - local).
        v = _parse_float(kv["Last offset"])
        r["offset_s"] = -v if v is not None else None
    # chrony's Frequency uses a different convention from timesyncd's, so it
    # is not mapped into frequency_ppm.
    if "Update interval" in kv:
        r["poll_interval_s"] = _parse_float(kv["Update interval"])
    if "Leap status" in kv:
        r["leap"] = kv["Leap status"].lower()
    rd = _parse_float(kv.get("Root delay", ""))
    rdisp = _parse_float(kv.get("Root dispersion", ""))
    if rd is not None and rdisp is not None:
        r["root_distance_s"] = rd / 2.0 + rdisp
    return r


# ----------------------------------------------------------- kernel clock ----

class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _Timex(ctypes.Structure):
    _fields_ = [
        ("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
        ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
        ("time", _Timeval), ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long), ("shift", ctypes.c_int), ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int), ("_pad", ctypes.c_int * 11),
    ]


_STA_UNSYNC = 0x0040
_STA_NANO = 0x2000

try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
except Exception:
    _libc = None


def read_kernel_clock() -> Dict[str, Any]:
    """adjtimex(modes=0): read-only, allowed for any user."""
    if _libc is None:
        return {}
    tx = _Timex()
    tx.modes = 0
    try:
        state = _libc.adjtimex(ctypes.byref(tx))
    except Exception:
        return {}
    if state < 0:
        return {}
    offset_unit = 1e-9 if tx.status & _STA_NANO else 1e-6
    return {
        "kernel_time_state": int(state),
        "kernel_synced": int(not (tx.status & _STA_UNSYNC)),
        "kernel_maxerror_s": tx.maxerror * 1e-6,
        "kernel_esterror_s": tx.esterror * 1e-6,
        "kernel_offset_s": tx.offset * offset_unit,
        "kernel_freq_ppm": tx.freq / 65536.0,
    }


# ----------------------------------------------------------------- logger ----

def _run(cmd) -> str:
    try:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=1.0, check=False)
        return cp.stdout if cp.returncode == 0 else ""
    except Exception:
        return ""


def read_timesync() -> Tuple[Dict[str, Any], float]:
    """Equivalent of: timedatectl timesync-status 2>/dev/null || chronyc tracking"""
    t0 = time.monotonic()
    r = parse_timedatectl(_run(["timedatectl", "timesync-status"]))
    if not r:
        r = parse_chronyc(_run(["chronyc", "tracking"]))
    if not r:
        r = {"source": "none"}
    return r, (time.monotonic() - t0) * 1000.0


class TimesyncLogger:
    def __init__(self, out_csv_path: str) -> None:
        self.logger = CsvLogger(out_csv_path, fieldnames=TIMESYNC_FIELDS, flush_every=1)

    def sample(self, ts_wall: float, ts_mono: float) -> None:
        row: Dict[str, Any] = dict.fromkeys(TIMESYNC_FIELDS, "")
        r, query_ms = read_timesync()
        row.update(r)
        row.update(read_kernel_clock())
        row["ts_wall_s"] = ts_wall
        row["ts_mono_s"] = ts_mono
        row["query_ms"] = query_ms
        self.logger.write({k: row[k] for k in TIMESYNC_FIELDS})

    def close(self) -> None:
        self.logger.close()


def timesync_path(resource_csv_path: str) -> str:
    """iperf_gst-cpu.csv -> iperf_gst-cpu_timesync.csv"""
    if resource_csv_path.endswith(".csv"):
        return resource_csv_path[:-4] + "_timesync.csv"
    return resource_csv_path + "_timesync.csv"