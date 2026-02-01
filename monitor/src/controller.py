from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass
from multiprocessing import Event, Process
from pathlib import Path
from typing import List, Optional, Sequence

from .cpu_monitor import CpuMonitor, CpuMonitorConfig
from .disk_monitor import DiskMonitor, DiskMonitorConfig
from .mem_monitor import MemMonitor, MemMonitorConfig
from .net_monitor import NetMonitorConfig, make_net_monitor

#TODO: add a func to read the process pids from the files!
# talk to John to make sure of the processes to parse 
# TODO: some reports are more the durations!!!!!!!

def _run_cpu(out_path: str, interval_s: float, pids: Optional[Sequence[int]], stop: Event) -> None:
    mon = CpuMonitor(CpuMonitorConfig(interval_s=interval_s, pids=pids), out_path)
    try:
        while not stop.is_set():
            mon.sample_once()
    finally:
        mon.close()


def _run_mem(out_path: str, interval_s: float, pids: Optional[Sequence[int]], stop: Event) -> None:
    mon = MemMonitor(MemMonitorConfig(interval_s=interval_s, pids=pids), out_path)
    try:
        while not stop.is_set():
            mon.sample_once()
            time.sleep(interval_s)
    finally:
        mon.close()


def _run_disk(out_path: str, interval_s: float, pids: Optional[Sequence[int]], disk_path: str, stop: Event) -> None:
    mon = DiskMonitor(DiskMonitorConfig(interval_s=interval_s, pids=pids, disk_path_for_usage=disk_path), out_path)
    try:
        while not stop.is_set():
            mon.sample_once()
            time.sleep(interval_s)
    finally:
        mon.close()


def _run_net(out_path: str, interval_s: float, backend: str, nic_include: Optional[str], nic_exclude: Optional[str], stop: Event, stop_flag_path: str) -> None:
    cfg = NetMonitorConfig(
        interval_s=interval_s,
        backend=backend,
        nic_include_regex=nic_include,
        nic_exclude_regex=nic_exclude,
    )
    mon = make_net_monitor(cfg, out_path)
    try:
        if backend == "bpftrace":
            mon.run_forever(stop_flag_path=stop_flag_path)
        else:
            while not stop.is_set():
                mon.sample_once()
                time.sleep(interval_s)
    finally:
        try:
            mon.close()
        except Exception:
            pass


@dataclass
class ControllerConfig:
    out_dir: str
    interval_s: float = 1.0
    duration_s: Optional[float] = None
    pids: Optional[List[int]] = None
    disk_path_for_usage: str = "/"
    net_backend: str = "psutil"
    nic_include_regex: Optional[str] = None
    nic_exclude_regex: Optional[str] = r"^(lo|docker\d+|br-|veth|virbr|cni\d+|flannel\.)"


class Controller:
    def __init__(self, cfg: ControllerConfig) -> None:
        self.cfg = cfg
        self.stop = Event()
        self.procs: List[Process] = []
        self.stop_flag_path = str(Path(cfg.out_dir) / ".stop_net_bpftrace")

    def start(self) -> None:
        out = Path(self.cfg.out_dir)
        out.mkdir(parents=True, exist_ok=True)

        try:
            if os.path.exists(self.stop_flag_path):
                os.remove(self.stop_flag_path)
        except Exception:
            pass

        self.procs = [
            Process(
                target=_run_cpu,
                kwargs={"out_path": str(out / "cpu.csv"), "interval_s": self.cfg.interval_s, "pids": self.cfg.pids, "stop": self.stop},
                daemon=True,
            ),
            Process(
                target=_run_mem,
                kwargs={"out_path": str(out / "mem.csv"), "interval_s": self.cfg.interval_s, "pids": self.cfg.pids, "stop": self.stop},
                daemon=True,
            ),
            Process(
                target=_run_disk,
                kwargs={"out_path": str(out / "disk.csv"), "interval_s": self.cfg.interval_s, "pids": self.cfg.pids, "disk_path": self.cfg.disk_path_for_usage, "stop": self.stop},
                daemon=True,
            ),
            Process(
                target=_run_net,
                kwargs={
                    "out_path": str(out / "net.csv"),
                    "interval_s": self.cfg.interval_s,
                    "backend": self.cfg.net_backend,
                    "nic_include": self.cfg.nic_include_regex,
                    "nic_exclude": self.cfg.nic_exclude_regex,
                    "stop": self.stop,
                    "stop_flag_path": self.stop_flag_path,
                },
                daemon=True,
            ),
        ]
        for p in self.procs:
            p.start()

    def stop_all(self) -> None:
        self.stop.set()
        try:
            Path(self.stop_flag_path).write_text("stop\n", encoding="utf-8")
        except Exception:
            pass

        for p in self.procs:
            p.join(timeout=5.0)
        for p in self.procs:
            if p.is_alive():
                p.terminate()

    def run(self) -> None:
        self.start()
        start = time.time()
        try:
            if self.cfg.duration_s is None:
                while True:
                    time.sleep(0.2)
            else:
                while time.time() - start < self.cfg.duration_s:
                    time.sleep(0.2)
        finally:
            self.stop_all()


def _parse_args(argv: Sequence[str]) -> ControllerConfig:
    ap = argparse.ArgumentParser(description="Concurrent resource monitor (CPU/Mem/Disk/Net) writing separate CSVs.")
    ap.add_argument("--out", required=True, default="./mon", help="Output directory (./mon)")
    ap.add_argument("--interval", type=float, default=1.0, help="Sampling interval seconds (default: 1.0)")
    ap.add_argument("--duration", type=float, default=None, help="Duration in seconds (default: run until Ctrl-C)")
    ap.add_argument("--pids", type=str, default=None, help="Comma separated PIDs for per-process aggregates")
    ap.add_argument("--disk-path", type=str, default="/", help="Filesystem path for disk_usage (default: /)")
    ap.add_argument("--net-backend", type=str, default="psutil", choices=["psutil", "bpftrace"],
                    help="Network backend (psutil or bpftrace); bpftrace requires root + bpftrace installed")
    ap.add_argument("--nic-include", type=str, default=None, help="Regex to include NICs (optional)")
    ap.add_argument("--nic-exclude", type=str, default=r"^(lo|docker\d+|br-|veth|virbr|cni\d+|flannel\.)",
                    help="Regex to exclude NICs (default filters virtual/loopback)")
    args = ap.parse_args(list(argv))

    pids = None
    if args.pids:
        pids = []
        for x in args.pids.split(","):
            x = x.strip()
            if x:
                pids.append(int(x))

    return ControllerConfig(
        out_dir=args.out,
        interval_s=args.interval,
        duration_s=args.duration,
        pids=pids,
        disk_path_for_usage=args.disk_path,
        net_backend=args.net_backend,
        nic_include_regex=args.nic_include,
        nic_exclude_regex=args.nic_exclude,
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
