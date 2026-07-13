from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from config import Config

from utils import make_file, start_statkit, stop_statkit
from utils import get_stream_id, start_tunnel, status_tunnel, stop_tunnel, delete_tunnel
from utils import init_listener_env, init_initiator_env
from utils import restart_gridftp, gridftp_config, gridftp_report, logging_gridftp
# from utils import start_iperf_server, start_iperf_client, cleanup_iperf
# from utils import start_iperf_server_base, start_iperf_client_base
# from utils import start_rsync_daemon_gst, start_rsync_transfer_gst
# from utils import start_rsync_daemon_base, start_rsync_transfer_base
# from utils import start_rsync_ssh, stop_rsync_daemon
#from utils import create_mini_yamls, start_mini_containers, wait_finish_transfer
# from utils import stop_mini_containers, prune_containers
#from utils import get_collection_id, start_globus_transfer
from utils import record_ping
from sysconf import system_state_report
from apsmini import start_mini_app, wait_finish_transfer, stop_mini_containers, prune_containers
from iperf import start_iperf_server, start_iperf_client, start_iperf_server_base, start_iperf_client_base, cleanup_iperf
from rsync import stop_rsync_daemon, start_rsync_daemon_gst
from rsync import start_rsync_transfer_gst, start_rsync_daemon_base, start_rsync_transfer_base, start_rsync_ssh
from gtransfer import start_globus_transfer, get_collection_id


def build_net_modes(splices: Sequence[int], include_encrypt: bool) -> list[tuple[int, int]]:
    """
    Return valid network modes as (splice, encrypt).
    Valid modes:
      (0, 0): no splice, no encryption
      (1, 0): splice enabled, encryption disabled
      (0, 1): encryption enabled, splice disabled
    Encryption is intentionally not combined with splice.
    """
    modes: list[tuple[int, int]] = []
    for splice in splices:
        if splice not in (0, 1):
            raise ValueError(f"Invalid splice value: {splice}. Expected 0 or 1.")
        mode = (splice, 0)
        if mode not in modes:
            modes.append(mode)
    if include_encrypt:
        mode = (0, 1)
        if mode not in modes:
            modes.append(mode)
    if not modes:
        raise ValueError("No network modes selected.")
    return modes


def net_mode_dir(splice: int, encrypt: int) -> str:
    if splice == 0 and encrypt == 0:
        return "A0"
    #if splice == 1 and encrypt == 0:
    if splice == 1:
        return "A1"
    #if splice == 0 and encrypt == 1:
    if encrypt == 1:
        return "E1"
    raise ValueError(f"Invalid mode: splice={splice}, encrypt={encrypt}")


