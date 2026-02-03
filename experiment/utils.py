from __future__ import annotations

import logging
import os
import re
import uuid
import shlex
import subprocess
import time
#from pathlib import Path
from typing import Iterable, Optional, Sequence, List, Dict
#from os.path import expanduser

from config import Config


# run subprocess via ssh python agent
def _ssh_base(host: str) -> list[str]:
    return ["ssh", host, "bash", "-lc"]


def _env_wrap(cmd: str, env: Optional[str]) -> str:
    prefix = "set -euo pipefail >/dev/null 2>&1; "
    #prefix = "set -euo pipefail; "
    if env:
        act = shlex.quote(env)
        cmd = (f"{prefix} source {act}; {cmd}")
    else:
        cmd = (f"{prefix} {cmd}")
    return cmd

def _build_argv(host: str, env: Optional[str], cmd: str, localhost: str) -> list[str]:
    wrapped = _env_wrap(cmd, env)
    if host == localhost:
        return ["bash", "-lc", wrapped]
    return _ssh_base(host) + [wrapped]


def run_subprocess(host: str, env: Optional[str], cmd: str, *, localhost: str, check: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
    argv = _build_argv(host, env, cmd, localhost=localhost)
    logging.debug("RUN: %s", argv)
    cp = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"Command failed on host={host}\n"
            f"ARGV: {argv}\n"
            f"RC={cp.returncode}\n"
            f"STDOUT:\n{cp.stdout}\n"
            f"STDERR:\n{cp.stderr}"
        )
    return cp


def popen_subprocess(host: str, env: Optional[str], cmd: str, *, localhost: str) -> subprocess.Popen[str]:
    argv = _build_argv(host, env, cmd,  localhost=localhost)
    logging.debug("POPEN: %s", argv)
    return subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# TODO: add run subprocess via globus python agent



# Helpers:
_UUID_CANDIDATE_RE = re.compile(r"[0-9a-fA-F-]{32,36}")
def parse_uuid(output: str) -> str:
    for m in _UUID_CANDIDATE_RE.finditer(output):
        try:
            return str(uuid.UUID(m.group(0)))
        except ValueError:
            pass
    raise RuntimeError(f"Could not find UUID in output:\n{output}")


def parse_contact_port(output: str) -> int:
    m = re.search(
        r"Your contact string is:\s*(?P<host>[^:\s]+)\s*:\s*(?P<port>\d+)",
        output, flags=re.IGNORECASE,
    )
    if not m:
        raise RuntimeError(f"Could not find contact string / port in output:\n{output}")
    return int(m.group("port"))



# Steps:
def restart_gridftp(cfg: Config, gateway: str) -> None:
    cp = run_subprocess(
        gateway,
        None,
        "sudo systemctl restart gridftp-server-restarter.service",
        localhost=cfg.localhost,
    )
    logging.info("%s: Restarting gridftp %s", gateway.upper(), cp.stdout.strip())


def get_stream_gateway_id(cfg: Config, gateway: str) -> str:
    cp = run_subprocess(gateway, None, "gcs stream-gateway list", localhost=cfg.localhost)
    return parse_uuid(cp.stdout + "\n" + cp.stderr)


def start_tunnel(cfg: Config, initiator_id: str, listener_id: str) -> str:
    cp = run_subprocess(
        cfg.localhost,
        cfg.local_env,
        "globus streams tunnel create "
        "--lifetime-minutes 360 -v "
        f"{shlex.quote(initiator_id)} {shlex.quote(listener_id)}",
        localhost=cfg.localhost,
    )
    return parse_uuid(cp.stdout + "\n" + cp.stderr)


def start_statkit(cfg: Config, parallel: int, run_idx: int) -> None:
    hosts = (cfg.initiator_ap, cfg.listener_ap,
            cfg.initiator_host, cfg.listener_host)
    
    for host in hosts:
        cp = popen_subprocess(
            host,
            cfg.remote_env,
            "python "
            "~/statkit/monitor/launcher.py "
            f"--out ~/statkit/reports/{parallel}/{run_idx} --duration {cfg.time_frames + 120} &"
            "echo $! > ~/statkit/launcher.pid",
            localhost=cfg.localhost,
        )
        logging.info("STATKIT: started on %s remote_pid=%s", host, cp.pid)


