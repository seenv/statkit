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


#-------------------------------------------------------------------------------
# Gridftp config and reset
def gridftp_config(cfg: Config, blk: int, awai:int, encr: int, out_dir: str, check: bool = True) -> None:
    if awai not in (0, 1):
        raise ValueError(f"Invalid splice value: {awai}. Expected 0 or 1")
    if encr not in (0, 1):
        raise ValueError(f"Invalid encrypt value: {encr}. Expected 0 or 1")
    if awai == 1 and encr == 1:
        raise ValueError("Invalid GridFTP mode: splice=1 and encrypt=1 cannot both be enabled.")
    globus_gridftp_debug_log = f"{out_dir}/globus-gridftp-debug.log"
    splice_buffer_size = blk * (1024 ** 2)
    for host in cfg.hosts.ap.values():
        if encr == 1:
            splice_line = "#$AWAI_SPLICE_ROUTING 0"
            encrypt_line = "$AWAI_WAN_ENCRYPTION 1"
            buffer_line = f"#$AWAI_SPLICE_ROUTING_BUFFER_SIZE {splice_buffer_size}"
        elif awai == 1:
            splice_line = "$AWAI_SPLICE_ROUTING 1"
            encrypt_line = "#$AWAI_WAN_ENCRYPTION 0"
            buffer_line = f"#$AWAI_SPLICE_ROUTING_BUFFER_SIZE {splice_buffer_size}"
        else:
            splice_line = "#$AWAI_SPLICE_ROUTING 0"
            encrypt_line = "#$AWAI_WAN_ENCRYPTION 0"
            buffer_line = f"#$AWAI_SPLICE_ROUTING_BUFFER_SIZE {splice_buffer_size}"
        extra = (
            f"-e 's|^[[:space:]]*#?[[:space:]]*\\$AWAI_SPLICE_ROUTING[[:space:]]+.*$|{splice_line}|' "
            f"-e 's|^[[:space:]]*#?[[:space:]]*\\$AWAI_WAN_ENCRYPTION[[:space:]]+.*$|{encrypt_line}|' "
            #f"-e 's|^[[:space:]]*#?[[:space:]]*\\$AWAI_SPLICE_ROUTING_BUFFER_SIZE[[:space:]]+.*$|{buffer_line}|' "
            f"-e 's|^[[:space:]]*#?[[:space:]]*\\$GLOBUS_GRIDFTP_SERVER_DEBUG[[:space:]]+.*$|$GLOBUS_GRIDFTP_SERVER_DEBUG ALL,{globus_gridftp_debug_log},1,ALL|' "
        )
        cp = run_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"sudo sed -i -E "
            f"-e 's|^[[:space:]]*blocksize[[:space:]]+.*$|blocksize {blk}M|' "
            f"{extra} "
            f"/etc/gridftp.d/zdebug && "
            #f"sudo systemctl restart apache2.service && "
            #f"sudo systemctl restart globus-gridftp-server.service && "
            #f"sudo systemctl restart gridftp-server-restarter.service && "
            f"sudo cat /etc/gridftp.d/zdebug ",
            localhost=cfg.localhost
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GTR: Failed changing the blocksize on {host.upper()}"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        #head = "\n".join(cp.stdout.splitlines()[:5])
        head = "\n".join(cp.stdout.splitlines())
        logging.debug("GTR: Gridftp splice and blocksize config on %s:\n%s", host.upper(), head)


def logging_gridftp(cfg: Config, out_dir: str, check: bool = True) -> None:
    audit_log = shlex.quote(f'{out_dir}/gridftp-audit.log')
    single_log = shlex.quote(f'{out_dir}/gridftp-single.log')

    for host in cfg.hosts.ep.values():
        cp = run_subprocess(
            host, None,
            f"sudo sed -i -E "
            f"-e 's|^[[:space:]]*log_audit[[:space:]]+.*$|log_audit {audit_log}|' "
            f"-e 's|^[[:space:]]*#?[[:space:]]*\\log_single[[:space:]]+.*$|log_single {single_log}|' "
            #f"-e 's|^[[:space:]]*#?[[:space:]]*\\log_transfer[[:space:]]+.*$|log_transfer {transfer_log}|' "
            f"/etc/gridftp.d/z_logging && "
            f"sudo cat /etc/gridftp.d/z_logging ",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GTR: Failed changing GridFTP logging paths on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )


