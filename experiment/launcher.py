from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from config import Config

from utils import make_file, cleanup_file, start_statkit, stop_statkit
from utils import get_stream_id, start_tunnel, status_tunnel, stop_tunnel, delete_tunnel
from utils import init_listener_env, init_initiator_env
from utils import restart_gridftp, gridftp_config, gridftp_report, logging_gridftp
#from utils import start_iperf_server, start_iperf_client, cleanup_iperf
# from utils import start_iperf_server_base, start_iperf_client_base
# from utils import start_rsync_daemon_gst, start_rsync_transfer_gst
# from utils import start_rsync_daemon_base, start_rsync_transfer_base
# from utils import start_rsync_ssh, stop_rsync_daemon
#from utils import create_mini_yamls, start_mini_containers, wait_finish_transfer
# from utils import stop_mini_containers, prune_containers
#from utils import get_collection_id, start_globus_transfer
from utils import record_ping
from utils import build_net_modes, net_mode_dir
from sysconf import system_state_report
from apsmini import start_mini_app, wait_finish_transfer, stop_mini_containers, prune_containers
from iperf import start_iperf_server, start_iperf_client, start_iperf_server_base, start_iperf_client_base, cleanup_iperf
from rsync import stop_rsync_daemon, start_rsync_daemon_gst
from rsync import start_rsync_transfer_gst, start_rsync_daemon_base, start_rsync_transfer_base, start_rsync_ssh
from gtransfer import get_collection_id, start_globus_transfer, start_globus_transfer_multiple


