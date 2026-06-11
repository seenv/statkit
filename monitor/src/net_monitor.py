from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

import psutil

from .util import CsvLogger, now_mono_s, now_wall_s


# TODO: use bpf, but check if the additional accuracy is necessary.
# TODO: separate mini-app from iperf so we do not need to monitor useless NICs.
# TODO: compare TCP retransmits here with iperf-reported retransmits.
# Note:
#   ethtool RX counters are not TCP retransmissions.
#   They are NIC/driver drop-pressure indicators that can explain retransmissions.


@dataclass
class NetMonitorConfig:
    interval_s: float = 1.0
    backend: str = "psutil"  # "psutil" or "bpftrace"

    nic_include_regex: Optional[str] = None
    nic_exclude_regex: Optional[str] = (
        r"^(lo|docker\d+|br-|veth|virbr|cni\d+|flannel\.)"
    )

    pids: Optional[Sequence[int]] = None  # placeholder

    # Extra psutil-backend collectors.
    collect_ip_link: bool = True
    collect_ethtool: bool = True
    collect_tcp_snmp: bool = True

    # Selected `ethtool -S <dev>` counters.
    ethtool_counters: Sequence[str] = (
        "rx_out_of_buffer",
        "rx_steer_missed_packets",
        "rx_discards_phy",
    )

    # Sum all per-RX-queue packet counters matching rx0_packets, rx1_packets, ...
    collect_rxq_packets_sum: bool = True


