from __future__ import annotations

import base64
import json

import logging
import re
import shlex
from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Sequence, Any

from remote import run_subprocess
from config import Config

# Helpers:
# #-------------------------------------------------------------------------------
# def _host_report_dir(
#     cfg: Config,
#     host: str,
#     out_dir: str,
# ) -> str:
#     hostname = _first_line(
#         _command_result(
#             cfg,
#             host,
#             "hostname -s",
#         ),
#         default=host,
#     )

#     safe_hostname = re.sub(
#         r"[^A-Za-z0-9_.-]+",
#         "_",
#         hostname,
#     )

#     return f"{out_dir}/system/{safe_hostname}"
# #-------------------------------------------------------------------------------
# # System reports

# _SYSCTL_KEYS = (
#     "net.ipv4.tcp_mtu_probing",
#     "net.core.optmem_max",
#     "net.ipv4.tcp_slow_start_after_idle",
#     "net.core.rmem_max",
#     "net.core.wmem_max",
#     "net.core.rmem_default",
#     "net.core.wmem_default",
#     "net.ipv4.tcp_rmem",
#     "net.ipv4.tcp_wmem",
#     "net.ipv4.tcp_congestion_control",
#     "net.core.default_qdisc",
#     "net.ipv4.tcp_no_metrics_save",
#     "net.ipv4.tcp_low_latency",
#     "net.ipv4.tcp_notsent_lowat",
#     "net.ipv4.tcp_autocorking",
#     "net.ipv4.tcp_limit_output_bytes",
#     "net.ipv4.tcp_pacing_ss_ratio",
#     "net.ipv4.tcp_pacing_ca_ratio",
#     "net.ipv4.tcp_adv_win_scale",
#     "net.ipv4.tcp_app_win",
#     "net.core.netdev_budget",
#     "net.core.netdev_budget_usecs",
#     "net.core.netdev_max_backlog",
#     "net.core.dev_weight",
#     "net.ipv4.tcp_window_scaling",
#     "net.ipv4.tcp_sack",
#     "net.ipv4.tcp_dsack",
#     "net.ipv4.tcp_timestamps",
#     "net.ipv4.tcp_ecn",
#     "net.ipv4.tcp_moderate_rcvbuf",
# )


# def _command_result(
#     cfg: Config,
#     host: str,
#     command: str,
#     *,
#     sudo: bool = False,
# ) -> Dict[str, Any]:
#     """Run a read-only report command and preserve failures in the JSON output."""
#     full_command = f"sudo -n {command}" if sudo else command
#     try:
#         cp = run_subprocess(
#             host,
#             None,
#             full_command,
#             localhost=cfg.localhost,
#             check=False,
#         )
#     except Exception as exc:
#         return {
#             "command": full_command,
#             "returncode": None,
#             "stdout": "",
#             "stderr": str(exc),
#             "available": False,
#         }

#     return {
#         "command": full_command,
#         "returncode": cp.returncode,
#         "stdout": (cp.stdout or "").rstrip(),
#         "stderr": (cp.stderr or "").rstrip(),
#         "available": cp.returncode == 0,
#     }


# def _output(result: Dict[str, Any]) -> str:
#     return str(result.get("stdout", "")).strip()


# def _first_line(result: Dict[str, Any], default: str = "unknown") -> str:
#     text = _output(result)
#     return text.splitlines()[0].strip() if text else default


# def _safe_int(value: Any) -> Optional[int]:
#     try:
#         return int(str(value).strip())
#     except (TypeError, ValueError):
#         return None


# def _sysctl_value(sysctls: Dict[str, Dict[str, Any]], key: str) -> Any:
#     result = sysctls.get(key, {})
#     if not result.get("available"):
#         return None
#     text = _output(result)
#     values = text.split()
#     if len(values) == 1:
#         integer = _safe_int(values[0])
#         return integer if integer is not None else values[0]
#     return text


# def _parse_ethtool_ring(text: str) -> Dict[str, Dict[str, Optional[int]]]:
#     parsed: Dict[str, Dict[str, Optional[int]]] = {
#         "maximum": {"rx": None, "tx": None},
#         "current": {"rx": None, "tx": None},
#     }
#     section: Optional[str] = None
#     for raw_line in text.splitlines():
#         line = raw_line.strip()
#         lowered = line.lower()
#         if lowered.startswith("pre-set maximums"):
#             section = "maximum"
#             continue
#         if lowered.startswith("current hardware settings"):
#             section = "current"
#             continue
#         if section is None or ":" not in line:
#             continue
#         name, value = (part.strip() for part in line.split(":", 1))
#         if name == "RX":
#             parsed[section]["rx"] = _safe_int(value)
#         elif name == "TX":
#             parsed[section]["tx"] = _safe_int(value)
#     return parsed


# def _parse_ethtool_channels(text: str) -> Dict[str, Dict[str, Optional[int]]]:
#     parsed: Dict[str, Dict[str, Optional[int]]] = {
#         "maximum": {"rx": None, "tx": None, "other": None, "combined": None},
#         "current": {"rx": None, "tx": None, "other": None, "combined": None},
#     }
#     section: Optional[str] = None
#     names = {"RX": "rx", "TX": "tx", "Other": "other", "Combined": "combined"}
#     for raw_line in text.splitlines():
#         line = raw_line.strip()
#         lowered = line.lower()
#         if lowered.startswith("pre-set maximums"):
#             section = "maximum"
#             continue
#         if lowered.startswith("current hardware settings"):
#             section = "current"
#             continue
#         if section is None or ":" not in line:
#             continue
#         name, value = (part.strip() for part in line.split(":", 1))
#         if name in names:
#             parsed[section][names[name]] = _safe_int(value)
#     return parsed


# def _parse_ethtool_features(text: str) -> Dict[str, Any]:
#     features: Dict[str, Any] = {}
#     for raw_line in text.splitlines():
#         line = raw_line.strip()
#         if not line or line.startswith("Features for ") or ":" not in line:
#             continue
#         name, value = (part.strip() for part in line.split(":", 1))
#         state = value.split()[0].lower()
#         if state == "on":
#             features[name] = True
#         elif state == "off":
#             features[name] = False
#         else:
#             features[name] = value
#     return features


# def _parse_private_flags(text: str) -> Dict[str, Any]:
#     flags: Dict[str, Any] = {}
#     for raw_line in text.splitlines():
#         line = raw_line.strip()
#         if not line or line.startswith("Private flags for ") or ":" not in line:
#             continue
#         name, value = (part.strip() for part in line.split(":", 1))
#         lowered = value.lower()
#         flags[name] = True if lowered == "on" else False if lowered == "off" else value
#     return flags


# def _parse_key_value_lines(text: str) -> Dict[str, Any]:
#     values: Dict[str, Any] = {}
#     for raw_line in text.splitlines():
#         line = raw_line.strip()
#         if not line or ":" not in line:
#             continue
#         key, value = (part.strip() for part in line.split(":", 1))
#         lowered = value.lower()
#         if lowered in {"on", "yes", "true"}:
#             values[key] = True
#         elif lowered in {"off", "no", "false"}:
#             values[key] = False
#         else:
#             integer = _safe_int(value)
#             values[key] = integer if integer is not None else value
#     return values


# def _parse_ip_link_sizes(text: str) -> Dict[str, Optional[int]]:
#     result: Dict[str, Optional[int]] = {
#         "mtu": None,
#         "tx_queue_length": None,
#         "gso_max_size": None,
#         "gro_max_size": None,
#         "gso_ipv4_max_size": None,
#         "gro_ipv4_max_size": None,
#     }
#     patterns = {
#         "mtu": r"\bmtu\s+(\d+)",
#         "tx_queue_length": r"\bqlen\s+(\d+)",
#         "gso_max_size": r"\bgso_max_size\s+(\d+)",
#         "gro_max_size": r"\bgro_max_size\s+(\d+)",
#         "gso_ipv4_max_size": r"\bgso_ipv4_max_size\s+(\d+)",
#         "gro_ipv4_max_size": r"\bgro_ipv4_max_size\s+(\d+)",
#     }
#     for key, pattern in patterns.items():
#         match = re.search(pattern, text)
#         if match:
#             result[key] = int(match.group(1))
#     return result