# ------------------------------------------------------------------------------
# iPerf3 GST
def run_iperf_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, 
    files: list[str], 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    print("\n")
    logging.info("----- Tests: %d / %d ------- Strarting iPerf3 Tunnel Tests", idx, total_runs)
    stream_ids, listen_ports = [], []
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

    try:
        logging.info("IGST: Creating %d tunnels on Localhost", parallel)
        for i in range(parallel):
            logging.debug("IGST: Creating the tunnel # %d tunnels on Localhost", parallel)
            tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}-parallel{i}"
            new_tunnel_id = (start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout))
            stream_ids.append(new_tunnel_id)

        logging.info("IGST: Activating %d tunnels", parallel)
        for tunnel in stream_ids:
            logging.debug("IGST: Waiting for tunnel %s to get activated", tunnel)
            status_tunnel(cfg, tunnel, "AWAITING_LISTENER")
    
        if not cfg.is_test:
            # launch statkit
            logging.info("IGST: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, "iperf_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        # init listener env 
        logging.info("IGST: Bringing up %d tunnels on endpoints", parallel)
        for i, tunnel in enumerate(stream_ids):
            logging.debug("IGST: Bringing up the tunnel %s on Listener EP", tunnel)
            init_listener_env(cfg, cfg.listener_ip, tunnel, start_port + (i * 2))
            
            # waiting till the tunnel gets activated
            status_tunnel(cfg, tunnel, "ACTIVE")

            # init initiator env + discover contact port
            logging.debug("IGST: Bringing up the tunnel %s on Initiator EP", tunnel)
            contact_port, listen_ip, gw_port = init_initiator_env(cfg, tunnel)
            listen_ports.append(contact_port)
            logging.debug("DEBUG: The Tunnel Fake Port assigned to Tunnel ID %s is %s of total ports of %s on Gateway of IP %s", tunnel, contact_port, listen_ports, listen_ip)

        # start iperf server
        logging.info("IGST: Starting iperf server")
        start_iperf_server(cfg, listener_host, start_port, stream_ids, parallel, numa, output_dir, "iperf_gst", files, timeout, temp_dir="/tmp/temp_files")
        time.sleep(cfg.sleep)

        # run iperf client
        logging.info("IGST: Starting iperf client")        
        start_iperf_client(cfg, initiator_host, stream_ids, listen_ports, parallel, numa, arg, files, "iperf_gst", output_dir, timeout)        

        if not cfg.is_test:
            # recording rtt
            logging.info("IGST: Recording the RTT")
            record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_gst", output_dir)

    except Exception as e:
        raise RuntimeError(f"IGST: Runtime Error: {e}") from e

    finally:
        cleanup_iperf(cfg)
        if not cfg.is_test:
            logging.info("IGST: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
        logging.info("IGST: Stopping the tunnel(s)")
        for tunnel in stream_ids:
            stop_tunnel(cfg, tunnel)
        for tunnel in stream_ids:
            status_tunnel(cfg, tunnel, "STOPPED")
            delete_tunnel(cfg, tunnel)
    # finally:
    #     if not cfg.is_test:
    #         try:
    #             stop_statkit(cfg)
    #         except Exception:
    #             logging.exception("IGST: Failed to stop statkit")
    #     try:
    #         cleanup_iperf(cfg)
    #     except Exception:
    #         logging.exception("IGST: Failed to clean up iperf processes")
    #     for tunnel in stream_ids:
    #         try:
    #             stop_tunnel(cfg, tunnel)
    #         except Exception:
    #             logging.exception("IGST: Failed to stop tunnel %s", tunnel)
    #     for tunnel in stream_ids:
    #         try:
    #             status_tunnel(cfg, tunnel, "STOPPED")
    #             delete_tunnel(cfg, tunnel)
    #         except Exception:
    #             logging.exception("IGST: Failed to delete tunnel %s", tunnel)


# ------------------------------------------------------------------------------
# iPerf3 Base
def run_iperf_base(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int,
    files: list[str], 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    
    print("\n")
    logging.info("----- Test %d / %d: Starting iPerf3 Direct Tests -----", idx, total_runs)
    stream_ids, listen_ports = [], []
    listen_ip = cfg.listener_pub
    try:
        if not cfg.is_test:
            # launch statkit
            logging.info("IBASE: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, "iperf_base", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        for i in range(parallel):
            listen_ports.append(start_port + (i * 2))
            stream_ids.append(listen_ip)

        # start iperf server
        logging.info("IBASE: Starting iperf server(s)")
        start_iperf_server_base(cfg, listener_host, start_port, stream_ids, parallel, numa, output_dir, "iperf_base", files, timeout, temp_dir="/tmp/temp_files")
        time.sleep(cfg.sleep)           # it takes more for them to initiates! TODO: find a better way

        # run iperf client
        logging.info("IBASE: Starting iperf client")
        #start_iperf_client_base(cfg, initiator_host, cfg.listener_ip, port, parallel, arg, temp_file, "iperf_base", output_dir, timeout)
        start_iperf_client_base(cfg, initiator_host, cfg.listener_pub, stream_ids, listen_ports, parallel, numa, arg, files, "iperf_base", output_dir, timeout)

        if not cfg.is_test:   
            logging.info("IBASE: Recording the RTT")
            #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_base", output_dir)
            record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"IBASE: Runtime Error: {e}") from e

    finally:
        cleanup_iperf(cfg)
        if not cfg.is_test:
            logging.info("IBASE: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)


# ------------------------------------------------------------------------------
# Rsync GST
def run_rsync_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, files: list[str], rsync_port: int, 
    listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting RSync GST Test -----", idx, total_runs)
    stream_ids, listen_ports = [], []
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]

    try:
        logging.info("RGST: Creating the tunnel on Localhost")
        for i in range(parallel):
            logging.debug("IGST: Creating the tunnel # %d tunnels on Localhost", parallel)
            tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}-parallel{i}"
            new_tunnel_id = (start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout))
            stream_ids.append(new_tunnel_id)

        logging.info("RGST: Activating %d tunnels", parallel)
        for tunnel in stream_ids:
            logging.debug("RGST: Waiting for tunnel %s to get activated", tunnel)
            status_tunnel(cfg, tunnel, "AWAITING_LISTENER")

        if not cfg.is_test:
            logging.info("RSYNC: Starting statkit monitoring")
            start_statkit(cfg, timeout, "rsync_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        # init listener env 
        logging.info("RGST: Bringing up %d tunnels on endpoints", parallel)
        for i, tunnel in enumerate(stream_ids):
            logging.debug("RGST: Bringing up the tunnel %s on Listener EP", tunnel)
            init_listener_env(cfg, cfg.listener_ip, tunnel, rsync_port + (i * 2))
            
            # waiting till the tunnel gets activated
            status_tunnel(cfg, tunnel, "ACTIVE")

            # init initiator env + discover contact port
            logging.debug("IGST: Bringing up the tunnel %s on Initiator EP", tunnel)
            contact_port, listen_ip, gw_port = init_initiator_env(cfg, tunnel)
            listen_ports.append(contact_port)
            logging.debug("DEBUG: The Tunnel Fake Port assigned to Tunnel ID %s is %s of total ports of %s on Gateway of IP %s", tunnel, contact_port, listen_ports, listen_ip)
        
        # start rsync daemon
        logging.info("RGST: Starting the rsync daemon on the host %s", listener_host.upper())
        deamon_cps = start_rsync_daemon_gst(
            cfg, listener_host, 
            rsync_port, stream_ids, parallel,
            numa, output_dir, timeout,
            "rsync_gst", "transfer", "/tmp/temp_files"
        )
    
        logging.info("RGST: Starting direct rsync transfer")
        start_rsync_transfer_gst(
            cfg, listener_host, initiator_host, listen_ip,
            stream_ids, listen_ports, parallel, numa, arg,
            files, output_dir, timeout,
            "rsync_gst", "transfer", "/tmp/temp_files"
        )

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
        stop_rsync_daemon(cfg, listener_host, parallel, "/tmp/temp_files")
        logging.info("RGST: Stopping the tunnel(s)")
        for tunnel in stream_ids:
            stop_tunnel(cfg, tunnel)
        for tunnel in stream_ids:
            status_tunnel(cfg, tunnel, "STOPPED")
            delete_tunnel(cfg, tunnel)


# ------------------------------------------------------------------------------
# Rsync Base
def run_rsync_base(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, files: list[str], rsync_port: int, 
    listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int
) -> None:

    print("\n")
    logging.info("----- Test %d / %d: Starting RSync Direct Test -----", idx, total_runs)
    stream_ids, listen_ports = [], []
    listen_ip = cfg.listener_pub
    try:
        for i in range(parallel):
            listen_ports.append(rsync_port + (i * 2))
            stream_ids.append(listen_ip)

        if not cfg.is_test:
            logging.info("RBASE: Starting statkit monitoring")
            start_statkit(cfg, timeout, "rsync_base", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        if encrypt == 1:
            logging.info("RBASE: Starting direct rsync transfer")
            start_rsync_ssh(
                cfg, listener_host, initiator_host, listen_ip,
                stream_ids, listen_ports, parallel, numa, arg,
                files, output_dir, timeout,
                "rsync_base", "transfer", "/tmp/temp_files")
        else:
            logging.info("RBASE: Starting the rsync daemon on the host %s", listener_host.upper())
            start_rsync_daemon_base(
                cfg, listener_host, 
                rsync_port, stream_ids, parallel,
                numa, output_dir, timeout,
                "rsync_base", "transfer", "/tmp/temp_files")
            time.sleep(cfg.sleep)

            logging.info("RBASE: Starting direct rsync transfer")
            start_rsync_transfer_base(
                cfg, listener_host, initiator_host, listen_ip,
                stream_ids, listen_ports, parallel, numa, arg,
                files, output_dir, timeout,
                "rsync_base", "transfer", "/tmp/temp_files")

        if not cfg.is_test:
            logging.info("RBASE: Recording RTT")        # it will run on the client
            #record_ping(cfg, initiator_host, cfg.listener_ip, "iperf_gst", output_dir)
            record_ping(cfg, initiator_host, cfg.listener_pub, "rsync_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"RBASE: Runtime Error: {e}") from e

    finally:
        if not cfg.is_test:
            logging.info("RBASE: Stopping statkit monitoring")
            stop_statkit(cfg)
        stop_rsync_daemon(cfg, listener_host, parallel, "/tmp/temp_files")


# ------------------------------------------------------------------------------
# Globus Transfer
def run_globus_transfer(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, files: list[str],
    listener_host: str, initiator_host: str, numa: str, output_dir: str,
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
        # start_globus_transfer(
        #     cfg, listener_collection_id, initiator_collection_id, transfer_label, 
        #     parallel,
        #     arg, encrypt, files, "globus_gtr_s", output_dir, timeout
        # )
        start_globus_transfer_multiple(
            cfg, listener_collection_id, initiator_collection_id, transfer_label, 
            parallel,
            arg, encrypt, files, "globus_gtr", output_dir, timeout
        )
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
    #tunnel_ids, tunnel_ports = [], []
    #init_gw_ip = None
    stream_ids, listen_ports = [], []
    listen_ip = None
    ids = get_stream_id(cfg)
    initiator_stream_id, listener_stream_id = ids["initiator"], ids["listener"]
    # if parallel > 3:
    #     logging.info("MGST: Parallel value is more than 3 which is the max number of tunnels; it will be set to 3")
    #     parallel = 3
    try:
        for i in range(parallel):
            logging.info("MGST: Creating the tunnel no %d tunnels on Localhost", i)
            tunnel_label = f"{cfg.lease.replace(' ', '_')}-{cfg.test.replace(' ', '_')}-idx{idx}-tot{total_runs}-parallel{i}"
            #tunnel_ids.append(start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout))
            #stream_ids.append(start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout))
            new_tunnel_id = (start_tunnel(cfg, initiator_stream_id, listener_stream_id, tunnel_label, timeout))
            stream_ids.append(new_tunnel_id)
            logging.debug("DEBUG:: The Tunnel ID is %s and the total Tunnel IDs are %s", new_tunnel_id, stream_ids)

        #for tunnel in tunnel_ids:
        for tunnel in stream_ids:
            logging.info("MGST: Waiting for tunnels to get activated")
            status_tunnel(cfg, tunnel, "AWAITING_LISTENER")
            
        if not cfg.is_test:
            logging.info("MGST: Starting statkit monitoring")
            start_statkit(cfg, timeout, "mini_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        #for i, tunnel in enumerate(tunnel_ids):
        for i, tunnel in enumerate(stream_ids):
            logging.info("MGST: Bringing up the tunnels on Listener AP")
            init_listener_env(cfg, cfg.listener_ip, tunnel, start_port + (i * 2))

            # waiting till the tunnel gets activated
            status_tunnel(cfg, tunnel, "ACTIVE")

            # init initiator env + discover contact port
            logging.info("MGST: Bringing up the tunnel on Initiator AP")
            #contact_port, init_gw_ip, gw_port = init_initiator_env(cfg, tunnel)
            #tunnel_ports.append(gw_port)
            contact_port, listen_ip, gw_port = init_initiator_env(cfg, tunnel)
            listen_ports.append(gw_port)
            logging.debug("DEBUG: The Tunnel Port assigned to Tunnel ID %s is %s of total ports of %s on Gateway of IP %s", tunnel, gw_port, listen_ports, listen_ip)

        logging.info("MGST: Starting the APS mini app containers on the endpoints")
        #start_mini_app(cfg, parallel, numa, "mini_gst", start_port, init_gw_ip, tunnel_ports, tunnel_ids, output_dir, tomo_file, timeout, module_path="/tmp/temp_files")
        start_mini_app(cfg, parallel, numa, "mini_gst", start_port, listen_ip, listen_ports, stream_ids, output_dir, tomo_file, timeout, module_path="/tmp/temp_files")

        if cfg.test == "stream":
            time.sleep(arg)
            stop_mini_containers(cfg, parallel,"mini_gst", output_dir, timeout)
        # else:
        #     wait_finish_transfer(cfg, parallel,"mini_gst", output_dir, timeout)
        #     stop_mini_containers(cfg, parallel,"mini_gst", output_dir, timeout)

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
        # for tunnel in tunnel_ids:
        #     stop_tunnel(cfg, tunnel)
        # for tunnel in tunnel_ids:
        #     status_tunnel(cfg, tunnel, "STOPPED")
        #     delete_tunnel(cfg, tunnel)
        for tunnel in stream_ids:
            stop_tunnel(cfg, tunnel)
        for tunnel in stream_ids:
            status_tunnel(cfg, tunnel, "STOPPED")
            delete_tunnel(cfg, tunnel)
            
# ------------------------------------------------------------------------------
# APS Mini App Base        
def run_mini_base(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, tomo_file: str, 
    parallel: int, arg: int, 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    #listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    print("\n")
    logging.info("----- Test %d / %d: Starting APS mini app GST Test -----", idx, total_runs)
    stream_ids, listen_ports = [], []
    listen_ip = cfg.listener_pub
    try:
        if not cfg.is_test:
            logging.info("MBASE: Starting statkit monitoring")
            start_statkit(cfg, timeout, "mini_base", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        #for i, tunnel in enumerate(tunnel_ids):
        for i in range(parallel):
            listen_ports.append(start_port + (i * 2))
            stream_ids.append(listen_ip)

        logging.info("MBASE: Starting APS mini app containers")
        start_mini_app(cfg, parallel, numa, "mini_base", start_port, listen_ip, listen_ports, stream_ids, output_dir, tomo_file, timeout, module_path="/tmp/temp_files")
        logging.info("MBASE: Starting the APS mini app containers on the endpoints")

        if cfg.test == "stream":
            time.sleep(arg)
            stop_mini_containers(cfg, parallel,"mini_base", output_dir, timeout)

        if not cfg.is_test:
            logging.info("MBASE: Recording RTT")        # it will run on the client
            record_ping(cfg, initiator_host, cfg.listener_pub, "mini_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"MBASE: Runtime Error: {e}") from e

    finally:
        if not cfg.is_test:
            logging.info("MBASE: Stopping statkit monitoring")
            stop_statkit(cfg)
        stop_mini_containers(cfg, parallel, "mini_base", output_dir, timeout)
        prune_containers(cfg, parallel, "mini_base", output_dir, timeout)


# ------------------------------------------------------------------------------
# Main
def experiment_main(cfg: Config) -> None:
    blocks = cfg.blocks
    parallels = cfg.parallels #if cfg.test == "stream" else [1]
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
    test_apps = ["iperf", "ibase", "mini", "mbase"] if cfg.test == "stream" else ["iperf", "ibase", "rsync", "rbase", "gtr"]
    total_tests = total_runs * len(cfg.test)
    for idx, (numa, block, parallel, arg, splice, encrypt, run) in enumerate(test_config, start=1):
        created_files = False
        mode_dir = net_mode_dir(splice, encrypt)
        context = (
            f"test={cfg.test}, config={idx}/{total_runs}, "
            f"numa={numa}, block={block}, parallel={parallel}, "
            f"arg={arg}, splice={splice}, encrypt={encrypt}, run={run}"
        )
        try:
            print("\n")
            logging.info(
                "--------------- Config: %d:%d / %d : NUMA %s / blocksize %s / arg %s / splice %s / encrypt %s / run %s / parallel %s ---------------",
                idx, idx * len(test_apps), total_tests, numa, block, arg, splice, encrypt, run, parallel
            )
            test_idx, files = 0, []
            if idx == 1 and (not(cfg.is_test)):
                logging.info("SYS: Recording the system reports")
                sys_dir = (Path(cfg.report_dir) / f"{cfg.test}" / f"{numa}" / f"{cfg.tcp_buffer}" / f"{cfg.ring_buffer}" / "sys-info")
                sys_report_dir = str(sys_dir)
                logging.debug("THE SYSCONFIG DIR IS %s ", sys_report_dir)
                system_state_report(cfg, sys_report_dir)

            files = [f"file{i}.bin" for i in range(parallel)]

            if cfg.test == "transfer":
                output_path = (
                    Path(cfg.report_dir) / f"{cfg.test}" / f"{numa}" / f"{cfg.tcp_buffer}" / f"{cfg.ring_buffer}" / f"B{block}" / f"P{parallel}" / f"S{arg}" / mode_dir / f"R{run}"
                )
                output_dir = str(output_path)
                make_file(cfg, parallel, arg, files)
                created_files = True

            elif cfg.test == "stream":
                output_path = (
                    Path(cfg.report_dir) / f"{cfg.test}" / f"{numa}" / f"{cfg.tcp_buffer}" / f"{cfg.ring_buffer}" / f"B{block}" / f"P{parallel}" / f"T{arg}" / mode_dir / f"R{run}"
                )
                output_dir = str(output_path)
            timeout = (arg * 120)

            if "iperf" in cfg.app:
                test_idx += 1
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

                start_port = cfg.encrypt_port if encrypt else cfg.tunnel_port
                run_iperf_gst(
                    #cfg, idx=idx, total_runs=total_runs, timeout=timeout, #block=block, run=run, 
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files, start_port=start_port,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir,
                    encrypt=encrypt
                )
                #time.sleep(cfg.sleep)

            if "ibase" in cfg.app:
                test_idx += 1
                start_port = cfg.direct_port
                run_iperf_base(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files, start_port=start_port,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt
                )
                #time.sleep(cfg.sleep)


            if "rsync" in cfg.app and cfg.test == "transfer":
                test_idx += 1
                if block != last_block or splice != last_splice or encrypt != last_encrypt:
                    logging.info("Applying GridFTP configuration: blocksize: %sM splice: %s encrypt: %s", block, splice, encrypt)
                    gridftp_config(cfg, block, splice, encrypt, output_dir)
                    last_block, last_splice, last_encrypt= block, splice, encrypt
                    time.sleep(cfg.sleep)
                logging.info("GTR: Recording the Gridftp configuration")
                gridftp_report(cfg, output_dir)
                
                run_rsync_gst(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files, rsync_port=cfg.rsync_port,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt,
                )


            if "rbase" in cfg.app and cfg.test == "transfer":
                test_idx += 1
                rsync_port = cfg.rsync_port
                run_rsync_base(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files, rsync_port=rsync_port,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt,
                )
                #time.sleep(cfg.sleep)

            if "gtr" in cfg.app and cfg.test == "transfer":
                test_idx += 1
                logging_gridftp(cfg, output_dir)
                logging.info("GTR: Changing the Gridftp log path")
                #restart_gridftp(cfg)
                #time.sleep(cfg.sleep)
                
                run_globus_transfer(
                    # cfg, idx=idx, total_runs=total_runs, timeout=timeout, temp_file=temp_file,
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt,
                )
                #time.sleep(cfg.sleep)

            if "mini" in cfg.app and cfg.test == "stream":
                test_idx += 1
                start_port = cfg.mini_port
                run_mini_gst(
                    cfg,
                    idx=test_idx,
                    #total_runs=total_runs,
                    total_runs=total_tests,
                    timeout=timeout,
                    tomo_file=cfg.tomo_file,
                    parallel=parallel,
                    arg=arg,
                    start_port=start_port,
                    listener_host=cfg.hosts.ep["listener"],
                    initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa,
                    output_dir=output_dir,
                    encrypt=encrypt,
                )
                
            if "mbase" in cfg.app and cfg.test == "stream":
                test_idx += 1
                start_port = cfg.mini_port
                run_mini_base(
                    cfg,
                    idx=test_idx,
                    #total_runs=total_runs,
                    total_runs=total_tests,
                    timeout=timeout,
                    tomo_file=cfg.tomo_file,
                    parallel=parallel,
                    arg=arg,
                    start_port=start_port,
                    listener_host=cfg.hosts.ep["listener"],
                    initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa,
                    output_dir=output_dir,
                    encrypt=encrypt,
                )

            time.sleep(cfg.sleep)

        except Exception as exc:
            logging.exception(
                "EXPERIMENT: Configuration failed: %s",
                context,
            )
            raise RuntimeError(
                f"Experiment configuration failed: {context}: {exc}"
            ) from exc

        finally:
            if created_files:
                try:
                    cleanup_file(cfg)
                except Exception:
                    logging.exception(
                        "EXPERIMENT: File cleanup failed: %s",
                        context,
                    )