# ------------------------------------------------------------------------------
# iPerf3 GST
def run_iperf_gst(cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, 
    temp_file: str, 
    port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Tests: %d / %d ------- Strarting iPerf3 Tunnel Tests", idx, total_runs)
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}"
    logging.info("IGST: Creating the tunnel on Localhost")
    tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout)
    #time.sleep(cfg.sleep)
    try:
        logging.info("IGST: Waiting for tunnel to get activated")
        status_tunnel(cfg, tunnel_id, "AWAITING_LISTENER")
    
        if not cfg.is_test:
            # launch statkit
            logging.info("IGST: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, "iperf_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)
    
        # init listener env 
        logging.info("IGST: Bringing up the tunnel on Listener AP")
        init_listener_env(cfg, cfg.listener_ip, tunnel_id, port)
        # waiting till the tunnel gets activated
        status_tunnel(cfg, tunnel_id, "ACTIVE")
        # init initiator env + discover contact port
        logging.info("IGST: Bringing up the tunnel on Initiator AP")
        contact_port, ini_gw_ip, gw_port = init_initiator_env(cfg, tunnel_id)
        #time.sleep(cfg.sleep)
    
        # start iperf server
        logging.info("IGST: Starting iperf server")
        start_iperf_server(cfg, listener_host, port, tunnel_id, temp_file, "iperf_gst", numa, output_dir)
        time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way

        # run iperf client
        logging.info("IGST: Starting iperf client")
        start_iperf_client(cfg, initiator_host, tunnel_id, contact_port, parallel, arg, temp_file, "iperf_gst", numa, output_dir, timeout)
        if not cfg.is_test:
            # recording rtt
            logging.info("IGST: Recording the RTT")
            #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
            record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_gst", output_dir)

    except Exception as e:
        raise RuntimeError(f"IGST: Runtime Error: {e}") from e

    finally:
        # TODO: check why monitor finishes before transfer!
        cleanup_iperf(cfg)
        if not cfg.is_test:
            logging.info("IGST: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
        stop_tunnel(cfg, tunnel_id)
        #status_tunnel(cfg, tunnel_id, "STOPPED")
        #delete_tunnel(cfg, tunnel_id)
        #hosts = list(cfg.hosts.ap.values())
        #restart_gridftp(cfg, hosts)


# ------------------------------------------------------------------------------
# iPerf3 Base
def run_iperf_base(cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int,
    temp_file: str, 
    port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting iPerf3 Direct Tests -----", idx, total_runs)
    try:
        if not cfg.is_test:
            # launch statkit
            logging.info("BASE: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, "iperf_base", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        # start iperf server
        logging.info("BASE: Starting iperf server")
        start_iperf_server_base(cfg, listener_host, port, temp_file, "iperf_base", numa, output_dir)
        time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way

        # run iperf client
        logging.info("BASE: Starting iperf client")
        #start_iperf_client_base(cfg, initiator_host, cfg.listener_ip, port, parallel, arg, temp_file, "iperf_base", output_dir, timeout)
        start_iperf_client_base(cfg, initiator_host, cfg.listener_pub, port, parallel, arg, temp_file, "iperf_base", numa, output_dir, timeout)

        if not cfg.is_test:   
            logging.info("BASE: Recording the RTT")
            #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_base", output_dir)
            record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"BASE: Runtime Error: {e}") from e

    finally:
        if not cfg.is_test:
            logging.info("BASE: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
        cleanup_iperf(cfg)


# ------------------------------------------------------------------------------
# Rsync GST
def run_rsync_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, 
    temp_file: str, 
    port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    #listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting RSync GST Test -----", idx, total_runs)
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}"
    logging.info("RGST: Creating the tunnel on Localhost")
    tunnel_id = start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout)
    try:
        logging.info("RGST: Waiting for tunnel to get activated")
        status_tunnel(cfg, tunnel_id, "AWAITING_LISTENER")
        if not cfg.is_test:
            logging.info("RSYNC: Starting statkit monitoring")
            start_statkit(cfg, timeout, "rsync_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        # init listener env 
        logging.info("RGST: Bringing up the tunnel on Listener AP")
        init_listener_env(cfg, cfg.listener_ip, tunnel_id, port)
        # waiting till the tunnel gets activated
        status_tunnel(cfg, tunnel_id, "ACTIVE")
        # init initiator env + discover contact port
        logging.info("RGST: Bringing up the tunnel on Initiator AP")
        contact_port, ini_gw_ip, gw_port = init_initiator_env(cfg, tunnel_id)
        
        # start rsync daemon
        logging.info("RGST: Starting the rsync daemon on the host %s", listener_host.upper())
        start_rsync_daemon_gst(
            cfg, listener_host, 
            port, tunnel_id, 
            numa, output_dir, timeout,
            "rsync_gst", "transfer", "/tmp/temp_files")
    
        logging.info("RGST: Starting direct rsync transfer")
        start_rsync_transfer_gst(
            cfg, listener_host, initiator_host,
            tunnel_id, contact_port, parallel, arg,
            temp_file, numa, output_dir, timeout,
            "rsync_gst", "transfer", "/tmp/temp_files")

        if not cfg.is_test:
            logging.info("RGST: Recording RTT")        # it will run on the client
            #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
            record_ping(cfg, initiator_host, cfg.listener_pub, "rsync_gst", output_dir)

    except Exception as e:
        raise RuntimeError(f"RGST: Runtime Error: {e}") from e

    finally:
        if not cfg.is_test:
            logging.info("RGST: Stopping statkit monitoring")
            stop_statkit(cfg)
        stop_rsync_daemon(cfg, listener_host, "/tmp/temp_files")
        stop_tunnel(cfg, tunnel_id)
        status_tunnel(cfg, tunnel_id, "STOPPED")
        delete_tunnel(cfg, tunnel_id)


# ------------------------------------------------------------------------------
# Rsync Base
def run_rsync_base(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, temp_file: str, 
    listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int
) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting RSync Direct Test -----", idx, total_runs)
    try:
        if not cfg.is_test:
            logging.info("RSYNC: Starting statkit monitoring")
            start_statkit(cfg, timeout, "rsync_base", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        if encrypt == 1:
            logging.info("RSYNC: Starting direct rsync transfer")
            start_rsync_ssh(cfg, listener_host, initiator_host, temp_file, numa, output_dir, cfg.rsync_port, timeout,
            "/tmp/temp_files",
            )
        else:
            logging.info("RSYNC: Starting the rsync daemon on the host %s", listener_host.upper())
            start_rsync_daemon_base(
                cfg, listener_host, numa, output_dir, cfg.rsync_port, timeout,
                "rsync_base", 
                cfg.test, "/tmp/temp_files")
            logging.info("RSYNC: Starting direct rsync transfer")

            start_rsync_transfer_base(
                cfg, listener_host, initiator_host, temp_file, numa, output_dir, cfg.rsync_port, timeout,
                "rsync_base", 
                cfg.test, "/tmp/temp_files")
        
        if not cfg.is_test:
            logging.info("RSYNC: Recording RTT")        # it will run on the client
            #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
            record_ping(cfg, initiator_host, cfg.listener_pub, "rsync_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"RSYNC: Runtime Error: {e}") from e

    finally:
        if not cfg.is_test:
            logging.info("RSYNC: Stopping statkit monitoring")
            stop_statkit(cfg)
        stop_rsync_daemon(cfg, listener_host, "/tmp/temp_files")


# ------------------------------------------------------------------------------
# Globus Transfer
def run_globus_transfer(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, temp_file: str, 
    listener_host: str, initiator_host: str, numa: str, output_dir: str, 
    arg: int, 
    encrypt: int
    ) -> None:
    print("\n")
    logging.info("----- Tests: %d / %d ------- Strarting Globus Transfer Tests", idx, total_runs)
    ids = get_collection_id(cfg)
    initiator_collection_id, listener_collection_id = ids["initiator"], ids["listener"]
    transfer_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}"
    try:
        if not cfg.is_test:
            # launch statkit
            logging.info("GTR: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, "globus_gtr", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        # start globus transfer
        logging.info("GTR: Starting globus transfer")
        start_globus_transfer(cfg, listener_collection_id, initiator_collection_id, transfer_label, 
        arg, encrypt, temp_file, "globus_gtr", output_dir, timeout)
        if not cfg.is_test:
            # recording rtt
            logging.info("GTR: Recording the RTT")
            record_ping(cfg, initiator_host, cfg.listener_pub, "globus_gtr", output_dir)
        
    except Exception as e:
        raise RuntimeError(f"GTR: Runtime Error: {e}") from e

    finally:
        # TODO: check why monitor finishes before transfer!
        logging.info("GTR: Stopping the statkit monitoring on the hosts")
        if not cfg.is_test:
            stop_statkit(cfg)
            #hosts = list(cfg.hosts.ep.values())
            #restart_gridftp(cfg, hosts)


# ------------------------------------------------------------------------------
# APS Mini App GST
def run_mini_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, tomo_file: str, 
    parallel: int, arg: int, 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    #listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting APS mini app GST Test -----", idx, total_runs)
    tunnel_ids, tunnel_ports = [], []
    init_gw_ip = None
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    # if parallel > 3:
    #     logging.info("MGST: Parallel value is more than 3 which is the max number of tunnels; it will be set to 3")
    #     parallel = 3
    try:
        for i in range(parallel):
            logging.info("MGST: Creating the tunnel no %d tunnels on Localhost", i)
            tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}-parallel{i}"
            tunnel_ids.append(start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout))

        for tunnel in tunnel_ids:
            logging.info("MGST: Waiting for tunnels to get activated")
            status_tunnel(cfg, tunnel, "AWAITING_LISTENER")
            
        if not cfg.is_test:
            logging.info("MGST: Starting statkit monitoring")
            start_statkit(cfg, timeout, "mini_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        for i, tunnel in enumerate(tunnel_ids):
            logging.info("MGST: Bringing up the tunnels on Listener AP")
            init_listener_env(cfg, cfg.listener_ip, tunnel, start_port + i)

            # waiting till the tunnel gets activated
            status_tunnel(cfg, tunnel, "ACTIVE")

            # init initiator env + discover contact port
            logging.info("MGST: Bringing up the tunnel on Initiator AP")
            contact_port, ini_gw_ip, gw_port = init_initiator_env(cfg, tunnel)
            tunnel_ports.append(gw_port)

        logging.info("MGST: Starting APS mini app containers")
        start_mini_app(cfg, parallel, numa, "mini_gst", start_port, ini_gw_ip, tunnel_ports, tunnel_ids, output_dir, tomo_file, timeout, module_path="/tmp/temp_files")
        logging.info("MGST: Starting the APS mini app containers on the endpoints")

        if cfg.test == "transfer":
            wait_finish_transfer(cfg, parallel,"mini_gst", output_dir, timeout)
            stop_mini_containers(cfg, parallel,"mini_gst", output_dir, timeout)
        else:
            time.sleep(arg)
            stop_mini_containers(cfg, parallel,"mini_gst", output_dir, timeout)

        if not cfg.is_test:
            logging.info("MGST: Recording RTT")        # it will run on the client
            record_ping(cfg, initiator_host, cfg.listener_pub, "mini_gst", output_dir)

    except Exception as e:
        raise RuntimeError(f"MGST: Runtime Error: {e}") from e

    finally:
        if not cfg.is_test:
            logging.info("MGST: Stopping statkit monitoring")
            stop_statkit(cfg)
        stop_mini_containers(cfg, parallel, "mini_gst", output_dir, timeout)
        prune_containers(cfg, parallel, "mini_gst", output_dir, timeout)
        for tunnel in tunnel_ids:
            stop_tunnel(cfg, tunnel)
        for tunnel in tunnel_ids:
            status_tunnel(cfg, tunnel, "STOPPED")
            delete_tunnel(cfg, tunnel)
            
# ------------------------------------------------------------------------------
# APS Mini App Base        
def run_mini_base(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, temp_file: str, 
) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting APS Mini App Base Test -----", idx, total_runs)
    pass



# ------------------------------------------------------------------------------
# Main
def experiment_main(cfg: Config) -> None:
    blocks = cfg.blocks
    parallels = cfg.parallels if cfg.test == "stream" else [1]
    args = cfg.time_frames if cfg.test == "stream" else cfg.file_sizes
    numactl = cfg.numactl
    net_modes = build_net_modes(cfg.splice, cfg.encrypt)
    runs = cfg.run_num

    test_config = (
        (numa, block, parallel, arg, splice, encrypt, run)
        for numa in numactl
        for block in blocks
        for parallel in parallels
        for arg in args
        for splice, encrypt in net_modes
        for run in range(1, runs + 1)
    )

    last_block, last_splice, last_encrypt = None, None, None
    total_runs = (
        len(numactl)
        * len(blocks)
        * len(parallels)
        * len(args)
        * len(net_modes)
        * runs
    )

    total_tests = total_runs * len(cfg.app)

    for idx, (numa, block, parallel, arg, splice, encrypt, run) in enumerate(test_config, start=1):
        print("\n")
        logging.info(
            "--------------- Config: %d:%d / %d : NUMA %s / blocksize %s / arg %s / splice %s / encrypt %s / run %s ---------------",
            idx, idx * len(cfg.app), total_tests, numa, block, arg, splice, encrypt, run,
        )
        if not cfg.is_test:
            logging.info("SYS: Recording the system reports")
            sys_report_dir = str(Path(cfg.report_dir) / f"{numa}" / f"{cfg.tcp_buffer}" / f"{cfg.ring_buffer}" / "sys-info")
            #system_state_report(cfg, sys_report_dir)
            system_state_report(cfg, sys_report_dir)

        mode_dir = net_mode_dir(splice, encrypt)
        temp_file = f"{arg}G.bin"
        if cfg.test == "transfer":
            output_path = (
                Path(cfg.report_dir) / f"{numa}" / f"{cfg.tcp_buffer}" / f"{cfg.ring_buffer}" / f"B{block}" / f"P{parallel}" / f"S{arg}" / mode_dir / f"R{run}"
            )
            output_dir = str(output_path)
            #make_output(cfg, output_dir)
            make_file(cfg, arg, temp_file)

        elif cfg.test == "stream":
            output_path = (
                Path(cfg.report_dir) / f"{numa}" / f"{cfg.tcp_buffer}" / f"{cfg.ring_buffer}" / f"B{block}" / f"P{parallel}" / f"T{arg}" / mode_dir / f"R{run}"
            )
            output_dir = str(output_path)
        timeout = (arg * 120)

        if "iperf" in cfg.app:
            if block != last_block or splice != last_splice or encrypt != last_encrypt:
                logging.info("Applying GridFTP configuration: blocksize: %sM splice: %s encrypt: %s", block, splice, encrypt)
                gridftp_config(cfg, block, splice, encrypt, output_dir)
                last_block, last_splice, last_encrypt= block, splice, encrypt
                #hosts = list(cfg.hosts.ap.values())
                #restart_gridftp(cfg, hosts)
                time.sleep(cfg.sleep)
            logging.info("GTR: Recording the Gridftp configuration")
            gridftp_report(cfg, output_dir)
            #time.sleep(cfg.sleep)
            
            run_iperf_gst(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, #block=block, run=run, 
                parallel=parallel, arg=arg, temp_file=temp_file, port=cfg.tunnel_port,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                numa=numa,
                output_dir=output_dir,
            )
            #time.sleep(cfg.sleep)

        if "ibase" in cfg.app:
            run_iperf_base(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout,
                parallel=parallel, arg=arg, temp_file=temp_file, port=cfg.direct_port,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                numa=numa,
                output_dir=output_dir,
            )
            #time.sleep(cfg.sleep)


        if "rsync" in cfg.app and cfg.test == "transfer":
            if block != last_block or splice != last_splice or encrypt != last_encrypt:
                logging.info("Applying GridFTP configuration: blocksize: %sM splice: %s encrypt: %s", block, splice, encrypt)
                gridftp_config(cfg, block, splice, encrypt, output_dir)
                last_block, last_splice, last_encrypt= block, splice, encrypt
                time.sleep(cfg.sleep)
            logging.info("GTR: Recording the Gridftp configuration")
            gridftp_report(cfg, output_dir)
            
            run_rsync_gst(
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, 
                parallel=parallel, arg=arg, 
                temp_file=temp_file,
                port=cfg.rsync_port,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                numa=numa, output_dir=output_dir,
                encrypt=encrypt,
            )


        if "rbase" in cfg.app and cfg.test == "transfer":
            run_rsync_base(
                # cfg, idx=idx, total_runs=total_runs, timeout=timeout, temp_file=temp_file,
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, 
                #parallel=parallel, arg=arg, 
                temp_file=temp_file,
                #port=cfg.rsync_port,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                numa=numa,
                output_dir=output_dir,
                encrypt=encrypt,
            )
            #time.sleep(cfg.sleep)

        if "gtr" in cfg.app and cfg.test == "transfer":
            logging_gridftp(cfg, output_dir)
            logging.info("GTR: Changing the Gridftp log path")
            #restart_gridftp(cfg)
            #time.sleep(cfg.sleep)
            
            run_globus_transfer(
                # cfg, idx=idx, total_runs=total_runs, timeout=timeout, temp_file=temp_file,
                cfg, idx=idx, total_runs=total_runs, timeout=timeout, temp_file=temp_file,
                listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                numa=numa,
                output_dir=output_dir, 
                arg=arg, encrypt=encrypt,
            )
            #time.sleep(cfg.sleep)

        if "mini" in cfg.app and cfg.test == "stream":
            run_mini_gst(
                cfg,
                idx=idx,
                total_runs=total_runs,
                timeout=timeout,
                tomo_file=cfg.tomo_file,
                parallel=parallel,
                arg=arg,
                start_port=cfg.mini_port,
                listener_host=cfg.hosts.ep["listener"],
                initiator_host=cfg.hosts.ep["initiator"],
                numa=numa,
                output_dir=output_dir,
                encrypt=encrypt,
            )
            
        # if "mbase" in cfg.app:
        #     run_mini_base()

        time.sleep(cfg.sleep)