# def _write_remote_json(
#     cfg: Config,
#     host: str,
#     path: str,
#     payload: Dict[str, Any],
#     *,
#     check: bool,
# ) -> None:
#     """Atomically write formatted JSON on a local or SSH host."""
#     serialized = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
#     encoded = base64.b64encode(serialized.encode("utf-8")).decode("ascii")
#     parent = str(PurePosixPath(path).parent)
#     temporary = f"{path}.tmp"
#     cmd = (
#         f"mkdir -p {shlex.quote(parent)} && "
#         f"printf '%s' {shlex.quote(encoded)} | base64 -d > {shlex.quote(temporary)} && "
#         f"mv {shlex.quote(temporary)} {shlex.quote(path)}"
#     )
#     cp = run_subprocess(
#         host,
#         None,
#         cmd,
#         localhost=cfg.localhost,
#         check=False,
#     )
#     if cp.returncode != 0:
#         message = (
#             f"REPORT: Failed writing JSON on {str(host).upper()} to {path}\n"
#             f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
#         )
#         if check:
#             raise RuntimeError(message)
#         logging.warning(message)


# def _get_cfg_value(cfg: Config, *names: str, default: Any = None) -> Any:
#     for name in names:
#         if hasattr(cfg, name):
#             value = getattr(cfg, name)
#             if value is not None:
#                 return value
#     return default


# def _get_report_devices(
#     cfg: Config,
#     host: str,
#     *,
#     check: bool = True,
# ) -> List[str]:
#     """
#     Return only the interfaces configured for this host.

#     The configured interfaces are verified against /sys/class/net on the
#     target machine. A host may have one or more interfaces.
#     """
#     devices = [
#         str(dev).strip()
#         for dev in cfg.interfaces.get(host, ())
#         if str(dev).strip()
#     ]

#     # Preserve order while removing duplicates.
#     devices = list(dict.fromkeys(devices))

#     if not devices:
#         message = (
#             f"REPORT: No interfaces configured for host "
#             f"{str(host).upper()}"
#         )

#         if check:
#             raise RuntimeError(message)

#         logging.warning(message)
#         return []

#     existing: List[str] = []

#     for dev in devices:
#         result = _command_result(
#             cfg,
#             host,
#             f"test -d /sys/class/net/{shlex.quote(dev)}",
#         )

#         if result.get("available"):
#             existing.append(dev)
#             continue

#         message = (
#             f"REPORT: Configured interface {dev!r} does not exist "
#             f"on {str(host).upper()}"
#         )

#         if check:
#             raise RuntimeError(message)

#         logging.warning(message)

#     return existing


# def _sysctl_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
#     for host in hosts:
#         values: Dict[str, Dict[str, Any]] = {}
#         for key in _SYSCTL_KEYS:
#             values[key] = _command_result(cfg, host, f"sysctl -n {shlex.quote(key)}")

#         report = {
#             "report_type": "network_stack",
#             "created_at": datetime.now().astimezone().isoformat(),
#             "host": host,
#             "sysctl": values,
#         }
#         _write_remote_json(cfg, host, f"{out_dir}/sysctl.json", report, check=check)
#         logging.debug("SYSCTL: Recorded sysctl values on %s", str(host).upper())


# def _host_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
#     for host in hosts:
#         report = {
#             "report_type": "system",
#             "created_at": datetime.now().astimezone().isoformat(),
#             "host": host,
#             "hostname": _command_result(cfg, host, "hostname"),
#             "uname": _command_result(cfg, host, "uname -a"),
#             "lscpu": _command_result(cfg, host, "lscpu"),
#             "ip_link": _command_result(cfg, host, "ip link"),
#             "ip_brief_address": _command_result(cfg, host, "ip -br a"),
#             "ip_route": _command_result(cfg, host, "ip route"),
#             "df": _command_result(cfg, host, "df -hT"),
#             "lsblk": _command_result(
#                 cfg,
#                 host,
#                 "lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL",
#             ),
#         }
#         _write_remote_json(cfg, host, f"{out_dir}/host.json", report, check=check)


# # def _nic_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
# #     for host in hosts:
# #         devices_result = _command_result(
# #             cfg,
# #             host,
# #             "find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\\n' | sort",
# #         )
# #         devices = [dev for dev in _output(devices_result).splitlines() if dev and dev != "lo"]

# #         for dev in devices:
# #             qdev = shlex.quote(dev)
# #             ip_details = _command_result(cfg, host, f"ip -d link show dev {qdev}")
# #             ethtool_ring = _command_result(cfg, host, f"ethtool -g {qdev}", sudo=True)
# #             ethtool_features = _command_result(cfg, host, f"ethtool -k {qdev}", sudo=True)
# #             ethtool_coalesce = _command_result(cfg, host, f"ethtool -c {qdev}", sudo=True)
# #             private_flags = _command_result(cfg, host, f"ethtool --show-priv-flags {qdev}", sudo=True)

# #             report = {
# #                 "report_type": "interface",
# #                 "created_at": datetime.now().astimezone().isoformat(),
# #                 "host": host,
# #                 "interface": dev,
# #                 "parsed": {
# #                     "link": _parse_ip_link_sizes(_output(ip_details)),
# #                     "ring": _parse_ethtool_ring(_output(ethtool_ring)),
# #                     "features": _parse_ethtool_features(_output(ethtool_features)),
# #                     "coalescing": _parse_key_value_lines(_output(ethtool_coalesce)),
# #                     "private_flags": _parse_private_flags(_output(private_flags)),
# #                 },
# #                 "commands": {
# #                     "ip_link_details": ip_details,
# #                     "ethtool": _command_result(cfg, host, f"ethtool {qdev}", sudo=True),
# #                     "driver": _command_result(cfg, host, f"ethtool -i {qdev}", sudo=True),
# #                     "pause": _command_result(cfg, host, f"ethtool -a {qdev}", sudo=True),
# #                     "ring": ethtool_ring,
# #                     "features": ethtool_features,
# #                     "coalescing": ethtool_coalesce,
# #                     "statistics": _command_result(cfg, host, f"ethtool -S {qdev}", sudo=True),
# #                     "fec": _command_result(cfg, host, f"ethtool --show-fec {qdev}", sudo=True),
# #                     "private_flags": private_flags,
# #                     "tx_queue_len": _command_result(cfg, host, f"cat /sys/class/net/{qdev}/tx_queue_len"),
# #                     "qdisc": _command_result(cfg, host, f"tc qdisc show dev {qdev}", sudo=True),
# #                     "qdisc_statistics": _command_result(cfg, host, f"tc -s qdisc show dev {qdev}", sudo=True),
# #                 },
# #             }
# #             _write_remote_json(cfg, host, f"{out_dir}/nic_{dev}.json", report, check=check)

# #         logging.debug("NIC: Recorded NIC reports on %s", str(host).upper())
# def _nic_report(
#     cfg: Config,
#     hosts: Sequence[str],
#     out_dir: str,
#     check: bool = True,
# ) -> None:
#     for host in hosts:
#         devices = _get_report_devices(
#             cfg,
#             host,
#             check=check,
#         )

#         for dev in devices:
#             qdev = shlex.quote(dev)

#             ip_details = _command_result(
#                 cfg,
#                 host,
#                 f"ip -d link show dev {qdev}",
#             )

#             ethtool_ring = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -g {qdev}",
#                 sudo=True,
#             )

#             ethtool_features = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -k {qdev}",
#                 sudo=True,
#             )

#             ethtool_coalesce = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -c {qdev}",
#                 sudo=True,
#             )

#             private_flags = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool --show-priv-flags {qdev}",
#                 sudo=True,
#             )

