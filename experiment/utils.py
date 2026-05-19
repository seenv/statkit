from __future__ import annotations

import logging
import os
import re
import uuid
import shlex
import subprocess
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, List, Dict, Tuple
#from os.path import expanduser
from pathlib import PurePosixPath

from config import Config, Role


# def make_session_id() -> str:
#     ts = time.strftime("%Y%m%d-%H%M%S")
#     short = uuid.uuid4().hex[:8]
#     return f"{ts}-{short}"

def setup_logging(verbose: bool, log_path: str = "/tmp/strefer.log") -> None:
    root = logging.getLogger()
    # removing existing handlers to avoid duplicate logs when re running
    # root.handlers.clear()
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        #"%(asctime)s %(levelname)s %(message)s",
        "%(asctime)s %(message)s ",
        datefmt="%Y-%m-%d %H:%M:%S"
        )
    # always INFO in the console
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    root.addHandler(sh)
    # DEBUG in file when verbose
    #if verbose:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)


# run subprocess via ssh
def _ssh_base(host: str) -> list[str]:
    return ["ssh", host, "bash", "-lc"]


def is_ssh_failure(cp: subprocess.CompletedProcess[str]) -> bool:
    if cp.returncode != 255:
        return False
    stderr = cp.stderr or ""
    needles = [
        "Connection closed by remote host",
        "Connection timed out",
        "Network is unreachable",
        "stdio forwarding failed",
    ]
    return any(x in stderr for x in needles)


def _env_wrap(cmd: str, env: Optional[str], discard: bool = False) -> str:
    prefix =  "set -euo pipefail >/dev/null 2>&1; set -x; " if not discard else "set -euo pipefail; set -x; "
    if env:
        act = shlex.quote(env)
        cmd = (f"{prefix} . {act} > /dev/null 2>&1; {cmd}")
    else:
        cmd = (f"{prefix} {cmd}")
    return cmd


def _build_argv(host: str, env: Optional[str], cmd: str, localhost: str) -> list[str]:
    wrapped = _env_wrap(cmd, env)
    if host == localhost:
        return ["bash", "-lc", wrapped]
    return _ssh_base(host) + [wrapped]


def run_subprocess(host: str, env: Optional[str], cmd: str, *, 
                   localhost: str, check: bool = True, timeout: Optional[int] = None,
                    retries: int = 100, sleep: int = 5) -> subprocess.CompletedProcess[str]:

    argv = _build_argv(host, env, cmd, localhost=localhost)
    total_attempts = retries + 1
    last_err: Optional[RuntimeError] = None

    logging.debug("RUN: %s %s", host.upper(), cmd)

    for attempt in range(1, total_attempts + 1):
        cp = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)

        if cp.returncode == 0:
            return cp

        err = RuntimeError(
            f"Command failed on host={host}\n"
            f"ARGV: {argv}\n"
            f"RC={cp.returncode}\n"
            f"STDOUT:\n{cp.stdout}\n"
            f"STDERR:\n{cp.stderr}"
        )
        last_err = err
        if not check:
            return cp

        retryable = is_ssh_failure(cp)
        if retryable and attempt < total_attempts:
            logging.warning(
                "SSH command failed on %s (attempt %d/%d), retrying in %.1fs",
                host,
                attempt,
                total_attempts,
                sleep,
            )
            time.sleep(sleep)
            continue
        raise err

    assert last_err is not None
    raise last_err


def popen_subprocess(host: str, env: Optional[str], cmd: str, *, localhost: str) -> subprocess.Popen[str]:
    argv = _build_argv(host, env, cmd,  localhost=localhost)
    logging.debug("POPEN: %s", argv)
    return subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# TODO: add run subprocess via globus python agent



# Helpers:
#def make_temp_file(cfg: Config, host: str, size: int, file_path: str) -> None:
def make_temp_file(cfg: Config, size: int, file_path: str) -> None:
    for host in cfg.hosts.ep.values():
        cp = run_subprocess(
            host,
            None,
            f"mkdir -p /tmp/temp_file/ && "
            f"fallocate -l {size}G {shlex.quote(file_path)} && "
            f"test -f {shlex.quote(file_path)} && "
            #f"ls -lh {shlex.quote(file_path)} && "
            f"du -h {shlex.quote(file_path)}",
            localhost=cfg.localhost,
        )
        #logging.info(f"{cfg.app.upper()}: Source file on %s: %s", host.upper(), cp.stdout.strip())
        logging.info("FILE: Source file on %s: %s", host.upper(), cp.stdout.strip())