def _run_text(cmd: Sequence[str], timeout_s: float = 1.0) -> str:
    try:
        cp = subprocess.run(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if cp.returncode != 0:
            return ""
        return cp.stdout
    except Exception:
        return ""


def _read_ethtool_stats(dev: str) -> Dict[str, int]:
    """
    Read `ethtool -S <dev>` into a dictionary.

    Example lines:
        rx_out_of_buffer: 123
        rx0_packets: 456
    """
    out = _run_text(["ethtool", "-S", dev])
    stats: Dict[str, int] = {}

    for line in out.splitlines():
        line = line.strip()
        if ":" not in line:
            continue

        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()

        try:
            stats[key] = int(val)
        except ValueError:
            continue

    return stats


def _read_ip_link_stats(dev: str) -> Dict[str, int]:
    """
    Parse `ip -s link show dev <dev>`.

    Common output:

        RX:  bytes packets errors dropped missed mcast
             ...
        TX:  bytes packets errors dropped carrier collsns
             ...

    Returns keys such as:
        ip_rx_bytes
        ip_rx_packets
        ip_rx_errors
        ip_rx_dropped
        ip_rx_missed
        ip_tx_bytes
        ip_tx_packets
        ip_tx_errors
        ip_tx_dropped
        ip_tx_carrier
    """
    out = _run_text(["ip", "-s", "link", "show", "dev", dev])
    lines = [ln.strip() for ln in out.splitlines()]

    stats: Dict[str, int] = {}

    for i, line in enumerate(lines):
        if line.startswith("RX:") and i + 1 < len(lines):
            headers = line.replace("RX:", "").split()
            values = lines[i + 1].split()

            for h, v in zip(headers, values):
                try:
                    stats[f"ip_rx_{h}"] = int(v)
                except ValueError:
                    continue

        elif line.startswith("TX:") and i + 1 < len(lines):
            headers = line.replace("TX:", "").split()
            values = lines[i + 1].split()

            for h, v in zip(headers, values):
                try:
                    stats[f"ip_tx_{h}"] = int(v)
                except ValueError:
                    continue

    return stats


def _read_tcp_snmp() -> Dict[str, int]:
    """
    Read global TCP counters from /proc/net/snmp.

    Important field:
        tcp_RetransSegs

    This is host-wide, not per NIC.
    """
    try:
        with open("/proc/net/snmp", "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.startswith("Tcp:")]
    except OSError:
        return {}

    if len(lines) < 2:
        return {}

    keys = lines[-2].split()[1:]
    vals = lines[-1].split()[1:]

    stats: Dict[str, int] = {}

    for key, val in zip(keys, vals):
        try:
            stats[f"tcp_{key}"] = int(val)
        except ValueError:
            continue

    return stats


def _delta_dict(cur: Dict[str, int], prev: Optional[Dict[str, int]]) -> Dict[str, int]:
    """
    Return deltas between current and previous monotonically increasing counters.
    If there is no previous sample, deltas are zero.
    """
    if prev is None:
        return {k: 0 for k in cur}

    out: Dict[str, int] = {}

    for key, val in cur.items():
        prev_val = prev.get(key)
        if prev_val is None:
            out[key] = 0
        else:
            out[key] = val - prev_val

    return out


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

                # psutil deltas
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

                # ip -s link deltas
                "ip_rx_errors_d",
                "ip_rx_dropped_d",
                "ip_rx_missed_d",
                "ip_rx_overrun_d",
                "ip_tx_errors_d",
                "ip_tx_dropped_d",
                "ip_tx_carrier_d",

                # ethtool -S deltas
                "ethtool_rx_out_of_buffer_d",
                "ethtool_rx_steer_missed_packets_d",
                "ethtool_rx_discards_phy_d",
                "ethtool_rxq_packets_sum_d",

                # /proc/net/snmp host-wide TCP deltas
                "tcp_retranssegs_d",
            ],
            flush_every=1,
        )

        self._prev_mono: Optional[float] = None
        self._prev = psutil.net_io_counters(pernic=True)

        self._inc = re.compile(cfg.nic_include_regex) if cfg.nic_include_regex else None
        self._exc = re.compile(cfg.nic_exclude_regex) if cfg.nic_exclude_regex else None

        self._prev_ip_link: Dict[str, Dict[str, int]] = {}
        self._prev_ethtool: Dict[str, Dict[str, int]] = {}

        self._prev_tcp_snmp: Optional[Dict[str, int]]
        if cfg.collect_tcp_snmp:
            self._prev_tcp_snmp = _read_tcp_snmp()
        else:
            self._prev_tcp_snmp = None

        self._rxq_packets_re = re.compile(r"^rx\d+_packets$")

    def close(self) -> None:
        self.logger.close()

    def _keep(self, nic: str) -> bool:
        if self._inc and not self._inc.search(nic):
            return False
        if self._exc and self._exc.search(nic):
            return False
        return True

    def _read_nic_ip_link_delta(self, nic: str) -> Dict[str, int]:
        if not self.cfg.collect_ip_link:
            return {}

        cur = _read_ip_link_stats(nic)
        prev = self._prev_ip_link.get(nic)
        delta = _delta_dict(cur, prev)
        self._prev_ip_link[nic] = cur

        return delta

    def _read_nic_ethtool_delta(self, nic: str) -> Dict[str, int]:
        if not self.cfg.collect_ethtool:
            return {}

        cur = _read_ethtool_stats(nic)
        prev = self._prev_ethtool.get(nic)
        delta = _delta_dict(cur, prev)
        self._prev_ethtool[nic] = cur

        return delta

    def _read_tcp_retrans_delta(self) -> int:
        if not self.cfg.collect_tcp_snmp:
            return 0

        cur = _read_tcp_snmp()
        delta = _delta_dict(cur, self._prev_tcp_snmp)
        self._prev_tcp_snmp = cur

        return int(delta.get("tcp_RetransSegs", 0))

    def sample_once(self) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()

        dt = None if self._prev_mono is None else max(1e-9, t_mono - self._prev_mono)
        self._prev_mono = t_mono

        cur = psutil.net_io_counters(pernic=True)
        prev = self._prev
        self._prev = cur

        # Host-wide, not per NIC. Repeated on each NIC row for easier plotting.
        tcp_retranssegs_d = self._read_tcp_retrans_delta()

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
                rx_mbps = None
                tx_mbps = None
            else:
                rx_mbps = (rx_d * 8.0) / dt / 1e6
                tx_mbps = (tx_d * 8.0) / dt / 1e6

            ip_d = self._read_nic_ip_link_delta(nic)
            eth_d = self._read_nic_ethtool_delta(nic)

            rxq_packets_sum_d = 0
            if self.cfg.collect_rxq_packets_sum:
                rxq_packets_sum_d = sum(
                    val
                    for key, val in eth_d.items()
                    if self._rxq_packets_re.match(key)
                )

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

                "ip_rx_errors_d": int(ip_d.get("ip_rx_errors", 0)),
                "ip_rx_dropped_d": int(ip_d.get("ip_rx_dropped", 0)),
                "ip_rx_missed_d": int(ip_d.get("ip_rx_missed", 0)),
                "ip_rx_overrun_d": int(ip_d.get("ip_rx_overrun", 0)),
                "ip_tx_errors_d": int(ip_d.get("ip_tx_errors", 0)),
                "ip_tx_dropped_d": int(ip_d.get("ip_tx_dropped", 0)),
                "ip_tx_carrier_d": int(ip_d.get("ip_tx_carrier", 0)),

                "ethtool_rx_out_of_buffer_d": int(eth_d.get("rx_out_of_buffer", 0)),
                "ethtool_rx_steer_missed_packets_d": int(
                    eth_d.get("rx_steer_missed_packets", 0)
                ),
                "ethtool_rx_discards_phy_d": int(eth_d.get("rx_discards_phy", 0)),
                "ethtool_rxq_packets_sum_d": int(rxq_packets_sum_d),

                "tcp_retranssegs_d": int(tcp_retranssegs_d),
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

        self._re_kv = re.compile(
            r'^\s*\@\w+\["(?P<nic>[^"]+)"\]\s*:\s*(?P<val>\d+)\s*$'
        )
        self._mode: Optional[str] = None  # "tx" or "rx"

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

                tx = {}
                rx = {}
                self._mode = None
                continue

            if line.strip().startswith("@tx"):
                self._mode = "tx"
            elif line.strip().startswith("@rx"):
                self._mode = "rx"

            m = self._re_kv.match(line)
            if not m:
                continue

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
            tx_bytes = tx.get(nic, 0)
            rx_bytes = rx.get(nic, 0)

            self.logger.write({
                "ts_wall_s": t_wall,
                "ts_mono_s": t_mono,
                "backend": "bpftrace",
                "nic": nic,
                "rx_bytes": int(rx_bytes),
                "tx_bytes": int(tx_bytes),
                "rx_Mbps": (rx_bytes * 8.0) / 1e6,
                "tx_Mbps": (tx_bytes * 8.0) / 1e6,
            })


def make_net_monitor(cfg: NetMonitorConfig, out_csv_path: str):
    if cfg.backend == "psutil":
        return PsutilNetMonitor(cfg, out_csv_path)

    if cfg.backend == "bpftrace":
        return BpftraceNetMonitor(cfg, out_csv_path)

    raise ValueError(f"Unknown net backend: {cfg.backend}")