#             report = {
#                 "report_type": "interface",
#                 "created_at": datetime.now().astimezone().isoformat(),
#                 "host": host,
#                 "interface": dev,
#                 "parsed": {
#                     "link": _parse_ip_link_sizes(
#                         _output(ip_details)
#                     ),
#                     "ring": _parse_ethtool_ring(
#                         _output(ethtool_ring)
#                     ),
#                     "features": _parse_ethtool_features(
#                         _output(ethtool_features)
#                     ),
#                     "coalescing": _parse_key_value_lines(
#                         _output(ethtool_coalesce)
#                     ),
#                     "private_flags": _parse_private_flags(
#                         _output(private_flags)
#                     ),
#                 },
#                 "commands": {
#                     "ip_link_details": ip_details,

#                     "ethtool": _command_result(
#                         cfg,
#                         host,
#                         f"ethtool {qdev}",
#                         sudo=True,
#                     ),

#                     "driver": _command_result(
#                         cfg,
#                         host,
#                         f"ethtool -i {qdev}",
#                         sudo=True,
#                     ),

#                     "pause": _command_result(
#                         cfg,
#                         host,
#                         f"ethtool -a {qdev}",
#                         sudo=True,
#                     ),

#                     "ring": ethtool_ring,

#                     "features": ethtool_features,

#                     "coalescing": ethtool_coalesce,

#                     "statistics": _command_result(
#                         cfg,
#                         host,
#                         f"ethtool -S {qdev}",
#                         sudo=True,
#                     ),

#                     "fec": _command_result(
#                         cfg,
#                         host,
#                         f"ethtool --show-fec {qdev}",
#                         sudo=True,
#                     ),

#                     "private_flags": private_flags,

#                     "tx_queue_len": _command_result(
#                         cfg,
#                         host,
#                         f"cat /sys/class/net/{qdev}/tx_queue_len",
#                     ),

#                     "qdisc": _command_result(
#                         cfg,
#                         host,
#                         f"tc qdisc show dev {qdev}",
#                         sudo=True,
#                     ),

#                     "qdisc_statistics": _command_result(
#                         cfg,
#                         host,
#                         f"tc -s qdisc show dev {qdev}",
#                         sudo=True,
#                     ),
#                 },
#             }

#             _write_remote_json(
#                 cfg,
#                 host,
#                 f"{out_dir}/nic_{dev}.json",
#                 report,
#                 check=check,
#             )

#         logging.debug(
#             "NIC: Recorded configured NIC reports on %s",
#             str(host).upper(),
#         )

# # def _cpu_irq_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
# #     for host in hosts:
# #         irqbalance_active = _command_result(cfg, host, "systemctl is-active irqbalance")
# #         irqbalance_enabled = _command_result(cfg, host, "systemctl is-enabled irqbalance")
# #         report = {
# #             "report_type": "cpu",
# #             "created_at": datetime.now().astimezone().isoformat(),
# #             "host": host,
# #             "parsed": {
# #                 "irqbalance_active": _first_line(irqbalance_active),
# #                 "irqbalance_enabled": _first_line(irqbalance_enabled),
# #             },
# #             "irqbalance": {
# #                 "active": irqbalance_active,
# #                 "enabled": irqbalance_enabled,
# #                 "status": _command_result(cfg, host, "systemctl status irqbalance --no-pager"),
# #                 "process": _command_result(cfg, host, "pgrep -a irqbalance"),
# #             },
# #             "cpu_governor": _command_result(
# #                 cfg,
# #                 host,
# #                 "for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; "
# #                 "do printf '%s: ' \"$f\"; cat \"$f\" 2>/dev/null || true; done",
# #             ),
# #             "cpu_frequencies": _command_result(
# #                 cfg,
# #                 host,
# #                 "for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; "
# #                 "do printf '%s: ' \"$f\"; cat \"$f\" 2>/dev/null || true; done",
# #             ),
# #             "interrupts": _command_result(cfg, host, "cat /proc/interrupts"),
# #             "irq_affinity": _command_result(
# #                 cfg,
# #                 host,
# #                 "for irq in /proc/irq/[0-9]*; do "
# #                 "n=${irq##*/}; "
# #                 "printf 'IRQ %s smp_affinity=' \"$n\"; "
# #                 "cat \"$irq/smp_affinity\" 2>/dev/null || true; "
# #                 "printf 'IRQ %s smp_affinity_list=' \"$n\"; "
# #                 "cat \"$irq/smp_affinity_list\" 2>/dev/null || true; "
# #                 "done",
# #             ),
# #         }
# #         _write_remote_json(cfg, host, f"{out_dir}/cpu_irq.json", report, check=check)
# #         logging.debug("CPU/IRQ: Recorded CPU and IRQ report on %s", str(host).upper())
# def _cpu_irq_report(
#     cfg: Config,
#     hosts: Sequence[str],
#     out_dir: str,
#     check: bool = True,
# ) -> None:
#     for host in hosts:
#         devices = _get_report_devices(
#             cfg,
#             host,
#             check=check,
#         )

#         device_args = " ".join(
#             shlex.quote(dev)
#             for dev in devices
#         )

#         irqbalance_active = _command_result(
#             cfg,
#             host,
#             "systemctl is-active irqbalance",
#         )

#         irqbalance_enabled = _command_result(
#             cfg,
#             host,
#             "systemctl is-enabled irqbalance",
#         )

#         nic_interrupts = _command_result(
#             cfg,
#             host,
#             (
#                 f"for dev in {device_args}; do "
#                 f"echo \"### interface: $dev\"; "
#                 f"grep -i -- \"$dev\" /proc/interrupts || true; "
#                 f"done"
#             ),
#         )

#         nic_irq_affinity = _command_result(
#             cfg,
#             host,
#             (
#                 f"for dev in {device_args}; do "
#                 f"echo \"### interface: $dev\"; "

#                 f"for irq in $("
#                 f"grep -i -- \"$dev\" /proc/interrupts "
#                 f"| awk -F: "
#                 f"'{{"
#                 f"gsub(/[[:space:]]/, \"\", $1); "
#                 f"print $1"
#                 f"}}'"
#                 f"); do "

#                 f"printf 'IRQ %s smp_affinity=' \"$irq\"; "
#                 f"cat \"/proc/irq/$irq/smp_affinity\" "
#                 f"2>/dev/null || echo NOT_AVAILABLE; "

#                 f"printf 'IRQ %s smp_affinity_list=' \"$irq\"; "
#                 f"cat \"/proc/irq/$irq/smp_affinity_list\" "
#                 f"2>/dev/null || echo NOT_AVAILABLE; "

#                 f"done; "
#                 f"done"
#             ),
#         )

#         report = {
#             "report_type": "cpu",
#             "created_at": datetime.now().astimezone().isoformat(),
#             "host": host,
#             "interfaces": devices,

#             "parsed": {
#                 "irqbalance_active": _first_line(
#                     irqbalance_active
#                 ),
#                 "irqbalance_enabled": _first_line(
#                     irqbalance_enabled
#                 ),
#             },

#             "irqbalance": {
#                 "active": irqbalance_active,
#                 "enabled": irqbalance_enabled,

#                 "status": _command_result(
#                     cfg,
#                     host,
#                     "systemctl status irqbalance --no-pager",
#                 ),

#                 "process": _command_result(
#                     cfg,
#                     host,
#                     "pgrep -a irqbalance",
#                 ),
#             },

#             "cpu_governor": _command_result(
#                 cfg,
#                 host,
#                 (
#                     "for f in "
#                     "/sys/devices/system/cpu/cpu*/cpufreq/"
#                     "scaling_governor; "
#                     "do "
#                     "printf '%s: ' \"$f\"; "
#                     "cat \"$f\" 2>/dev/null || true; "
#                     "done"
#                 ),
#             ),

