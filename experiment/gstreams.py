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
from pathlib import PurePosixPath
from datetime import datetime
import socket
import traceback

from config import Config, Role
from remote import run_subprocess, popen_subprocess



#-------------------------------------------------------------------------------
# Helpers
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


_UUID_CANDIDATE = re.compile(r"[0-9a-fA-F-]{32,36}")
def _parse_uid(output: str) -> str:
    for m in _UUID_CANDIDATE.finditer(output):
        try:
            return str(uuid.UUID(m.group(0)))
        except ValueError:
            pass
    raise RuntimeError(f"Could not find UUID in output:\n{output}")

def _parse_gateway_id(output: str,  parts: list[str], *, exact: bool = False) -> str:
    for line in output.splitlines():
        if "|" not in line:
            continue
        if line.strip().startswith("---"):
            continue
        # first column is Display Name
        display = line.split("|", 1)[0].strip()
        if not all(part in display for part in parts):
            continue
        m = _UUID_CANDIDATE.search(line)
        if not m:
            raise RuntimeError(f"Matched name but no ID found on line:\n{line}")
        return str(uuid.UUID(m.group(0)))
    raise RuntimeError(f"No gateway row matched name={parts!r}.\nOutput:\n{output}")


def _parse_contact_port(output: str) -> tuple[int, str, int]:
    m = re.search(
        r"Your contact string is:\s*(?P<host>[^:\s]+)\s*:\s*(?P<port>\d+)",
        output, flags=re.IGNORECASE
    )
    if not m:
        raise RuntimeError(f"Could not find contact string / port in output:\n{output}")
    mm = re.search(
            r"^connector_contact_string\s*=\s*(?P<host>[^:\s]+)\s*:\s*(?P<port>\d+)\s*$",
            output,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    if not mm:
            raise RuntimeError(f"Could not find connector_contact_string in output:\n{output}")
    
    return int(m.group("port")), mm.group("host"), int(mm.group("port"))


def status_tunnel(cfg: Config, tunnel_id: str, stat: str, retry: int = 100, wait: int = 5) -> tuple[str, str]:
    for ret in range(1, retry + 1):
        cp = run_subprocess(
            cfg.localhost, cfg.local_env,
            f"globus streams tunnel show {shlex.quote(tunnel_id)}",
            localhost=cfg.localhost,
            check=False,
        )
        state, status = _parse_status((cp.stdout + "\n" + cp.stderr).strip())   # AWAITING_LISTENER, ACTIVE, STOPPING, STOPPED
        if state == stat:
            logging.debug("GST: Tunnel State %s | Status %s", state, status)
            return state, status
        if ret < retry:
            logging.debug(
                "GST: Waiting for tunnel to reache %s. Current state: %s. Retry: %d / %d next try in %d secs", 
                stat, state, ret, retry, wait)
            time.sleep(wait)
    raise RuntimeError(
        f"GST: The tunnel state is {state} and did not change to {stat} after "
        f"{retry} attempts over about {max(0, retry - 1) * wait}s."
    )


def stop_tunnel(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        f"globus streams tunnel stop {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,
    )
    logging.debug("LOCAL: Stoping the stream tunnel %s", tunnel_id)


def delete_tunnel(cfg: Config, tunnel_id: str) -> None:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        f"globus streams tunnel delete {shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
        check=False,
    )
    logging.debug("LOCAL: Deleted the streams tunnel %s", tunnel_id)



def get_stream_id(cfg: Config, check: bool = True) -> Dict[Role, str]:
    out: dict[Role, str] = {}
    for role, host in cfg.hosts.ap.items():
        cp = run_subprocess(host, None, "gcs stream-gateway list \n", localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GST: Failed getting the stream id on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        out[role] = _parse_gateway_id(cp.stdout + "\n" + cp.stderr, [cfg.lease.capitalize(), role.capitalize()], exact=False)
        logging.debug("GST: Stream Gateway id on %s %s", host.upper(), cp.stdout.strip())
    missing = {"initiator", "listener"} - set(out.keys())
    if missing:
        raise RuntimeError(f"GST: Missing stream ids for roles: {sorted(missing)}")
    return out


def start_tunnel(cfg: Config, initiator_id: str, listener_id: str, lbl: str, timeout: int, check: bool = True) -> str:
    cp = run_subprocess(
        cfg.localhost, cfg.local_env,
        "globus streams tunnel create "
        "--lifetime-minutes 3600 -v "
        f"--label {shlex.quote(lbl)} "
        f"{shlex.quote(initiator_id)} {shlex.quote(listener_id)} ",
        localhost=cfg.localhost,
        #timeout=timeout,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"LOCAL: Failed creating the streams tunnel on {cfg.localhost.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    id = _parse_uid(cp.stdout + "\n" + cp.stderr)
    logging.debug("LOCAL: Created the stream tunnel on %s with id: %s", cfg.localhost.upper(), id)
    return id


def init_listener_env(cfg: Config, listener_ip: str, tunnel_id: str, port: int, check: bool = True) -> None:
    host = cfg.hosts.ep["listener"]
    cp = run_subprocess(
        host, cfg.remote_env,
        "globus-streams environment initialize "
        f"--listener-contact-string {listener_ip}:{port} "
        f"{shlex.quote(tunnel_id)}",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"IPERF: Failed initializing listener environment on {host.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    logging.debug("IPERF: Listener environment initializing on %s:\n%s", host.upper(), cp.stdout.strip())


def init_initiator_env(cfg: Config, tunnel_id: str, check: bool = True) -> tuple[int, str, int]:
    host = cfg.hosts.ep["initiator"]
    cp = run_subprocess(
        host, cfg.remote_env,
        #f"globus-streams environment initialize {shlex.quote(tunnel_id)} ",
        f"globus-streams environment initialize {shlex.quote(tunnel_id)} && "
        f"cat $HOME/.globus/streams/{shlex.quote(tunnel_id)}.conf ",
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
