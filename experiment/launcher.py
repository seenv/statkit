from __future__ import annotations

import logging
import time
from pathlib import Path

from config import Config

from utils import restart_gridftp, get_stream_id, start_tunnel, status_tunnel
from utils import stop_tunnel, delete_tunnel, record_ping, gridftp_config
from utils import init_listener_env, init_initiator_env, gridftp_report, logging_gridftp
from utils import make_file, start_statkit, stop_statkit, cleanup_iperf
from utils import start_iperf_server, start_iperf_client, system_state_report
from utils import base_start_iperf_server, base_start_iperf_client
from utils import start_rsync_daemon, start_rsync_transfer
from utils import get_collection_id, start_globus_transfer


def run_iperf_gst(cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, 
    temp_file: str, 
    port: int, listener_host: str, initiator_host: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Tests: %d / %d ------- Strarting iPerf3 Tunnel Tests", idx, total_runs)
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}"
    tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label)
    time.sleep(cfg.sleep)

    try:
        logging.info("GST: Waiting for tunnel to get activated")
        status_tunnel(cfg, tunnel_id, "AWAITING_LISTENER")
        # launch statkit
        logging.info("GST: Starting the statkit monitoring on the hosts")
        start_statkit(cfg, timeout, "iperf_gst", output_dir)   #size as duration which will be * 60s
        time.sleep(cfg.sleep)
        # init listener env 
        logging.info("GST: Bringing up the tunnel on Listener AP")
        init_listener_env(cfg, cfg.listener_ip, tunnel_id)
        # waiting till the tunnel gets activated
        status_tunnel(cfg, tunnel_id, "ACTIVE")
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
        #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
        record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_gst", output_dir)

    except Exception as e:
        raise RuntimeError(f"GST: Runtime Error: {e}") from e

    finally:
        # TODO: check why monitor finishes before transfer!
        logging.info("GST: Stopping the statkit monitoring on the hosts")
        cleanup_iperf(cfg)
        stop_statkit(cfg)
        stop_tunnel(cfg, tunnel_id)
        status_tunnel(cfg, tunnel_id, "STOPPED")
        delete_tunnel(cfg, tunnel_id)
        restart_gridftp(cfg)


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
        #base_start_iperf_client(cfg, initiator_host, cfg.listener_ip, port, parallel, arg, temp_file, "iperf_base", output_dir, timeout)
        base_start_iperf_client(cfg, initiator_host, cfg.listener_pub, port, parallel, arg, temp_file, "iperf_base", output_dir, timeout)    
        logging.info("BASE: Recording the RTT")
        #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_base", output_dir)
        record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"BASE: Runtime Error: {e}") from e

    finally:
        logging.info("BASE: Stopping the statkit monitoring on the hosts")
        stop_statkit(cfg)
        cleanup_iperf(cfg)


def run_rsync(cfg: Config, *, idx: int, total_runs: int, timeout: int, temp_file: str, 
    listener_host: str, initiator_host: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting RSync Test -----", idx, total_runs)
    try:
        logging.info("RSYNC: Starting statkit monitoring")
        start_statkit(cfg, timeout, "rsync", output_dir)   #size as duration which will be * 60s
        time.sleep(cfg.sleep)
        logging.info("RSYNC: Starting the rsync deamon on the host %s", initiator_host.upper())
        start_rsync_daemon(
            cfg, initiator_host, output_dir, cfg.rsync_port, timeout,
            cfg.test, "/tmp/temp_files")
        logging.info("RSYNC: Starting direct rsync transfer")
        start_rsync_transfer(
            cfg, listener_host, initiator_host, temp_file, output_dir, cfg.rsync_port, timeout,
            cfg.test, "/tmp/temp_files")
        logging.info("RSYNC: Recording RTT")        # it will run on the client
        #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
        record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_gst", output_dir)

    except Exception as e:
        raise RuntimeError(f"RSYNC: Runtime Error: {e}") from e

    finally:
        logging.info("RSYNC: Stopping statkit monitoring")
        stop_statkit(cfg)


def run_globus_transfer(cfg: Config, *, idx: int, total_runs: int, timeout: int, temp_file: str, 
    listener_host: str, initiator_host: str, output_dir: str, 
    arg: int, 
    encrypt: int
    ) -> None:
    print("\n")
    logging.info("----- Tests: %d / %d ------- Strarting Globus Transfer Tests", idx, total_runs)
    ids = get_collection_id(cfg)
    initiator_collection_id, listener_collection_id = ids["initiator"], ids["listener"]
    transfer_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}"
    try:
        # launch statkit
        logging.info("GTR: Starting the statkit monitoring on the hosts")
        start_statkit(cfg, timeout, "globus_gtr", output_dir)   #size as duration which will be * 60s
        time.sleep(cfg.sleep)
        # start globus transfer
        logging.info("GTR: Starting globus transfer")
        start_globus_transfer(cfg, listener_collection_id, initiator_collection_id, transfer_label, 
        arg, encrypt, temp_file, "globus_gtr", output_dir, timeout)
        # recording rtt
        logging.info("GTR: Recording the RTT")
        record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_gst", output_dir)
        
    except Exception as e:
        raise RuntimeError(f"GTR: Runtime Error: {e}") from e

    finally:
        # TODO: check why monitor finishes before transfer!
        logging.info("GTR: Stopping the statkit monitoring on the hosts")
        # cleanup_iperf(cfg)
        stop_statkit(cfg)