#             "cpu_frequencies": _command_result(
#                 cfg,
#                 host,
#                 (
#                     "for f in "
#                     "/sys/devices/system/cpu/cpu*/cpufreq/"
#                     "scaling_cur_freq; "
#                     "do "
#                     "printf '%s: ' \"$f\"; "
#                     "cat \"$f\" 2>/dev/null || true; "
#                     "done"
#                 ),
#             ),

#             "nic_interrupts": nic_interrupts,
#             "nic_irq_affinity": nic_irq_affinity,
#         }

#         _write_remote_json(
#             cfg,
#             host,
#             f"{out_dir}/cpu_irq.json",
#             report,
#             check=check,
#         )

#         logging.debug(
#             "CPU/IRQ: Recorded configured NIC IRQ report on %s",
#             str(host).upper(),
#         )


# # def _rss_rps_xps_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
# #     for host in hosts:
# #         devices_result = _command_result(
# #             cfg,
# #             host,
# #             "find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\\n' | sort",
# #         )
# #         devices = [dev for dev in _output(devices_result).splitlines() if dev and dev != "lo"]

# #         for dev in devices:
# #             qdev = shlex.quote(dev)
# #             channels = _command_result(cfg, host, f"ethtool -l {qdev}", sudo=True)
# #             report = {
# #                 "report_type": "topology",
# #                 "created_at": datetime.now().astimezone().isoformat(),
# #                 "host": host,
# #                 "interface": dev,
# #                 "parsed": {
# #                     "channels": _parse_ethtool_channels(_output(channels)),
# #                 },
# #                 "channels": channels,
# #                 "rss": _command_result(cfg, host, f"ethtool -x {qdev}", sudo=True),
# #                 "rps_xps": _command_result(
# #                     cfg,
# #                     host,
# #                     f"find /sys/class/net/{qdev}/queues -type f "
# #                     "\\( -name rps_cpus -o -name rps_flow_cnt -o -name xps_cpus \\) "
# #                     "-exec sh -c 'for f do printf \"%s: \" \"$f\"; cat \"$f\"; done' sh {} +",
# #                 ),
# #                 "numa_node": _command_result(cfg, host, f"cat /sys/class/net/{qdev}/device/numa_node"),
# #                 "local_cpulist": _command_result(cfg, host, f"cat /sys/class/net/{qdev}/device/local_cpulist"),
# #             }
# #             _write_remote_json(
# #                 cfg,
# #                 host,
# #                 f"{out_dir}/rss_rps_xps_{dev}.json",
# #                 report,
# #                 check=check,
# #             )

# #         logging.debug("RSS/RPS/XPS: Recorded reports on %s", str(host).upper())
# def _rss_rps_xps_report(
#     cfg: Config,
#     hosts: Sequence[str],
#     out_dir: str,
#     check: bool = True,
# ) -> None:
#     for host in hosts:
#         devices = _get_report_devices(
#             cfg,
#             host,
#             check=check,
#         )

#         for dev in devices:
#             qdev = shlex.quote(dev)

#             channels = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -l {qdev}",
#                 sudo=True,
#             )

#             report = {
#                 "report_type": "topology",
#                 "created_at": datetime.now().astimezone().isoformat(),
#                 "host": host,
#                 "interface": dev,

#                 "parsed": {
#                     "channels": _parse_ethtool_channels(
#                         _output(channels)
#                     ),
#                 },

#                 "channels": channels,

#                 "rss": _command_result(
#                     cfg,
#                     host,
#                     f"ethtool -x {qdev}",
#                     sudo=True,
#                 ),

#                 "rps_xps": _command_result(
#                     cfg,
#                     host,
#                     (
#                         f"find /sys/class/net/{qdev}/queues "
#                         f"-type f "
#                         f"\\( "
#                         f"-name rps_cpus "
#                         f"-o -name rps_flow_cnt "
#                         f"-o -name xps_cpus "
#                         f"\\) "
#                         f"-exec sh -c "
#                         f"'for f do "
#                         f"printf \"%s: \" \"$f\"; "
#                         f"cat \"$f\"; "
#                         f"done' sh {{}} +"
#                     ),
#                 ),

#                 "numa_node": _command_result(
#                     cfg,
#                     host,
#                     f"cat /sys/class/net/{qdev}/device/numa_node",
#                 ),

#                 "local_cpulist": _command_result(
#                     cfg,
#                     host,
#                     f"cat /sys/class/net/{qdev}/device/local_cpulist",
#                 ),
#             }

#             _write_remote_json(
#                 cfg,
#                 host,
#                 f"{out_dir}/rss_rps_xps_{dev}.json",
#                 report,
#                 check=check,
#             )

#         logging.debug(
#             "RSS/RPS/XPS: Recorded configured reports on %s",
#             str(host).upper(),
#         )


# def _storage_report(
#     cfg: Config,
#     hosts: Sequence[str],
#     out_dir: str,
#     path: str = "/",
#     check: bool = True,
# ) -> None:
#     for host in hosts:
#         report = {
#             "report_type": "storage",
#             "created_at": datetime.now().astimezone().isoformat(),
#             "host": host,
#             "df": _command_result(cfg, host, "df -hT"),
#             "mount": _command_result(cfg, host, "mount"),
#             "lsblk": _command_result(
#                 cfg,
#                 host,
#                 "lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,ROTA",
#             ),
#             "scheduler": _command_result(
#                 cfg,
#                 host,
#                 "for f in /sys/block/*/queue/scheduler; "
#                 "do printf '%s: ' \"$f\"; cat \"$f\" 2>/dev/null || true; done",
#             ),
#             "experiment_path_filesystem": _command_result(
#                 cfg,
#                 host,
#                 f"df -hT {shlex.quote(path)}",
#             ),
#         }
#         _write_remote_json(cfg, host, f"{out_dir}/storage.json", report, check=check)


# def _software_security_report(
#     cfg: Config,
#     hosts: Sequence[str],
#     out_dir: str,
#     check: bool = True,
# ) -> None:
#     for host in hosts:
#         software = {
#             "report_type": "software",
#             "created_at": datetime.now().astimezone().isoformat(),
#             "host": host,
#             "python": _command_result(cfg, host, "python3 --version"),
#             "iperf3": _command_result(cfg, host, "iperf3 --version"),
#             "rsync": _command_result(cfg, host, "rsync --version | head -n 3"),
#             "ethtool": _command_result(cfg, host, "ethtool --version"),
#             "iproute2": _command_result(cfg, host, "ip -Version"),
#             "docker": _command_result(cfg, host, "docker --version"),
#             "globus_cli": _command_result(cfg, host, "globus --version"),
#         }
#         security = {
#             "report_type": "security",
#             "created_at": datetime.now().astimezone().isoformat(),
#             "host": host,
#             "selinux": _command_result(cfg, host, "getenforce"),
#             "apparmor": _command_result(cfg, host, "aa-status"),
#             "ufw": _command_result(cfg, host, "ufw status verbose", sudo=True),
#             "firewalld": _command_result(cfg, host, "systemctl is-active firewalld"),
#             "iptables": _command_result(cfg, host, "iptables-save", sudo=True),
#         }
#         _write_remote_json(cfg, host, f"{out_dir}/software.json", software, check=check)
#         _write_remote_json(cfg, host, f"{out_dir}/security.json", security, check=check)


# # def _summary_report(
# #     cfg: Config,
# #     hosts: Sequence[str],
# #     out_dir: str,
# #     check: bool = True,
# # ) -> None:
# #     """Create a compact, directly readable summary alongside detailed reports."""
# #     created_at = datetime.now().astimezone().isoformat()
# #     experiment_id = str(
# #         _get_cfg_value(
# #             cfg,
# #             "experiment_id",
# #             "exp_id",
# #             default=PurePosixPath(out_dir).name or "unknown",
# #         )
# #     )

# #     for host in hosts:
# #         sysctls = {
# #             key: _command_result(cfg, host, f"sysctl -n {shlex.quote(key)}")
# #             for key in _SYSCTL_KEYS
# #         }
# #         devices_result = _command_result(
# #             cfg,
# #             host,
# #             "find /sys/class/net -mindepth 1 -maxdepth 1 -printf '%f\\n' | sort",
# #         )
# #         devices = [dev for dev in _output(devices_result).splitlines() if dev and dev != "lo"]

