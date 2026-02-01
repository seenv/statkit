from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import psutil

#from .util import CsvLogger, now_mono_s, now_wall_s, percentile, read_text
from .util import CsvLogger, now_mono_s, now_wall_s, read_text


# using the interval of 1s instead of interval=None
# so it will block for 1s, 
# cgroup numbers the Linux kernel exposes about how much 
# CPU/memory/IO a group of processes has used, and whether 
# it was limited/throttled (control group statistics)

def _procstat_procs_running_blocked() -> Tuple[Optional[int], Optional[int]]:
    """
    total runnable tasks exist, total blocked on I/O
    /proc/stat has:
      procs_running <N>
      procs_blocked <N>
    Returns (procs_running, procs_blocked)
    """
    txt = read_text("/proc/stat")
    if not txt:
        return None, None
    running = blocked = None
    for line in txt.splitlines():
        if line.startswith("procs_running"):
            try:
                running = int(line.split()[1])
            except Exception:
                pass
        elif line.startswith("procs_blocked"):
            try:
                blocked = int(line.split()[1])
            except Exception:
                pass
        if running is not None and blocked is not None:
            break
    return running, blocked


def _find_cgroup_cpu_stat_paths() -> list[str]:
    """
    finding cgroup cpu stat paths for both v2/v1
    """
    paths: list[str] = []
    paths.append("/sys/fs/cgroup/cpu.stat")  # v2 root

    cg = read_text("/proc/self/cgroup")
    if cg:
        for line in cg.splitlines():
            parts = line.split(":")
            if len(parts) != 3:
                continue
            _, controllers, rel = parts
            rel = rel.strip()
            if rel == "":
                continue
            if controllers == "":
                paths.append(os.path.join("/sys/fs/cgroup", rel.lstrip("/"), "cpu.stat"))
            else:
                if "cpu" in controllers.split(","):
                    paths.append(os.path.join("/sys/fs/cgroup", controllers, rel.lstrip("/"), "cpu.stat"))
                    paths.append(os.path.join("/sys/fs/cgroup", "cpu,cpuacct", rel.lstrip("/"), "cpu.stat"))

    out: list[str] = []
    seen = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _read_cgroup_cpu_throttle() -> dict[str, Optional[int]]:
    """
    Parse cgroup cpu stat 
    to find the cases whenin mini-apps one of containers didn;t start
    v2 cpu.stat has:
      nr_periods <N>
      nr_throttled <N>
      throttled_usec <N>
    """
    # some kernels use throttled_ns
    txt = None
    for p in _find_cgroup_cpu_stat_paths():
        if os.path.exists(p):
            txt = read_text(p)
            if txt:
                break

    out = {
        "cg_nr_periods": None,
        "cg_nr_throttled": None,
        "cg_throttled_usec": None,
    }
    if not txt:
        return out

    kv: dict[str, int] = {}
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        k, v = parts
        try:
            kv[k] = int(v)
        except Exception:
            pass

    if "nr_periods" in kv:
        out["cg_nr_periods"] = kv["nr_periods"]
    if "nr_throttled" in kv:
        out["cg_nr_throttled"] = kv["nr_throttled"]
    if "throttled_usec" in kv:
        out["cg_throttled_usec"] = kv["throttled_usec"]
    elif "throttled_ns" in kv:
        out["cg_throttled_usec"] = kv["throttled_ns"] // 1000
    return out


@dataclass
class CpuMonitorConfig:
    interval_s: float = 1.0   # TODO: try a test with setting the interval to duration      
    pids: Optional[Sequence[int]] = None
    #


