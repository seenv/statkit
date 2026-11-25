from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set

import psutil


# Which processes to measure, re-evaluated every sample so processes that start
# after the monitor are picked up:
#   - --pids that are still alive (and are still the same process: a PID that
#     exited and was reused by something else is dropped),
#   - processes matching --process-names (exact name, or substring of the
#     full command line),
#   - all children of both (optional).
# The monitor's own processes are excluded: their command lines contain the
# names passed via --process-names.
#
# Cost: one pass over /proc per call (pid + ppid, plus name + cmdline only
# when --process-names is used).


@dataclass
class Targets:
    pids: List[int]                 # every process to measure, sorted
    by_name: Dict[str, List[int]]   # direct name matches only (no children)
    cgroup_pid: Optional[int]       # process whose cgroup the CPU monitor reads
    ppid: Dict[int, int]            # parent PID of every target


class TargetResolver:
    def __init__(
        self,
        pids: Optional[Sequence[int]] = None,
        process_names: Optional[Sequence[str]] = None,
        *,
        include_children: bool = True,
        recursive_children: bool = True,
    ) -> None:
        self._names: List[str] = [n.strip() for n in (process_names or []) if n and n.strip()]
        self._include_children = include_children
        self._recursive = recursive_children

        # Remember each given PID's start time to detect PID reuse.
        # PIDs that are not alive at start are ignored.
        self._given: Dict[int, float] = {}
        for pid in pids or []:
            try:
                self._given[int(pid)] = psutil.Process(int(pid)).create_time()
            except Exception:
                continue

        self.configured: bool = bool(pids) or bool(self._names)

    def resolve(self) -> Targets:
        if not self.configured:
            return Targets(pids=[], by_name={}, cgroup_pid=None, ppid={})

        attrs = ["pid", "ppid"] + (["name", "cmdline"] if self._names else [])
        ppid: Dict[int, int] = {}
        info: Dict[int, tuple] = {}
        for p in psutil.process_iter(attrs):
            try:
                pi = p.info
                pid = int(pi["pid"])
                ppid[pid] = int(pi.get("ppid") or 0)
                if self._names:
                    info[pid] = (pi.get("name") or "", " ".join(pi.get("cmdline") or []))
            except Exception:
                continue

        children: Dict[int, List[int]] = {}
        for c, par in ppid.items():
            children.setdefault(par, []).append(c)

        exclude = self._monitor_pids(ppid, children)

        roots: Set[int] = set()
        given_alive: List[int] = []
        for pid, ct in self._given.items():
            if pid not in ppid or pid in exclude:
                continue
            try:
                if psutil.Process(pid).create_time() != ct:
                    continue  # PID reused by another process
            except Exception:
                continue
            roots.add(pid)
            given_alive.append(pid)

        by_name: Dict[str, List[int]] = {n: [] for n in self._names}
        for pid, (pname, cmdline) in info.items():
            if pid in exclude:
                continue
            for n in self._names:
                if pname == n or n in cmdline:
                    by_name[n].append(pid)
        for n in by_name:
            by_name[n].sort()
            roots.update(by_name[n])

        all_pids: Set[int] = set(roots)
        if self._include_children:
            all_pids |= self._descendants(roots, children, exclude)

        # cgroup: prefer an app found by name (a --pids list from a broad
        # pattern often starts with daemons such as dockerd).
        cgroup_pid: Optional[int] = None
        for n in self._names:
            if by_name.get(n):
                cgroup_pid = by_name[n][0]
                break
        if cgroup_pid is None and given_alive:
            cgroup_pid = min(given_alive)

        return Targets(
            pids=sorted(all_pids),
            by_name=by_name,
            cgroup_pid=cgroup_pid,
            ppid={pid: ppid.get(pid, 0) for pid in all_pids},
        )

    def _descendants(self, roots: Set[int], children: Dict[int, List[int]], exclude: Set[int]) -> Set[int]:
        out: Set[int] = set()
        q = deque((r, 0) for r in roots)
        while q:
            pid, depth = q.popleft()
            if not self._recursive and depth >= 1:
                continue
            for c in children.get(pid, ()):
                if c in exclude or c in out or c in roots:
                    continue
                out.add(c)
                q.append((c, depth + 1))
        return out

    @staticmethod
    def _monitor_pids(ppid: Dict[int, int], children: Dict[int, List[int]]) -> Set[int]:
        """This process, its ancestors (controller, shells) and every process the controller started."""
        out: Set[int] = set()
        cur = os.getpid()
        while cur and cur not in out:
            out.add(cur)
            cur = ppid.get(cur, 0)

        q = deque([os.getppid()])
        while q:
            pid = q.popleft()
            for c in children.get(pid, ()):
                if c not in out:
                    out.add(c)
                    q.append(c)
        return out


# ---- start-time checks -------------------------------------------------------
# "Did this process/thread start during the last interval?" is answered on the
# kernel's boot-relative clock. psutil's create_time() adds a boot time that the
# kernel reports in whole seconds, so it can be off by up to 1s against
# time.time() and misclassify processes that started near a sample.

_CLK_TCK = os.sysconf("SC_CLK_TCK")


def boottime_now() -> float:
    """Seconds since boot, same clock as the start time in /proc/<pid>/stat."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def start_boottime(pid: int, tid: Optional[int] = None) -> Optional[float]:
    """Start time of a process (or thread) in seconds since boot, 10 ms resolution."""
    path = f"/proc/{pid}/task/{tid}/stat" if tid is not None else f"/proc/{pid}/stat"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        fields = txt.rsplit(")", 1)[1].split()
        return int(fields[19]) / float(_CLK_TCK)  # field 22 overall
    except Exception:
        return None


def started_after(since_boottime: float, pid: int, tid: Optional[int] = None) -> bool:
    st = start_boottime(pid, tid)
    return st is not None and st >= since_boottime


def ext_path(path: str) -> str:
    """iperf_gst-cpu.csv -> iperf_gst-cpu_ext.csv"""
    root, ext = os.path.splitext(path)
    return f"{root}_ext{ext or '.csv'}"