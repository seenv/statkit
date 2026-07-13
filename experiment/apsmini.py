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

from config import Config
from remote import run_subprocess, popen_subprocess


def _daq_service(port: int, file: str, i: int) -> str:
    return (
        f"docker run --rm --network host "
        f"--name daq-{i} "
        f"seenv/aps-mini-apps-daq:latest "
        f"python /aps-mini-apps/build/python/streamer-daq/DAQStream.py "
        f"--mode 1 "
        f"--simulation_file /aps-mini-apps/data/{shlex.quote(file)} "
        f"--d_iteration 10000 "
        f"--publisher_addr tcp://*:{port} "
        f"--iteration_sleep=1 "
        f"--projection_sleep=0 "
        f"--synch_count 1; "
    )
    
def _dist_service(
    # init_gw_ip: str,
    # init_gw_port: int,
    ip: str,
    port: int,
    i: int,
) -> str:
    return (
        f"docker run --rm --network host "
        f"--name dist-{i} "
        f"seenv/aps-mini-apps-dist:latest "
        f"python /aps-mini-apps/build/python/streamer-dist/"
        f"ModDistStreamPubDemo.py "
        f"--data_source_addr tcp://{ip}:{port} "
        #f"--data_source_addr tcp://{shlex(ip)}:{port} "
        f"--cast_to_float32 "
        f"--normalize "
        f"--my_distributor_addr tcp://127.0.0.1:4200{i} "
        f"--beg_sinogram 1500 "
        f"--num_sinograms 2 "
        f"--num_columns 2560; "
    )


def _sirt_service(i: int) -> str:
    return (
        f"docker run --rm --network host "
        f"--name sirt-{i} "
        f"seenv/aps-mini-apps-sirt:latest "
        f"/aps-mini-apps/build/bin/sirt_stream "
        f"--write-freq 4 "
        f"--dest-host 127.0.0.1 "
        f"--dest-port 4200{i} "
        f"--window-iter 1 "
        f"--window-step 1 "
        f"--window-length 4 "
        f"-t 2 "
        f"-c 1427 "
        f"--pub-addr tcp://*:5300{i}; "
    )
