from __future__ import annotations

import logging
#import os
import time

import shlex
import subprocess
from typing import Optional

from config import Config, Role

from utils import popen_subprocess, run_subprocess
from utils import restart_gridftp, get_stream_id, start_tunnel
from utils import stop_tunnel, delete_tunnel, record_ping, gridftp_config
from utils import init_listener_env, init_initiator_env
from utils import start_statkit, stop_statkit, cleanup_iperf
from utils import start_iperf_server, start_iperf_client
from utils import base_start_iperf_server, base_start_iperf_client
#from baseline import baseline_main


def iperf_main(cfg: Config) -> None:
    test_config = (
        (block, duration, parallel, run)
        for block in cfg.blocks
        for duration in cfg.time_frames
        for parallel in cfg.parallels
        for run in range(1, cfg.run_num + 1)
    )
    last_block = None
    total_runs = len(cfg.time_frames) * len(cfg.parallels) * cfg.run_num * len(cfg.blocks)
    for idx, (block, duration, parallel, run) in enumerate(test_config, start=1):
        logging.info(
            "--------------- Test %d / %d : blocksize %s / duration: %s / parallel %s / run %s ---------------",
            idx, total_runs, block, duration, parallel, run
        )
        
        tunnel_out_dir = f"{cfg.report_dir}/{block}/{parallel}/{run}"
        direct_out_dir = f"{cfg.report_dir}/{block}/{parallel}/{run}"
        
        #restart_gridftp(cfg)
        #cleanup_iperf(cfg)
        #time.sleep(cfg.sleep)
        # initial safty check reseting/cleaning up
        if block != last_block: # or splice != last_splice:
            logging.info("Applying block configuration: %sM", block)
            gridftp_config(cfg, block)
            last_block = block

        # launch the baseline test
        if cfg.baseline:
            try:
                # launch statkit
                logging.info("BASE: Starting the statkit monitoring on the hosts")
                #start_statkit(cfg, duration, parallel, run, direct_out_dir)
                start_statkit(cfg, duration, "iperf_base", direct_out_dir)   #size as duration which will be * 60s
                time.sleep(cfg.sleep)
                
                logging.info("BASE: Starting iperf server")
                base_start_iperf_server(cfg, cfg.hosts.ep.get("listener"), cfg.direct_port, "iperf_base", direct_out_dir)
                time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
                
                # run iperf client
                logging.info("BASE: Starting iperf client")
                base_start_iperf_client(
                    cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, 
                    cfg.direct_port, duration, parallel, "iperf_base", direct_out_dir)
                
                logging.info("BASE: Recording the RTT")
                record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, "iperf_base", direct_out_dir)
                
            except Exception as e:
                raise RuntimeError(f"BASE ERROR: {e}") from e
            
            finally:
                logging.info("BASE: Stopping the statkit monitoring on the hosts")
                stop_statkit(cfg)
                cleanup_iperf(cfg)
        
        time.sleep(cfg.sleep)
            
        #restart_gridftp(cfg)
        #cleanup_iperf(cfg)
        #time.sleep(cfg.sleep)

        ids = get_stream_id(cfg)
        initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

        # create tunnel (local)
        tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, block, parallel, run)
        time.sleep(cfg.sleep)

        try:
            # launch statkit
            logging.info("GST: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, duration, "iperf_gst", tunnel_out_dir)
            time.sleep(cfg.sleep)
            
            # init listener env 
            logging.info("GST: Bringing up the tunnel on Listener AP")
            init_listener_env(cfg, tunnel_id)
            time.sleep(cfg.sleep)

            # init initiator env + discover contact port
            logging.info("GST: Bringing up the tunnel on Initiator AP")
            contact_port = init_initiator_env(cfg, tunnel_id)
            #time.sleep(cfg.sleep)
            
            logging.info("GST: Starting iperf server")
            start_iperf_server(cfg, cfg.hosts.ep.get("listener"), cfg.tunnel_port, tunnel_id, "iperf_gst", tunnel_out_dir)
            time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
            
            # run iperf client
            logging.info("GST: Starting iperf client")
            start_iperf_client(
                cfg, cfg.hosts.ep.get("initiator"), tunnel_id, 
                contact_port, duration, parallel, "iperf_gst", tunnel_out_dir
            )
            
            logging.info("GST: Recording the RTT")
            #record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, tunnel_out_dir)       # chameleon doesn't have direct gateway between sites
            record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, "iperf_gst", tunnel_out_dir)
            
        except Exception as e:
            raise RuntimeError(f"EXPERIMENT ERROR: {e}") from e

        finally:
            # TODO: check why monitor finishes before transfer!
            logging.info("GST: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
            cleanup_iperf(cfg)
            stop_tunnel(cfg, tunnel_id)
            time.sleep(cfg.sleep)
            restart_gridftp(cfg)
            
            # delete_tunnel(cfg, tunnel_id)
            # time.sleep(cfg.sleep)
            # TODO: check the tunnel status and continue when it stopped
