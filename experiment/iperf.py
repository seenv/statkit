from __future__ import annotations

import logging
#import os
import time

import shlex
import subprocess
from typing import Optional

from config import Config, Role

from utils import popen_subprocess, run_subprocess, blk_config
from utils import restart_gridftp, get_stream_id, start_tunnel
from utils import stop_tunnel, delete_tunnel
from utils import init_listener_env, init_initiator_env
from utils import start_statkit, stop_statkit, cleanup_iperf
from baseline import baseline_main


#TODO: run a direct stream with iper before each test through the tunnel


def start_iperf_server(cfg: Config, host: str, tunnel_id: str, out_dir: str) -> subprocess.Popen[str]:
    #host = cfg.hosts.ep.get("listener")
    cp = popen_subprocess(
    #cp = run_subprocess(
        host,
        cfg.remote_env,
        "globus-streams-launch "
        f"-p {cfg.base_port} {shlex.quote(tunnel_id)} "
        f"iperf3 -s -p {cfg.base_port} -1 --timestamps "
        f"-J --logfile {out_dir}/iperf.json --forceflush & "
        "echo $! " ,
        localhost=cfg.localhost,
    )
    logging.info("%s: Started iperf3 server", host.upper())


def run_iperf_client(cfg: Config, host: str, tunnel_id: str, contact_port: int, t : int, parallel: int, out_dir: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    #host = cfg.hosts.ep.get("initiator")
    cp = run_subprocess(
        host,
        cfg.remote_env,
        "globus-streams-launch "
        f"{shlex.quote(tunnel_id)} "
        f"iperf3 -c globus.{shlex.quote(tunnel_id)} -p {contact_port} "
        f"-J --logfile {shlex.quote(out_dir)}/iperf.json --forceflush "
        f"-P {parallel} -O 3 -Z -R -t {t} --timestamps ",
        localhost=cfg.localhost,
        timeout= t + 120,
    )
    n = parallel * 2 + 6        # 2x lines per each direction, 2x sums + 4 extra
    tail = "\n".join(cp.stdout.splitlines()[-n:])
    logging.info("%s: iPerf3 log (when -J is not set)\n%s", host.upper(), tail)
    return cp


def iperf_main(cfg: Config) -> None:
    test_config = (
        (block, duration, parallel, run)
        for block in cfg.blocks
        for duration in cfg.time_frames
        for parallel in cfg.parallels
        for run in range(1, cfg.run_num + 1)
    )

    total_runs = len(cfg.time_frames) * len(cfg.parallels) * cfg.run_num * len(cfg.blocks)
    for idx, (block, duration, parallel, run) in enumerate(test_config, start=1):
        logging.info("--------------- Test %d / %d : duration: %s / run %s / parallel %s", idx, total_runs, duration, run , parallel)
        out_dir = f"{cfg.report_dir}/globus/{block}/{parallel}/{run}"
        
        # launch the baseline test
        if cfg.baseline:
            baseline_main(cfg, duration, parallel, run, block)
        time.sleep(5)
        
        # initial safty check reseting/cleaning up
        if run == 1:
            blk_config(cfg, block)
            #time.sleep(5)
            
        restart_gridftp(cfg)

        cleanup_iperf(cfg)
        time.sleep(5)

        ids = get_stream_id(cfg)
        initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

        # create tunnel (local)
        tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id)
        time.sleep(5)

        try:
            # launch statkit
            logging.info("IPERF: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, duration, parallel, run, out_dir)
            time.sleep(5)
            
            # init listener env 
            logging.info("IPERF: Bringing up the tunnel on Listener AP")
            init_listener_env(cfg, tunnel_id)
            time.sleep(5)

            # init initiator env + discover contact port
            logging.info("IPERF: Bringing up the tunnel on Initiator AP")
            contact_port = init_initiator_env(cfg, tunnel_id)
            time.sleep(5)
            
            logging.info("IPERF: Starting iperf server")
            start_iperf_server(cfg, cfg.hosts.ep.get("listener"), tunnel_id, out_dir)
            time.sleep(5)           # it takes more for them to initiates! TODO: find a better way
            
            # run iperf client
            logging.info("IPERF: Starting iperf client")
            iperf_clt = run_iperf_client(cfg, cfg.hosts.ep.get("initiator"), tunnel_id, contact_port, duration, parallel, out_dir)
            time.sleep(5)

        finally:
            # TODO: check why monitor finishes before transfer!
            logging.info("IPERF: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
            time.sleep(2)

            stop_tunnel(cfg, tunnel_id)
            time.sleep(5)
            
            delete_tunnel(cfg, tunnel_id)
            time.sleep(5)
            # TODO: check the tunnel status and continue when it stopped
