from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import psutil

from .targets import ext_path
from .util import CsvLogger, now_mono_s, now_wall_s


# TODO: separate mini-app from iperf so we do not need to monitor useless NICs.
# TODO: compare TCP retransmits here with iperf-reported retransmits.
# Note:
#   ethtool RX counters are not TCP retransmissions.
#   They are NIC/driver drop-pressure indicators that can explain retransmissions.
#
# Output files (psutil backend), one row per NIC per sample in both:
#   <app>-net.csv: LEGACY_FIELDS, the original columns.
#   <app>-net_ext.csv: ts_wall_s, ts_mono_s, dt_s, nic (to join with net.csv),
#       then the fixed new columns, then the TcpExt columns (tcpext_<Name>_d),
#       whose exact set depends on the kernel, so they come last.


LEGACY_FIELDS: List[str] = [
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
]

# /proc/net/snmp Tcp fields other than RetransSegs (already a legacy column).
# Gauges are logged as raw values; counters as per-interval deltas.
TCP_SNMP_GAUGES: Tuple[str, ...] = ("RtoAlgorithm", "RtoMin", "RtoMax", "MaxConn", "CurrEstab")
TCP_SNMP_COUNTERS: Tuple[str, ...] = (
    "ActiveOpens", "PassiveOpens", "AttemptFails", "EstabResets",
    "InSegs", "OutSegs", "InErrs", "OutRsts", "InCsumErrors",
)

# ip -s link fields that were parsed before but not written.
IP_LINK_EXTRA: Tuple[str, ...] = (
    "ip_rx_bytes", "ip_rx_packets", "ip_rx_mcast",
    "ip_tx_bytes", "ip_tx_packets", "ip_tx_collsns",
)

# /proc/net/softnet_stat column index -> name (all counters, summed over CPUs).
SOFTNET_COUNTERS: Tuple[Tuple[int, str], ...] = (
    (0, "processed"),       # packets processed by the softirq
    (1, "dropped"),         # backlog full -> net.core.netdev_max_backlog
    (2, "time_squeeze"),    # budget/time ran out -> net.core.netdev_budget(_usecs)
    (8, "cpu_collision"),
    (9, "received_rps"),    # packets steered here by RPS/RFS
    (10, "flow_limit_count"),
)
SOFTNET_BACKLOG_LEN_COL = 11  # gauge, kernel 5.10+
SOFTNET_CPU_INDEX_COL = 12    # kernel 5.10+

CONNTRACK_STAT_FIELDS: Tuple[str, ...] = (
    "invalid", "insert_failed", "drop", "early_drop", "search_restart", "clashres",
)


def _fixed_new_fields() -> List[str]:
    fields: List[str] = []
    fields += [f"tcp_{n.lower()}" for n in TCP_SNMP_GAUGES]
    fields += [f"tcp_{n.lower()}_d" for n in TCP_SNMP_COUNTERS]
    fields += [f"{n}_d" for n in IP_LINK_EXTRA]
    fields += [f"softnet_{name}_d" for _, name in SOFTNET_COUNTERS]
    fields += [
        "softnet_backlog_len",
        "softnet_cpus_active",          # CPUs that processed packets this interval
        "softnet_processed_max_cpu_d",  # busiest CPU (imbalance indicator)
    ]
    fields += [
        "qdisc_root_kind",
        "qdisc_bytes_d", "qdisc_packets_d", "qdisc_drops_d",
        "qdisc_overlimits_d", "qdisc_requeues_d",
        "qdisc_backlog_bytes", "qdisc_qlen",
    ]
    fields += ["conntrack_count", "conntrack_max"]
    fields += [f"conntrack_{n}_d" for n in CONNTRACK_STAT_FIELDS]
    fields += [
        "ethtool_selected_missing",     # selected counters this driver doesn't have
        "ethtool_all_nonzero_d_json",   # every ethtool -S counter that changed
    ]
    return fields


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
    collect_tcp_netstat: bool = True   # /proc/net/netstat TcpExt
    collect_softnet: bool = True       # /proc/net/softnet_stat
    collect_qdisc: bool = True         # tc -s qdisc (root qdisc per NIC)
    collect_conntrack: bool = True     # nf_conntrack count/max/stats
    collect_ethtool_all_json: bool = True

    # Selected `ethtool -S <dev>` counters.
    ethtool_counters: Sequence[str] = (
        "rx_out_of_buffer",
        "rx_steer_missed_packets",
        "rx_discards_phy",
    )

    # Sum all per-RX-queue packet counters matching rx0_packets, rx1_packets, ...
    collect_rxq_packets_sum: bool = True


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #

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


