from __future__ import annotations

import logging
import time

from config import Config

from utils import restart_gridftp, get_stream_id, start_tunnel
from utils import stop_tunnel, delete_tunnel, record_ping, gridftp_config
from utils import init_listener_env, init_initiator_env, gridftp_report
from utils import make_temp_file, start_statkit, stop_statkit, cleanup_iperf
from utils import start_iperf_server, start_iperf_client, system_state_report
from utils import base_start_iperf_server, base_start_iperf_client
from utils import start_rsync, prepare_remote_dest


def run_iperf_gst(cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, 
    temp_file: str, 
    port: int, listener_host: str, initiator_host: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Tests: %d / %d ------- Strarting iPerf3 Tunnel Tests", idx, total_runs)
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    #tunnel_label = f"{cfg.lease.replace(" ", "_")}-idx{idx}-ttl{total_runs}"
    tunnel_label = f"{cfg.lease.replace(' ', '_')}-idx{idx}-tot{total_runs}"
    tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label)
    time.sleep(cfg.sleep)

    try:
        # launch statkit
        logging.info("GST: Starting the statkit monitoring on the hosts")
        start_statkit(cfg, timeout, "iperf_gst", output_dir)   #size as duration which will be * 60s
        time.sleep(cfg.sleep)
        # init listener env 
        logging.info("GST: Bringing up the tunnel on Listener AP")
        init_listener_env(cfg, tunnel_id)
        time.sleep(cfg.sleep)
        # init initiator env + discover contact port
        logging.info("GST: Bringing up the tunnel on Initiator AP")
        contact_port = init_initiator_env(cfg, tunnel_id)
        time.sleep(cfg.sleep)
        # start iperf server
        logging.info("GST: Starting iperf server")
        start_iperf_server(cfg, listener_host, port, tunnel_id, temp_file, "iperf_gst", output_dir)
        time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
        # run iperf client
        logging.info("GST: Starting iperf client")
        start_iperf_client(cfg, initiator_host, tunnel_id, contact_port, parallel, arg, temp_file, "iperf_gst", output_dir, timeout)
        # recording rtt
        logging.info("GST: Recording the RTT")
        record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
        
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


def run_iperf_baseline(cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int,
    temp_file: str, 
    port: int, listener_host: str, initiator_host: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting iPerf3 Direct Tests -----", idx, total_runs)
    try:
        # launch statkit
        logging.info("BASE: Starting the statkit monitoring on the hosts")
        start_statkit(cfg, timeout, "iperf_base", output_dir)   #size as duration which will be * 60s
        time.sleep(cfg.sleep)
        # start iperf server
        logging.info("BASE: Starting iperf server")
        base_start_iperf_server(cfg, listener_host, port, temp_file, "iperf_base", output_dir)
        time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way
        # run iperf client
        logging.info("BASE: Starting iperf client")
        base_start_iperf_client(cfg, initiator_host, cfg.listener_ip, port, parallel, arg, temp_file, "iperf_base", output_dir, timeout)    
        logging.info("BASE: Recording the RTT")
        record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_base", output_dir)
        
    except Exception as e:
        raise RuntimeError(f"BASE ERROR: {e}") from e
    
    finally:
        logging.info("BASE: Stopping the statkit monitoring on the hosts")
        stop_statkit(cfg)
        cleanup_iperf(cfg)


def run_rsync(cfg: Config, *, idx: int, total_runs: int, timeout: int, #size: int, 
    temp_file: str, listener_host: str, initiator_host: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Test %d / %d: starting rsync test -----", idx, total_runs)
    try:
        logging.info("RSYNC: Starting statkit monitoring")
        start_statkit(cfg, timeout, "rsync", output_dir)   #size as duration which will be * 60s
        time.sleep(cfg.sleep)
        logging.info("RSYNC: Starting direct rsync transfer")   #it will run on server and log it there
        start_rsync(
            cfg, listener_host, initiator_host,
            temp_file, output_dir, timeout
        )

        logging.info("RSYNC: Recording RTT")        # it will run on the client
        record_ping(cfg, initiator_host, cfg.listener_ip, "rsync", output_dir)

    except Exception as e:
        raise RuntimeError(f"RSYNC ERROR: {e}") from e

    finally:
        logging.info("RSYNC: Stopping statkit monitoring")
        stop_statkit(cfg)



def experiment_main(cfg: Config) -> None:
    
    parallels = cfg.parallels if cfg.test == "globus" else [1]
    args = cfg.time_frames if cfg.test == "globus" else cfg.file_sizes
    splices = cfg.splice
    blocks = cfg.blocks
    runs = cfg.run_num
    test_config = (
        (splice, block, parallel, arg, run)
        for splice in splices
        for block in blocks
        for parallel in parallels
        for arg in args
        for run in range(1, runs + 1)
    )
    last_block, last_splice = None, None
    total_runs = len(splices) * len(blocks) * len(parallels) * len(args) * runs
    
    logging.info("SYS: Recording the system reports")
    system_state_report(cfg, cfg.report_dir)

    for idx, (splice, block, parallel, arg, run) in enumerate(test_config, start=1):
        print("\n")
        logging.info(
            "--------------- Tests: %d / %d : splice %s / blocksize %s / arg %s / run %s ---------------",
            idx, total_runs, splice, block, arg, run)

        #tunnel_out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        #direct_out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        #rsync_out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        output_dir = f"{cfg.report_dir}/S{splice}/{block}/{parallel}/{arg}/{run}"
        timeout = (arg * 120) #if cfg.test == "transfer" else (duration * 120)

        if block != last_block or splice != last_splice:
            logging.info("Applying GridFTP configuration: splice: %s and blocksize: %sM", splice, block)
            gridftp_config(cfg, block, splice)
            last_block, last_splice = block, splice

        temp_file = f"/tmp/temp_file/{arg}G.bin"
        if cfg.test == "transfer":
            make_temp_file(cfg, arg, temp_file)

        logging.info("GFTP: Recording the Gridftp configuration")
        gridftp_report(cfg, output_dir)
        time.sleep(cfg.sleep)

        if "iperf" in cfg.app:
            run_iperf_gst(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, #block=block, run=run, 
                parallel=parallel, arg=arg,
                temp_file=temp_file, port=cfg.tunnel_port,
                listener_host=cfg.hosts.ep.get("listener"), initiator_host=cfg.hosts.ep.get("initiator"), 
                output_dir=output_dir,
            )
            time.sleep(cfg.sleep)

        if "base" in cfg.app:
            run_iperf_baseline(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout,
                parallel=parallel, arg=arg,
                temp_file=temp_file, port=cfg.direct_port,
                listener_host=cfg.hosts.ep.get("listener"), initiator_host=cfg.hosts.ep.get("initiator"),
                output_dir=output_dir,
            )
            time.sleep(cfg.sleep)

        if "rsync" in cfg.app:
            run_rsync(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, arg=arg,
                temp_file=temp_file,
                listener_host=cfg.hosts.ep.get("listener"), initiator_host=cfg.hosts.ep.get("initiator"),
                output_dir=output_dir,
            )
            time.sleep(cfg.sleep)