def prepare_remote_dest(cfg: Config, host: str, dest_path: str) -> None:
    dest_dir = str(PurePosixPath(dest_path).parent)

    run_subprocess(
        host, None,
        f"mkdir -p {shlex.quote(dest_dir)} ", #&& ",
        localhost=cfg.localhost,
    )
    logging.info("RSYNC: Prepared destination on %s: temp_file=%s", host.upper(), dest_path)

_UUID_CANDIDATE = re.compile(r"[0-9a-fA-F-]{32,36}")
def _parse_uid(output: str) -> str:
    for m in _UUID_CANDIDATE.finditer(output):
        try:
            return str(uuid.UUID(m.group(0)))
        except ValueError:
            pass
    raise RuntimeError(f"Could not find UUID in output:\n{output}")

#def _parse_gateway_id_by_name(output: str, name: str, *, exact: bool = False) -> str:
def _parse_gateway_uid(output: str, name: str, *, exact: bool = False) -> str:
    want = name.strip()
    for line in output.splitlines():
        if "|" not in line:
            continue
        if line.strip().startswith("---"):
            continue

        # first column is Display Name
        display = line.split("|", 1)[0].strip()

        match = (display == want) if exact else (want in display)
        if not match:
            continue

        m = _UUID_CANDIDATE.search(line)
        if not m:
            raise RuntimeError(f"Matched name but no UUID found on line:\n{line}")
        return str(uuid.UUID(m.group(0)))

    raise RuntimeError(f"No gateway row matched name={name!r}.\nOutput:\n{output}")


def _parse_contact_port(output: str) -> int:
    m = re.search(
        r"Your contact string is:\s*(?P<host>[^:\s]+)\s*:\s*(?P<port>\d+)",
        output, flags=re.IGNORECASE
    )
    if not m:
        raise RuntimeError(f"Could not find contact string / port in output:\n{output}")
    return int(m.group("port"))


def blk_config(cfg: Config, blk: int, check: bool = True) -> None:
    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            f"sudo sed -i -E 's|^[[:space:]]*blocksize[[:space:]]+.*$|blocksize {blk}M|' /etc/gridftp.d/zdebug; "
            f"cat /etc/gridftp.d/zdebug ",
            localhost=cfg.localhost
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"IPERF: Failed changing the blocksize on {host.upper()}"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        head = "\n".join(cp.stdout.splitlines()[:5])
        logging.debug("IPERF: Gridftp blocksize config on %s:\n%s", host.upper(), head)


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
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
        f"( "
        f"echo '### ip link show dev' $dev; ip link show dev $dev; "
        f"echo; echo '### ethtool' $dev; sudo ethtool $dev; "
        f"echo; echo '### ethtool -i' $dev; sudo ethtool -i $dev; "
        f"echo; echo '### ethtool -g' $dev; sudo ethtool -g $dev; "
        f"echo; echo '### ethtool -k' $dev; sudo ethtool -k $dev; "
        f"echo; echo '### ethtool -c' $dev; sudo ethtool -c $dev; "
        f"echo; echo '### ethtool -S' $dev; sudo ethtool -S $dev; "
        f"echo; echo '### tc -s qdisc show dev' $dev; sudo tc -s qdisc show dev $dev; "
        f") > {shlex.quote(out_dir)}/nic_${{dev}}.log 2>&1; "
        f"done"
    )
    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"NIC: Failed on {str(host).upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("NIC: Recorded NIC reports on %s", str(host).upper())


def _cpu_irq_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"( "
        f"echo '### CPU governor'; "
        f"for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo \"$f: $(cat $f 2>/dev/null)\"; done; "
        f"echo; echo '### CPU frequencies'; "
        f"for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do echo \"$f: $(cat $f 2>/dev/null)\"; done; "
        f"echo; echo '### NIC IRQs and affinity'; "
        f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
        f"echo; echo '## device:' $dev; "
        f"grep -i $dev /proc/interrupts || true; "
        f"for irq in $(grep -i $dev /proc/interrupts | awk -F: '{{print $1}}' | tr -d ' '); do "
        f"echo IRQ $irq; "
        f"echo -n 'smp_affinity: '; cat /proc/irq/$irq/smp_affinity 2>/dev/null || true; "
        f"echo -n 'smp_affinity_list: '; cat /proc/irq/$irq/smp_affinity_list 2>/dev/null || true; "
        f"done; "
        f"done "
        f") > {shlex.quote(out_dir)}/cpu_irq.log 2>&1; "
    )
    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"CPU/IRQ: Failed on {str(host).upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("CPU/IRQ: Recorded CPU and IRQ report on %s", str(host).upper())


