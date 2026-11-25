from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import psutil

from .targets import TargetResolver, Targets, boottime_now, ext_path, started_after
from .util import CsvLogger, now_mono_s, now_wall_s, read_text


# Output files:
#   <app>-cpu.csv, <app>-cpu_core.csv, <app>-cpu_thread.csv, <app>-<name>_core.csv:
#       original columns, corrected values.
#   <app>-cpu_ext.csv: columns added since the original version.
#   <app>-cpu_thread_ext.csv, <app>-<name>_core_ext.csv: the exact interval of
#       each thread/process row (thr_dt_s / proc_dt_s), joinable on
#       (ts_mono_s, pid[, tid]).
#
# Per-process/thread rates (cores_equiv, pct_total) are divided by that
# process's or thread's own interval: the time between its two reads, not the
# sample's dt_s, since processes are read some ms after the sample starts and
# that offset varies. dt_s in the original files stays the sample interval.
# A process/thread that started during the interval counts from the start of
# the interval (the previous sample's ts_mono_s), so the rates of all
# processes cover the same second and add up.
#
# Sampling is non-blocking: the controller calls sample_once() on a fixed time
# grid, and every delta in a row (per-core %, cpu_times, ctx switches, process
# and thread CPU) covers the same window: previous sample -> this sample.
#
# cgroup numbers: how much CPU a group of processes used and whether it was
# throttled by a CPU limit (container quotas).

# cpu_times fields that are already included in user/nice on Linux.
# Adding them to the total double counts guest (VM) time.
_TIME_FIELDS_INCLUDED_ELSEWHERE = ("guest", "guest_nice")


