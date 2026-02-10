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

from config import Config, Role


# def make_session_id() -> str:
#     """Generate a sortable unique session id: YYYYMMDD-HHMMSS-<8hex>"""
#     ts = time.strftime("%Y%m%d-%H%M%S")
#     short = uuid.uuid4().hex[:8]
#     return f"{ts}-{short}"

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


def run_subprocess(host: str, env: Optional[str], cmd: str, *, localhost: str, 
            check: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
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
def _parse_uuid(output: str) -> str:
    for m in _UUID_CANDIDATE_RE.finditer(output):
        try:
            return str(uuid.UUID(m.group(0)))
        except ValueError:
            pass
    raise RuntimeError(f"Could not find UUID in output:\n{output}")


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
                f"{host.upper()}: Failed changing the blocksize"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        head = "\n".join(cp.stdout.splitlines()[:3])
        logging.info("%s: Gridftp blocksize config:\n%s", host.upper(), head)


def restart_gridftp(cfg: Config, check: bool = True) -> None:
    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            "sudo systemctl restart gridftp-server-restarter.service "
            "sudo systemctl restart apache2.service ",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"{host.upper()}: Failed restarting gridftp\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("%s: Restarted gridftp (%s)", host.upper(), cp.stdout.strip())


def cleanup_iperf(cfg: Config, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
    # for host in cfg.hosts.ep.values():
    # host = cfg.hosts.ep.get("listener")
        cp = run_subprocess(host, None,"pkill -TERM -f '[i]perf3' || true ", localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"{host.upper()}: Failed killing iperf\n "
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr} "
            )
    logging.debug("%s: Killed iperf %s", host.upper(), cp.stdout)


def get_stream_id(cfg: Config, check: bool = True) -> Dict[Role, str]:
    out: dict[Role, str] = {}
    for role, host in cfg.hosts.ap.items():
        cp = run_subprocess(host, None, "gcs stream-gateway list \n", localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"{host.upper()}: Failed gettuing the stream id:\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        out[role] = _parse_uuid(cp.stdout + "\n" + cp.stderr)
        logging.debug("%s: Stream Gateway id %s", host.upper(), cp.stdout.strip())
    missing = {"initiator", "listener"} - set(out.keys())
    if missing:
        raise RuntimeError(f"Missing stream ids for roles: {sorted(missing)}")
    return out


def start_tunnel(cfg: Config, initiator_id: str, listener_id: str, check: bool = True) -> str:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        "globus streams tunnel create "
        "--lifetime-minutes 360 -v "
        f"{shlex.quote(initiator_id)} {shlex.quote(listener_id)}",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"{cfg.localhost.upper()}: Failed creating the streams tunnel\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    id = _parse_uuid(cp.stdout + "\n" + cp.stderr)
    logging.debug("%s: Created the stream tunnel with id: %s", cfg.localhost.upper(), id)
    return id


def start_statkit(cfg: Config, t : int, parallel: int, run_idx: int, out_dir: str, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} && "
            "pids=$(pgrep -d, -f globus-gridftp-server || true); "
            "python ~/statkit/monitor/launcher.py  --pids \"$pids\" "
            #"python ~/statkit/monitor/launcher.py --pids 187333"
            f"--out {shlex.quote(out_dir)} --duration {t + 120} & "
            f"echo $! > {shlex.quote(out_dir)}/launcher.pid ", 
            localhost=cfg.localhost,
        )
        logging.debug("%s: Started on statkit %s", host.upper(), cp.stdout)


def init_listener_env(cfg: Config, tunnel_id: str, check: bool = True) -> None:
    host = cfg.hosts.ep.get("listener")
    cp = run_subprocess(
        host, cfg.remote_env,
        "globus-streams environment initialize "
        f"--listener-contact-string {cfg.listener_ip}:{cfg.base_port} "
        f"{shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"{host.upper()}: Failed initializing listener environment\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    logging.debug("%s: Listener environment initializing:\n%s", host.upper(), cp.stdout.strip())


def init_initiator_env(cfg: Config, tunnel_id: str, check: bool = True) -> int:
    host = cfg.hosts.ep.get("initiator")
    cp = run_subprocess(
        host, cfg.remote_env,
        f"globus-streams environment initialize {shlex.quote(tunnel_id)} ",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"{host.upper()}: Failed initializing initiator environment\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    logging.debug("%s: Initiator environment initializing:\n%s", host.upper(), cp.stdout.strip())
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
        logging.debug("%s: Stopped statkit ", host)
        

def stop_tunnel(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        f"globus streams tunnel stop {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,
    )
    out = (cp.stdout + "\n" + cp.stderr).strip()
    if out:
        logging.debug("LOCAL: Stop tunnel output:\n%s", out)

