from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import psutil

from .util import CsvLogger, now_mono_s, now_wall_s

# per process memory using pid by RSS/VMS + USS/PSS 
# RSS and VMS give reak and ram foot print but 
# memory_full_info depends on os/kernel support and permissions
# on some systems it won’t provide uss/pss
# so the code logs them as None

@dataclass
class MemMonitorConfig:
    interval_s: float = 1.0
    pids: Optional[Sequence[int]] = None


class MemMonitor:
    def __init__(self, cfg: MemMonitorConfig, out_csv_path: str) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            out_csv_path,
            fieldnames=[
                "ts_wall_s",
                "ts_mono_s",
                "dt_s",
                "mem_total_bytes",
                "mem_available_bytes",
                "mem_used_bytes",
                "mem_percent",
                "buffers_bytes",
                "cached_bytes",
                "shared_bytes",
                "swap_total_bytes",
                "swap_used_bytes",
                "swap_free_bytes",
                "swap_percent",
                "swap_sin_bytes_d",
                "swap_sout_bytes_d",
                "proc_rss_bytes_sum",
                "proc_vms_bytes_sum",
                "proc_uss_bytes_sum",
                "proc_pss_bytes_sum",
            ],
            flush_every=1,
        )
        self._prev_mono: Optional[float] = None
        self._prev_swap = psutil.swap_memory()

    def close(self) -> None:
        self.logger.close()

    def _proc_mem_sums(self):
        if not self.cfg.pids:
            return None, None, None, None
        rss = vms = 0
        uss = pss = 0
        have_uss = have_pss = False

        for pid in self.cfg.pids:
            pid = int(pid)
            try:
                p = psutil.Process(pid)
                mi = p.memory_info()
                rss += int(mi.rss)
                vms += int(mi.vms)
                try:
                    mfi = p.memory_full_info()
                    if hasattr(mfi, "uss"):
                        uss += int(mfi.uss)
                        have_uss = True
                    if hasattr(mfi, "pss"):
                        pss += int(mfi.pss)
                        have_pss = True
                except Exception:
                    pass
            except Exception:
                continue

        return rss, vms, (uss if have_uss else None), (pss if have_pss else None)

    def sample_once(self) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        dt = None if self._prev_mono is None else max(0.0, t_mono - self._prev_mono)
        self._prev_mono = t_mono

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        swap_sin_d = swap.sin - self._prev_swap.sin
        swap_sout_d = swap.sout - self._prev_swap.sout
        self._prev_swap = swap

        buffers = getattr(vm, "buffers", None)
        cached = getattr(vm, "cached", None)
        shared = getattr(vm, "shared", None)

        prss, pvms, puss, ppss = self._proc_mem_sums()

        self.logger.write({
            "ts_wall_s": t_wall,
            "ts_mono_s": t_mono,
            "dt_s": dt,
            "mem_total_bytes": int(vm.total),
            "mem_available_bytes": int(vm.available),
            "mem_used_bytes": int(vm.used),
            "mem_percent": float(vm.percent),
            "buffers_bytes": int(buffers) if buffers is not None else None,
            "cached_bytes": int(cached) if cached is not None else None,
            "shared_bytes": int(shared) if shared is not None else None,
            "swap_total_bytes": int(swap.total),
            "swap_used_bytes": int(swap.used),
            "swap_free_bytes": int(swap.free),
            "swap_percent": float(swap.percent),
            "swap_sin_bytes_d": int(swap_sin_d),
            "swap_sout_bytes_d": int(swap_sout_d),
            "proc_rss_bytes_sum": prss,
            "proc_vms_bytes_sum": pvms,
            "proc_uss_bytes_sum": puss,
            "proc_pss_bytes_sum": ppss,
        })
