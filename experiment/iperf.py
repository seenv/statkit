from __future__ import annotations

import logging
import time

from config import Config

from utils import restart_gridftp, get_stream_id, start_tunnel
from utils import stop_tunnel, delete_tunnel, record_ping, gridftp_config
from utils import init_listener_env, init_initiator_env, gridftp_report
from utils import start_statkit, stop_statkit, cleanup_iperf
from utils import start_iperf_server, start_iperf_client
from utils import base_start_iperf_server, base_start_iperf_client

# UDP(default 1 Mbit/sec for UDP, unlimited for TCP) or TCP? xmit/recv the specified file
# dont-fragment? zerocopy? TCP/SCTP no delay, disabling Nagle's Algorithm

def iperf_main(cfg: Config) -> None:
    test_config = (
        (splice, block, duration, parallel, run)
        for splice in cfg.splice
        for block in cfg.blocks
        for duration in cfg.time_frames
        for parallel in cfg.parallels
        for run in range(1, cfg.run_num + 1)
    )

    last_block, last_splice = None, None
    total_runs = len(cfg.splice) * len(cfg.time_frames) * len(cfg.parallels) * cfg.run_num * len(cfg.blocks)
    for idx, (splice, block, duration, parallel, run) in enumerate(test_config, start=1):
        logging.info(
            "--------------- Test %d / %d : splice %s / blocksize %s / duration: %s / parallel %s / run %s ---------------",
            idx, total_runs, block, duration, parallel, run)

        if block != last_block or splice != last_splice:
            logging.info("Applying GridFTP configuration: splice: %s and blocksize: %sM", splice, block)
            gridftp_config(cfg, block, splice)
            last_block, last_splice = block, splice

        tunnel_out_dir = f"{cfg.report_dir}/S{splice}/globus/{block}/{parallel}/{run}"
        direct_out_dir = f"{cfg.report_dir}/S{splice}/direct/{block}/{parallel}/{run}"
        #output_dir = f"{cfg.report_dir}/S{splice}/direct/{block}/{parallel}/{run}"
        
        # launch the baseline test
        if cfg.baseline:
            try:
                logging.info("GFTP: Recording the Gridftp configuration")
                gridftp_report(cfg, direct_out_dir)
                time.sleep(cfg.sleep)
                # launch statkit
                logging.info("BASELINE: Starting the statkit monitoring on the hosts")
                start_statkit(cfg, duration, parallel, run, direct_out_dir)
                time.sleep(cfg.sleep)
                
                logging.info("BASELINE: Starting iperf server")
                base_start_iperf_server(cfg, cfg.hosts.ap.get("listener"), cfg.ap_port, direct_out_dir)
                time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
                
                # run iperf client
                logging.info("BASELINE: Starting iperf client")
                base_start_iperf_client(
                    cfg, cfg.hosts.ap.get("initiator"), cfg.listener_ap_ip, 
                    cfg.ap_port, duration, parallel, direct_out_dir)
                
                logging.info("BASELINE: Recording the RTT")
                record_ping(cfg, cfg.hosts.ap.get("initiator"), cfg.listener_ap_ip, direct_out_dir)
                
            except Exception as e:
                raise RuntimeError(f"BASELINE ERROR: {e}") from e
            
            finally:
                logging.info("BASELINE: Stopping the statkit monitoring on the hosts")
                stop_statkit(cfg)
                cleanup_iperf(cfg)
        
        time.sleep(cfg.sleep)

        ids = get_stream_id(cfg)
        initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

        # create tunnel (local)
        tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, block, parallel, run)
        time.sleep(cfg.sleep)

        try:
            logging.info("GFTP: Recording the Gridftp configuration")
            gridftp_report(cfg, tunnel_out_dir)
            time.sleep(cfg.sleep)
            # launch statkit
            logging.info("IPERF: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, duration, parallel, run, tunnel_out_dir)
            time.sleep(cfg.sleep)
            
            # init listener env 
            logging.info("IPERF: Bringing up the tunnel on Listener AP")
            init_listener_env(cfg, tunnel_id)
            time.sleep(cfg.sleep)

            # init initiator env + discover contact port
            logging.info("IPERF: Bringing up the tunnel on Initiator AP")
            contact_port = init_initiator_env(cfg, tunnel_id)
            #time.sleep(cfg.sleep)
            
            logging.info("IPERF: Starting iperf server")
            start_iperf_server(cfg, cfg.hosts.ep.get("listener"), cfg.ep_port, tunnel_id, tunnel_out_dir)
            time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
            
            # run iperf client
            logging.info("IPERF: Starting iperf client")
            start_iperf_client(
                cfg, cfg.hosts.ep.get("initiator"), tunnel_id, 
                contact_port, duration, parallel, tunnel_out_dir
            )
            
            logging.info("IPERF: Recording the RTT")
            #record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, tunnel_out_dir)       # chameleon doesn't have direct gateway between sites
            record_ping(cfg, cfg.hosts.ap.get("initiator"), cfg.listener_ap_ip, tunnel_out_dir)
            
        except Exception as e:
            raise RuntimeError(f"EXPERIMENT ERROR: {e}") from e

        finally:
            # TODO: check why monitor finishes before transfer!
            logging.info("IPERF: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
            cleanup_iperf(cfg)
            stop_tunnel(cfg, tunnel_id)
            time.sleep(cfg.sleep)
            restart_gridftp(cfg)
            
            delete_tunnel(cfg, tunnel_id)
            time.sleep(cfg.sleep)
