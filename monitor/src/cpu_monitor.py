from __future__ import annotations

import os
from dataclasses import dataclass
#from typing import Dict, Optional, Sequence, Tuple
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

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
    v2 cpu.stat:
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


def _expand_pid_tree(root_pids: Sequence[int], *, recursive: bool = True) -> list[int]:
    """
    Return sorted unique PIDs = roots + children that exist now.
    """
    out: set[int] = set()
    for r in root_pids:
        try:
            p = psutil.Process(int(r))
        except Exception:
            continue
        out.add(int(p.pid))
        try:
            for ch in p.children(recursive=recursive):
                out.add(int(ch.pid))
        except Exception:
            pass
    return sorted(out)


def _safe_filename_part(s: str) -> str:
    """
    Make process-name strings safe to use as CSV filenames.
    """
    out = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s.strip())
    return out or "process"


def _find_pids_by_names(names: Sequence[str]) -> dict[str, list[int]]:
    """
    Find matching PIDs every sample.

    Matching rule:
      - exact match against process name, OR
      - substring match against full command line

    This lets monitoring work even when the process does not exist at controller start.
    """
    wanted = [n.strip() for n in names if n and n.strip()]
    out: dict[str, set[int]] = {n: set() for n in wanted}
    if not wanted:
        return {}

    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = int(p.info["pid"])
            pname = p.info.get("name") or ""
            cmdline = " ".join(p.info.get("cmdline") or [])

            for wanted_name in wanted:
                if pname == wanted_name or wanted_name in cmdline:
                    out[wanted_name].add(pid)
        except Exception:
            continue

    return {k: sorted(v) for k, v in out.items()}


# @dataclass
# class CpuMonitorConfig:
#     interval_s: float = 1.0   # TODO: try a test with setting the interval to duration      
#     pids: Optional[Sequence[int]] = None

#     # tree handling
#     include_children: bool = True
#     recursive_children: bool = True

#     # per thread logging, writes in another csv
#     record_threads: bool = True
@dataclass
class CpuMonitorConfig:
    interval_s: float = 1.0
    pids: Optional[Sequence[int]] = None
    process_names: Optional[Sequence[str]] = None

    # tree handling
    include_children: bool = True
    recursive_children: bool = True

    # per thread logging, writes in another csv
    record_threads: bool = True

    # per logical CPU logging, writes in another csv
    record_cpus: bool = True
    active_cpu_threshold_pct: float = 5.0

