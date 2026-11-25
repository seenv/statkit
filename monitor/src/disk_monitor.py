from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import psutil

from .targets import TargetResolver, boottime_now, ext_path, started_after
from .util import CsvLogger, now_mono_s, now_wall_s

# Output files:
#   <app>-disk.csv: original columns, corrected values.
#   <app>-disk_ext.csv: columns added since the original version.
#
# disk_usage(path) reports the filesystem that contains <path>. If /mini-app is
# a separate mount (another disk, a bind mount, an overlay), "/" and "/mini-app"
# are different filesystems, so their numbers differ. Check with: df -h / /mini-app
#
# The disk_* totals in disk.csv sum every block device psutil reports, which
# includes dm-* (LVM/crypt) and md* (software RAID) on top of their member
# disks, so the same IO can be counted twice. disk_ext.csv counts whole
# physical disks only.
#
# proc_* columns cover the target processes (--pids, --process-names and their
# children), re-found every sample. Per-process IO (/proc/<pid>/io) is only
# readable for processes of the same user unless the monitor runs as root;
# unreadable processes are skipped.
#
# Reaped children: when a process waits for (reaps) a finished child, Linux
# adds the child's whole IO total to the parent's counter. If both are
# targets, the child's IO up to its last sample was already counted, so that
# part is subtracted from the parent's next delta to avoid counting it twice.

_VIRTUAL_DISK_RE = re.compile(r"^(loop|ram|zram|sr|fd|dm-|md|nbd)")


def _physical_disks() -> set[str]:
    """Whole disks from /sys/block, excluding virtual/stacked devices."""
    try:
        names = os.listdir("/sys/block")
    except OSError:
        return set()
    return {n for n in names if not _VIRTUAL_DISK_RE.match(n)}


@dataclass
class DiskMonitorConfig:
    interval_s: float = 1.0
    disk_path_for_usage: str = "/"
    pids: Optional[Sequence[int]] = None
    process_names: Optional[Sequence[str]] = None
    include_children: bool = True
    recursive_children: bool = True


