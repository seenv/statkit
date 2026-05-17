from __future__ import annotations

import logging
import time

from config import Config

from utils import restart_gridftp, get_stream_id, start_tunnel
from utils import stop_tunnel, delete_tunnel, record_ping, blk_config
from utils import init_listener_env, init_initiator_env
from utils import make_temp_file, start_statkit, stop_statkit, cleanup_iperf
from utils import start_iperf_server, start_iperf_client
from utils import base_start_iperf_server, base_start_iperf_client
from utils import start_rsync, prepare_remote_dest


def experiment_main(cfg: Config) -> None:
    test_config = (
        (block, size, run)
        for block in cfg.blocks
        for size in cfg.file_size
        for run in range(1, cfg.run_num + 1)
    )
    tmp = None
    total_runs = len(cfg.blocks) * len(cfg.file_size) * cfg.run_num
    
    for idx, (block, size, run) in enumerate(test_config, start=1):
        logging.info(
            "--------------- Tests: %d / %d : blocksize %s / size %sG / run %s ---------------",
            idx, total_runs, block,
            size, run)

        if block != tmp:
            blk_config(cfg, block)
            tmp = block

        tunnel_out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        direct_out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        rsync_out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        
        temp_file = f"/tmp/temp_file/{size}G.bin"
        make_temp_file(cfg, size, temp_file)
        time.sleep(cfg.sleep)

        ########################################################################
        # IPERF THROUGH THE GLOBUS STREAMS TUNNEL
        ids = get_stream_id(cfg)
        initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

        # create tunnel (local)
        tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, block, size, run)
        time.sleep(cfg.sleep)

        try:
            print("\n")
            logging.info("------- Strarting iPerf3 Tests")
            # launch statkit
            logging.info("GST: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, size, "iperf_gst", tunnel_out_dir)   #size as duration which will be * 60s
            
            # init listener env 
            logging.info("GST: Bringing up the tunnel on Listener AP")
            init_listener_env(cfg, tunnel_id)
            time.sleep(cfg.sleep)

            # init initiator env + discover contact port
            logging.info("GST: Bringing up the tunnel on Initiator AP")
            contact_port = init_initiator_env(cfg, tunnel_id)
            time.sleep(cfg.sleep)
            
            logging.info("GST: Starting iperf server")
            start_iperf_server(cfg, cfg.hosts.ep.get("listener"), cfg.ep_port, tunnel_id, temp_file, "iperf_gst", tunnel_out_dir)
            time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
            
            # run iperf client
            logging.info("GST: Starting iperf client")
            start_iperf_client(
                cfg, cfg.hosts.ep.get("initiator"), tunnel_id, 
                contact_port, size, temp_file, "iperf_gst", tunnel_out_dir
            )
            
            logging.info("GST: Recording the RTT")
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
            
        time.sleep(cfg.sleep)
        
        ########################################################################
        # IPERF BASELINE
        if cfg.baseline:
            try:
                print("\n")
                logging.info("------- Strarting iPerf3 Baseline Tests")
                # launch statkit
                logging.info("BASE: Starting the statkit monitoring on the hosts")
                start_statkit(cfg, size, "iperf_base", direct_out_dir)   #size as duration which will be * 60s
                time.sleep(cfg.sleep)
                
                logging.info("BASE: Starting iperf server")
                base_start_iperf_server(cfg, cfg.hosts.ep.get("listener"), cfg.ap_port, temp_file, "iperf_base", direct_out_dir)
                time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
                
                # run iperf client
                logging.info("BASE: Starting iperf client")
                base_start_iperf_client(
                    cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip,
                    cfg.ap_port, size, temp_file, "iperf_base", direct_out_dir)     #for port used ap to distinguish
                
                logging.info("BASE: Recording the RTT")
                record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, "iperf_base", direct_out_dir)
                
            except Exception as e:
                raise RuntimeError(f"BASE ERROR: {e}") from e
            
            finally:
                logging.info("BASE: Stopping the statkit monitoring on the hosts")
                stop_statkit(cfg)
                cleanup_iperf(cfg)
        
        time.sleep(cfg.sleep)

        ########################################################################
        # RSYNC
        try:
            print("\n")
            logging.info("------- Strarting rSync Tests")
            logging.info("RSYNC: Starting statkit monitoring")
            start_statkit(cfg, size, "rsync", rsync_out_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

            logging.info("RSYNC: Starting direct rsync transfer")   #it will run on server and log it there
            start_rsync(
                cfg=cfg,
                src_host=cfg.hosts.ep.get("listener"),
                dst_host=cfg.hosts.ep.get("initiator"),
                temp_file=temp_file,
                out_dir=rsync_out_dir,
                timeout=(size * 60)
            )

            logging.info("RSYNC: Recording RTT")        # it will run on the client
            record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, "rsync", rsync_out_dir)

        except Exception as e:
            raise RuntimeError(f"RSYNC ERROR: {e}") from e

        finally:
            logging.info("RSYNC: Stopping statkit monitoring")
            stop_statkit(cfg)
            time.sleep(cfg.sleep)