# #         interfaces: List[Dict[str, Any]] = []
# #         numa_nodes: Dict[str, Any] = {}
# #         for dev in devices:
# #             qdev = shlex.quote(dev)
# #             ip_details = _command_result(cfg, host, f"ip -d link show dev {qdev}")
# #             link = _parse_ip_link_sizes(_output(ip_details))
# #             ring = _parse_ethtool_ring(
# #                 _output(_command_result(cfg, host, f"ethtool -g {qdev}", sudo=True))
# #             )
# #             features = _parse_ethtool_features(
# #                 _output(_command_result(cfg, host, f"ethtool -k {qdev}", sudo=True))
# #             )
# #             qdisc = _first_line(
# #                 _command_result(cfg, host, f"tc qdisc show dev {qdev}", sudo=True),
# #                 default="unknown",
# #             )
# #             numa_node_result = _command_result(
# #                 cfg,
# #                 host,
# #                 f"cat /sys/class/net/{qdev}/device/numa_node",
# #             )
# #             numa_node = _safe_int(_first_line(numa_node_result, default=""))
# #             numa_nodes[dev] = numa_node

# #             interfaces.append(
# #                 {
# #                     "name": dev,
# #                     "mtu": link["mtu"],
# #                     "tx_queue_length": link["tx_queue_length"],
# #                     "gso_max_size": link["gso_max_size"],
# #                     "gro_max_size": link["gro_max_size"],
# #                     "gso_ipv4_max_size": link["gso_ipv4_max_size"],
# #                     "gro_ipv4_max_size": link["gro_ipv4_max_size"],
# #                     "rx_ring": ring["current"]["rx"],
# #                     "tx_ring": ring["current"]["tx"],
# #                     "rx_ring_max": ring["maximum"]["rx"],
# #                     "tx_ring_max": ring["maximum"]["tx"],
# #                     "tso": features.get("tcp-segmentation-offload"),
# #                     "gso": features.get("generic-segmentation-offload"),
# #                     "gro": features.get("generic-receive-offload"),
# #                     "lro": features.get("large-receive-offload"),
# #                     "rx_gro_hw": features.get("rx-gro-hw"),
# #                     "rx_checksum": features.get("rx-checksumming"),
# #                     "tx_checksum": features.get("tx-checksumming"),
# #                     "qdisc": qdisc,
# #                     "numa_node": numa_node,
# #                 }
# #             )

# #         governor_result = _command_result(
# #             cfg,
# #             host,
# #             "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
# #         )
# #         irqbalance_result = _command_result(cfg, host, "systemctl is-active irqbalance")

# #         summary = {
# #             "experiment_id": experiment_id,
# #             "created_at": created_at,
# #             "host": host,
# #             "workload": {
# #                 "test": _get_cfg_value(cfg, "test"),
# #                 "application": _get_cfg_value(cfg, "app", "application"),
# #                 "run": _get_cfg_value(cfg, "run", "repetition"),
# #                 "duration_s": _get_cfg_value(cfg, "duration", "timeout"),
# #                 "transfer_size_gb": _get_cfg_value(cfg, "size", "transfer_size"),
# #                 "parallel_streams": _get_cfg_value(cfg, "parallel", "streams"),
# #             },
# #             "system": {
# #                 "hostname": _first_line(_command_result(cfg, host, "hostname")),
# #                 "kernel": _first_line(_command_result(cfg, host, "uname -r")),
# #                 "cpu_governor": _first_line(governor_result),
# #                 "irqbalance": _first_line(irqbalance_result),
# #             },
# #             "network_stack": {
# #                 "tcp_mtu_probing": _sysctl_value(sysctls, "net.ipv4.tcp_mtu_probing"),
# #                 "optmem_max": _sysctl_value(sysctls, "net.core.optmem_max"),
# #                 "rmem_max": _sysctl_value(sysctls, "net.core.rmem_max"),
# #                 "wmem_max": _sysctl_value(sysctls, "net.core.wmem_max"),
# #                 "tcp_rmem": _sysctl_value(sysctls, "net.ipv4.tcp_rmem"),
# #                 "tcp_wmem": _sysctl_value(sysctls, "net.ipv4.tcp_wmem"),
# #                 "tcp_congestion_control": _sysctl_value(sysctls, "net.ipv4.tcp_congestion_control"),
# #                 "default_qdisc": _sysctl_value(sysctls, "net.core.default_qdisc"),
# #                 "netdev_budget": _sysctl_value(sysctls, "net.core.netdev_budget"),
# #                 "netdev_budget_usecs": _sysctl_value(sysctls, "net.core.netdev_budget_usecs"),
# #                 "netdev_max_backlog": _sysctl_value(sysctls, "net.core.netdev_max_backlog"),
# #                 "tcp_ecn": _sysctl_value(sysctls, "net.ipv4.tcp_ecn"),
# #                 "tcp_moderate_rcvbuf": _sysctl_value(sysctls, "net.ipv4.tcp_moderate_rcvbuf"),
# #             },
# #             "interface": interfaces,
# #             "cpu": {
# #                 "online_cpus": _first_line(
# #                     _command_result(cfg, host, "cat /sys/devices/system/cpu/online")
# #                 ),
# #                 "irqbalance_active": _first_line(irqbalance_result),
# #             },
# #             "numa": {
# #                 "configured_mode": _get_cfg_value(cfg, "numa", "numactl", "numa_mode"),
# #                 "interface_nodes": numa_nodes,
# #                 "hardware": _command_result(cfg, host, "numactl --hardware"),
# #             },
# #             "storage": {
# #                 "experiment_path": out_dir,
# #                 "filesystem": _first_line(
# #                     _command_result(cfg, host, f"df -PT {shlex.quote(out_dir)} | tail -n 1")
# #                 ),
# #             },
# #             "security": {
# #                 "ufw": _first_line(_command_result(cfg, host, "ufw status", sudo=True)),
# #                 "selinux": _first_line(_command_result(cfg, host, "getenforce")),
# #                 "apparmor": _first_line(_command_result(cfg, host, "aa-status --enabled")),
# #             },
# #             "software": {
# #                 "python": _first_line(_command_result(cfg, host, "python3 --version")),
# #                 "iperf3": _first_line(_command_result(cfg, host, "iperf3 --version")),
# #                 "rsync": _first_line(_command_result(cfg, host, "rsync --version")),
# #                 "ethtool": _first_line(_command_result(cfg, host, "ethtool --version")),
# #             },
# #             "topology": {
# #                 "addresses": _command_result(cfg, host, "ip -br address"),
# #                 "routes": _command_result(cfg, host, "ip route"),
# #             },
# #         }

# #         _write_remote_json(cfg, host, f"{out_dir}/summary.json", summary, check=check)
# #         logging.debug("SUMMARY: Recorded compact report on %s", str(host).upper())


# def _summary_report(
#     cfg: Config,
#     hosts: Sequence[str],
#     out_dir: str,
#     check: bool = True,
#     *,
#     active_app: Optional[str] = None,
#     active_parallel: Optional[int] = None,
#     active_duration_s: Optional[int] = None,
#     active_transfer_size_gb: Optional[int] = None,
#     active_block_mb: Optional[int] = None,
#     active_numa_mode: Optional[str] = None,
#     active_splice: Optional[int] = None,
# ) -> None:
#     """Create one compact summary for each host."""
#     created_at = datetime.now().astimezone().isoformat()
#     experiment_id = PurePosixPath(out_dir).name or "unknown"

#     for host in hosts:
#         sysctls = {
#             key: _command_result(
#                 cfg,
#                 host,
#                 f"sysctl -n {shlex.quote(key)}",
#             )
#             for key in _SYSCTL_KEYS
#         }

#         devices = _get_report_devices(
#             cfg,
#             host,
#             check=check,
#         )