def experiment_main(cfg: Config) -> None:
    blocks = cfg.blocks
    parallels = cfg.parallels if cfg.test == "stream" else [1]
    args = cfg.time_frames if cfg.test == "stream" else cfg.file_sizes
    splices = cfg.splice
    encrypts = cfg.encrypt
    runs = cfg.run_num

    test_config = (
        (block, parallel, arg, splice, encrypt, run)
        for block in blocks
        for parallel in parallels
        for arg in args
        for splice in splices
        for encrypt in encrypts
        for run in range(1, runs + 1)
    )

    last_block, last_splice, last_encrypt= None, None, None
    total_runs =  len(blocks) * len(parallels) * len(args) * len(splices) * len(encrypts) * runs
    
    logging.info("SYS: Recording the system reports")
    sys_report_dir = str(Path(cfg.report_dir) / "sys-info")
    system_state_report(cfg, sys_report_dir)

    for idx, (block, parallel, arg, splice, encrypt, run) in enumerate(test_config, start=1):
        print("\n")
        logging.info(
            "--------------- Tests: %d / %d : blocksize %s / arg %s / splice %s / encrypt: %s / run %s ---------------",
            idx, total_runs, block, arg, splice, encrypt, run)

        temp_file = f"{arg}G.bin"
        if cfg.test == "transfer":
            output_path = Path(cfg.report_dir) / f"B{block}" / f"P{parallel}" / f"S{arg}" / f"A{splice}" / f"E{encrypt}" / f"R{run}"
            output_dir = str(output_path)
            make_file(cfg, arg, temp_file)
        elif cfg.test == "stream":
            output_path = Path(cfg.report_dir) / f"B{block}" / f"P{parallel}" / f"T{arg}" / f"A{splice}" / f"E{encrypt}" / f"R{run}"
            output_dir = str(output_path)
        timeout = (arg * 120)

        if "iperf" in cfg.app:
            if block != last_block or splice != last_splice or encrypt != last_encrypt:
                logging.info("Applying GridFTP configuration: blocksize: %sM splice: %s encrypt: %s", block, splice, encrypt)
                gridftp_config(cfg, block, splice, encrypt)
                last_block, last_splice, last_encrypt= block, splice, encrypt
                restart_gridftp(cfg)
            logging.info("GTR: Recording the Gridftp configuration")
            gridftp_report(cfg, output_dir)
            time.sleep(cfg.sleep)
            
            run_iperf_gst(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, #block=block, run=run, 
                parallel=parallel, arg=arg, temp_file=temp_file, port=cfg.tunnel_port,
                #listener_host=cfg.hosts.ep.get("listener"), initiator_host=cfg.hosts.ep.get("initiator"), 
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                output_dir=output_dir,
            )
            time.sleep(cfg.sleep)

        if "base" in cfg.app:
            run_iperf_baseline(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout,
                parallel=parallel, arg=arg, temp_file=temp_file, port=cfg.direct_port,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                output_dir=output_dir,
            )
            time.sleep(cfg.sleep)

        if "rsync" in cfg.app:
            run_rsync(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, temp_file=temp_file,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                output_dir=output_dir,
            )
            time.sleep(cfg.sleep)

        if "gtr" in cfg.app:
            logging_gridftp(cfg, output_dir)
            logging.info("GTR: Changing the Gridftp log path")
            restart_gridftp(cfg)
            time.sleep(cfg.sleep)
            
            run_globus_transfer(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, temp_file=temp_file,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                output_dir=output_dir, 
                arg=arg, encrypt=encrypt,
            )
            time.sleep(cfg.sleep)