def init_listener_env(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.listener_host,
        cfg.remote_env,
        "globus-streams environment initialize "
        f"--listener-contact-string {cfg.listener_ip}:{cfg.base_port} "
        f"{shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
    )
    logging.info("LISTENER: environment initialize:\n%s", cp.stdout.strip())


def init_initiator_env(cfg: Config, tunnel_id: str) -> int:
    cp = run_subprocess(
        cfg.initiator_host,
        cfg.remote_env,
        f"globus-streams environment initialize {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
    )
    combined = cp.stdout + "\n" + cp.stderr
    logging.info("INITIATOR: environment initialize:\n%s", cp.stdout.strip())
    return parse_contact_port(combined)


def stop_statkit(cfg: Config) -> None:
    hosts = (cfg.initiator_ap, cfg.listener_ap,
            cfg.initiator_host, cfg.listener_host)
    
    for host in hosts:
        cp = run_subprocess(
            host,
            None,
            #"pid=$(cat /home/cc/statkit/launcher.pid) "
            #"echo pid=$pid "
            #"pkill -TERM -P $pid "
            #"sleep 2 "
            #"kill -KILL -P $pid ",
            "pkill -TERM -f 'monitor/launcher\.py' ",
            localhost=cfg.localhost,
        )
        logging.info("STATKIT: stopped on %s with return code=%s", host, cp.returncode)#cp.stdout.strip())


def stop_tunnel(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.localhost,
        cfg.local_env,
        f"globus streams tunnel stop {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,  # stopping is best-effort in finally blocks
    )
    out = (cp.stdout + "\n" + cp.stderr).strip()
    if out:
        logging.info("LOCAL: Stop tunnel output:\n%s", out)






















# def run_subprocess(cmd: Sequence[str], *, text: bool = True, shell: bool = False) -> subprocess.Popen:
#     """
#     Wrapper to run the list commands (shell=False)
#     - If shell=True is passed, cmd should be a list like ["bash","-lc", "..."] or a single string
#     """
#     try:
#         if shell:
#             # if caller passed a list, join for bash -lc, otherwise use string directly to conflict
#             if isinstance(cmd, (list, tuple)):
#                 if len(cmd) == 1:
#                     pop = subprocess.Popen(cmd[0], shell=True, text=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#                 else:
#                     pop = subprocess.Popen(" ".join(cmd), shell=True, text=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#             else:
#                 pop = subprocess.Popen(cmd, shell=True, text=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # ignore[arg-type]
#         else:
#             pop = subprocess.Popen(list(cmd), text=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#         return pop
#     except Exception:
#         logging.error("SUBPROCESS: Failed to start command: %s", cmd, exc_info=True)
#         return None  # type: ignore[return-value]


# def _ssh_base(host: str) -> list[str]:
#     return ["ssh", host, "bash", "-lc"]


# def ssh_run(host: str, remote_cmd: str, *, check: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
#     cmd = _ssh_base(host) + [shlex.quote(remote_cmd)]
#     logging.debug("SSH: %s", cmd)
#     cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
#     if check and cp.returncode != 0:
#         raise RuntimeError(f"SSH failed on {host}: {remote_cmd}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
#     return cp


# def ssh_popen(host: str, remote_cmd: str) -> subprocess.Popen:
#     cmd = _ssh_base(host) + [remote_cmd]
#     return subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


# def local_run(cmd: str, *, check: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
#     cp = subprocess.run(["bash", "-lc", cmd], text=True, capture_output=True, timeout=timeout)
#     if check and cp.returncode != 0:
#         raise RuntimeError(f"LOCAL failed: {cmd}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
#     return cp