#         interfaces: List[Dict[str, Any]] = []
#         numa_nodes: Dict[str, Any] = {}

#         for dev in devices:
#             qdev = shlex.quote(dev)

#             ip_details = _command_result(
#                 cfg,
#                 host,
#                 f"ip -d link show dev {qdev}",
#             )

#             link = _parse_ip_link_sizes(
#                 _output(ip_details)
#             )

#             ring_result = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -g {qdev}",
#                 sudo=True,
#             )

#             ring = _parse_ethtool_ring(
#                 _output(ring_result)
#             )

#             features_result = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -k {qdev}",
#                 sudo=True,
#             )

#             features = _parse_ethtool_features(
#                 _output(features_result)
#             )

#             channels_result = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -l {qdev}",
#                 sudo=True,
#             )

#             channels = _parse_ethtool_channels(
#                 _output(channels_result)
#             )

#             coalescing_result = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool -c {qdev}",
#                 sudo=True,
#             )

#             coalescing = _parse_key_value_lines(
#                 _output(coalescing_result)
#             )

#             private_flags_result = _command_result(
#                 cfg,
#                 host,
#                 f"ethtool --show-priv-flags {qdev}",
#                 sudo=True,
#             )

#             private_flags = _parse_private_flags(
#                 _output(private_flags_result)
#             )

#             qdisc = _first_line(
#                 _command_result(
#                     cfg,
#                     host,
#                     f"tc qdisc show dev {qdev}",
#                     sudo=True,
#                 ),
#                 default="unknown",
#             )

#             numa_node_result = _command_result(
#                 cfg,
#                 host,
#                 f"cat /sys/class/net/{qdev}/device/numa_node",
#             )

#             numa_node = _safe_int(
#                 _first_line(
#                     numa_node_result,
#                     default="",
#                 )
#             )

#             numa_nodes[dev] = numa_node

#             interfaces.append(
#                 {
#                     "name": dev,

#                     "mtu": link["mtu"],
#                     "tx_queue_length": link["tx_queue_length"],

#                     "gso_max_size": link["gso_max_size"],
#                     "gro_max_size": link["gro_max_size"],

#                     "gso_ipv4_max_size": link[
#                         "gso_ipv4_max_size"
#                     ],
#                     "gro_ipv4_max_size": link[
#                         "gro_ipv4_max_size"
#                     ],

#                     "rx_ring": ring["current"]["rx"],
#                     "tx_ring": ring["current"]["tx"],

#                     "rx_ring_max": ring["maximum"]["rx"],
#                     "tx_ring_max": ring["maximum"]["tx"],

#                     "combined_channels": channels[
#                         "current"
#                     ]["combined"],

#                     "combined_channels_max": channels[
#                         "maximum"
#                     ]["combined"],

#                     "tso": features.get(
#                         "tcp-segmentation-offload"
#                     ),

#                     "gso": features.get(
#                         "generic-segmentation-offload"
#                     ),

#                     "gro": features.get(
#                         "generic-receive-offload"
#                     ),

#                     "lro": features.get(
#                         "large-receive-offload"
#                     ),

#                     "rx_gro_hw": features.get(
#                         "rx-gro-hw"
#                     ),

#                     "rx_gro_list": features.get(
#                         "rx-gro-list"
#                     ),

#                     "rx_checksum": features.get(
#                         "rx-checksumming"
#                     ),

#                     "tx_checksum": features.get(
#                         "tx-checksumming"
#                     ),

#                     "scatter_gather": features.get(
#                         "scatter-gather"
#                     ),

#                     "ntuple": features.get(
#                         "ntuple-filters"
#                     ),

#                     "hw_tc_offload": features.get(
#                         "hw-tc-offload"
#                     ),

#                     "adaptive_rx": coalescing.get(
#                         "Adaptive RX"
#                     ),

#                     "adaptive_tx": coalescing.get(
#                         "Adaptive TX"
#                     ),

#                     "rx_usecs": coalescing.get(
#                         "rx-usecs"
#                     ),

#                     "rx_frames": coalescing.get(
#                         "rx-frames"
#                     ),

#                     "tx_usecs": coalescing.get(
#                         "tx-usecs"
#                     ),

#                     "tx_frames": coalescing.get(
#                         "tx-frames"
#                     ),

#                     "cqe_mode_rx": coalescing.get(
#                         "cqe-mode-rx"
#                     ),

#                     "cqe_mode_tx": coalescing.get(
#                         "cqe-mode-tx"
#                     ),

#                     "private_flags": {
#                         key: private_flags.get(key)
#                         for key in (
#                             "rx_cqe_moder",
#                             "tx_cqe_moder",
#                             "rx_cqe_compress",
#                             "rx_striding_rq",
#                             "xdp_tx_mpwqe",
#                             "skb_tx_mpwqe",
#                             "tx_port_ts",
#                         )
#                     },

#                     "qdisc": qdisc,
#                     "numa_node": numa_node,
#                 }
#             )

#         governor_result = _command_result(
#             cfg,
#             host,
#             (
#                 "cat "
#                 "/sys/devices/system/cpu/cpu0/cpufreq/"
#                 "scaling_governor"
#             ),
#         )

#         irqbalance_result = _command_result(
#             cfg,
#             host,
#             "systemctl is-active irqbalance",
#         )

#         summary = {
#             "experiment_id": experiment_id,
#             "created_at": created_at,
#             "host": host,

#             "workload": {
#                 "test": cfg.test,
#                 "configured_applications": list(cfg.app),
#                 "active_application": active_app,

#                 "run": cfg.run_num,

#                 "configured_duration_values_s": list(
#                     cfg.time_frames
#                 ),
#                 "active_duration_s": active_duration_s,

#                 "configured_transfer_sizes_gb": list(
#                     cfg.file_sizes
#                 ),
#                 "active_transfer_size_gb": (
#                     active_transfer_size_gb
#                 ),

#                 "configured_parallel_stream_values": list(
#                     cfg.parallels
#                 ),
#                 "active_parallel_streams": active_parallel,

#                 "configured_block_sizes_mb": list(
#                     cfg.blocks
#                 ),
#                 "active_block_mb": active_block_mb,

#                 "encryption": cfg.encrypt,

#                 "configured_splice_values": list(
#                     cfg.splice
#                 ),
#                 "active_splice": active_splice,
#             },

#             "experiment_settings": {
#                 "lease": cfg.lease,
#                 "is_test": cfg.is_test,

#                 "configured_numa_modes": list(
#                     cfg.numactl
#                 ),
#                 "active_numa_mode": active_numa_mode,

#                 "tcp_buffer_label": cfg.tcp_buffer,
#                 "ring_buffer_label": cfg.ring_buffer,
#             },

#             "system": {
#                 "hostname": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "hostname",
#                     )
#                 ),

#                 "kernel": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "uname -r",
#                     )
#                 ),

#                 "cpu_governor": _first_line(
#                     governor_result
#                 ),

#                 "irqbalance": _first_line(
#                     irqbalance_result
#                 ),
#             },

#             "network_stack": {
#                 "tcp_mtu_probing": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_mtu_probing",
#                 ),

#                 "optmem_max": _sysctl_value(
#                     sysctls,
#                     "net.core.optmem_max",
#                 ),

#                 "rmem_max": _sysctl_value(
#                     sysctls,
#                     "net.core.rmem_max",
#                 ),

#                 "wmem_max": _sysctl_value(
#                     sysctls,
#                     "net.core.wmem_max",
#                 ),

#                 "tcp_rmem": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_rmem",
#                 ),

#                 "tcp_wmem": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_wmem",
#                 ),

#                 "tcp_congestion_control": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_congestion_control",
#                 ),

#                 "default_qdisc": _sysctl_value(
#                     sysctls,
#                     "net.core.default_qdisc",
#                 ),

#                 "tcp_no_metrics_save": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_no_metrics_save",
#                 ),

#                 "tcp_notsent_lowat": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_notsent_lowat",
#                 ),

