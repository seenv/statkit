from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import psutil

from .util import CsvLogger, now_mono_s, now_wall_s

# TODO: check why setting path to / is different from /mini-app
@dataclass
class DiskMonitorConfig:
    interval_s: float = 1.0
    disk_path_for_usage: str = "/"
    pids: Optional[Sequence[int]] = None


class DiskMonitor:
    def __init__(self, cfg: DiskMonitorConfig, out_csv_path: str) -> None:
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
        self._prev_mono: Optional[float] = None
        self._prev_io = psutil.disk_io_counters(perdisk=False)
        self._proc_prev: dict[int, Tuple[int, int]] = {}
        if cfg.pids:
            for pid in cfg.pids:
                self._proc_prev[int(pid)] = (0, 0)

    def close(self) -> None:
        self.logger.close()

    def _proc_io_deltas(self):
        if not self.cfg.pids:
            return None, None
        total_r = 0
        total_w = 0
        any_ok = False
        for pid in self.cfg.pids:
            pid = int(pid)
            try:
                p = psutil.Process(pid)
                io = p.io_counters()
                cur = (int(io.read_bytes), int(io.write_bytes))
            except Exception:
                continue
            prev = self._proc_prev.get(pid, cur)
            self._proc_prev[pid] = cur
            dr = cur[0] - prev[0]
            dw = cur[1] - prev[1]
            if dr < 0 or dw < 0:
                continue
            total_r += dr
            total_w += dw
            any_ok = True
        return (total_r if any_ok else None), (total_w if any_ok else None)

    def sample_once(self) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        dt = None if self._prev_mono is None else max(1e-9, t_mono - self._prev_mono)
        self._prev_mono = t_mono

        try:
            du = psutil.disk_usage(self.cfg.disk_path_for_usage)
            fs_total, fs_used, fs_free, fs_pct = int(du.total), int(du.used), int(du.free), float(du.percent)
        except Exception:
            fs_total = fs_used = fs_free = None
            fs_pct = None

        io = psutil.disk_io_counters(perdisk=False)
        prev = self._prev_io
        self._prev_io = io

        rb_d = io.read_bytes - prev.read_bytes
        wb_d = io.write_bytes - prev.write_bytes
        rc_d = io.read_count - prev.read_count
        wc_d = io.write_count - prev.write_count
        bt_d = getattr(io, "busy_time", 0) - getattr(prev, "busy_time", 0)

        if dt is None or dt <= 0:
            rMBps = wMBps = riops = wiops = None
        else:
            rMBps = (rb_d / 1e6) / dt
            wMBps = (wb_d / 1e6) / dt
            riops = rc_d / dt
            wiops = wc_d / dt

        pr_d, pw_d = self._proc_io_deltas()

        self.logger.write({
            "ts_wall_s": t_wall,
            "ts_mono_s": t_mono,
            "dt_s": dt,
            "fs_path": self.cfg.disk_path_for_usage,
            "fs_total_bytes": fs_total,
            "fs_used_bytes": fs_used,
            "fs_free_bytes": fs_free,
            "fs_percent": fs_pct,
            "disk_read_bytes_d": int(rb_d),
            "disk_write_bytes_d": int(wb_d),
            "disk_read_count_d": int(rc_d),
            "disk_write_count_d": int(wc_d),
            "disk_read_MBps": rMBps,
            "disk_write_MBps": wMBps,
            "disk_read_iops": riops,
            "disk_write_iops": wiops,
            "disk_busy_time_ms_d": int(bt_d),
            "proc_read_bytes_d_sum": pr_d,
            "proc_write_bytes_d_sum": pw_d,
        })