def _rss_rps_xps_report(cfg: Config, hosts: Sequence[str], out_dir: str, check: bool = True) -> None:
    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"for dev in $(ls /sys/class/net | grep -v '^lo$'); do "
        f"( "
        f"echo '### ethtool -l channels' $dev; sudo ethtool -l $dev || true; "
        f"echo; echo '### ethtool -x RSS indirection' $dev; sudo ethtool -x $dev || true; "
        f"echo; echo '### RPS/XPS sysfs' $dev; "
        f"find /sys/class/net/$dev/queues -type f "
        f"\\( -name rps_cpus -o -name rps_flow_cnt -o -name xps_cpus \\) "
        f"-exec sh -c 'for f do echo \"$f: $(cat \"$f\")\"; done' sh {{}} + || true; "
        f") > {shlex.quote(out_dir)}/rss_rps_xps_${{dev}}.log 2>&1; "
        f"done"
    )
    for host in hosts:
        cp = run_subprocess(host, None, cmd, localhost=cfg.localhost)
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


def system_state_report(cfg: Config, out_dir: str, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    _sysctl_report(cfg, hosts, out_dir, check=check)
    _host_report(cfg, hosts, out_dir, check=check)
    _nic_report(cfg, hosts, out_dir, check=check)
    _cpu_irq_report(cfg, hosts, out_dir, check=check)
    _rss_rps_xps_report(cfg, hosts, out_dir, check=check)
    _storage_report(cfg, hosts, out_dir, path=out_dir, check=check)


def restart_gridftp(cfg: Config, check: bool = True) -> None:
    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            #"sudo systemctl restart apache2.service "
            "sudo systemctl restart gridftp-server-restarter.service ",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GFTP: Failed restarting gridftp on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("IPERF: Restarted gridftp on %s (%s)", host.upper(), cp.stdout.strip())


def cleanup_iperf(cfg: Config, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
    # for host in cfg.hosts.ep.values():
    # host = cfg.hosts.ep.get("listener")
        cp = run_subprocess(host, None,"pkill -TERM -f '[i]perf3' || true ", localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"IPERF: Failed killing iperf on {host.upper()}\n "
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr} "
            )
        logging.debug("IPERF: Killed iperf on %s %s", host.upper(), cp.stdout)


def get_stream_id(cfg: Config, check: bool = True) -> Dict[Role, str]:
    out: dict[Role, str] = {}
    for role, host in cfg.hosts.ap.items():
        cp = run_subprocess(host, None, "gcs stream-gateway list \n", localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"IPERF: Failed getting the stream id on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        #out[role] = _parse_gateway_uid(cp.stdout + "\n" + cp.stderr)
        out[role] = _parse_gateway_uid(cp.stdout + "\n" + cp.stderr, "AP")
        logging.debug("IPERF: Stream Gateway id on %s %s", host.upper(), cp.stdout.strip())
    missing = {"initiator", "listener"} - set(out.keys())
    if missing:
        raise RuntimeError(f"Missing stream ids for roles: {sorted(missing)}")
    return out


#def start_tunnel(cfg: Config, initiator_id: str, listener_id: str, blk: int, parallel: int, run: int, check: bool = True) -> str:
def start_tunnel(cfg: Config, initiator_id: str, listener_id: str, lbl: str, check: bool = True) -> str:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        "globus streams tunnel create "
        "--lifetime-minutes 3120 -v "
        f"--label {shlex.quote(lbl)} "
        f"{shlex.quote(initiator_id)} {shlex.quote(listener_id)}",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"LOCAL: Failed creating the streams tunnel on {cfg.localhost.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    id = _parse_uid(cp.stdout + "\n" + cp.stderr)
    logging.debug("LOCAL: Created the stream tunnel on %s with id: %s", cfg.localhost.upper(), id)
    return id


#def start_statkit(cfg: Config, t : int, parallel: int, run_idx: int, out_dir: str, check: bool = True) -> None:
def start_statkit(cfg: Config, timeout : int , app: str, out_dir: str, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} && "
            #"python ~/statkit/monitor/launcher.py "
            #"pids=$(pgrep -d, -f globus-gridftp-server || iperf || true); "
            #"pids=$(pgrep -d, -f globus-gridftp-server|iperf || true); "
            "pids=$(pgrep -d, -f globus-gridftp-server || true); "
            "python ~/statkit/monitor/launcher.py  --pids \"$pids\" "
            #f"--out {shlex.quote(out_dir)} --duration {t * 120} & "
            #f"echo $! > {shlex.quote(out_dir)}/launcher.pid ", 
            f"--out {shlex.quote(out_dir)} --app {shlex.quote(app)} --duration {timeout} & "
            f"echo $! > {shlex.quote(out_dir)}/{shlex.quote(app)}-launcher.pid ", 
            localhost=cfg.localhost,
        )
        logging.debug("IPERF: Started on statkit on %s %s", host.upper(), cp.stdout)


def init_listener_env(cfg: Config, tunnel_id: str, check: bool = True) -> None:
    host = cfg.hosts.ep["listener"]
    cp = run_subprocess(
        host, cfg.remote_env,
        "globus-streams environment initialize "
        f"--listener-contact-string {cfg.listener_ip}:{cfg.tunnel_port} "
        f"{shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"IPERF: Failed initializing listener environment on {host.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    logging.debug("IPERF: Listener environment initializing on %s:\n%s", host.upper(), cp.stdout.strip())


def init_initiator_env(cfg: Config, tunnel_id: str, check: bool = True) -> int:
    host = cfg.hosts.ep("initiator")
    cp = run_subprocess(
        host, cfg.remote_env,
        f"globus-streams environment initialize {shlex.quote(tunnel_id)} ",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"IPERF: Failed initializing initiator environment on {host.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    logging.debug("IPERF: Initiator environment initializing on %s:\n%s", host.upper(), cp.stdout.strip())
    combined = cp.stdout + "\n" + cp.stderr
    return _parse_contact_port(combined)


def stop_statkit(cfg: Config) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = popen_subprocess(
            host, None,
            r"pkill -TERM -f 'monitor/launcher\.py' || true",
            localhost=cfg.localhost,
        )
        logging.debug("IPERF: Stopped statkit on %s", host.upper())


_STATE = re.compile(r"^\s*State:\s*(?P<state>\S+)\s*$", re.MULTILINE)
_STATUS = re.compile(r"^\s*Status:\s*(?P<status>.+?)\s*$", re.MULTILINE)
def _parse_status(output: str) -> tuple[str, str]:
    m_state = _STATE.search(output)
    if not m_state:
        raise RuntimeError(f"Could not find State in output:\n{output}")

    m_status = _STATUS.search(output)
    status = m_status.group("status").strip() if m_status else ""
    state = m_state.group("state")
    return state, status



def status_tunnel(cfg: Config, tunnel_id: str) -> tuple[str, str]:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        f"globus streams tunnel show {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,
    )
    state, status = _parse_status((cp.stdout + "\n" + cp.stderr).strip())
    return state, status


def stop_tunnel(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        f"globus streams tunnel stop {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,
    )
    state, status = status_tunnel(cfg, tunnel_id)
    #logging.info("LOCAL: Stop stream tunnel %s: %s \n\n", tunnel_id, state)
    logging.info("LOCAL: Stop stream tunnel %s: %s ", tunnel_id, state, status)


def delete_tunnel(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        f"globus streams tunnel delete {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,
    )
    out = (cp.stdout + "\n" + cp.stderr).strip()
    if out:
        logging.info("LOCAL: Delete streams tunnel %s: %s", tunnel_id, out)
    else:
        raise RuntimeError(f"LOCAL: Tunnel was not deleted: {tunnel_id}\n")

#def start_iperf_server(cfg: Config, host: str, port:int, tunnel_id: str, out_dir: str) -> subprocess.Popen[str]:
def start_iperf_server(cfg: Config, host: str, port: int, tunnel_id: str, temp_file: str, app: str, out_dir: str) -> subprocess.Popen[str]:
    #host = cfg.hosts.ep.get("listener")
    extra_arg = f"-F {shlex.quote(temp_file)} " if cfg.test == "transfer" else ""
    cp = popen_subprocess(
    #cp = run_subprocess(
        host, cfg.remote_env,
        "globus-streams-launch "
        f"-p {port} {shlex.quote(tunnel_id)} "
        f"iperf3 -s -p {port} -1  --timestamps  --forceflush "
        #f"-F {temp_file}"
        f"{extra_arg} "
        f"-J --logfile {out_dir}/{shlex.quote(app)}.json & "
        "echo $! " ,
        localhost=cfg.localhost,
    )
    logging.info("IPERF: Started iperf3 server on host %s", host.upper())


#def start_iperf_client(cfg: Config, host: str, tunnel_id: str, contact_port: int, t : int, parallel: int, out_dir: str, check: bool = True) -> subprocess.CompletedProcess[str]:
def start_iperf_client(cfg: Config, host: str, tunnel_id: str, contact_port: int, 
    #host = cfg.hosts.ep.get("initiator")
    parallel: int, arg: int, 
    temp_file: str, app: str, out_dir: str, 
    timeout: int, check: bool = True) -> subprocess.CompletedProcess[str]:
    extra_arg = f"-Z -R -n {arg}G -F {shlex.quote(temp_file)} " if cfg.test == "transfer" else f"-P {parallel} -i 10 -O 10 -Z -R -t {arg} "
    cp = run_subprocess(
        host, cfg.remote_env,
        "globus-streams-launch "
        f"{shlex.quote(tunnel_id)} "
        f"iperf3 -c globus.{shlex.quote(tunnel_id)} -p {contact_port} --timestamps  --forceflush "
        f"{extra_arg} "
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json ",
        #f"-P {parallel} -i 10 -O 10 -Z -R -t {t} ",
        #f"-Z -R -n {arg}G -F {temp_file} ",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    n = parallel * 2 + 6        # 2x lines per each direction, 2x sums + 4 extra
    tail = "\n".join(cp.stdout.splitlines()[-n:])
    logging.info("IPERF: iPerf3 log (when -J is not set) on %s %s", host.upper(), tail)
    return cp


#def base_start_iperf_server(cfg: Config, host: str, port: int, out_dir: str) -> subprocess.Popen[str]:
def base_start_iperf_server(cfg: Config, host: str, port: int, temp_file: str, app: str, out_dir: str) -> subprocess.Popen[str]:
    extra_arg = f"-F {shlex.quote(temp_file)} " if cfg.test == "transfer" else ""
    cp = popen_subprocess(
        host, None,
        f"iperf3 -s -p {port} -1 --timestamps --forceflush "
        #f"-F {temp_file} "
        f"{extra_arg} "
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json  & "
        "echo $! " ,
        localhost=cfg.localhost,
    )
    logging.info("BASELINE: Started iperf3 server on %s", host.upper())


#def base_start_iperf_client(cfg: Config, host: str, listener_ip: str, port: int, t : int, parallel: int, out_dir: str, check: bool = True) -> subprocess.CompletedProcess[str]:
def base_start_iperf_client(cfg: Config, host: str, listener_ip: str, port: int, 
    parallel: int, arg: int, 
    temp_file: str, app: str, out_dir: str, 
    timeout: int, check: bool = True) -> subprocess.CompletedProcess[str]:
    extra_arg = f"-Z -R -n {arg}G -F {shlex.quote(temp_file)} " if cfg.test == "transfer" else f"-P {parallel} -i 10 -O 10 -Z -R -t {arg} "
    cp = run_subprocess(
        host, cfg.remote_env,
        f"iperf3 -c {listener_ip} -p {port} --timestamps --forceflush "
        #f"-P {parallel} -i 10 -O 10 -Z -R -t {t} ",
        #f"-Z -R -n {arg}G -F {temp_file} ",
        f"{extra_arg} "
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json ",
        localhost=cfg.localhost,
        timeout= timeout,
    )
    n = parallel * 2 + 6        # 2x lines per each direction, 2x sums + 4 extra
    tail = "\n".join(cp.stdout.splitlines()[-n:])
    logging.info("BASELINE: iPerf3 log (when -J is not set) on %s %s", host.upper(), tail)
    return cp

def record_ping(cfg: Config, host: str, dest_ip: str, out_dir: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = run_subprocess(
        host, None,
        #f"netserver   # on listener side "
        #f"netperf -H <listener_ip> -t TCP_RR -l 30   # from initiator side"
        #f"ping -4 -n -q -i 0.5 -c 20 {dest_ip} > {shlex.quote(out_dir)}/ping.log ",
        #f"ping -4 -n -D -i 0.5 -c 20 {dest_ip} > {shlex.quote(out_dir)}/ping.log ",
        f"ping -4 -n -q -i 0.5 -c 20 {dest_ip} | tee {shlex.quote(out_dir)}/ping.log ",
        localhost=cfg.localhost,
    )
    logging.debug("%s: Ping log %s", host.upper(), cp.stdout)
    return cp
