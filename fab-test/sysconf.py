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
    quoted_keys = " ".join(shlex.quote(key) for key in keys)
    for host in hosts:
        cp = run_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} && "
            #f"sysctl {' '.join(keys)} > {shlex.quote(out_dir)}/sysctl.log",
            f": > {shlex.quote(out_dir)}/sysctl.log && "
            f"for key in {quoted_keys}; do "
            f"sysctl \"$key\" >> {shlex.quote(out_dir)}/sysctl.log 2>&1 "
            f"|| echo \"$key = unavailable\" "
            f">> {shlex.quote(out_dir)}/sysctl.log; "
            f"done ",
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
        f") > {shlex.quote(f'{out_dir}/host.log')} 2>&1 "
    )
    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(f"HOST: Failed on {str(host).upper()}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")


def _nic_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    for host in hosts:
        devices = _report_devices(cfg, host, check=check)
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
        devices = _report_devices(cfg, host, check=check)
        dev_list = " ".join(shlex.quote(d) for d in devices)
        cp = run_subprocess(
            host, None, 
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"( "
            f"echo '### irqbalance service status'; "
            f"echo; echo '## irqbalance is-active'; systemctl is-active irqbalance || true; "
            f"echo; echo '## irqbalance is-enabled'; systemctl is-enabled irqbalance || true; "
            f"echo; echo '## irqbalance status'; systemctl status irqbalance --no-pager || true; "
            f"echo; echo '## irqbalance pgrep'; pgrep -a irqbalance || true;  "
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


# def _rss_rps_xps_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
#     for host in hosts:
#         devices = _report_devices(cfg, host, check=check)
#         dev_list = " ".join(shlex.quote(d) for d in devices)
#         cp = run_subprocess(
#             host, None, 
#             f"mkdir -p {shlex.quote(out_dir)} && "
#             #f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
#             f"for dev in {dev_list}; do "
#             f"( "
#             f"echo '### ethtool -l channels' $dev; sudo ethtool -l $dev || true; "
#             f"echo; echo '### ethtool -x RSS indirection' $dev; sudo ethtool -x $dev || true; "
#             f"echo; echo '### RPS/XPS sysfs' $dev; "
#             f"find /sys/class/net/$dev/queues -type f "
#             f"\\( -name rps_cpus -o -name rps_flow_cnt -o -name xps_cpus \\) "
#             f"-exec sh -c 'for f do echo \"$f: $(cat \"$f\")\"; done' sh {{}} + || true; "
#             f"echo; echo '### NUMA node' $dev; "
#             f"echo; echo '## NUMA hardware;' numactl --hardware || true; "
#             f"echo; echo '## NUMA system;' cat /sys/devices/system/cpu/online || true; "
#             f"cat /sys/class/net/$dev/device/numa_node || true; "
#             f"echo; echo '### local CPU list' $dev; "
#             f"cat /sys/class/net/$dev/device/local_cpulist || true; "
#             f") > {shlex.quote(out_dir)}/rss_rps_xps_${{dev}}.log 2>&1; "
#             f"done",
#             localhost=cfg.localhost)
#         if check and cp.returncode != 0:
#             raise RuntimeError(
#                 f"RSS/RPS/XPS: Failed on {str(host).upper()}\n"
#                 f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
#             )
#         logging.debug("RSS/RPS/XPS: Recorded reports on %s", str(host).upper())
def _rss_rps_xps_report(
    cfg: Config,
    hosts: Sequence[str],
    out_dir: str,
    check: bool = True,
) -> None:
    for host in hosts:
        devices = _report_devices(cfg, host, check=check)
        dev_list = " ".join(shlex.quote(d) for d in devices)

        cp = run_subprocess(
            host,
            None,
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"( "
            f"echo '### NUMA hardware'; "
            f"numactl --hardware || true; "
            f"echo; echo '### Online CPUs'; "
            f"cat /sys/devices/system/cpu/online || true; "
            f") > {shlex.quote(out_dir)}/numa_cpu.log 2>&1; "
            f"for dev in {dev_list}; do "
            f"( "
            f"echo '### ethtool -l channels' $dev; "
            f"sudo ethtool -l \"$dev\" || true; "
            f"echo; echo '### ethtool -x RSS indirection' $dev; "
            f"sudo ethtool -x \"$dev\" || true; "
            f"echo; echo '### RPS/XPS sysfs' $dev; "
            f"find \"/sys/class/net/$dev/queues\" -type f "
            f"\\( -name rps_cpus -o -name rps_flow_cnt "
            f"-o -name xps_cpus \\) "
            f"-exec sh -c "
            f"'for f do echo \"$f: $(cat \"$f\")\"; done' sh {{}} + "
            f"|| true; "
            f"echo; echo '### NUMA node' $dev; "
            f"cat \"/sys/class/net/$dev/device/numa_node\" || true; "
            f"echo; echo '### local CPU list' $dev; "
            f"cat \"/sys/class/net/$dev/device/local_cpulist\" || true; "
            f") > {shlex.quote(out_dir)}/rss_rps_xps_${{dev}}.log 2>&1; "
            f"done",
            localhost=cfg.localhost,
            check=False,
        )

        if check and cp.returncode != 0:
            raise RuntimeError(f"RSS/RPS/XPS: Failed on {str(host).upper()}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

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