#                 "tcp_autocorking": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_autocorking",
#                 ),

#                 "tcp_limit_output_bytes": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_limit_output_bytes",
#                 ),

#                 "netdev_budget": _sysctl_value(
#                     sysctls,
#                     "net.core.netdev_budget",
#                 ),

#                 "netdev_budget_usecs": _sysctl_value(
#                     sysctls,
#                     "net.core.netdev_budget_usecs",
#                 ),

#                 "netdev_max_backlog": _sysctl_value(
#                     sysctls,
#                     "net.core.netdev_max_backlog",
#                 ),

#                 "tcp_ecn": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_ecn",
#                 ),

#                 "tcp_moderate_rcvbuf": _sysctl_value(
#                     sysctls,
#                     "net.ipv4.tcp_moderate_rcvbuf",
#                 ),
#             },

#             "interface": interfaces,

#             "cpu": {
#                 "online_cpus": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "cat /sys/devices/system/cpu/online",
#                     )
#                 ),

#                 "irqbalance_active": _first_line(
#                     irqbalance_result
#                 ),
#             },

#             "numa": {
#                 "configured_modes": list(
#                     cfg.numactl
#                 ),

#                 "active_mode": active_numa_mode,

#                 "interface_nodes": numa_nodes,

#                 "hardware": _command_result(
#                     cfg,
#                     host,
#                     "numactl --hardware",
#                 ),
#             },

#             "storage": {
#                 "experiment_path": out_dir,

#                 "filesystem": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         (
#                             f"df -PT {shlex.quote(out_dir)} "
#                             f"| tail -n 1"
#                         ),
#                     )
#                 ),
#             },

#             "security": {
#                 "ufw": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "ufw status",
#                         sudo=True,
#                     )
#                 ),

#                 "selinux": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "getenforce",
#                     )
#                 ),

#                 "apparmor": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "aa-status --enabled",
#                     )
#                 ),
#             },

#             "software": {
#                 "python": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "python3 --version",
#                     )
#                 ),

#                 "iperf3": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "iperf3 --version",
#                     )
#                 ),

#                 "rsync": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "rsync --version",
#                     )
#                 ),

#                 "ethtool": _first_line(
#                     _command_result(
#                         cfg,
#                         host,
#                         "ethtool --version",
#                     )
#                 ),
#             },

#             "topology": {
#                 "configured_interfaces": list(
#                     cfg.interfaces.get(host, ())
#                 ),

#                 "addresses": _command_result(
#                     cfg,
#                     host,
#                     "ip -br address",
#                 ),

#                 "routes": _command_result(
#                     cfg,
#                     host,
#                     "ip route",
#                 ),
#             },
#         }

#         _write_remote_json(
#             cfg,
#             host,
#             f"{out_dir}/summary.json",
#             summary,
#             check=check,
#         )

#         logging.debug(
#             "SUMMARY: Recorded compact report on %s",
#             str(host).upper(),
#         )

# def system_state_report(
#     cfg: Config,
#     out_dir: str,
#     check: bool = True,
#     *,
#     active_app: Optional[str] = None,
#     active_parallel: Optional[int] = None,
#     active_duration_s: Optional[int] = None,
#     active_transfer_size_gb: Optional[int] = None,
#     active_block_mb: Optional[int] = None,
#     active_numa_mode: Optional[str] = None,
#     active_splice: Optional[int] = None,
# ) -> None:
#     """
#     Record detailed and summarized system configuration for every host.

#     Output layout:

#         <out_dir>/system/<hostname>/
#             summary.json
#             sysctl.json
#             host.json
#             cpu_irq.json
#             storage.json
#             software.json
#             security.json
#             nic_<device>.json
#             rss_rps_xps_<device>.json
#     """
#     hosts = (
#         list(cfg.hosts.ap.values())
#         + list(cfg.hosts.ep.values())
#     )

#     # Preserve order while removing duplicate hosts.
#     hosts = list(dict.fromkeys(hosts))

#     for host in hosts:
#         # host_out_dir = _host_report_dir(
#         #     cfg,
#         #     host,
#         #     out_dir,
#         # )
#         host_out_dir = out_dir

#         one_host = [host]

#         _sysctl_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#         )

#         _host_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#         )

#         _nic_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#         )

#         _cpu_irq_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#         )

#         _rss_rps_xps_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#         )

#         _storage_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             path=out_dir,
#             check=check,
#         )

#         _software_security_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#         )

#         _summary_report(
#             cfg,
#             one_host,
#             host_out_dir,
#             check=check,
#             active_app=active_app,
#             active_parallel=active_parallel,
#             active_duration_s=active_duration_s,
#             active_transfer_size_gb=active_transfer_size_gb,
#             active_block_mb=active_block_mb,
#             active_numa_mode=active_numa_mode,
#             active_splice=active_splice,
#         )
        






# -------------------------------------------------------------------------------
# def _report_devices(cfg: Config, host: str) -> list[str]:
#     devices = [
#         str(dev).strip()
#         for dev in cfg.interfaces.get(host, ())
#         if str(dev).strip()
#     ]

#     # preserve order and remove duplicates
#     return list(dict.fromkeys(devices))
def _report_devices(
    cfg: Config,
    host: str,
    *,
    check: bool = True,
) -> list[str]:
    devices = [
        str(dev).strip()
        for dev in cfg.interfaces.get(host, ())
        if str(dev).strip()
    ]

    devices = list(dict.fromkeys(devices))

    if not devices:
        message = f"REPORT: No interfaces configured for {host.upper()}"
        if check:
            raise RuntimeError(message)
        logging.warning(message)
        return []

    existing: list[str] = []

    for dev in devices:
        cp = run_subprocess(
            host,
            None,
            f"test -d /sys/class/net/{shlex.quote(dev)}",
            localhost=cfg.localhost,
            check=False,
        )

        if cp.returncode == 0:
            existing.append(dev)
        else:
            message = (
                f"REPORT: Interface {dev!r} does not exist on {host.upper()}"
            )
            if check:
                raise RuntimeError(message)
            logging.warning(message)

    return existing

# System reports
def _sysctl_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    keys = (
        "net.ipv4.tcp_mtu_probing",
        "net.core.optmem_max",
        "net.ipv4.tcp_slow_start_after_idle",
        "net.core.rmem_max",
        "net.core.wmem_max",
        "net.core.rmem_default",
        "net.core.wmem_default",
        "net.ipv4.tcp_rmem",
        "net.ipv4.tcp_wmem",
        "net.ipv4.tcp_congestion_control",
        "net.core.default_qdisc",
        "net.ipv4.tcp_no_metrics_save",
        "net.ipv4.tcp_low_latency",
        "net.ipv4.tcp_notsent_lowat",
        "net.ipv4.tcp_autocorking",
        "net.ipv4.tcp_limit_output_bytes",
        "net.ipv4.tcp_pacing_ss_ratio",
        "net.ipv4.tcp_pacing_ca_ratio",
        "net.ipv4.tcp_adv_win_scale",
        "net.ipv4.tcp_app_win",
        "net.core.netdev_budget",
        "net.core.netdev_budget_usecs",
        "net.core.netdev_max_backlog",
        "net.core.dev_weight",
        "net.ipv4.tcp_window_scaling",
        "net.ipv4.tcp_sack",
        "net.ipv4.tcp_dsack",
        "net.ipv4.tcp_timestamps",
        "net.ipv4.tcp_ecn",
        "net.ipv4.tcp_moderate_rcvbuf",
    )
    for host in hosts:
        cp = run_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"sysctl {' '.join(keys)} > {shlex.quote(out_dir)}/sysctl.log",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"SYSCTL: Failed recording sysctl values on {str(host).upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("SYSCTL: Recorded sysctl values on %s", str(host).upper())