def gridftp_report(cfg: Config, out_dir: str, check: bool = True) -> None:
    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"sudo cat /etc/gridftp.d/zdebug > {shlex.quote(out_dir)}/gridftp-stream.log ",
            localhost=cfg.localhost
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GTR: Failed changing the gridftp configuration on {host.upper()}"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("GTR: Recorded the Gridftp configuration on %s", host.upper())


def restart_gridftp(cfg: Config, hosts: list[str], check: bool = True) -> None:
    # hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
    # for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            f"sudo systemctl restart globus-gridftp-server.service && "
            f"sudo systemctl restart apache2.service ",
            #"sudo systemctl restart apache2.service && "
            #"sudo systemctl restart gcs_manager.service && "
            #"sudo systemctl restart gcs_manager_assistant.service && "
            #f"sudo systemctl restart globus-gridftp-server.service && "
            #f"sudo systemctl restart gridftp-server-restarter.service ",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GTR: Failed restarting gridftp on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug("GTR: Restarted gridftp on %s (%s)", host.upper(), cp.stdout.strip())


def check_gridftp_config(
    cfg, 
    block, splice, encrypt,
    last_block, last_splice, last_encrypt, 
    output_dir
) -> tuple[int, int, int]:
    if block != last_block or splice != last_splice or encrypt != last_encrypt:
        logging.info("Applying GridFTP configuration: blocksize: %sM splice: %s encrypt: %s", block, splice, encrypt)
        gridftp_config(cfg, block, splice, encrypt, output_dir)
        last_block, last_splice, last_encrypt= block, splice, encrypt
        hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
        restart_gridftp(cfg, hosts)
        time.sleep(cfg.sleep)
    logging.info("GTR: Recording the Gridftp configuration")
    gridftp_report(cfg, output_dir)

    return block, splice, encrypt,

#-------------------------------------------------------------------------------
# Globus Streams Tunnel setup
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


def start_globus_streams(cfg, parallel, start_port, app, idx, timeout) ->  tuple[list[str], list[int], str]:

    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    stream_ids, listen_ports = [], []

    try:
        logging.info("GST: Creating %d tunnels on Localhost", parallel)
        for i in range(parallel):
            logging.debug("GST: Creating the tunnel # %d tunnels on Localhost", parallel)
            tunnel_label = f"{cfg.test.replace(' ', '_')}-app{app}-idx{idx}-parallel{i}"
            new_tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout)
            stream_ids.append(new_tunnel_id)

        logging.info("GST: Activating %d tunnels", parallel)
        for tunnel in stream_ids:
            logging.debug("GST: Waiting for tunnel %s to get activated", tunnel)
            status_tunnel(cfg, tunnel, "AWAITING_LISTENER")

        # init listener env 
        logging.info("GST: Bringing up %d tunnels on endpoints", parallel)
        for i, tunnel in enumerate(stream_ids):
            logging.debug("GST: Bringing up the tunnel %s on Listener EP", tunnel)
            init_listener_env(cfg, cfg.listener_ip, tunnel, start_port + (i * 2))
            
            # waiting till the tunnel gets activated
            status_tunnel(cfg, tunnel, "ACTIVE")

            # init initiator env + discover contact port
            logging.debug("GST: Bringing up the tunnel %s on Initiator EP", tunnel)
            contact_port, listen_ip, gw_port = init_initiator_env(cfg, tunnel)
            listen_ports.append(contact_port) if app != "mini_gst" else listen_ports.append(gw_port)
            logging.debug("DEBUG: The Tunnel Fake Port assigned to Tunnel ID %s is %s of total ports of %s on Gateway of IP %s", tunnel, contact_port, listen_ports, listen_ip)

        return stream_ids, listen_ports, listen_ip

    except Exception as e:
        raise RuntimeError(f"IGST: Runtime Error: {e}") from e