class CpuMonitor:
    """
      - system cpu utilization + saturation + breakdown per interval
      - aggregate cpi usage of a target PID set + expanded to child processes
      - per-thread cpu deltas (pid, tid rows)
    """

    # TODO: monitor specific processes(with all threads and children) by name and not only pids 
    # also consider that some additional threads will be created after and during the workflow
    # TODO: get the per processes stats, and specifically for active processes 
    # plus the ones from input pids or names
    #def __init__(self, cfg: CpuMonitorConfig, out_csv_path: str) -> None:
    
    # def __init__(self, cfg: CpuMonitorConfig, out_cpu_path: str, *,
    #     out_thread_path: str, flush_every: int = 1) -> None:
    def __init__(
        self,
        cfg: CpuMonitorConfig,
        out_cpu_path: str,
        *,
        out_thread_path: str,
        out_core_path: str,
        flush_every: int = 1,
    ) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            #out_csv_path
            out_cpu_path,
            fieldnames=[
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
                #"cpu_p50_core_percent",
                #"cpu_p95_core_percent",
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
            ],
            flush_every=flush_every,
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
        if cfg.process_names:
            core_parent = Path(out_core_path).parent if out_core_path else Path(out_cpu_path).parent
            for process_name in cfg.process_names:
                process_name = process_name.strip()
                if not process_name:
                    continue
                safe_process_name = _safe_filename_part(process_name)
                self.proc_name_core_loggers[process_name] = CsvLogger(
                    str(core_parent / f"{safe_process_name}_core.csv"),
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

        self._cpu_logical = psutil.cpu_count(logical=True) or 0
        self._cpu_physical = psutil.cpu_count(logical=False) or 0

        # snapshots
        self._prev_cpu_times = psutil.cpu_times()
        self._prev_cpu_stats = psutil.cpu_stats()
        self._prev_cg = _read_cgroup_cpu_throttle()
        #self._prev_mono: Optional[float] = None

        # per pid previous cpu times: pid -> (user+system) # TODO: or just user!?
        # self._proc_prev_cpu: Dict[int, float] = {}
        self._proc_prev_cpu: Dict[Any, float] = {}

        # per thread previous cpu times: (pid, tid) -> (user+system)
        self._thr_prev_cpu: Dict[Tuple[int, int], float] = {}

        # psutil cpu_percent internal state
        try:
            psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

        # pid cpu baselines (procs may not exist yet)
        if cfg.pids:
            for pid in self._current_pid_list():
                self._proc_prev_cpu[pid] = self._read_pid_cpu(pid)

            if self.thread_logger is not None:
                self._thread_baselines(self._current_pid_list())

        # if cfg.pids:
        #     for pid in _expand_pid_tree(cfg.pids):
        #         try:
        #             p = psutil.Process(pid)
        #             ct = p.cpu_times()
        #             self._proc_prev_cpu[pid] = float(ct.user + ct.system)
        #         except Exception:
        #             self._proc_prev_cpu[pid] = float("nan")

        # try:
        #     psutil.cpu_percent(interval=None, percpu=True)
        #     psutil.cpu_percent(interval=None, percpu=False)
        # except Exception:
        #     pass

    # def close(self) -> None:
    #     self.logger.close()

    # def _freq_stats(self) -> Tuple[Optional[float], Optional[float]]:
    #     try:
    #         freqs = psutil.cpu_freq(percpu=True)
    #         if not freqs:
    #             return None, None
    #         cur = [f.current for f in freqs if f and f.current is not None]
    #         if not cur:
    #             return None, None
    #         return float(sum(cur) / len(cur)), float(max(cur))
    #     except Exception:
    #         return None, None

    # def _loadavg(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    #     try:
    #         la = psutil.getloadavg()
    #         return float(la[0]), float(la[1]), float(la[2])
    #     except Exception:
    #         return None, None, None

    # def close(self) -> None:
    #     try:
    #         self.logger.close()
    #     finally:
    #         if self.thread_logger is not None:
    #             self.thread_logger.close()
    # def close(self) -> None:
    #     try:
    #         self.logger.close()
    #     finally:
    #         try:
    #             if self.thread_logger is not None:
    #                 self.thread_logger.close()
    #         finally:
    #             if self.core_logger is not None:
    #                 self.core_logger.close()
    def close(self) -> None:
        try:
            self.logger.close()
        finally:
            try:
                if self.thread_logger is not None:
                    self.thread_logger.close()
            finally:
                try:
                    if self.core_logger is not None:
                        self.core_logger.close()
                finally:
                    for logger in self.proc_name_core_loggers.values():
                        logger.close()

    def _current_pid_list(self) -> list[int]:
        if not self.cfg.pids:
            return []
        if self.cfg.include_children:
            return _expand_pid_tree(self.cfg.pids, recursive=self.cfg.recursive_children)
        return sorted({int(x) for x in self.cfg.pids})

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
            p = psutil.Process(pid)
            ct = p.cpu_times()
            return float(ct.user + ct.system)
        except Exception:
            return float("nan")

    #def _proc_cpu_deltas(self, dt_s: float) -> Tuple[Optional[float], Optional[float]]:
    #def _proc_cpu_deltas(self, dt_s: float, pids: Sequence[int]) -> Tuple[Optional[float], Optional[float]]:
    def _proc_cpu_deltas(self, pid_list: list[int], dt_s: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        returns:
          (sum_cpu_seconds, pct_of_total_logical, cores_equiv)
        """
        if not pid_list or dt_s <= 0 or self._cpu_logical <= 0:
            return None, None, None
        total_cpu_sec = 0.0
        total_cpu_pct = 0.0
        #for pid in self.cfg.pids:
        #for pid in _expand_pid_tree(self.cfg.pids):
        # for pid in pid:
        #     pid = int(pid)
        #     try:
        #         p = psutil.Process(pid)
        #         ct = p.cpu_times()
        #         cur = float(ct.user + ct.system)
        #     except Exception:
        #         cur = float("nan")
        
            # prev = self._proc_prev_cpu.get(pid, float("nan"))
            # self._proc_prev_cpu[pid] = cur
            # prev = self._proc_prev_cpu.get(pid)
            # self._proc_prev_cpu[pid] = cur
        for pid in pid_list:
            cur = self._read_pid_cpu(pid)
            prev = self._proc_prev_cpu.get(pid)
            self._proc_prev_cpu[pid] = cur

            # if not (cur == cur and prev == prev):  # NaN
            if prev is None or not (cur == cur and prev == prev):  # new pid or NaN
                continue
            d = cur - prev
            if d < 0:
                continue
            total_cpu_sec += d
            # TODO: check the normalize
            # total_cpu_pct += (d / (dt_s * float(self._cpu_logical))) * 100.0
        pct_total = (total_cpu_sec / (dt_s * float(self._cpu_logical))) * 100.0
        cores_equiv = total_cpu_sec / dt_s
        # return total_cpu_sec, total_cpu_pct
        return total_cpu_sec, pct_total, cores_equiv

    def _thread_baselines(self, pid_list: list[int]) -> None:
        for pid in pid_list:
            try:
                p = psutil.Process(pid)
                for th in p.threads():
                    key = (pid, int(th.id))
                    self._thr_prev_cpu[key] = float(th.user_time + th.system_time)
            except Exception:
                continue

    def _write_core_usage(self, per_core: Sequence[float], dt_s: float, ts_wall: float, ts_mono: float) -> None:
        """
        Write one row per logical CPU for this sample.

        psutil.cpu_percent(..., percpu=True) returns usage per logical CPU:
          CPU 0, CPU 1, ..., CPU N-1
        """
        if self.core_logger is None:
            return
        if dt_s <= 0:
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

    def _write_process_name_cpu_rows(self, dt_s: float, ts_wall: float, ts_mono: float) -> None:
        """
        Write per-process-name CPU rows into separate CSV files.

        Notes:
          - This finds processes by name/cmdline on every sample, so it works
            even if the process starts after the monitor starts.
          - cpu_num is the last logical CPU where the process was observed.
            It is not perfect interval-level core attribution because Linux may
            migrate the process/thread during the interval.
        """
        if not self.cfg.process_names:
            return
        if dt_s <= 0 or self._cpu_logical <= 0:
            return

        name_to_pids = _find_pids_by_names(self.cfg.process_names)
        live_keys: set[tuple[str, str, int]] = set()

        for process_name, pid_list in name_to_pids.items():
            logger = self.proc_name_core_loggers.get(process_name)
            if logger is None:
                continue

            for pid in pid_list:
                try:
                    p = psutil.Process(pid)
                    ct = p.cpu_times()
                    cur = float(ct.user + ct.system)
                    cpu_num = p.cpu_num()
                except Exception:
                    continue

                key = ("process_name", process_name, int(pid))
                live_keys.add(key)
                prev = self._proc_prev_cpu.get(key)
                self._proc_prev_cpu[key] = cur

                if prev is None:
                    continue
                if not (cur == cur and prev == prev):  # NaN
                    continue

                d = cur - prev
                if d < 0:
                    continue

                cores_equiv = d / dt_s
                pct_total = (d / (dt_s * float(self._cpu_logical))) * 100.0

                logger.write({
                    "ts_wall_s": ts_wall,
                    "ts_mono_s": ts_mono,
                    "dt_s": float(dt_s),
                    "process_name": process_name,
                    "pid": int(pid),
                    "cpu_num": int(cpu_num),
                    "proc_cpu_sec_d": float(d),
                    "proc_cpu_pct_total": float(pct_total),
                    "proc_cpu_cores_equiv": float(cores_equiv),
                })

        # Remove only stale process-name keys. Keep numeric PID keys used by --pids.
        for k in list(self._proc_prev_cpu.keys()):
            if isinstance(k, tuple) and len(k) == 3 and k[0] == "process_name" and k not in live_keys:
                del self._proc_prev_cpu[k]

    def _write_thread_deltas(self, pid_list: list[int], dt_s: float, ts_wall: float, ts_mono: float) -> None:
        if self.thread_logger is None:
            return
        if dt_s <= 0 or self._cpu_logical <= 0:
            return

        cur_map: Dict[Tuple[int, int], float] = {}
        for pid in pid_list:
            try:
                p = psutil.Process(pid)
                for th in p.threads():
                    key = (pid, int(th.id))
                    cur_map[key] = float(th.user_time + th.system_time)
            except Exception:
                continue

        # update and emit deltas
        for key, cur in cur_map.items():
            prev = self._thr_prev_cpu.get(key)
            self._thr_prev_cpu[key] = cur
            if prev is None:
                continue
            if not (cur == cur and prev == prev):  # NaN
                continue

            d = cur - prev
            if d < 0:
                continue

            thr_cores_equiv = d / dt_s
            thr_pct_total = (d / (dt_s * float(self._cpu_logical))) * 100.0

            pid, tid = key
            self.thread_logger.write({
                "ts_wall_s": ts_wall,
                "ts_mono_s": ts_mono,
                "dt_s": float(dt_s),
                "pid": int(pid),
                "tid": int(tid),
                "thr_cpu_sec_d": float(d),
                "thr_cpu_pct_total": float(thr_pct_total),
                "thr_cpu_cores_equiv": float(thr_cores_equiv),
            })

        # TODO: why it didn't fix it!
        for k in list(self._thr_prev_cpu.keys()):
            if k not in cur_map:
                del self._thr_prev_cpu[k]

    def sample_once(self) -> None:
        # record timestamps before + after to compute the real dt
        t0_wall = now_wall_s()
        t0_mono = now_mono_s()

        per_core = psutil.cpu_percent(interval=self.cfg.interval_s, percpu=True)
        t1_mono = now_mono_s()
        dt = t1_mono - t0_mono
        if dt <= 0:
            dt = float(self.cfg.interval_s)
        #cpu_total_pct = psutil.cpu_percent(interval=None, percpu=False)
        # cpu_total_pct = (sum(per_core) / len(per_core)) if per_core else 0.0
        # t1_mono = now_mono_s()
        # dt = t1_mono - t0_mono
        # if dt <= 0:
        #     dt = float(self.cfg.interval_s)
        
        # utilization stats over cores
        # cores = sorted(float(x) for x in per_core) if per_core else []
        # mean_core = (sum(cores) / len(cores)) if cores else None
        # max_core = (max(cores)) if cores else None
        # p50 = percentile(cores, 50.0) if cores else None
        # p95 = percentile(cores, 95.0) if cores else None
        cores = [float(x) for x in per_core] if per_core else []
        cpu_mean_core = (sum(cores) / len(cores)) if cores else None
        cpu_max_core = (max(cores)) if cores else None
        # total over same interval: mean over cores
        cpu_total_pct = cpu_mean_core

        if cores:
            self._write_core_usage(cores, dt, t0_wall, t1_mono)

        self._write_process_name_cpu_rows(dt, t0_wall, t1_mono)

        # saturation
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

        # breakdown via cpu_times deltas
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
        sec_total_d: Optional[float] = 0.0
        for fname in getattr(cur_times, "_fields", ()):
            try:
                sec_total_d += max(0.0, dfield(fname))
            except Exception:
                pass
        if sec_total_d is not None and sec_total_d <= 0:
            sec_total_d = None

        def pct(x: float) -> Optional[float]:
            if sec_total_d is None or sec_total_d <= 0:
                return None
            return (x / sec_total_d) * 100.0

        # per process cpu time deltas
        # pid_list = _expand_pid_tree(self.cfg.pids) if self.cfg.pids else []
        # proc_cpu_sec_sum, proc_cpu_pct_sum = self._proc_cpu_deltas(dt, pid_list)
        # proc_cpu_cores_equiv = (proc_cpu_sec_sum / dt) if (proc_cpu_sec_sum is not None and dt > 0) else None
        pid_list = self._current_pid_list()
        proc_cpu_sec_sum, proc_cpu_pct_sum, proc_cpu_cores_equiv = self._proc_cpu_deltas(pid_list, dt)

        # per thread rows
        if self.thread_logger is not None and pid_list:
            self._write_thread_deltas(pid_list, dt, t0_wall, t1_mono)

        freq_mean, freq_max = self._freq_stats()

        self.logger.write({
            "ts_wall_s": t0_wall,
            "ts_mono_s": t1_mono,
            "dt_s": float(dt),
            "cpu_logical": int(self._cpu_logical),
            "cpu_physical": int(self._cpu_physical),
            "cpu_freq_cur_mhz_mean": freq_mean,
            "cpu_freq_cur_mhz_max": freq_max,
            "cpu_total_percent": cpu_total_pct,
            "cpu_mean_core_percent": cpu_mean_core,
            "cpu_max_core_percent": cpu_max_core,
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
            "proc_pid_count": len(pid_list),
            "proc_cpu_sec_d_sum": proc_cpu_sec_sum,
            "proc_cpu_pct_total_sum": proc_cpu_pct_sum,
            "proc_cpu_cores_equiv": proc_cpu_cores_equiv,
        })