class CpuMonitor:
    """
    A) Utilization:
        - per-core cpu_percent (blocking interval for precise window)
        - mean/max  (wasn't useful at all so commented out /p50/p95)
        - total cpu_percent
    B) Saturation:
        - loadavg 1/5/15 (for unix)
        - procs_running/procs_blocked (in linux using /proc/stat)
        - context switches / interrupts deltas (cpu_stats)
        - cgroup throttling counters
    B) Breakdown:
        - cpu time breakdown per interval (%) for user/system/iowait/irq/softirq/steal/idle/...
    D) CPU time deltas (s) per interval
        - system cpu_times deltas
        - per process cpu time deltas and derived utilization
    """
    def __init__(self, cfg: CpuMonitorConfig, out_csv_path: str) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            out_csv_path,
            fieldnames=[
                "ts_wall_s",
                "ts_mono_s",
                "dt_s",
                "cpu_logical",
                "cpu_physical",
                "cpu_freq_cur_mhz_mean",
                "cpu_freq_cur_mhz_max",
                # A
                "cpu_total_percent",
                "cpu_mean_core_percent",
                "cpu_max_core_percent",
                #"cpu_p50_core_percent",
                #"cpu_p95_core_percent",
                "cpu_core_count_sampled",
                # B
                "loadavg_1",
                "loadavg_5",
                "loadavg_15",
                "procs_running",
                "procs_blocked",
                "ctx_switches_d",
                "interrupts_d",
                "soft_interrupts_d",
                "syscalls_d",
                "cg_nr_periods",
                "cg_nr_throttled",
                "cg_throttled_usec",
                "cg_nr_throttled_d",
                "cg_throttled_usec_d",
                # C (percent)
                "pct_user",
                "pct_system",
                "pct_idle",
                "pct_iowait",
                "pct_irq",
                "pct_softirq",
                "pct_steal",
                "pct_guest",
                "pct_guest_nice",
                # D (seconds)
                "sec_user_d",
                "sec_system_d",
                "sec_idle_d",
                "sec_iowait_d",
                "sec_irq_d",
                "sec_softirq_d",
                "sec_steal_d",
                "sec_guest_d",
                "sec_guest_nice_d",
                "sec_total_d",
                # per-process aggregate
                "proc_cpu_sec_d_sum",
                "proc_cpu_pct_total_sum",
            ],
            flush_every=1,
        )
        self._cpu_logical = psutil.cpu_count(logical=True) or 0
        self._cpu_physical = psutil.cpu_count(logical=False) or 0

        self._prev_cpu_times = psutil.cpu_times()
        self._prev_cpu_stats = psutil.cpu_stats()
        self._prev_cg = _read_cgroup_cpu_throttle()
        self._prev_mono: Optional[float] = None

        self._proc_prev_cpu: Dict[int, float] = {}
        if cfg.pids:
            for pid in cfg.pids:
                pid = int(pid)
                try:
                    p = psutil.Process(pid)
                    ct = p.cpu_times()
                    self._proc_prev_cpu[pid] = float(ct.user + ct.system)
                except Exception:
                    self._proc_prev_cpu[pid] = float("nan")

        try:
            psutil.cpu_percent(interval=None, percpu=True)
            psutil.cpu_percent(interval=None, percpu=False)
        except Exception:
            pass

    def close(self) -> None:
        self.logger.close()

    def _freq_stats(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            freqs = psutil.cpu_freq(percpu=True)
            if not freqs:
                return None, None
            cur = [f.current for f in freqs if f and f.current is not None]
            if not cur:
                return None, None
            return float(sum(cur) / len(cur)), float(max(cur))
        except Exception:
            return None, None

    def _loadavg(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        try:
            la = psutil.getloadavg()
            return float(la[0]), float(la[1]), float(la[2])
        except Exception:
            return None, None, None

    def _proc_cpu_deltas(self, dt_s: float) -> Tuple[Optional[float], Optional[float]]:
        if not self.cfg.pids or dt_s <= 0 or self._cpu_logical <= 0:
            return None, None
        total_cpu_sec = 0.0
        total_cpu_pct = 0.0
        for pid in self.cfg.pids:
            pid = int(pid)
            try:
                p = psutil.Process(pid)
                ct = p.cpu_times()
                cur = float(ct.user + ct.system)
            except Exception:
                cur = float("nan")

            prev = self._proc_prev_cpu.get(pid, float("nan"))
            self._proc_prev_cpu[pid] = cur

            if not (cur == cur and prev == prev):  # NaN
                continue
            d = cur - prev
            if d < 0:
                continue
            total_cpu_sec += d
            total_cpu_pct += (d / (dt_s * float(self._cpu_logical))) * 100.0
        return total_cpu_sec, total_cpu_pct

    def sample_once(self) -> None:
        # a blocking, per core read for a precise window
        # record timestamps before + after to compute the real dt
        t0_wall = now_wall_s()
        t0_mono = now_mono_s()

        per_core = psutil.cpu_percent(interval=self.cfg.interval_s, percpu=True)
        cpu_total_pct = psutil.cpu_percent(interval=None, percpu=False)

        t1_mono = now_mono_s()
        dt = t1_mono - t0_mono
        if dt <= 0:
            dt = float(self.cfg.interval_s)

        # A: utilization stats over cores
        cores = sorted(float(x) for x in per_core) if per_core else []
        mean_core = (sum(cores) / len(cores)) if cores else None
        max_core = (max(cores)) if cores else None
        # p50 = percentile(cores, 50.0) if cores else None
        # p95 = percentile(cores, 95.0) if cores else None

        # B: saturation
        la1, la5, la15 = self._loadavg()
        procs_running, procs_blocked = _procstat_procs_running_blocked()
        cur_stats = psutil.cpu_stats()

        ctx_d = cur_stats.ctx_switches - self._prev_cpu_stats.ctx_switches
        intr_d = cur_stats.interrupts - self._prev_cpu_stats.interrupts
        soft_d = cur_stats.soft_interrupts - self._prev_cpu_stats.soft_interrupts
        sysc_d = getattr(cur_stats, "syscalls", 0) - getattr(self._prev_cpu_stats, "syscalls", 0)
        self._prev_cpu_stats = cur_stats

        cg = _read_cgroup_cpu_throttle()
        cg_throttled_d = None
        cg_usec_d = None
        try:
            if cg["cg_nr_throttled"] is not None and self._prev_cg["cg_nr_throttled"] is not None:
                cg_throttled_d = cg["cg_nr_throttled"] - self._prev_cg["cg_nr_throttled"]
            if cg["cg_throttled_usec"] is not None and self._prev_cg["cg_throttled_usec"] is not None:
                cg_usec_d = cg["cg_throttled_usec"] - self._prev_cg["cg_throttled_usec"]
        except Exception:
            pass
        self._prev_cg = cg

        # C + D: breakdown via cpu_times deltas
        cur_times = psutil.cpu_times()
        prev_times = self._prev_cpu_times
        self._prev_cpu_times = cur_times

        def dfield(name: str) -> float:
            return float(getattr(cur_times, name, 0.0) - getattr(prev_times, name, 0.0))

        sec_user_d = dfield("user")
        sec_system_d = dfield("system")
        sec_idle_d = dfield("idle")
        sec_iowait_d = dfield("iowait")
        sec_irq_d = dfield("irq")
        sec_softirq_d = dfield("softirq")
        sec_steal_d = dfield("steal")
        sec_guest_d = dfield("guest")
        sec_guest_nice_d = dfield("guest_nice")

        # total sum of all deltas
        sec_total_d = 0.0
        for fname in getattr(cur_times, "_fields", ()):
            try:
                sec_total_d += max(0.0, dfield(fname))
            except Exception:
                pass
        sec_total_d = sec_total_d if sec_total_d > 0 else None

        def pct(x: float) -> Optional[float]:
            if sec_total_d is None or sec_total_d <= 0:
                return None
            return (x / sec_total_d) * 100.0

        # D: per process cpu time deltas
        proc_cpu_sec_sum, proc_cpu_pct_sum = self._proc_cpu_deltas(dt)

        freq_mean, freq_max = self._freq_stats()

        self.logger.write({
            "ts_wall_s": t0_wall,
            "ts_mono_s": t1_mono,
            "dt_s": float(dt),
            "cpu_logical": self._cpu_logical,
            "cpu_physical": self._cpu_physical,
            "cpu_freq_cur_mhz_mean": freq_mean,
            "cpu_freq_cur_mhz_max": freq_max,
            "cpu_total_percent": float(cpu_total_pct),
            "cpu_mean_core_percent": mean_core,
            "cpu_max_core_percent": max_core,
            #"cpu_p50_core_percent": p50,
            #"cpu_p95_core_percent": p95,
            "cpu_core_count_sampled": len(cores),
            "loadavg_1": la1,
            "loadavg_5": la5,
            "loadavg_15": la15,
            "procs_running": procs_running,
            "procs_blocked": procs_blocked,
            "ctx_switches_d": int(ctx_d),
            "interrupts_d": int(intr_d),
            "soft_interrupts_d": int(soft_d),
            "syscalls_d": int(sysc_d),
            "cg_nr_periods": cg["cg_nr_periods"],
            "cg_nr_throttled": cg["cg_nr_throttled"],
            "cg_throttled_usec": cg["cg_throttled_usec"],
            "cg_nr_throttled_d": cg_throttled_d,
            "cg_throttled_usec_d": cg_usec_d,
            "pct_user": pct(sec_user_d),
            "pct_system": pct(sec_system_d),
            "pct_idle": pct(sec_idle_d),
            "pct_iowait": pct(sec_iowait_d),
            "pct_irq": pct(sec_irq_d),
            "pct_softirq": pct(sec_softirq_d),
            "pct_steal": pct(sec_steal_d),
            "pct_guest": pct(sec_guest_d),
            "pct_guest_nice": pct(sec_guest_nice_d),
            "sec_user_d": sec_user_d,
            "sec_system_d": sec_system_d,
            "sec_idle_d": sec_idle_d,
            "sec_iowait_d": sec_iowait_d,
            "sec_irq_d": sec_irq_d,
            "sec_softirq_d": sec_softirq_d,
            "sec_steal_d": sec_steal_d,
            "sec_guest_d": sec_guest_d,
            "sec_guest_nice_d": sec_guest_nice_d,
            "sec_total_d": sec_total_d,
            "proc_cpu_sec_d_sum": proc_cpu_sec_sum,
            "proc_cpu_pct_total_sum": proc_cpu_pct_sum,
        })