class DiskMonitor:
    def __init__(self, cfg: DiskMonitorConfig, out_csv_path: str, out_ext_path: Optional[str] = None) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            out_csv_path,
            fieldnames=[
                "ts_wall_s",
                "ts_mono_s",
                "dt_s",
                "fs_path",
                "fs_total_bytes",
                "fs_used_bytes",
                "fs_free_bytes",
                "fs_percent",
                "disk_read_bytes_d",
                "disk_write_bytes_d",
                "disk_read_count_d",
                "disk_write_count_d",
                "disk_read_MBps",
                "disk_write_MBps",
                "disk_read_iops",
                "disk_write_iops",
                "disk_busy_time_ms_d",
                "proc_read_bytes_d_sum",
                "proc_write_bytes_d_sum",
            ],
            flush_every=1,
        )
        self.ext_logger = CsvLogger(
            out_ext_path or ext_path(out_csv_path),
            fieldnames=[
                "ts_wall_s",
                "ts_mono_s",
                "dt_s",
                "disk_phys_devices",
                "disk_phys_read_bytes_d",
                "disk_phys_write_bytes_d",
                "disk_phys_read_MBps",
                "disk_phys_write_MBps",
                "disk_phys_busy_time_ms_max_d",  # busiest disk; ~1000 per second = saturated
                "proc_io_pids",                  # PIDs counted in proc_* this interval
                "proc_io_unreadable",            # target PIDs whose IO couldn't be read (permissions)
            ],
            flush_every=1,
        )
        self._phys = _physical_disks()
        self._targets = TargetResolver(
            cfg.pids,
            cfg.process_names,
            include_children=cfg.include_children,
            recursive_children=cfg.recursive_children,
        )

        self._prev_mono: Optional[float] = None
        self._prev_boot: float = boottime_now()
        self._prev_io = psutil.disk_io_counters(perdisk=False)
        self._prev_perdisk = self._read_perdisk()

        # Real baselines for processes that already exist (starting from (0, 0)
        # made the first row report everything since the process started).
        self._proc_prev: dict[int, Tuple[int, int]] = {}
        self._proc_ppid: dict[int, int] = {}
        self._pending_sub: dict[int, Tuple[int, int]] = {}  # parent -> IO of reaped children
        t0 = self._targets.resolve()
        self._proc_ppid.update(t0.ppid)
        for pid in t0.pids:
            cur = self._read_proc_io(pid)
            if cur is not None:
                self._proc_prev[pid] = cur

    def close(self) -> None:
        for lg in (self.logger, self.ext_logger):
            try:
                lg.close()
            except Exception:
                pass

    def _read_perdisk(self) -> dict:
        try:
            d = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            return {}
        return {k: v for k, v in d.items() if k in self._phys}

    @staticmethod
    def _read_proc_io(pid: int) -> Optional[Tuple[int, int]]:
        try:
            io = psutil.Process(pid).io_counters()
            return int(io.read_bytes), int(io.write_bytes)
        except Exception:
            return None

    def _proc_io_deltas(self, since_boot: float):
        """Returns (read_sum, write_sum, counted_pids, unreadable_pids)."""
        if not self._targets.configured:
            return None, None, [], []
        t = self._targets.resolve()
        pid_list = t.pids
        live = set(pid_list)

        # Targets that disappeared: if reaped by a target parent, remember
        # their last counted totals so the parent's jump isn't counted again.
        for k in list(self._proc_prev.keys()):
            if k in live:
                continue
            par = self._proc_ppid.get(k)
            if par in live and not psutil.pid_exists(k):
                r, w = self._proc_prev[k]
                pr, pw = self._pending_sub.get(par, (0, 0))
                self._pending_sub[par] = (pr + r, pw + w)
            del self._proc_prev[k]
            self._proc_ppid.pop(k, None)
        for k in list(self._pending_sub.keys()):
            if k not in live:
                del self._pending_sub[k]
        self._proc_ppid.update(t.ppid)

        total_r = 0
        total_w = 0
        counted: list[int] = []
        unreadable: list[int] = []
        for pid in pid_list:
            cur = self._read_proc_io(pid)
            if cur is None:
                try:
                    if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                        unreadable.append(pid)  # alive but /proc/<pid>/io not readable
                except Exception:
                    pass  # exited
                continue
            prev = self._proc_prev.get(pid)
            self._proc_prev[pid] = cur
            sub_r, sub_w = self._pending_sub.pop(pid, (0, 0))
            if prev is None:
                # First time seen: count from 0 only if it started in this window.
                if not started_after(since_boot, pid):
                    continue
                prev = (0, 0)
            dr = cur[0] - prev[0]
            dw = cur[1] - prev[1]
            if dr < 0 or dw < 0:
                continue
            total_r += max(0, dr - sub_r)
            total_w += max(0, dw - sub_w)
            counted.append(pid)
        any_ok = bool(counted)
        return (total_r if any_ok else None), (total_w if any_ok else None), counted, unreadable

    def sample_once(self) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        self.last_ts = (t_wall, t_mono)  # used for the matching timesync row
        dt = None if self._prev_mono is None else max(1e-9, t_mono - self._prev_mono)
        self._prev_mono = t_mono
        t_boot = boottime_now()
        since_boot = self._prev_boot
        self._prev_boot = t_boot

        try:
            du = psutil.disk_usage(self.cfg.disk_path_for_usage)
            fs_total, fs_used, fs_free, fs_pct = int(du.total), int(du.used), int(du.free), float(du.percent)
        except Exception:
            fs_total = fs_used = fs_free = None
            fs_pct = None

        io = psutil.disk_io_counters(perdisk=False)
        prev = self._prev_io
        self._prev_io = io

        # psutil returns None when no disks are visible (some containers).
        if io is None or prev is None:
            rb_d = wb_d = rc_d = wc_d = bt_d = None
        else:
            rb_d = int(io.read_bytes - prev.read_bytes)
            wb_d = int(io.write_bytes - prev.write_bytes)
            rc_d = int(io.read_count - prev.read_count)
            wc_d = int(io.write_count - prev.write_count)
            bt_d = int(getattr(io, "busy_time", 0) - getattr(prev, "busy_time", 0))

        if dt is None or dt <= 0 or rb_d is None:
            rMBps = wMBps = riops = wiops = None
        else:
            rMBps = (rb_d / 1e6) / dt
            wMBps = (wb_d / 1e6) / dt
            riops = rc_d / dt
            wiops = wc_d / dt

        # physical disks only
        cur_pd = self._read_perdisk()
        prev_pd = self._prev_perdisk
        self._prev_perdisk = cur_pd
        ph_r = ph_w = 0
        ph_busy_max = 0
        for name, c in cur_pd.items():
            p = prev_pd.get(name)
            if p is None:
                continue
            ph_r += max(0, c.read_bytes - p.read_bytes)
            ph_w += max(0, c.write_bytes - p.write_bytes)
            ph_busy_max = max(ph_busy_max, getattr(c, "busy_time", 0) - getattr(p, "busy_time", 0))
        if dt is None or dt <= 0:
            ph_rMBps = ph_wMBps = None
        else:
            ph_rMBps = (ph_r / 1e6) / dt
            ph_wMBps = (ph_w / 1e6) / dt

        pr_d, pw_d, counted, unreadable = self._proc_io_deltas(since_boot)

        self.logger.write({
            "ts_wall_s": t_wall,
            "ts_mono_s": t_mono,
            "dt_s": dt,
            "fs_path": self.cfg.disk_path_for_usage,
            "fs_total_bytes": fs_total,
            "fs_used_bytes": fs_used,
            "fs_free_bytes": fs_free,
            "fs_percent": fs_pct,
            "disk_read_bytes_d": rb_d,
            "disk_write_bytes_d": wb_d,
            "disk_read_count_d": rc_d,
            "disk_write_count_d": wc_d,
            "disk_read_MBps": rMBps,
            "disk_write_MBps": wMBps,
            "disk_read_iops": riops,
            "disk_write_iops": wiops,
            "disk_busy_time_ms_d": bt_d,
            "proc_read_bytes_d_sum": pr_d,
            "proc_write_bytes_d_sum": pw_d,
        })

        self.ext_logger.write({
            "ts_wall_s": t_wall,
            "ts_mono_s": t_mono,
            "dt_s": dt,
            "disk_phys_devices": ";".join(sorted(cur_pd)),
            "disk_phys_read_bytes_d": ph_r,
            "disk_phys_write_bytes_d": ph_w,
            "disk_phys_read_MBps": ph_rMBps,
            "disk_phys_write_MBps": ph_wMBps,
            "disk_phys_busy_time_ms_max_d": int(ph_busy_max),
            "proc_io_pids": ";".join(str(x) for x in counted),
            "proc_io_unreadable": ";".join(str(x) for x in unreadable),
        })