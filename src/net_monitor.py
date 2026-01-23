from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

import psutil

from .util import CsvLogger, now_mono_s, now_wall_s

#TODO: use bpf, but check if the additional accuracy is necessary
#TODO: seperate mini-app from iperf so we don't need to monitor useless nics
#TODO: get the rtt and retansmits and compare with iperf! but iperf only reports
# retrans from endpoint to its access point and not server to client! double check
# with the new version!

@dataclass
class NetMonitorConfig:
    interval_s: float = 1.0
    backend: str = "psutil" #"bpftrace"   # bpf is more kernel level
    nic_include_regex: Optional[str] = None
    nic_exclude_regex: Optional[str] = r"^(lo|docker\d+|br-|veth|virbr|cni\d+|flannel\.)"
    pids: Optional[Sequence[int]] = None  # placeholder 
    
    #TODO: see if per process net monitor is possible (not implemented)
    # also it might be available in bpf, or maybe can find something in ring


class PsutilNetMonitor:
    def __init__(self, cfg: NetMonitorConfig, out_csv_path: str) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            out_csv_path,
            fieldnames=[
                "ts_wall_s",
                "ts_mono_s",
                "dt_s",
                "backend",
                "nic",
                "rx_bytes_d",
                "tx_bytes_d",
                "rx_Mbps",
                "tx_Mbps",
                "rx_pkts_d",
                "tx_pkts_d",
                "dropin_d",
                "dropout_d",
                "errin_d",
                "errout_d",
            ],
            flush_every=1,
        )
        self._prev_mono: Optional[float] = None
        self._prev = psutil.net_io_counters(pernic=True)
        self._inc = re.compile(cfg.nic_include_regex) if cfg.nic_include_regex else None
        self._exc = re.compile(cfg.nic_exclude_regex) if cfg.nic_exclude_regex else None

    def close(self) -> None:
        self.logger.close()

    def _keep(self, nic: str) -> bool:
        if self._inc and not self._inc.search(nic):
            return False
        if self._exc and self._exc.search(nic):
            return False
        return True

    def sample_once(self) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        dt = None if self._prev_mono is None else max(1e-9, t_mono - self._prev_mono)
        self._prev_mono = t_mono

        cur = psutil.net_io_counters(pernic=True)
        prev = self._prev
        self._prev = cur

        for nic, c in cur.items():
            if not self._keep(nic):
                continue
            p = prev.get(nic)
            if p is None:
                continue

            rx_d = c.bytes_recv - p.bytes_recv
            tx_d = c.bytes_sent - p.bytes_sent
            rxp_d = c.packets_recv - p.packets_recv
            txp_d = c.packets_sent - p.packets_sent

            dropin_d = getattr(c, "dropin", 0) - getattr(p, "dropin", 0)
            dropout_d = getattr(c, "dropout", 0) - getattr(p, "dropout", 0)
            errin_d = getattr(c, "errin", 0) - getattr(p, "errin", 0)
            errout_d = getattr(c, "errout", 0) - getattr(p, "errout", 0)

            if dt is None or dt <= 0:
                rx_mbps = tx_mbps = None
            else:
                rx_mbps = (rx_d * 8.0) / dt / 1e6
                tx_mbps = (tx_d * 8.0) / dt / 1e6

            self.logger.write({
                "ts_wall_s": t_wall,
                "ts_mono_s": t_mono,
                "dt_s": dt,
                "backend": "psutil",
                "nic": nic,
                "rx_bytes_d": int(rx_d),
                "tx_bytes_d": int(tx_d),
                "rx_Mbps": rx_mbps,
                "tx_Mbps": tx_mbps,
                "rx_pkts_d": int(rxp_d),
                "tx_pkts_d": int(txp_d),
                "dropin_d": int(dropin_d),
                "dropout_d": int(dropout_d),
                "errin_d": int(errin_d),
                "errout_d": int(errout_d),
            })


_BPFTRACE_SCRIPT = r"""
tracepoint:net:net_dev_queue
{
  @tx[args->name] = sum(args->len);
}

tracepoint:net:netif_receive_skb
{
  @rx[args->name] = sum(args->len);
}

interval:s:1
{
  printf("=== %d ===\n", nsecs);
  print(@tx);
  print(@rx);
  clear(@tx);
  clear(@rx);
}
"""


class BpftraceNetMonitor:
    def __init__(self, cfg: NetMonitorConfig, out_csv_path: str) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            out_csv_path,
            fieldnames=[
                "ts_wall_s",
                "ts_mono_s",
                "backend",
                "nic",
                "rx_bytes",
                "tx_bytes",
                "rx_Mbps",
                "tx_Mbps",
            ],
            flush_every=1,
        )
        self._inc = re.compile(cfg.nic_include_regex) if cfg.nic_include_regex else None
        self._exc = re.compile(cfg.nic_exclude_regex) if cfg.nic_exclude_regex else None
        self._bpftrace = shutil.which("bpftrace")
        if not self._bpftrace:
            raise RuntimeError("bpftrace not found on PATH")

        self._proc = subprocess.Popen(
            [self._bpftrace, "-q", "-e", _BPFTRACE_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        #didn;t work with bpf
        
        self._re_kv = re.compile(r'^\s*\@\w+\["(?P<nic>[^"]+)"\]\s*:\s*(?P<val>\d+)\s*$')
        self._mode = None  # "tx" or "rx"

    def close(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        finally:
            self.logger.close()

    def _keep(self, nic: str) -> bool:
        if self._inc and not self._inc.search(nic):
            return False
        if self._exc and self._exc.search(nic):
            return False
        return True

    def run_forever(self, stop_flag_path: str) -> None:
        if not self._proc.stdout:
            raise RuntimeError("bpftrace stdout missing")

        tx: Dict[str, int] = {}
        rx: Dict[str, int] = {}

        for line in self._proc.stdout:
            if os.path.exists(stop_flag_path):
                break

            line = line.rstrip("\n")
            if line.startswith("==="):
                if tx or rx:
                    self._emit(tx, rx)
                tx, rx = {}, {}
                self._mode = None
                continue

            if line.strip().startswith("@tx"):
                self._mode = "tx"
            elif line.strip().startswith("@rx"):
                self._mode = "rx"

            m = self._re_kv.match(line)
            if m:
                nic = m.group("nic")
                if not self._keep(nic):
                    continue
                val = int(m.group("val"))
                if self._mode == "tx":
                    tx[nic] = val
                elif self._mode == "rx":
                    rx[nic] = val

        if tx or rx:
            self._emit(tx, rx)

        try:
            if self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    def _emit(self, tx: Dict[str, int], rx: Dict[str, int]) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        nics = set(tx.keys()) | set(rx.keys())
        for nic in sorted(nics):
            tbytes = tx.get(nic, 0)
            rbytes = rx.get(nic, 0)
            self.logger.write({
                "ts_wall_s": t_wall,
                "ts_mono_s": t_mono,
                "backend": "bpftrace",
                "nic": nic,
                "rx_bytes": int(rbytes),
                "tx_bytes": int(tbytes),
                "rx_Mbps": (rbytes * 8.0) / 1e6,
                "tx_Mbps": (tbytes * 8.0) / 1e6,
            })


def make_net_monitor(cfg: NetMonitorConfig, out_csv_path: str):
    if cfg.backend == "psutil":
        return PsutilNetMonitor(cfg, out_csv_path)
    if cfg.backend == "bpftrace":
        return BpftraceNetMonitor(cfg, out_csv_path)
    raise ValueError(f"Unknown net backend: {cfg.backend}")



