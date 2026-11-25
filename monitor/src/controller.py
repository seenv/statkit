from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass
from multiprocessing import Event, Process
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .cpu_monitor import CpuMonitor, CpuMonitorConfig
from .disk_monitor import DiskMonitor, DiskMonitorConfig
from .mem_monitor import MemMonitor, MemMonitorConfig
from .net_monitor import NetMonitorConfig, make_drop_tracer, make_net_monitor
from .timesync import TimesyncLogger, timesync_path

#TODO: add a func to read the process pids from the files!
# talk to John to make sure of the processes to parse 
# TODO: some reports are more the durations!!!!!!!


def _run_periodic(sample: Callable[[], None], interval_s: float, stop: Event, t0: float,
                  after: Optional[Callable[[], None]] = None) -> None:
    """
    Call sample() on a fixed grid t0, t0+interval, t0+2*interval, ...
    (time.monotonic is system-wide on Linux, so all monitor processes share
    the same grid). The time sample() takes no longer stretches the period.
    If a sample overruns, the missed ticks are skipped instead of bursting.
    """
    interval_s = max(1e-3, interval_s)
    now = time.monotonic()
    k = max(0, -(-(now - t0) // interval_s))  # first tick at or after now
    next_t = t0 + k * interval_s

    while not (stop.is_set() or _term_requested):
        delay = next_t - time.monotonic()
        if delay > 0 and stop.wait(delay):
            break
        if _term_requested:
            break

        sample()
        if after is not None:
            try:
                after()
            except Exception:
                pass

        next_t += interval_s
        now = time.monotonic()
        if next_t <= now:
            next_t += ((now - next_t) // interval_s + 1) * interval_s


def _timesync_hook(mon, out_path: str, enabled: bool):
    """Write one <app>-<resource>_timesync.csv row with the timestamps of mon's latest row."""
    if not enabled:
        return None, None
    ts = TimesyncLogger(timesync_path(out_path))

    def after() -> None:
        last = getattr(mon, "last_ts", None)
        if last is not None:
            ts.sample(*last)

    return ts, after


# Set by SIGTERM in a monitor process. A plain flag, because calling
# multiprocessing.Event.set() inside a signal handler deadlocks when the same
# process is blocked in Event.wait() (Condition.notify_all waits for the
# sleeper, which is the interrupted thread itself).
_term_requested = False


def _on_sigterm(signum, frame) -> None:
    global _term_requested
    _term_requested = True


def _child_init(stop: Optional[Event] = None) -> None:
    """
    Signals for monitor processes:
      - SIGINT (Ctrl-C reaches the whole process group): ignored; the
        controller stops everyone through the stop Event.
      - SIGTERM (e.g. `pkill -f launcher.py` also hits the forked monitors,
        which share the controller's command line): finish the current row,
        then exit at the next tick and close files. Without this, children
        inherit the controller's handler and run stop_all() themselves.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, _on_sigterm)


def _run_cpu(out_path: str, out_thread_path: str, out_core_path: str, interval_s: float, pids: Optional[Sequence[int]], process_names: Optional[Sequence[str]], stop: Event, t0: float, name_file_prefix: str = "", timesync: bool = True) -> None:
    _child_init(stop)
    mon = CpuMonitor(CpuMonitorConfig(interval_s=interval_s, pids=pids, process_names=process_names), out_path, out_thread_path=out_thread_path, out_core_path=out_core_path, name_file_prefix=name_file_prefix)
    ts, after = _timesync_hook(mon, out_path, timesync)
    try:
        _run_periodic(mon.sample_once, interval_s, stop, t0, after)
    finally:
        mon.close()
        if ts is not None:
            ts.close()


def _run_mem(out_path: str, interval_s: float, pids: Optional[Sequence[int]], process_names: Optional[Sequence[str]], stop: Event, t0: float, timesync: bool = True) -> None:
    _child_init(stop)
    mon = MemMonitor(MemMonitorConfig(interval_s=interval_s, pids=pids, process_names=process_names), out_path)
    ts, after = _timesync_hook(mon, out_path, timesync)
    try:
        _run_periodic(mon.sample_once, interval_s, stop, t0, after)
    finally:
        mon.close()
        if ts is not None:
            ts.close()


def _run_disk(out_path: str, interval_s: float, pids: Optional[Sequence[int]], process_names: Optional[Sequence[str]], disk_path: str, stop: Event, t0: float, timesync: bool = True) -> None:
    _child_init(stop)
    mon = DiskMonitor(DiskMonitorConfig(interval_s=interval_s, pids=pids, process_names=process_names, disk_path_for_usage=disk_path), out_path)
    ts, after = _timesync_hook(mon, out_path, timesync)
    try:
        _run_periodic(mon.sample_once, interval_s, stop, t0, after)
    finally:
        mon.close()
        if ts is not None:
            ts.close()


def _run_net(out_path: str, interval_s: float, backend: str, nic_include: Optional[str], nic_exclude: Optional[str], stop: Event, stop_flag_path: str, t0: float, timesync: bool = True) -> None:
    _child_init(stop)
    cfg = NetMonitorConfig(
        interval_s=interval_s,
        backend=backend,
        nic_include_regex=nic_include,
        nic_exclude_regex=nic_exclude,
    )
    mon = make_net_monitor(cfg, out_path)
    ts, after = _timesync_hook(mon, out_path, timesync and backend != "bpftrace")
    try:
        if backend == "bpftrace":
            mon.run_forever(stop_flag_path=stop_flag_path)
        else:
            _run_periodic(mon.sample_once, interval_s, stop, t0, after)
    finally:
        try:
            mon.close()
        except Exception:
            pass
        if ts is not None:
            ts.close()


def _run_net_drops(out_path: str, interval_s: float, stop_flag_path: str, stop: Event) -> None:
    _child_init(stop)
    tracer = make_drop_tracer(NetMonitorConfig(interval_s=interval_s), out_path)
    try:
        tracer.run_forever(stop_flag_path=stop_flag_path)
    finally:
        try:
            tracer.close()
        except Exception:
            pass


@dataclass
class ControllerConfig:
    out_dir: str
    interval_s: float = 1.0
    duration_s: Optional[float] = None
    pids: Optional[List[int]] = None
    process_names: Optional[List[str]] = None
    disk_path_for_usage: str = "/"
    net_backend: str = "psutil"
    nic_include_regex: Optional[str] = None
    nic_exclude_regex: Optional[str] = r"^(lo|docker\d+|br-|veth|virbr|cni\d+|flannel\.)"
    app: str = "app"
    net_drop_trace: bool = False
    timesync: bool = True


class Controller:
    def __init__(self, cfg: ControllerConfig) -> None:
        self.cfg = cfg
        self.stop = Event()
        self.procs: List[Process] = []
        self._owner_pid = os.getpid()
        self.stop_flag_path = str(Path(cfg.out_dir) / ".stop_net_bpftrace")

    def start(self) -> None:
        out = Path(self.cfg.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        
        app = self.cfg.app.strip()
        prefix = f"{app}-" if app else ""

        try:
            if os.path.exists(self.stop_flag_path):
                os.remove(self.stop_flag_path)
        except Exception:
            pass

        # Shared sampling grid for all monitors.
        t0 = time.monotonic()

        self.procs = [
            Process(
                target=_run_cpu,
                kwargs={"out_path": str(out / f"{prefix}cpu.csv"),
                        "out_thread_path": str(out / f"{prefix}cpu_thread.csv"),
                        "out_core_path": str(out / f"{prefix}cpu_core.csv"),
                        "interval_s": self.cfg.interval_s, "pids": self.cfg.pids, "process_names": self.cfg.process_names, "stop": self.stop, "t0": t0,
                        "name_file_prefix": prefix, "timesync": self.cfg.timesync},
                daemon=True
            ),
            Process(
                target=_run_mem,
                kwargs={"out_path": str(out / f"{prefix}mem.csv"), "interval_s": self.cfg.interval_s, "pids": self.cfg.pids,
                        "process_names": self.cfg.process_names, "stop": self.stop, "t0": t0,
                        "timesync": self.cfg.timesync},
                daemon=True
            ),
            Process(
                target=_run_disk,
                kwargs={"out_path": str(out / f"{prefix}disk.csv"), "interval_s": self.cfg.interval_s, "pids": self.cfg.pids,
                        "process_names": self.cfg.process_names,
                        "disk_path": self.cfg.disk_path_for_usage, "stop": self.stop, "t0": t0,
                        "timesync": self.cfg.timesync},
                daemon=True
            ),
            Process(
                target=_run_net,
                kwargs={"out_path": str(out / f"{prefix}net.csv"), "interval_s": self.cfg.interval_s, "backend": self.cfg.net_backend,
                    "nic_include": self.cfg.nic_include_regex, "nic_exclude": self.cfg.nic_exclude_regex, "stop": self.stop, 
                    "stop_flag_path": self.stop_flag_path, "t0": t0, "timesync": self.cfg.timesync},
                daemon=True,
            ),
        ]

        if self.cfg.net_drop_trace:
            self.procs.append(
                Process(
                    target=_run_net_drops,
                    kwargs={"out_path": str(out / f"{prefix}net_drops.csv"), "interval_s": self.cfg.interval_s,
                            "stop_flag_path": self.stop_flag_path, "stop": self.stop},
                    daemon=True,
                )
            )

        for p in self.procs:
            p.start()

    def stop_all(self) -> None:
        self.stop.set()
        try:
            Path(self.stop_flag_path).write_text("stop\n", encoding="utf-8")
        except Exception:
            pass

        if os.getpid() != self._owner_pid:
            return  # never run from a forked child

        for p in self.procs:
            p.join(timeout=5.0)
        for p in self.procs:
            if p.is_alive():
                p.terminate()
        for p in self.procs:
            p.join(timeout=2.0)
            if p.is_alive():
                p.kill()

    def run(self) -> None:
        self.start()
        start = time.monotonic()
        try:
            if self.cfg.duration_s is None:
                while True:
                    time.sleep(0.2)
            else:
                while time.monotonic() - start < self.cfg.duration_s:
                    time.sleep(0.2)
        finally:
            self.stop_all()


def _parse_args(argv: Sequence[str]) -> ControllerConfig:
    ap = argparse.ArgumentParser(description="Concurrent resource monitor (CPU/Mem/Disk/Net) writing separate CSVs.")
    ap.add_argument("--out", default="./mon", help="Output directory (./mon)")
    ap.add_argument("--interval", type=float, default=1.0, help="Sampling interval seconds (default: 1.0)")
    ap.add_argument("--duration", type=float, default=None, help="Duration in seconds (default: run until Ctrl-C)")
    ap.add_argument("--pids", type=str, default=None, help="Comma separated PIDs for per-process aggregates")
    ap.add_argument("--process-names", type=str, default=None, help="Comma-separated process names or cmdline substrings to monitor, e.g. iperf3,rsync,globus-streams-launch")
    ap.add_argument("--disk-path", type=str, default="/", help="Filesystem path for disk_usage (default: /)")
    ap.add_argument("--net-backend", type=str, default="psutil", choices=["psutil", "bpftrace"],
                    help="Network backend (psutil or bpftrace); bpftrace requires root + bpftrace installed")
    ap.add_argument("--nic-include", type=str, default=None, help="Regex to include NICs (optional)")
    ap.add_argument("--nic-exclude", type=str, default=r"^(lo|docker\d+|br-|veth|virbr|cni\d+|flannel\.)",
                    help="Regex to exclude NICs (default filters virtual/loopback)")
    ap.add_argument("--app", type=str, default="app", help="The application that will be monitored")
    ap.add_argument("--net-drop-trace", action="store_true",
                    help="Also trace kernel packet-drop reasons with bpftrace into <app>-net_drops.csv "
                         "(root, bpftrace, kernel 5.17+)")
    ap.add_argument("--no-timesync", action="store_true",
                    help="Don't write <app>-<resource>_timesync.csv (clock-sync state per sample)")
    args = ap.parse_args(list(argv))

    pids = None
    if args.pids:
        pids = []
        for x in args.pids.split(","):
            x = x.strip()
            if x:
                pids.append(int(x))

    process_names = None
    if args.process_names:
        process_names = []
        for x in args.process_names.split(","):
            x = x.strip()
            if x:
                process_names.append(x)

    return ControllerConfig(
        out_dir=args.out,
        interval_s=args.interval,
        duration_s=args.duration,
        pids=pids,
        process_names=process_names,
        disk_path_for_usage=args.disk_path,
        net_backend=args.net_backend,
        nic_include_regex=args.nic_include,
        nic_exclude_regex=args.nic_exclude,
        app=args.app,
        net_drop_trace=args.net_drop_trace,
        timesync=not args.no_timesync,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    
    cfg = _parse_args(sys.argv[1:] if argv is None else argv)
    sup = Controller(cfg)

    def _handle(sig, frame):
        sup.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    sup.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())