def _get_container_stats(
    cfg: Config, 
    host: str,
    timeout: int, check: bool = False,
) -> int:
    cp = run_subprocess(
        host, None,
        #f"docker ps -aq | wc -l && "           # counts all the containers
        f"docker ps -q | wc -l ",               # counts only running containers
        localhost=cfg.localhost,
        timeout=timeout,
        check=check,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"MINI: Failed checking the container stats on {host.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    return int(cp.stdout.strip())


def _are_containers_running(cfg, host, parallel, service, timeout, retries):
    total_containers = parallel * 2 if host == cfg.hosts.ep["initiator"] and service == "sirt" else parallel
    for retry in range(1, retries + 1):
        status = _get_container_stats(cfg, host, timeout)
        if status != total_containers and retry < retries:
            time.sleep(cfg.sleep)
        elif status != total_containers and retry == retries:
            raise RuntimeError(f"MINI: {service.upper()} containers didn't start after {retries * cfg.sleep} seconds on {host.capitalize()}")
        elif status == total_containers and retry < retries:
            logging.debug("MINI: Started %dx %s container on the %s", status, service.upper(), host.upper())
            break


def _start_mini_service(
    cfg: Config,
    host: str,
    parallel: int, 
    numa: str,
    app: str, 
    service: str, 
    start_port: int,
    #tunnel_ids: Sequence[str],
    #tunnel_ports: Sequence[int],
    #initiator_gw_ip: str,
    stream_ids: Sequence[str],
    listen_ports: Sequence[int],
    listener_ip: str,
    timeout: int,
    file: str,
    out_dir: str,
    module_path: str = "/tmp/temp_files",
    check: bool = True,
) -> None:
    launch_processes = []
    #for i, (tunnel_port, tunnel_id) in enumerate(zip(tunnel_ports, tunnel_ids)):
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        # wrapper_cmd = f"globus-streams-launch -p {start_port + i} {stream_id}  " if service == "daq" else f"globus-streams-launch {stream_id} "STD
        if app == "mini_base":
            wrapper_cmd = ""
        else:
            wrapper_cmd = f"globus-streams-launch -p {start_port + i} {stream_id}  " if service == "daq" else f"globus-streams-launch {stream_id} "

        if service == "daq":
            # cmd = _daq_service(start_port + i, file, i)
            cmd = _daq_service(start_port + i, file, i)
        elif service == "dist":
            # cmd = _dist_service(initiator_gw_ip, tunnel_port, i)
            cmd = _dist_service(listener_ip, listen_port, i)
        elif service == "sirt":
            cmd = _sirt_service(i)

        logging.debug(f"DEBUG ")
        logging.debug(f"DEBUG: Command to run the containers: {wrapper_cmd} {cmd} ")
        logging.debug(f"DEBUG ")

        cp = popen_subprocess(
            host, None,
            f"set +x; mkdir -p {shlex.quote(out_dir)} && "
            f"{{ "
            f'echo "START $(date \'+%Y-%m-%d %H:%M:%S\')"; '
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{service}-{i}-time.log "
            f"{wrapper_cmd} {cmd} "
            f"rc=$?; "
            f'echo "END $(date \'+%Y-%m-%d %H:%M:%S\') rc=$rc"; '
            f"exit $rc; "
            f"}} 2>&1 "
            f"| tr '\\r' '\\n' "
            f"| stdbuf -oL awk "
            f"'NF {{ print $0; fflush(); }}' "
            f"| tee {shlex.quote(out_dir)}/{shlex.quote(app)}-{service}-{i}.log "
            f"> /dev/null & ",
            localhost=cfg.localhost,
        )
        launch_processes.append((i, cp))

    # for i, cp in launch_processes:
    #     try:
    #         stdout, stderr = cp.communicate(timeout=timeout)
    #     except subprocess.TimeoutExpired:
    #         cp.kill()
    #         stdout, stderr = cp.communicate()
    #         raise RuntimeError(
    #             f"MINI: Timed out submitting {service}-{i} "
    #             f"on {host.upper()}\n"
    #             f"STDOUT:\n{stdout or ''}\n"
    #             f"STDERR:\n{stderr or ''}"
    #         )
    #     if check and cp.returncode != 0:
    #         raise RuntimeError(
    #             f"MINI: Failed submitting {service}-{i} "
    #             f"on {host.upper()}\n"
    #             f"Return code: {cp.returncode}\n"
    #             f"STDOUT:\n{stdout or ''}\n"
    #             f"STDERR:\n{stderr or ''}"
    #         )
    logging.info("MINI: Started %s containers on %s", service.upper(), host.upper())


def start_mini_app(
    cfg: Config, 
    parallel: int, 
    numa: str,
    app: str, 
    #service: str,
    start_port: int,
    #initiator_gw_ip: str,
    #tunnel_ports: Sequence[int],
    #tunnel_ids: Sequence[str],
    listen_ip: str,
    listen_ports: Sequence[int],
    stream_ids: Sequence[str],
    out_dir: str, 
    file: str,
    timeout: int, 
    module_path: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> None:

    # if not (len(tunnel_ports) == parallel and len(tunnel_ids) == parallel)):
    #     raise RuntimeError(f"Expected all mini lists to have length parallel={parallel}, got ports={len(tunnel_ports)}, sync_tunnel_ids={len(tunnel_ids)}")
    if not (len(listen_ports) == parallel and len(stream_ids) == parallel):
        raise RuntimeError(f"Expected all mini lists to have length parallel={parallel}, got ports={len(listen_ports)}, sync_tunnel_ids={len(stream_ids)}")
    
    logging.info("MINI: Starting APS mini app containers on the endpoints")

    host, service = cfg.hosts.ep["listener"], "daq"
    logging.debug("MINI: Starting APS mini app's %s service %s", service.capitalize(), host.upper())
    _start_mini_service(
        cfg, host, parallel, numa, app, "daq", start_port,
        #tunnel_ids, tunnel_ports, initiator_gw_ip, timeout, file, out_dir,
        stream_ids, listen_ports, listen_ip, timeout, file, out_dir,
    )
    _are_containers_running(cfg, host, parallel, service, timeout, retries)
        
    host, service = cfg.hosts.ep["initiator"], "dist"
    logging.debug("MINI: Starting APS mini app's %s service %s", service.capitalize(), host.upper())
    _start_mini_service(
        cfg, host, parallel, numa, app, "dist", start_port,
        #tunnel_ids, tunnel_ports, initiator_gw_ip, timeout, file, out_dir,
        stream_ids, listen_ports, listen_ip, timeout, file, out_dir,
    )
    _are_containers_running(cfg, host, parallel, service, timeout, retries)
    time.sleep(cfg.sleep)

    host, service = cfg.hosts.ep["initiator"], "sirt"
    logging.debug("MINI: Starting APS mini app's %s service %s", service.capitalize(), host.upper())
    _start_mini_service(
        cfg, host, parallel, numa, app, "sirt", start_port,
        #tunnel_ids, tunnel_ports, initiator_gw_ip, timeout, file, out_dir,
        stream_ids, listen_ports, listen_ip, timeout, file, out_dir,
    )
    _are_containers_running(cfg, host, parallel, service, timeout, retries)

    logging.info("MINI: APS mini app containers on the endpoints are online")
    logging.debug("MINI: Started the APS Mini app")


def wait_finish_transfer(
    cfg: Config, parallel: int,
    app: str, out_dir: str,
    timeout: int, 
    retries: int = 100,
    check: bool = False,
) -> None:
    for retry in range(1, retries + 1):
        listener_status = _get_container_stats(cfg, cfg.hosts.ep["listener"] , timeout)
        initiator_status = _get_container_stats(cfg, cfg.hosts.ep["initiator"], timeout)

        # if listener_status < parallel and initiator_status < (parallel * 2):
        if listener_status == 0 and initiator_status == 0:
            logging.info("MINI: Finished APS mini app's transfers on the endpoints")
            return
        if retry < retries:
            time.sleep(cfg.sleep)
        else:
            raise RuntimeError(
                f"MINI: Containers didn't finish the transfer after {retries * cfg.sleep} seconds. "
                f"listener={listener_status}, initiator={initiator_status}"
            )

def stop_mini_containers(
    cfg: Config, parallel: int,
    app: str, out_dir: str,
    timeout: int, check: bool = False,
) -> None:
    for host in cfg.hosts.ep.values():
        cp = run_subprocess(
            host, None,
            f"docker ps -q | xargs --no-run-if-empty docker kill ",
            localhost=cfg.localhost,
            timeout=timeout,
            check=check,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"MINI: Stoping the containers on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
    logging.info("MINI: Stopped APS mini app's containers on the endpoints")
    logging.debug("MINI stdout:\n%s", cp.stdout)


def prune_containers(
    cfg: Config, parallel: int,
    app: str, out_dir: str,
    timeout: int, 
    retries: int = 100,
    check: bool = False,
) -> None:
    for host in cfg.hosts.ep.values():
        for retry in range(1, retries + 1):
            status = _get_container_stats(cfg, host, timeout)
            if status == 0:
                cp = run_subprocess(
                    host, None,
                    'docker ps -q | xargs --no-run-if-empty docker stop && docker container prune -f ',
                    localhost=cfg.localhost,
                    timeout=timeout,
                    check=check,
                )
                if check and cp.returncode != 0:
                    raise RuntimeError(
                        f"MINI: Failed prunning the container stats on {host.upper()}\n"
                        f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
                break
            elif status != 0 and retry < retries:
                time.sleep(cfg.sleep)
            else:
                raise RuntimeError(f"MINI: Containers didn't finish after {retries * cfg.sleep} seconds on {host.capitalize()}")
    logging.info("MINI: Pruned APS mini app's containers on the endpoints")