def _host_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"( "
        f"echo '### hostname'; hostname; "
        f"echo; echo '### uname'; uname -a; "
        f"echo; echo '### lscpu'; lscpu; "
        f"echo; echo '### ip link'; ip link; "
        f"echo; echo '### ip -br a'; ip -br a; "
        f"echo; echo '### ip route'; ip route; "
        f"echo; echo '### df -hT'; df -hT; "
        f"echo; echo '### lsblk'; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL; "
        f") > {shlex.quote(f'{out_dir}/host.log')}"
    )
    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(f"HOST: Failed on {str(host).upper()}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")


def _nic_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    for host in hosts:
        devices = _report_devices(cfg, host)
        dev_list = " ".join(shlex.quote(d) for d in devices)
        cp = run_subprocess(
            host, None, 
            f"mkdir -p {shlex.quote(out_dir)} && "
            #f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
            f"for dev in {dev_list}; do "
            f"( "
            f"echo '### ip -d link show dev' $dev; ip -d link show dev $dev; "
            f"echo; echo '### ethtool' $dev; sudo ethtool $dev; "
            f"echo; echo '### ethtool -i' $dev; sudo ethtool -i $dev; "
            f"echo; echo '### ethtool -a' $dev; sudo ethtool -a $dev; "
            f"echo; echo '### ethtool -g' $dev; sudo ethtool -g $dev; "
            f"echo; echo '### ethtool -k' $dev; sudo ethtool -k $dev; "
            f"echo; echo '### ethtool -c' $dev; sudo ethtool -c $dev; "
            f"echo; echo '### ethtool -S' $dev; sudo ethtool -S $dev; "
            f"echo; echo '### ethtool --show-fec' $dev; sudo ethtool --show-fec $dev; "
            f"echo; echo '### ethtool --show-priv-flags' $dev; sudo ethtool --show-priv-flags $dev; "
            f"echo; echo '### tx_queue_len' $dev; cat /sys/class/net/$dev/tx_queue_len; "
            f"echo; echo '### tc -s qdisc show dev' $dev; sudo tc -s qdisc show dev $dev; "
            f") > {shlex.quote(out_dir)}/nic_${{dev}}.log 2>&1; "
            f"done",
            localhost=cfg.localhost
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"NIC: Failed on {str(host).upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("NIC: Recorded NIC reports on %s", str(host).upper())


def _cpu_irq_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    for host in hosts:
        devices = _report_devices(cfg, host)
        dev_list = " ".join(shlex.quote(d) for d in devices)
        cp = run_subprocess(
            host, None, 
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"( "
            f"echo '### irqbalance service status'; "
            f"echo; echo '### irqbalance status'; systemctl status irqbalance --no-pager || true; "
            f"echo '### CPU governor'; "
            f"for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo \"$f: $(cat $f 2>/dev/null)\"; done; "
            f"echo; echo '### CPU frequencies'; "
            f"for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do echo \"$f: $(cat $f 2>/dev/null)\"; done; "
            f"echo; echo '### NIC IRQs and affinity'; "
            #f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
            f"for dev in {dev_list}; do "
            f"echo; echo '## device:' $dev; "
            #f"grep -i $dev /proc/interrupts || true; "
            f"grep -i -- \"$dev\" /proc/interrupts || true; "
            #f"for irq in $(grep -i $dev /proc/interrupts | awk -F: '{{print $1}}' | tr -d ' '); do "
            f"for irq in $(grep -i -- \"$dev\" /proc/interrupts "
            f"| awk -F: '{{gsub(/[[:space:]]/, \"\", $1); print $1}}'); do "
            f"echo IRQ $irq; "
            f"echo -n 'smp_affinity: '; cat /proc/irq/$irq/smp_affinity 2>/dev/null || true; "
            f"echo -n 'smp_affinity_list: '; cat /proc/irq/$irq/smp_affinity_list 2>/dev/null || true; "
            f"done; "
            f"done "
            f") > {shlex.quote(out_dir)}/cpu_irq.log 2>&1; ",
            localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"CPU/IRQ: Failed on {str(host).upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("CPU/IRQ: Recorded CPU and IRQ report on %s", str(host).upper())


def _rss_rps_xps_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    for host in hosts:
        devices = _report_devices(cfg, host)
        dev_list = " ".join(shlex.quote(d) for d in devices)
        cp = run_subprocess(
            host, None, 
            f"mkdir -p {shlex.quote(out_dir)} && "
            #f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
            f"for dev in {dev_list}; do "
            f"( "
            f"echo '### ethtool -l channels' $dev; sudo ethtool -l $dev || true; "
            f"echo; echo '### ethtool -x RSS indirection' $dev; sudo ethtool -x $dev || true; "
            f"echo; echo '### RPS/XPS sysfs' $dev; "
            f"find /sys/class/net/$dev/queues -type f "
            f"\\( -name rps_cpus -o -name rps_flow_cnt -o -name xps_cpus \\) "
            f"-exec sh -c 'for f do echo \"$f: $(cat \"$f\")\"; done' sh {{}} + || true; "
            f"echo; echo '### NUMA node' $dev; "
            f"cat /sys/class/net/$dev/device/numa_node || true; "
            f"echo; echo '### local CPU list' $dev; "
            f"cat /sys/class/net/$dev/device/local_cpulist || true; "
            f") > {shlex.quote(out_dir)}/rss_rps_xps_${{dev}}.log 2>&1; "
            f"done",
            localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"RSS/RPS/XPS: Failed on {str(host).upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("RSS/RPS/XPS: Recorded reports on %s", str(host).upper())


def _storage_report(cfg: Config, hosts: Sequence[str], out_dir: str, path: str = "/", check: bool = True) -> None:
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"( "
        f"echo '### df -hT'; df -hT; "
        f"echo; echo '### mount'; mount; "
        f"echo; echo '### lsblk'; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,ROTA; "
        f"echo; echo '### block devices scheduler'; "
        f"for f in /sys/block/*/queue/scheduler; do echo \"$f: $(cat $f 2>/dev/null)\"; done; "
        f"echo; echo '### filesystem for path {path}'; df -hT {shlex.quote(path)}; "
        f") > {shlex.quote(out_dir)}/storage.log 2>&1"
    )
    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(f"STORAGE: Failed on {str(host).upper()}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")


def _software_report(
    cfg: Config,
    hosts: Sequence[str],
    out_dir: str,
    check: bool = True,
) -> None:
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"( "
        f"echo '### python'; python3 --version || true; "
        f"echo; echo '### iperf3'; iperf3 --version || true; "
        f"echo; echo '### rsync'; rsync --version | head -n 3 || true; "
        f"echo; echo '### ethtool'; ethtool --version || true; "
        f"echo; echo '### iproute2'; ip -Version || true; "
        f"echo; echo '### docker'; docker --version || true; "
        f"echo; echo '### globus'; globus --version || true; "
        f") > {shlex.quote(out_dir)}/software.log 2>&1"
    )

    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(f"SOFTWARE: Failed on {host.upper()}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

def _security_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"( "
        f"echo '### SELinux'; getenforce; "
        f"echo; echo '### AppArmor'; aa-status; "
        f"echo; echo '### UFW'; sudo ufw status verbose; "
        f"echo; echo '### firewalld'; systemctl is-active firewalld; "
        f"echo; echo '### iptables'; sudo iptables-save; "
        f") > {shlex.quote(out_dir)}/security.log 2>&1"
    )

    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(f"SECURITY: Failed on {host.upper()}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
            

def system_state_report(cfg: Config, out_dir: str, check: bool = True) -> None:
    #hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    hosts = list(dict.fromkeys(hosts))
    _sysctl_report(cfg, hosts, out_dir, check=check)
    _host_report(cfg, hosts, out_dir, check=check)
    _nic_report(cfg, hosts, out_dir, check=check)
    _cpu_irq_report(cfg, hosts, out_dir, check=check)
    _rss_rps_xps_report(cfg, hosts, out_dir, check=check)
    _storage_report(cfg, hosts, out_dir, path=out_dir, check=check)
    _software_report(cfg, hosts, out_dir, check=check)
    _security_report(cfg, hosts, out_dir, check=check)