# def get_username(host: str) -> str:
#     try:
#         cp = ssh_run(host, "echo $USER", check=True)
#         return cp.stdout.strip()
#     except Exception:
#         # fall back to local username
#         return Config._USERNAME


# def mkdir(host: str, output_dir: Path | str) -> None:
#     # Create directory on remote host
#     out = str(output_dir)
#     ssh_run(host, f"mkdir -p {shlex.quote(out)}", check=True)


# def sys_reload(host: str) -> None:
#     """
#     Apply sysctl changes
#     """
#     try:
#         ssh_run(host, "sudo sysctl -p >/dev/null 2>&1 || true", check=False)
#     except Exception:
#         logging.warning("SYSCTL: sys_reload failed on %s", host, exc_info=True)


# def scp_sys_script() -> None:
#     """
#     Copy sys_monitor.py to each remote host home directory
#     """
#     local_sys_script = Config._LOCAL_STATKIT
#     if not os.path.exists(local_sys_script):
#         raise FileNotFoundError(f"Local sys_monitor.py not found at: {local_sys_script}")

#     for host in Config._HOSTS.values():
#         try:
#             remote_user = get_username(host).strip()
#             dest = f"{remote_user}@{host}:/home/{remote_user}/"
#             cp = subprocess.run(["scp", local_sys_script, dest], text=True, capture_output=True)
#             if cp.returncode != 0:
#                 raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())
#             logging.info("STATS: Copied sys_monitor.py to %s", host)
#         except Exception as e:
#             logging.error("STATS: Failed copying sys_monitor.py to %s: %s", host, e, exc_info=True)
#             raise


# def run_stats(host: str, duration: int, run: int, log_file: str, src_dir: str) -> subprocess.Popen:
#     """
#     Start system monitor on a remote host and return a Popen handle
#     """
#     sys_script = Config._RMT_SYS_SCRIPT  # remote path
#     # command tries preferred python, falls back
#     cmd = (
#         f"timeout {int(duration) + 4} "
#         f"( {Config._REMOTE_PYTHON} {shlex.quote(sys_script)} --log_file {shlex.quote(log_file)} "
#         f"|| python3 {shlex.quote(sys_script)} --log_file {shlex.quote(log_file)} )"
#     )
#     proc = ssh_popen(host, cmd)
#     logging.info("STATS: Started System Monitor on %s -> %s", host, log_file)
#     return proc


# def run_dumpcap(host: str, duration: int, run: int, output_dir: str | Path) -> subprocess.Popen:
#     """
#     Capture packets on a host using dumpcap 
#     """
#     output_dir = str(output_dir)
#     pcap = os.path.join(output_dir, f"stream_R{run}.pcapng")
#     cmd = (
#         f"timeout {int(duration) + 4} "
#         f"dumpcap -i {shlex.quote(Config._DEV)} -s {int(Config._DUMPCAP_SNAPLEN)} "
#         #f"dumpcap -i eno1np0 -s 96 -f "portrange 5100-5110" -w {pcap}""
#         f"-f {shlex.quote(Config._DUMPCAP_FILTER)} -w {shlex.quote(pcap)}"
#     )
#     proc = ssh_popen(host, cmd)
#     logging.info("DUMP: Started dumpcap on %s -> %s", host, pcap)
#     return proc


# # Traffic control (fq maxrate)
# _MAXRATE_MAP = {
#     1: {"replace": "10gbit", "show": "10Gbit"},
#     2: {"replace": "5gbit", "show": "5Gbit"},
#     3: {"replace": "3.334gbit", "show": "3334Mbit"},
#     5: {"replace": "2gbit", "show": "2Gbit"},
#     10: {"replace": "1gbit", "show": "1Gbit"},
# }