def _procstat_procs_running_blocked() -> Tuple[Optional[int], Optional[int]]:
    """
    /proc/stat:
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


def _find_cgroup_cpu_stat_paths(pid: Optional[int] = None) -> list[str]:
    """
    cpu.stat candidates for the cgroup of <pid> (or of this process), v2 and v1.
    Most specific first; the v2 root goes last because it exists everywhere but
    never has throttling fields.
    """
    paths: list[str] = []

    cg = read_text(f"/proc/{pid}/cgroup" if pid else "/proc/self/cgroup")
    if cg:
        for line in cg.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            _, controllers, rel = parts
            rel = rel.strip().lstrip("/")
            if controllers == "":
                # cgroup v2
                paths.append(os.path.join("/sys/fs/cgroup", rel, "cpu.stat"))
            elif "cpu" in controllers.split(","):
                # cgroup v1
                paths.append(os.path.join("/sys/fs/cgroup", controllers, rel, "cpu.stat"))
                paths.append(os.path.join("/sys/fs/cgroup", "cpu,cpuacct", rel, "cpu.stat"))
                paths.append(os.path.join("/sys/fs/cgroup", "cpu", rel, "cpu.stat"))

    paths.append("/sys/fs/cgroup/cpu.stat")  # v2 root, fallback only

    out: list[str] = []
    seen = set()
    for p in paths:
        p = os.path.normpath(p)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _read_cgroup_cpu_throttle(pid: Optional[int] = None) -> dict[str, Any]:
    """
    Parse cgroup cpu.stat to find cases where a container was CPU-throttled
    (e.g. in mini-apps where one of the containers didn't start).
      v2: nr_periods, nr_throttled, throttled_usec
      v1: nr_periods, nr_throttled, throttled_time (ns)
    """
    out: dict[str, Any] = {
        "cg_nr_periods": None,
        "cg_nr_throttled": None,
        "cg_throttled_usec": None,
        "cg_path": None,
    }

    txt = None
    for p in _find_cgroup_cpu_stat_paths(pid):
        if os.path.exists(p):
            txt = read_text(p)
            if txt:
                out["cg_path"] = p
                break
    if not txt:
        return out

    kv: dict[str, int] = {}
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            kv[parts[0]] = int(parts[1])
        except Exception:
            pass

    out["cg_nr_periods"] = kv.get("nr_periods")
    out["cg_nr_throttled"] = kv.get("nr_throttled")
    if "throttled_usec" in kv:
        out["cg_throttled_usec"] = kv["throttled_usec"]
    elif "throttled_time" in kv:      # v1, nanoseconds
        out["cg_throttled_usec"] = kv["throttled_time"] // 1000
    elif "throttled_ns" in kv:
        out["cg_throttled_usec"] = kv["throttled_ns"] // 1000
    return out


def _safe_filename_part(s: str) -> str:
    """
    Make process-name strings safe to use as CSV filenames.
    """
    out = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s.strip())
    return out or "process"


@dataclass
class CpuMonitorConfig:
    interval_s: float = 1.0
    pids: Optional[Sequence[int]] = None
    process_names: Optional[Sequence[str]] = None

    # tree handling (applies to --pids and --process-names matches)
    include_children: bool = True
    recursive_children: bool = True

    # per thread logging, writes in another csv
    record_threads: bool = True

    # per logical CPU logging, writes in another csv
    record_cpus: bool = True
    active_cpu_threshold_pct: float = 5.0


CPU_FIELDS = [
    "ts_wall_s",
    "ts_mono_s",
    "dt_s",
    "cpu_logical",
    "cpu_physical",
    "cpu_freq_cur_mhz_mean",
    "cpu_freq_cur_mhz_max",

    "cpu_total_percent",
    "cpu_mean_core_percent",
    "cpu_max_core_percent",
    "cpu_core_count_sampled",

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
    # cpu time breakdown (%)
    "pct_user",
    "pct_system",
    "pct_idle",
    "pct_iowait",
    "pct_irq",
    "pct_softirq",
    "pct_steal",
    "pct_guest",
    "pct_guest_nice",
    # cpu time deltas (s)
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
    # process-tree aggregate
    "proc_pid_count",
    "proc_cpu_sec_d_sum",
    "proc_cpu_pct_total_sum",
    "proc_cpu_cores_equiv",
]

CPU_EXT_FIELDS = [
    "ts_wall_s",
    "ts_mono_s",
    "dt_s",
    "pct_nice",
    "sec_nice_d",
    "cg_path",      # which cpu.stat the cg_* columns in cpu.csv came from
    "proc_pids",    # PIDs counted in proc_* this interval (";"-separated)
]


class CpuMonitor:
    """
      - system cpu utilization + saturation + breakdown per interval
      - aggregate cpu usage of the target processes (--pids, --process-names,
        and their children; re-evaluated every sample)
      - per-thread cpu deltas (pid, tid rows)
    """

    def __init__(
        self,
        cfg: CpuMonitorConfig,
        out_cpu_path: str,
        *,
        out_thread_path: str,
        out_core_path: str,
        out_ext_path: Optional[str] = None,
        name_file_prefix: str = "",
        flush_every: int = 1,
    ) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(out_cpu_path, fieldnames=CPU_FIELDS, flush_every=flush_every)
        self.ext_logger = CsvLogger(
            out_ext_path or ext_path(out_cpu_path), fieldnames=CPU_EXT_FIELDS, flush_every=flush_every
        )

        self.core_logger: Optional[CsvLogger] = None
        if cfg.record_cpus:
            if not out_core_path:
                raise ValueError("record_cpus=True requires out_core_path")
            self.core_logger = CsvLogger(
                out_core_path,
                fieldnames=[
                    "ts_wall_s",
                    "ts_mono_s",
                    "dt_s",
                    "cpu_id",
                    "cpu_percent",
                    "active",
                ],
                flush_every=flush_every,
            )

        self.proc_name_core_loggers: dict[str, CsvLogger] = {}
        self.proc_name_ext_loggers: dict[str, CsvLogger] = {}
        if cfg.process_names:
            core_parent = Path(out_core_path).parent if out_core_path else Path(out_cpu_path).parent
            for process_name in cfg.process_names:
                process_name = process_name.strip()
                if not process_name:
                    continue
                safe_process_name = _safe_filename_part(process_name)
                self.proc_name_ext_loggers[process_name] = CsvLogger(
                    str(core_parent / f"{name_file_prefix}{safe_process_name}_core_ext.csv"),
                    fieldnames=["ts_wall_s", "ts_mono_s", "dt_s", "process_name", "pid", "proc_dt_s"],
                    flush_every=flush_every,
                )
                self.proc_name_core_loggers[process_name] = CsvLogger(
                    str(core_parent / f"{name_file_prefix}{safe_process_name}_core.csv"),
                    fieldnames=[
                        "ts_wall_s",
                        "ts_mono_s",
                        "dt_s",
                        "process_name",
                        "pid",
                        "cpu_num",
                        "proc_cpu_sec_d",
                        "proc_cpu_pct_total",
                        "proc_cpu_cores_equiv",
                    ],
                    flush_every=flush_every,
                )

        self.thread_logger: Optional[CsvLogger] = None
        if cfg.record_threads:
            if not out_thread_path:
                raise ValueError("record_threads=True requires out_thread_path")
            self.thread_logger = CsvLogger(
                out_thread_path,
                fieldnames=[
                    "ts_wall_s",
                    "ts_mono_s",
                    "dt_s",
                    "pid",
                    "tid",
                    "thr_cpu_sec_d",
                    "thr_cpu_pct_total",
                    "thr_cpu_cores_equiv",
                ],
                flush_every=flush_every,
            )
            root, ext = os.path.splitext(out_thread_path)
            self.thread_ext_logger: Optional[CsvLogger] = CsvLogger(
                f"{root}_ext{ext or '.csv'}",
                fieldnames=["ts_wall_s", "ts_mono_s", "dt_s", "pid", "tid", "thr_dt_s"],
                flush_every=flush_every,
            )
        else:
            self.thread_ext_logger = None

        self._cpu_logical = psutil.cpu_count(logical=True) or 0
        self._cpu_physical = psutil.cpu_count(logical=False) or 0

        self._targets = TargetResolver(
            cfg.pids,
            cfg.process_names,
            include_children=cfg.include_children,
            recursive_children=cfg.recursive_children,
        )

        # per pid previous (cpu seconds user+system, monotonic time of the read)
        # process-name keys are ("process_name", name, pid)
        self._proc_prev_cpu: Dict[Any, Tuple[float, float]] = {}

        # per thread previous (cpu seconds, monotonic time of the read): (pid, tid) -> ...
        self._thr_prev_cpu: Dict[Tuple[int, int], Tuple[float, float]] = {}

        # Baselines for processes that already exist.
        t = self._targets.resolve()
        for pid in t.pids:
            self._proc_prev_cpu[pid] = self._read_pid_cpu_ts(pid)
        for name, pids in t.by_name.items():
            for pid in pids:
                self._proc_prev_cpu[("process_name", name, pid)] = self._read_pid_cpu_ts(pid)
        if self.thread_logger is not None:
            self._thread_baselines(t.pids)
        self._cg_pid: Optional[int] = t.cgroup_pid

        # System baselines, all taken together, right before the first window starts.
        self._prev_cg = _read_cgroup_cpu_throttle(self._cg_pid)
        self._prev_cpu_stats = psutil.cpu_stats()
        self._prev_cpu_times = psutil.cpu_times()
        try:
            psutil.cpu_percent(interval=None, percpu=True)  # prime non-blocking mode
        except Exception:
            pass
        self._prev_mono = now_mono_s()
        self._prev_boot = boottime_now()

    def close(self) -> None:
        loggers = [self.logger, self.ext_logger, self.thread_logger, self.thread_ext_logger, self.core_logger]
        loggers += list(self.proc_name_core_loggers.values())
        loggers += list(self.proc_name_ext_loggers.values())
        for lg in loggers:
            if lg is None:
                continue
            try:
                lg.close()
            except Exception:
                pass

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

    @staticmethod
    def _read_pid_cpu(pid: int) -> float:
        """
        Returns (user+system) cpu time in seconds for the pid, or NaN on failure.
        """
        try:
            ct = psutil.Process(pid).cpu_times()
            return float(ct.user + ct.system)
        except Exception:
            return float("nan")

    @classmethod
    def _read_pid_cpu_ts(cls, pid: int) -> Tuple[float, float]:
        """(cpu seconds, monotonic time right after the read)"""
        cpu = cls._read_pid_cpu(pid)
        return cpu, now_mono_s()

    @staticmethod
    def _own_interval(prev_ts: float, cur_ts: float, fallback: float) -> float:
        d = cur_ts - prev_ts
        return d if d > 0 else fallback

    def _proc_cpu_deltas(
        self, pid_list: list[int], dt_s: float, since_boot: float, since_mono: float
    ) -> Tuple[Optional[float], Optional[float], Optional[float], list[int]]:
        """
        returns:
          (sum_cpu_seconds, pct_of_total_logical, cores_equiv, pids_counted)
        cores_equiv = sum over processes of (cpu seconds / that process's own interval).
        """
        # drop PIDs that are no longer targets
        live = set(pid_list)
        for k in list(self._proc_prev_cpu.keys()):
            if isinstance(k, int) and k not in live:
                del self._proc_prev_cpu[k]

        if not pid_list or dt_s <= 0 or self._cpu_logical <= 0:  # same as original: None when nothing to measure
            return None, None, None, []
        total_cpu_sec = 0.0
        cores_equiv = 0.0
        counted: list[int] = []
        for pid in pid_list:
            cur, cur_ts = self._read_pid_cpu_ts(pid)
            prev = self._proc_prev_cpu.get(pid)
            self._proc_prev_cpu[pid] = (cur, cur_ts)

            if prev is None and started_after(since_boot, pid):
                prev = (0.0, since_mono)
            if prev is None or not (cur == cur and prev[0] == prev[0]):  # unknown baseline or NaN
                continue
            d = cur - prev[0]
            if d < 0:
                continue
            total_cpu_sec += d
            cores_equiv += d / self._own_interval(prev[1], cur_ts, dt_s)
            counted.append(pid)

        pct_total = (cores_equiv / float(self._cpu_logical)) * 100.0
        return total_cpu_sec, pct_total, cores_equiv, counted

    def _thread_baselines(self, pid_list: list[int]) -> None:
        for pid in pid_list:
            try:
                threads = psutil.Process(pid).threads()
            except Exception:
                continue
            ts = now_mono_s()
            for th in threads:
                self._thr_prev_cpu[(pid, int(th.id))] = (float(th.user_time + th.system_time), ts)

    def _write_core_usage(self, per_core: Sequence[float], dt_s: float, ts_wall: float, ts_mono: float) -> None:
        """
        One row per logical CPU (psutil order: CPU 0 .. N-1).
        """
        if self.core_logger is None or dt_s <= 0:
            return

        for cpu_id, cpu_pct in enumerate(per_core):
            cpu_pct = float(cpu_pct)
            self.core_logger.write({
                "ts_wall_s": ts_wall,
                "ts_mono_s": ts_mono,
                "dt_s": float(dt_s),
                "cpu_id": int(cpu_id),
                "cpu_percent": cpu_pct,
                "active": int(cpu_pct >= self.cfg.active_cpu_threshold_pct),
            })

    def _write_process_name_cpu_rows(
        self, by_name: Dict[str, list[int]], dt_s: float, since_boot: float, since_mono: float,
        ts_wall: float, ts_mono: float,
    ) -> None:
        """
        Per-process-name CPU rows in <app>-<name>_core.csv (direct name matches only),
        and each row's own interval in <app>-<name>_core_ext.csv.
        cpu_num is the last logical CPU where the process was observed;
        Linux may migrate it during the interval.
        """
        if not self.proc_name_core_loggers:
            return
        if dt_s <= 0 or self._cpu_logical <= 0:
            return

        live_keys: set[tuple[str, str, int]] = set()

        for process_name, pid_list in by_name.items():
            logger = self.proc_name_core_loggers.get(process_name)
            ext_logger = self.proc_name_ext_loggers.get(process_name)
            if logger is None:
                continue

            for pid in pid_list:
                try:
                    p = psutil.Process(pid)
                    ct = p.cpu_times()
                    cur_ts = now_mono_s()
                    cur = float(ct.user + ct.system)
                    cpu_num = p.cpu_num()
                except Exception:
                    continue

                key = ("process_name", process_name, int(pid))
                live_keys.add(key)
                prev = self._proc_prev_cpu.get(key)
                self._proc_prev_cpu[key] = (cur, cur_ts)

                if prev is None and started_after(since_boot, pid):
                    prev = (0.0, since_mono)
                if prev is None or not (cur == cur and prev[0] == prev[0]):
                    continue

                d = cur - prev[0]
                if d < 0:
                    continue
                own_dt = self._own_interval(prev[1], cur_ts, dt_s)

                logger.write({
                    "ts_wall_s": ts_wall,
                    "ts_mono_s": ts_mono,
                    "dt_s": float(dt_s),
                    "process_name": process_name,
                    "pid": int(pid),
                    "cpu_num": int(cpu_num),
                    "proc_cpu_sec_d": float(d),
                    "proc_cpu_pct_total": float((d / (own_dt * float(self._cpu_logical))) * 100.0),
                    "proc_cpu_cores_equiv": float(d / own_dt),
                })
                if ext_logger is not None:
                    ext_logger.write({
                        "ts_wall_s": ts_wall,
                        "ts_mono_s": ts_mono,
                        "dt_s": float(dt_s),
                        "process_name": process_name,
                        "pid": int(pid),
                        "proc_dt_s": float(own_dt),
                    })

        for k in list(self._proc_prev_cpu.keys()):
            if isinstance(k, tuple) and len(k) == 3 and k[0] == "process_name" and k not in live_keys:
                del self._proc_prev_cpu[k]

    def _write_thread_deltas(
        self, pid_list: list[int], dt_s: float, since_boot: float, since_mono: float,
        ts_wall: float, ts_mono: float,
    ) -> None:
        if self.thread_logger is None:
            return
        if dt_s <= 0 or self._cpu_logical <= 0:
            return

        cur_map: Dict[Tuple[int, int], Tuple[float, float]] = {}
        for pid in pid_list:
            try:
                threads = psutil.Process(pid).threads()
            except Exception:
                continue
            ts = now_mono_s()  # all threads of a process are read in one call
            for th in threads:
                cur_map[(pid, int(th.id))] = (float(th.user_time + th.system_time), ts)

        for key, (cur, cur_ts) in cur_map.items():
            prev = self._thr_prev_cpu.get(key)
            self._thr_prev_cpu[key] = (cur, cur_ts)

            # Threads created during this interval count from its start.
            if prev is None and started_after(since_boot, key[0], key[1]):
                prev = (0.0, since_mono)
            if prev is None or not (cur == cur and prev[0] == prev[0]):
                continue

            d = cur - prev[0]
            if d < 0:
                continue
            own_dt = self._own_interval(prev[1], cur_ts, dt_s)

            pid, tid = key
            self.thread_logger.write({
                "ts_wall_s": ts_wall,
                "ts_mono_s": ts_mono,
                "dt_s": float(dt_s),
                "pid": int(pid),
                "tid": int(tid),
                "thr_cpu_sec_d": float(d),
                "thr_cpu_pct_total": float((d / (own_dt * float(self._cpu_logical))) * 100.0),
                "thr_cpu_cores_equiv": float(d / own_dt),
            })
            if self.thread_ext_logger is not None:
                self.thread_ext_logger.write({
                    "ts_wall_s": ts_wall,
                    "ts_mono_s": ts_mono,
                    "dt_s": float(dt_s),
                    "pid": int(pid),
                    "tid": int(tid),
                    "thr_dt_s": float(own_dt),
                })

        for k in list(self._thr_prev_cpu.keys()):
            if k not in cur_map:
                del self._thr_prev_cpu[k]

    def sample_once(self) -> None:
        # ---- read all system-wide counters together (same window) ----
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        self.last_ts = (t_wall, t_mono)  # used for the matching timesync row
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        cur_times = psutil.cpu_times()
        cur_stats = psutil.cpu_stats()

        since_mono = self._prev_mono
        dt = t_mono - since_mono
        if dt <= 0:
            dt = float(self.cfg.interval_s)
        t_boot = boottime_now()
        since_boot = self._prev_boot
        self._prev_mono = t_mono
        self._prev_boot = t_boot

        cores = [float(x) for x in per_core] if per_core else []
        cpu_mean_core = (sum(cores) / len(cores)) if cores else None
        cpu_max_core = max(cores) if cores else None
        cpu_total_pct = cpu_mean_core  # mean over cores = total over the same interval

        if cores:
            self._write_core_usage(cores, dt, t_wall, t_mono)

        # ---- saturation ----
        la1, la5, la15 = self._loadavg()
        procs_running, procs_blocked = _procstat_procs_running_blocked()

        ctx_d = cur_stats.ctx_switches - self._prev_cpu_stats.ctx_switches
        intr_d = cur_stats.interrupts - self._prev_cpu_stats.interrupts
        soft_d = cur_stats.soft_interrupts - self._prev_cpu_stats.soft_interrupts
        sysc_d = getattr(cur_stats, "syscalls", 0) - getattr(self._prev_cpu_stats, "syscalls", 0)
        self._prev_cpu_stats = cur_stats

        # ---- breakdown via cpu_times deltas ----
        prev_times = self._prev_cpu_times
        self._prev_cpu_times = cur_times

        def dfield(name: str) -> float:
            return float(getattr(cur_times, name, 0.0) - getattr(prev_times, name, 0.0))

        sec_user_d = dfield("user")
        sec_nice_d = dfield("nice")
        sec_system_d = dfield("system")
        sec_idle_d = dfield("idle")
        sec_iowait_d = dfield("iowait")
        sec_irq_d = dfield("irq")
        sec_softirq_d = dfield("softirq")
        sec_steal_d = dfield("steal")
        sec_guest_d = dfield("guest")
        sec_guest_nice_d = dfield("guest_nice")

        sec_total_d: Optional[float] = 0.0
        for fname in getattr(cur_times, "_fields", ()):
            if fname in _TIME_FIELDS_INCLUDED_ELSEWHERE:
                continue
            sec_total_d += max(0.0, dfield(fname))
        if sec_total_d <= 0:
            sec_total_d = None

        def pct(x: float) -> Optional[float]:
            if sec_total_d is None:
                return None
            return (x / sec_total_d) * 100.0

        # ---- target processes (re-found every sample) ----
        targets: Targets = self._targets.resolve()
        self._write_process_name_cpu_rows(targets.by_name, dt, since_boot, since_mono, t_wall, t_mono)

        pid_list = targets.pids
        proc_cpu_sec_sum, proc_cpu_pct_sum, proc_cpu_cores_equiv, counted = self._proc_cpu_deltas(
            pid_list, dt, since_boot, since_mono
        )

        if self.thread_logger is not None and pid_list:
            self._write_thread_deltas(pid_list, dt, since_boot, since_mono, t_wall, t_mono)

        # ---- cgroup throttling of the monitored app ----
        if targets.cgroup_pid is not None:
            self._cg_pid = targets.cgroup_pid
        cg = _read_cgroup_cpu_throttle(self._cg_pid)
        cg_throttled_d = None
        cg_usec_d = None
        if cg["cg_path"] is not None and cg["cg_path"] == self._prev_cg.get("cg_path"):
            if cg["cg_nr_throttled"] is not None and self._prev_cg["cg_nr_throttled"] is not None:
                cg_throttled_d = cg["cg_nr_throttled"] - self._prev_cg["cg_nr_throttled"]
            if cg["cg_throttled_usec"] is not None and self._prev_cg["cg_throttled_usec"] is not None:
                cg_usec_d = cg["cg_throttled_usec"] - self._prev_cg["cg_throttled_usec"]
        self._prev_cg = cg

        freq_mean, freq_max = self._freq_stats()

        self.logger.write({
            "ts_wall_s": t_wall,
            "ts_mono_s": t_mono,
            "dt_s": float(dt),
            "cpu_logical": int(self._cpu_logical),
            "cpu_physical": int(self._cpu_physical),
            "cpu_freq_cur_mhz_mean": freq_mean,
            "cpu_freq_cur_mhz_max": freq_max,
            "cpu_total_percent": cpu_total_pct,
            "cpu_mean_core_percent": cpu_mean_core,
            "cpu_max_core_percent": cpu_max_core,
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
            "proc_pid_count": len(pid_list),
            "proc_cpu_sec_d_sum": proc_cpu_sec_sum,
            "proc_cpu_pct_total_sum": proc_cpu_pct_sum,
            "proc_cpu_cores_equiv": proc_cpu_cores_equiv,
        })

        self.ext_logger.write({
            "ts_wall_s": t_wall,
            "ts_mono_s": t_mono,
            "dt_s": float(dt),
            "pct_nice": pct(sec_nice_d),
            "sec_nice_d": sec_nice_d,
            "cg_path": cg["cg_path"],
            "proc_pids": ";".join(str(x) for x in counted),
        })