def _read_int_file(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_ethtool_stats(dev: str) -> Dict[str, int]:
    """Read `ethtool -S <dev>` into a dictionary."""
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
    """Parse `ip -s link show dev <dev>` into ip_rx_<field> / ip_tx_<field>."""
    out = _run_text(["ip", "-s", "link", "show", "dev", dev])
    lines = [ln.strip() for ln in out.splitlines()]

    stats: Dict[str, int] = {}

    for i, line in enumerate(lines):
        for tag, prefix in (("RX:", "ip_rx_"), ("TX:", "ip_tx_")):
            if line.startswith(tag) and i + 1 < len(lines):
                headers = line.replace(tag, "").split()
                values = lines[i + 1].split()
                for h, v in zip(headers, values):
                    try:
                        stats[f"{prefix}{h}"] = int(v)
                    except ValueError:
                        continue

    return stats


def _read_proc_section(path: str, prefix: str) -> Dict[str, int]:
    """
    Read a header/value line pair from /proc/net/snmp or /proc/net/netstat,
    e.g. prefix "Tcp:" or "TcpExt:". Keys keep the kernel's names.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.split() for ln in f if ln.startswith(prefix)]
    except OSError:
        return {}

    if len(lines) < 2:
        return {}

    stats: Dict[str, int] = {}
    for key, val in zip(lines[-2][1:], lines[-1][1:]):
        try:
            stats[key] = int(val)
        except ValueError:
            continue
    return stats


def _read_tcp_snmp() -> Dict[str, int]:
    """Global Tcp counters from /proc/net/snmp, keys prefixed with tcp_ (host-wide)."""
    return {f"tcp_{k}": v for k, v in _read_proc_section("/proc/net/snmp", "Tcp:").items()}


def _read_tcpext() -> Dict[str, int]:
    """TcpExt counters from /proc/net/netstat (host-wide)."""
    return _read_proc_section("/proc/net/netstat", "TcpExt:")


def _read_softnet() -> Dict[int, List[int]]:
    """Per-CPU rows of /proc/net/softnet_stat (hex), keyed by CPU index."""
    rows: Dict[int, List[int]] = {}
    try:
        with open("/proc/net/softnet_stat", "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                try:
                    vals = [int(p, 16) for p in line.split()]
                except ValueError:
                    continue
                cpu = vals[SOFTNET_CPU_INDEX_COL] if len(vals) > SOFTNET_CPU_INDEX_COL else i
                rows[cpu] = vals
    except OSError:
        pass
    return rows


def _read_conntrack() -> Tuple[Optional[int], Optional[int], Dict[str, int]]:
    """Return (count, max, per-CPU-summed stats). Empty if nf_conntrack isn't loaded."""
    count = _read_int_file("/proc/sys/net/netfilter/nf_conntrack_count")
    max_ = _read_int_file("/proc/sys/net/netfilter/nf_conntrack_max")

    sums: Dict[str, int] = {}
    try:
        with open("/proc/net/stat/nf_conntrack", "r", encoding="utf-8") as f:
            lines = [ln.split() for ln in f if ln.strip()]
    except OSError:
        return count, max_, sums

    if not lines:
        return count, max_, sums

    header = lines[0]
    for parts in lines[1:]:
        if len(parts) != len(header):
            continue
        for h, v in zip(header, parts):
            if h == "entries":
                continue
            try:
                sums[h] = sums.get(h, 0) + int(v, 16)
            except ValueError:
                continue
    return count, max_, sums


_TC_SENT_RE = re.compile(
    r"Sent (\d+) bytes (\d+) pkt \(dropped (\d+), overlimits (\d+) requeues (\d+)\)"
)
_TC_BACKLOG_RE = re.compile(r"backlog (\d+)b (\d+)p")


def _read_qdisc_root(dev: str) -> Dict[str, object]:
    """
    Stats of the root qdisc of <dev>. With an mq root, the kernel reports the
    sum of all per-queue child qdiscs (fq / fq_codel), so nothing is double counted.
    """
    out = _run_text(["tc", "-s", "-j", "qdisc", "show", "dev", dev])
    if out.strip():
        try:
            for q in json.loads(out):
                if q.get("root"):
                    return {
                        "kind": str(q.get("kind", "")),
                        "bytes": int(q.get("bytes", 0)),
                        "packets": int(q.get("packets", 0)),
                        "drops": int(q.get("drops", 0)),
                        "overlimits": int(q.get("overlimits", 0)),
                        "requeues": int(q.get("requeues", 0)),
                        "backlog_bytes": int(q.get("backlog", 0)),
                        "qlen": int(q.get("qlen", 0)),
                    }
        except (ValueError, TypeError, AttributeError):
            pass

    # Fallback for old iproute2 without JSON output.
    out = _run_text(["tc", "-s", "qdisc", "show", "dev", dev])
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if not (line.startswith("qdisc ") and " root" in line):
            continue
        res: Dict[str, object] = {"kind": line.split()[1]}
        for nxt in lines[i + 1:]:
            if nxt.startswith("qdisc "):
                break
            m = _TC_SENT_RE.search(nxt)
            if m:
                for k, v in zip(("bytes", "packets", "drops", "overlimits", "requeues"), m.groups()):
                    res[k] = int(v)
            m = _TC_BACKLOG_RE.search(nxt)
            if m:
                res["backlog_bytes"] = int(m.group(1))
                res["qlen"] = int(m.group(2))
        return res
    return {}


# --------------------------------------------------------------------------- #
# Delta helpers
# --------------------------------------------------------------------------- #

def _delta_dict(cur: Dict[str, int], prev: Optional[Dict[str, int]]) -> Dict[str, int]:
    """
    Legacy behavior (kept unchanged for the legacy columns): deltas between
    consecutive samples; zero when there is no previous sample.
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


def _delta_reset_safe(cur: Dict[str, int], prev: Optional[Dict[str, int]]) -> Dict[str, int]:
    """
    Like _delta_dict, but if a counter went backwards (e.g. a qdisc was replaced
    when switching fq <-> fq_codel) the current value is used as the delta.
    """
    if prev is None:
        return {k: 0 for k in cur}
    out: Dict[str, int] = {}
    for key, val in cur.items():
        p = prev.get(key)
        if p is None:
            out[key] = 0
        elif val < p:
            out[key] = val
        else:
            out[key] = val - p
    return out


# --------------------------------------------------------------------------- #
# psutil backend
# --------------------------------------------------------------------------- #

class PsutilNetMonitor:
    def __init__(self, cfg: NetMonitorConfig, out_csv_path: str, out_ext_path: Optional[str] = None) -> None:
        self.cfg = cfg

        # Prime host-wide collectors first so their first deltas cover the same
        # window as the existing tcp_retranssegs_d column.
        self._prev_tcp_snmp: Optional[Dict[str, int]] = (
            _read_tcp_snmp() if cfg.collect_tcp_snmp else None
        )
        self._prev_tcpext: Optional[Dict[str, int]] = (
            _read_tcpext() if cfg.collect_tcp_netstat else None
        )
        self._prev_softnet: Dict[int, List[int]] = (
            _read_softnet() if cfg.collect_softnet else {}
        )
        self._prev_conntrack: Optional[Dict[str, int]] = (
            _read_conntrack()[2] if cfg.collect_conntrack else None
        )

        # TcpExt fields vary by kernel; discover them once and append last.
        self._tcpext_names: List[str] = list(self._prev_tcpext or {})

        self._fieldnames: List[str] = list(LEGACY_FIELDS)
        self._ext_fieldnames: List[str] = (
            ["ts_wall_s", "ts_mono_s", "dt_s", "nic"]
            + _fixed_new_fields()
            + [f"tcpext_{n}_d" for n in self._tcpext_names]
        )

        self.logger = CsvLogger(out_csv_path, fieldnames=self._fieldnames, flush_every=1)
        self.ext_logger = CsvLogger(
            out_ext_path or ext_path(out_csv_path), fieldnames=self._ext_fieldnames, flush_every=1
        )

        self._prev_mono: Optional[float] = None
        self._prev = psutil.net_io_counters(pernic=True)

        self._inc = re.compile(cfg.nic_include_regex) if cfg.nic_include_regex else None
        self._exc = re.compile(cfg.nic_exclude_regex) if cfg.nic_exclude_regex else None

        self._prev_ip_link: Dict[str, Dict[str, int]] = {}
        self._prev_ethtool: Dict[str, Dict[str, int]] = {}
        self._prev_qdisc: Dict[str, Dict[str, int]] = {}

        self._rxq_packets_re = re.compile(r"^rx\d+_packets$")

    def close(self) -> None:
        for lg in (self.logger, self.ext_logger):
            try:
                lg.close()
            except Exception:
                pass

    def _keep(self, nic: str) -> bool:
        if self._inc and not self._inc.search(nic):
            return False
        if self._exc and self._exc.search(nic):
            return False
        return True

    # ---- per-NIC collectors ------------------------------------------------ #

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

    def _read_nic_qdisc(self, nic: str) -> Dict[str, object]:
        if not self.cfg.collect_qdisc:
            return {}

        raw = _read_qdisc_root(nic)
        if not raw:
            return {}

        counters = {
            k: int(raw[k])
            for k in ("bytes", "packets", "drops", "overlimits", "requeues")
            if k in raw
        }
        delta = _delta_reset_safe(counters, self._prev_qdisc.get(nic))
        self._prev_qdisc[nic] = counters

        out: Dict[str, object] = {"qdisc_root_kind": raw.get("kind", "")}
        for k, v in delta.items():
            out[f"qdisc_{k}_d"] = v
        for k in ("backlog_bytes", "qlen"):
            if k in raw:
                out[f"qdisc_{k}"] = raw[k]
        return out

    # ---- host-wide collectors (read once per sample) ----------------------- #

    def _read_tcp_snmp_cols(self) -> Dict[str, object]:
        """Returns the legacy retrans column plus all other Tcp fields."""
        if not self.cfg.collect_tcp_snmp:
            return {"tcp_retranssegs_d": 0}

        cur = _read_tcp_snmp()
        delta = _delta_dict(cur, self._prev_tcp_snmp)
        self._prev_tcp_snmp = cur

        out: Dict[str, object] = {"tcp_retranssegs_d": int(delta.get("tcp_RetransSegs", 0))}
        for n in TCP_SNMP_GAUGES:
            if f"tcp_{n}" in cur:
                out[f"tcp_{n.lower()}"] = cur[f"tcp_{n}"]
        for n in TCP_SNMP_COUNTERS:
            if f"tcp_{n}" in delta:
                out[f"tcp_{n.lower()}_d"] = delta[f"tcp_{n}"]
        return out

    def _read_tcpext_cols(self) -> Dict[str, object]:
        if not self.cfg.collect_tcp_netstat:
            return {}
        cur = _read_tcpext()
        delta = _delta_dict(cur, self._prev_tcpext)
        self._prev_tcpext = cur
        return {f"tcpext_{n}_d": delta[n] for n in self._tcpext_names if n in delta}

    def _read_softnet_cols(self) -> Dict[str, object]:
        if not self.cfg.collect_softnet:
            return {}
        cur = _read_softnet()
        prev = self._prev_softnet
        self._prev_softnet = cur

        sums = {name: 0 for _, name in SOFTNET_COUNTERS}
        backlog_len = 0
        cpus_active = 0
        processed_max = 0

        for cpu, vals in cur.items():
            pvals = prev.get(cpu)
            for col, name in SOFTNET_COUNTERS:
                if col >= len(vals) or pvals is None or col >= len(pvals):
                    continue
                d = vals[col] - pvals[col]
                if d < 0:  # 32-bit counter wrap
                    d += 1 << 32
                sums[name] += d
                if name == "processed":
                    if d > 0:
                        cpus_active += 1
                    processed_max = max(processed_max, d)
            if SOFTNET_BACKLOG_LEN_COL < len(vals):
                backlog_len += vals[SOFTNET_BACKLOG_LEN_COL]

        out: Dict[str, object] = {f"softnet_{name}_d": v for name, v in sums.items()}
        out["softnet_backlog_len"] = backlog_len
        out["softnet_cpus_active"] = cpus_active
        out["softnet_processed_max_cpu_d"] = processed_max
        return out

    def _read_conntrack_cols(self) -> Dict[str, object]:
        if not self.cfg.collect_conntrack:
            return {}
        count, max_, stats = _read_conntrack()
        delta = _delta_reset_safe(stats, self._prev_conntrack)
        self._prev_conntrack = stats

        out: Dict[str, object] = {}
        if count is not None:
            out["conntrack_count"] = count
        if max_ is not None:
            out["conntrack_max"] = max_
        for n in CONNTRACK_STAT_FIELDS:
            if n in delta:
                out[f"conntrack_{n}_d"] = delta[n]
        return out

    # ---- sampling ---------------------------------------------------------- #

    def sample_once(self) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        self.last_ts = (t_wall, t_mono)  # used for the matching timesync row

        dt = None if self._prev_mono is None else max(1e-9, t_mono - self._prev_mono)
        self._prev_mono = t_mono

        cur = psutil.net_io_counters(pernic=True)
        prev = self._prev
        self._prev = cur

        # Host-wide, not per NIC. Repeated on each NIC row for easier plotting.
        host_cols: Dict[str, object] = {}
        host_cols.update(self._read_tcp_snmp_cols())
        host_cols.update(self._read_softnet_cols())
        host_cols.update(self._read_conntrack_cols())
        host_cols.update(self._read_tcpext_cols())

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
            qdisc_cols = self._read_nic_qdisc(nic)

            rxq_packets_sum_d = 0
            if self.cfg.collect_rxq_packets_sum:
                rxq_packets_sum_d = sum(
                    val
                    for key, val in eth_d.items()
                    if self._rxq_packets_re.match(key)
                )

            # Every field present; collectors that found nothing leave "".
            row: Dict[str, object] = dict.fromkeys(self._fieldnames + self._ext_fieldnames, "")

            # ---- legacy columns (unchanged) ----
            row.update({
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
            })

            # ---- new columns ----
            row.update(host_cols)

            for n in IP_LINK_EXTRA:
                if n in ip_d:
                    row[f"{n}_d"] = int(ip_d[n])

            row.update(qdisc_cols)

            if self.cfg.collect_ethtool:
                row["ethtool_selected_missing"] = ";".join(
                    n for n in self.cfg.ethtool_counters if n not in eth_d
                )
                if self.cfg.collect_ethtool_all_json:
                    row["ethtool_all_nonzero_d_json"] = json.dumps(
                        {k: v for k, v in eth_d.items() if v != 0},
                        sort_keys=True, separators=(",", ":"),
                    )

            self.logger.write({k: row[k] for k in self._fieldnames})
            self.ext_logger.write({k: row[k] for k in self._ext_fieldnames})


# --------------------------------------------------------------------------- #
# bpftrace backends
# --------------------------------------------------------------------------- #

# bpftrace prints map entries as  @name[key]: value  (string keys usually
# without quotes). Accept both quoted and unquoted keys.
_BPF_KV_RE = re.compile(r"^\s*@(?P<map>\w+)\[(?P<key>.+)\]\s*:\s*(?P<val>\d+)\s*$")

_BPFTRACE_TPUT_SCRIPT = r"""
tracepoint:net:net_dev_queue
{
  @tx[args->name] = sum(args->len);
}

tracepoint:net:netif_receive_skb
{
  @rx[args->name] = sum(args->len);
}

interval:ms:__INTERVAL_MS__
{
  printf("=== %d ===\n", nsecs);
  print(@tx);
  print(@rx);
  clear(@tx);
  clear(@rx);
}

END
{
  clear(@tx);
  clear(@rx);
}
"""

# Kernel 5.17+: every dropped skb carries a drop reason. location = the kernel
# function that dropped it (e.g. tcp_v4_rcv, nf_hook_slow, enqueue_to_backlog).
_BPFTRACE_DROP_SCRIPT = r"""
tracepoint:skb:kfree_skb
{
  @drop[args->reason, ksym(args->location)] = count();
}

interval:ms:__INTERVAL_MS__
{
  printf("=== %d ===\n", nsecs);
  print(@drop);
  clear(@drop);
}

END
{
  clear(@drop);
}
"""


def _start_bpftrace(script: str, interval_s: float, log_path: str):
    bpftrace = shutil.which("bpftrace")
    if not bpftrace:
        raise RuntimeError("bpftrace not found on PATH")

    interval_ms = max(1, int(round(interval_s * 1000)))
    # stderr goes to a file: an unread PIPE can fill up and stall bpftrace.
    log_f = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [bpftrace, "-q", "-e", script.replace("__INTERVAL_MS__", str(interval_ms))],
        stdout=subprocess.PIPE,
        stderr=log_f,
        text=True,
        bufsize=1,
    )
    return proc, log_f


def _stop_proc(proc, log_f) -> None:
    try:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except Exception:
                proc.kill()
    finally:
        try:
            log_f.close()
        except Exception:
            pass


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

        self._proc, self._log_f = _start_bpftrace(
            _BPFTRACE_TPUT_SCRIPT, cfg.interval_s, out_csv_path + ".bpftrace.log"
        )

    def close(self) -> None:
        try:
            _stop_proc(self._proc, self._log_f)
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
                continue

            m = _BPF_KV_RE.match(line)
            if not m:
                continue

            nic = m.group("key").strip().strip('"')
            if not self._keep(nic):
                continue

            if m.group("map") == "tx":
                tx[nic] = int(m.group("val"))
            elif m.group("map") == "rx":
                rx[nic] = int(m.group("val"))

        if tx or rx:
            self._emit(tx, rx)

    def _emit(self, tx: Dict[str, int], rx: Dict[str, int]) -> None:
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        interval = max(1e-9, self.cfg.interval_s)

        for nic in sorted(set(tx) | set(rx)):
            tx_bytes = tx.get(nic, 0)
            rx_bytes = rx.get(nic, 0)

            self.logger.write({
                "ts_wall_s": t_wall,
                "ts_mono_s": t_mono,
                "backend": "bpftrace",
                "nic": nic,
                "rx_bytes": int(rx_bytes),
                "tx_bytes": int(tx_bytes),
                "rx_Mbps": (rx_bytes * 8.0) / interval / 1e6,
                "tx_Mbps": (tx_bytes * 8.0) / interval / 1e6,
            })


def _load_drop_reason_names() -> Dict[int, str]:
    """Map drop-reason numbers to names using the tracepoint's format file."""
    for path in (
        "/sys/kernel/tracing/events/skb/kfree_skb/format",
        "/sys/kernel/debug/tracing/events/skb/kfree_skb/format",
    ):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        names: Dict[int, str] = {}
        for num, name in re.findall(r"\{\s*(\d+)\s*,\s*\"(\w+)\"\s*\}", text):
            names[int(num)] = name
        if names:
            return names
    return {}


class DropReasonTracer:
    """
    Runs alongside either backend. Writes one row per (interval, reason, location)
    with a non-zero count, to a separate CSV. Requires root, bpftrace and kernel 5.17+.
    """

    def __init__(self, cfg: NetMonitorConfig, out_csv_path: str) -> None:
        self.cfg = cfg
        self.logger = CsvLogger(
            out_csv_path,
            fieldnames=["ts_wall_s", "ts_mono_s", "reason_id", "reason", "location", "count"],
            flush_every=1,
        )
        self._names = _load_drop_reason_names()
        self._proc, self._log_f = _start_bpftrace(
            _BPFTRACE_DROP_SCRIPT, cfg.interval_s, out_csv_path + ".bpftrace.log"
        )

    def close(self) -> None:
        try:
            _stop_proc(self._proc, self._log_f)
        finally:
            self.logger.close()

    def _parse_reason(self, raw: str) -> Tuple[object, str]:
        raw = raw.strip()
        if raw.isdigit():
            rid = int(raw)
            return rid, self._names.get(rid, str(rid))
        # Newer bpftrace may print the enum name directly.
        return "", raw.replace("SKB_DROP_REASON_", "")

    def run_forever(self, stop_flag_path: str) -> None:
        if not self._proc.stdout:
            raise RuntimeError("bpftrace stdout missing")

        batch: List[Tuple[object, str, str, int]] = []

        for line in self._proc.stdout:
            if os.path.exists(stop_flag_path):
                break

            line = line.rstrip("\n")
            if line.startswith("==="):
                self._emit(batch)
                batch = []
                continue

            m = _BPF_KV_RE.match(line)
            if not m or m.group("map") != "drop":
                continue

            reason_raw, _, location = m.group("key").partition(",")
            rid, name = self._parse_reason(reason_raw)
            batch.append((rid, name, location.strip(), int(m.group("val"))))

        self._emit(batch)

    def _emit(self, batch: List[Tuple[object, str, str, int]]) -> None:
        if not batch:
            return
        t_wall = now_wall_s()
        t_mono = now_mono_s()
        for rid, name, location, count in batch:
            self.logger.write({
                "ts_wall_s": t_wall,
                "ts_mono_s": t_mono,
                "reason_id": rid,
                "reason": name,
                "location": location,
                "count": count,
            })


def make_net_monitor(cfg: NetMonitorConfig, out_csv_path: str, out_ext_path: Optional[str] = None):
    if cfg.backend == "psutil":
        return PsutilNetMonitor(cfg, out_csv_path, out_ext_path)

    if cfg.backend == "bpftrace":
        return BpftraceNetMonitor(cfg, out_csv_path)

    raise ValueError(f"Unknown net backend: {cfg.backend}")


def make_drop_tracer(cfg: NetMonitorConfig, out_csv_path: str) -> DropReasonTracer:
    return DropReasonTracer(cfg, out_csv_path)