# def traffic_check(name: str, host: str, parallel: int) -> bool:
#     dev = Config._DEV
#     values = _MAXRATE_MAP.get(parallel)
#     if values is None:
#         raise RuntimeError(f"TRAFFIC: No maxrate specified for parallel={parallel}")
#     display = values["show"]
#     cp = ssh_run(host, f"tc -s qdisc show dev {shlex.quote(dev)} root", check=False)
#     if cp.returncode != 0:
#         logging.warning("TRAFFIC: tc check failed on %s: %s", host, cp.stderr.strip())
#         return False
#     ok = display in cp.stdout
#     if ok:
#         logging.info("TRAFFIC: MaxRate OK on %s (%s) for P=%d", name.upper(), host, parallel)
#     else:
#         logging.info("TRAFFIC: MaxRate NOT OK on %s (%s) for P=%d (expected %s)", name.upper(), host, parallel, display)
#     return ok


# def sys_config_log(name: str, host: str, output_dir: str | Path) -> None:
#     """
#     Write a snapshot of key sysctl/qdisc settings into sys_conf.log on the remote host
#     """
#     output_dir = str(output_dir)
#     log_file = os.path.join(output_dir, "sys_conf.log")
#     dev = Config._DEV
#     cmd = (
#         f'(date ; '
#         f'sysctl net.core.rmem_max ; sysctl net.core.wmem_max ; '
#         f'sysctl net.ipv4.tcp_rmem ; sysctl net.ipv4.tcp_wmem ; '
#         f'sysctl net.ipv4.tcp_congestion_control ; '
#         f'sysctl net.ipv4.tcp_no_metrics_save ; '
#         f'sysctl net.core.default_qdisc ; '
#         f'tc -s qdisc show dev {shlex.quote(dev)} ; '
#         f'ethtool -g {shlex.quote(dev)} ; ) > {shlex.quote(log_file)}'
#     )
#     ssh_run(host, cmd, check=False)
#     logging.debug("SYSLOG: Wrote %s on %s", log_file, host)


# def traffic_ctl(name: str, host: str, parallel: int, output_dir: str | Path) -> None:
#     """
#     Enforce fq maxrate depending on parallelism
#       - logs a sys_conf snapshot after applying
#     """
#     dev = Config._DEV
#     values = _MAXRATE_MAP.get(parallel)
#     if values is None:
#         raise RuntimeError(f"TRAFFIC: No maxrate specified for parallel={parallel}")

#     maxrate, display = values["replace"], values["show"]
#     if parallel == Config._PARALLELS[0] or not traffic_check(name, host, parallel):
#         cmd = (
#             f"sudo tc qdisc replace dev {shlex.quote(dev)} root fq "
#             f"maxrate {maxrate} horizon 100ms && sleep 1 && "
#             f"tc -s qdisc show dev {shlex.quote(dev)} root"
#         )
#         cp = ssh_run(host, cmd, check=True)
#         if display not in cp.stdout:
#             raise RuntimeError(
#                 f"TRAFFIC: MaxRate did not change to {display} on {name.upper()}.\n{cp.stdout}\n{cp.stderr}"
#             )
#         logging.info("TRAFFIC: Set MaxRate=%s on %s (%s) for P=%d", display, name.upper(), host, parallel)

#     sys_config_log(name, host, output_dir)




# python3 - <<'PY'
# import csv, datetime, zoneinfo
# tz = zoneinfo.ZoneInfo("America/Chicago")

# inp = "cpu.csv"
# out = "cpu_with_local_time.csv"

# with open(inp, newline="") as f_in, open(out, "w", newline="") as f_out:
#     r = csv.DictReader(f_in)
#     fieldnames = ["ts_wall_local"] + r.fieldnames
#     w = csv.DictWriter(f_out, fieldnames=fieldnames)
#     w.writeheader()
#     for row in r:
#         ts = float(row["ts_wall_s"])
#         dt = datetime.datetime.fromtimestamp(ts, tz)
#         row["ts_wall_local"] = dt.strftime("%Y-%m-%d %H:%M:%S.%f %Z")
#         w.writerow(row)

# print(f"Wrote {out}")
# PY
# Wrote cpu_with_local_time.csv
