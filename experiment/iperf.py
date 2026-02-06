from __future__ import annotations

import logging
#import os
import time

import shlex
import subprocess
from typing import Optional

from config import Config, Role

from utils import popen_subprocess, run_subprocess
from utils import restart_gridftp, get_stream_id, start_tunnel, stop_tunnel
from utils import init_listener_env, init_initiator_env
from utils import start_statkit, stop_statkit, cleanup_iperf



def start_iperf_server(cfg: Config, tunnel_id: str, parallel: int, run: int) -> subprocess.Popen[str]:
    host = cfg.hosts.ep.get("listener")
    out_dir = f"{cfg.report_dir}/{parallel}/{run}"
    cp = popen_subprocess(
        host,
        cfg.remote_env,
        f"mkdir -p {shlex.quote(out_dir)} && "
        "globus-streams-launch "
        f"-p {cfg.base_port} {shlex.quote(tunnel_id)} "
        f"iperf3 -s -p {cfg.base_port} -1 --timestamps "
        f"-J --logfile {shlex.quote(out_dir)}/iperf.json ",
        localhost=cfg.localhost,
    )
    logging.debug("%s: Started iperf3 server \n", host.upper())


def run_iperf_client(cfg: Config, tunnel_id: str, contact_port: int, t : int, parallel: int, run: int) -> subprocess.CompletedProcess[str]:
    host = cfg.hosts.ep.get("initiator")
    out_dir = f"{cfg.report_dir}/{parallel}/{run}"
    cp = run_subprocess(
        host,
        cfg.remote_env,
        f"mkdir -p {shlex.quote(out_dir)} && "
        "globus-streams-launch "
        f"{shlex.quote(tunnel_id)} "
        f"iperf3 -c globus.{shlex.quote(tunnel_id)} -p {contact_port} "
        #f"-J --logfile {shlex.quote(out_dir)}/iperf.json "
        f"--timestamps -P {parallel} -O 5 -Z -R -t {t} ",
        localhost=cfg.localhost,
        timeout= t + 120,
    )
    logging.debug("%s: Started iperf3 server %s\n", host.upper(), cp.stdout.strip())
    return cp


def iperf_main(cfg: Config) -> None:
    
    test_config = (
        (duration, parallel, run)
        for duration in cfg.time_frames
        for parallel in cfg.parallels
        for run in range(1, cfg.run_num + 1)
    )
    
    total_runs = len(cfg.time_frames) * len(cfg.parallels) * cfg.run_num    
    for idx, (duration, parallel, run) in enumerate(test_config, start=1):
        logging.info("Test %d / %d : duration: %s / run %s / parallel %s", idx, total_runs, duration, run , parallel)

        # initial safty check reseting/cleaning up
        restart_gridftp(cfg)
        cleanup_iperf(cfg)
        time.sleep(5)

        ids = get_stream_id(cfg)
        initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

        # create tunnel (local)
        tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id)
        time.sleep(2)

        try:
            # launch statkit
            logging.info("Starting the statkit monitoring on the hosts")
            start_statkit(cfg, duration, parallel, run, True)
            time.sleep(2)
            
            # init listener env 
            logging.info("Bringing up the tunnel on Listener AP")
            init_listener_env(cfg, tunnel_id)
            time.sleep(2)

            # init initiator env + discover contact port
            logging.info("Bringing up the tunnel on Initiator AP")
            contact_port = init_initiator_env(cfg, tunnel_id)
            time.sleep(2)
            
            logging.info("Starting iperf server")
            start_iperf_server(cfg, tunnel_id, parallel, run)
            time.sleep(2)
            # TODO: add pkill -9 iperf
            
            # run iperf client
            logging.info("Starting iperf client")
            iperf_clt = run_iperf_client(cfg, tunnel_id, contact_port, duration, parallel, run)
            time.sleep(5)

        finally:
            logging.info("Stopping the tunnel and statkit")
            # TODO: check why monitor finishes before transfer!
            stop_statkit(cfg)
            time.sleep(2)
            stop_tunnel(cfg, tunnel_id)
            time.sleep(5)
            # TODO: check the tunnel status and continue when it stopped
