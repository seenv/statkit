from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from config import Config

from utils import make_file, cleanup_file, start_statkit, stop_statkit
from gstreams import get_stream_id, start_tunnel, status_tunnel, stop_tunnel, delete_tunnel
from gstreams import start_globus_streams, init_listener_env, init_initiator_env
from gstreams import check_gridftp_config, restart_gridftp, gridftp_config, gridftp_report, logging_gridftp
from utils import record_ping
from utils import build_net_modes, net_mode_dir
from sysconf import system_state_report
from apsmini import start_mini_app, wait_finish_transfer, stop_mini_containers, prune_containers
from iperf import start_iperf_server, start_iperf_client, start_iperf_server_base, start_iperf_client_base, cleanup_iperf
from rsync import stop_rsync_daemon, start_rsync_daemon_gst
from rsync import start_rsync_transfer_gst, start_rsync_daemon_base, start_rsync_transfer_base, start_rsync_ssh
from gtransfer import get_collection_id, start_globus_transfer, start_globus_transfer_multiple
from scistream import start_scistream, stop_scistream
from iperf import start_iperf_server_scistream, start_iperf_client_scistream

# ------------------------------------------------------------------------------
# iPerf3 GST
def run_iperf_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, 
    files: list[str], 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    logging.info("")
    logging.info("--------------- Tests: %d / %d ------- Strarting iPerf3 GST Tests ---------------", idx, total_runs)
    stream_ids, listen_ports = [], []

    try:
        stream_ids, listen_ports, listen_ip = start_globus_streams(cfg, parallel, start_port, "iperf_gst", idx, timeout)
        
        if not cfg.is_test:
            # launch statkit
            logging.info("IGST: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, "iperf_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

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


# ------------------------------------------------------------------------------
# iPerf3 Base
def run_iperf_base(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int,
    files: list[str], 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    
    logging.info("")
    logging.info("--------------- Test %d / %d: Starting iPerf3 Direct Tests ---------------", idx, total_runs)
    stream_ids, listen_ports = [], []
    listen_ip = cfg.listener_pub
    try:
        if not cfg.is_test:
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
        start_iperf_client_base(cfg, initiator_host, cfg.listener_pub, stream_ids, listen_ports, parallel, numa, arg, files, "iperf_base", output_dir, timeout)

        if not cfg.is_test:   
            logging.info("IBASE: Recording the RTT")
            record_ping(cfg, initiator_host, cfg.listener_pub, "iperf_base", output_dir)

    except Exception as e:
        raise RuntimeError(f"IBASE: Runtime Error: {e}") from e

    finally:
        cleanup_iperf(cfg)
        if not cfg.is_test:
            logging.info("IBASE: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)


# ------------------------------------------------------------------------------
# iPerf3 SciStream
def run_iperf_scistream(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int,
    files: list[str], 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int, app_tag: str,
) -> None:
    
    logging.info("")
    logging.info("--------------- Test %d / %d: Starting iPerf3 Scistream Tests ---------------", idx, total_runs)
    stream_ids, listen_ports = [], []
    listen_ip = cfg.listener_pub

    try:
        logging.info("ISCI: Creating %d SciStream tunnels ", parallel)
        stream_ids, listen_ap_ports, initiate_ap_ports, listen_ep_ports, initiate_ep_ports = start_scistream(cfg, encrypt, parallel, timeout)

        if not cfg.is_test:
            logging.info("ISCI: Starting the statkit monitoring on the hosts")
            start_statkit(cfg, timeout, app_tag, output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)
        
        # start iperf server
        logging.info("ISCI: Starting iperf server(s)")
        start_iperf_server_scistream(cfg, listener_host, listen_ep_ports, stream_ids, parallel, numa, output_dir, app_tag, files, timeout, temp_dir="/tmp/temp_files")
        time.sleep(cfg.sleep)

        # run iperf client
        logging.info("ISCI: Starting iperf client")
        start_iperf_client_scistream(cfg, initiator_host, cfg.initiator_ap_ip, stream_ids, initiate_ap_ports, parallel, numa, arg, files, app_tag, output_dir, timeout)

        if not cfg.is_test:   
            logging.info("ISCI: Recording the RTT")
            record_ping(cfg, initiator_host, cfg.listener_pub, app_tag, output_dir)

    except Exception as e:
        raise RuntimeError(f"ISCI: Runtime Error: {e}") from e

    finally:
        cleanup_iperf(cfg)
        stop_scistream(cfg)
        if not cfg.is_test:
            logging.info("ISCI: Stopping the statkit monitoring on the hosts")
            stop_statkit(cfg)
 
 
# ------------------------------------------------------------------------------
# Rsync GST
def run_rsync_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, 
    parallel: int, arg: int, files: list[str], start_port: int, 
    listener_host: str, initiator_host: str, numa: str, output_dir: str,
    encrypt: int,
) -> None:
    logging.info("")
    logging.info("--------------- Test %d / %d: Starting Rsync GST Test ---------------", idx, total_runs)
    stream_ids, listen_ports = [], []

    try:
        stream_ids, listen_ports, listen_ip = start_globus_streams(cfg, parallel, start_port, "rsync_gst", idx, timeout)
        
        if not cfg.is_test:
            logging.info("RSYNC: Starting statkit monitoring")
            start_statkit(cfg, timeout, "rsync_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        # start rsync daemon
        logging.info("RGST: Starting the rsync daemon on the host %s", listener_host.upper())
        deamon_cps = start_rsync_daemon_gst(
            cfg, listener_host, 
            start_port, stream_ids, parallel,
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

    logging.info("")
    logging.info("--------------- Test %d / %d: Starting RSync Direct Test ---------------", idx, total_runs)
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
    logging.info("")
    logging.info("--------------- Tests: %d / %d ------- Strarting Globus Transfer Tests ---------------", idx, total_runs)
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


# ------------------------------------------------------------------------------
# APS Mini App GST
def run_mini_gst(
    cfg: Config, *, idx: int, total_runs: int, timeout: int, tomo_file: str, parallel: int, arg: int, 
    start_port: int, listener_host: str, initiator_host: str, numa: str, output_dir: str, encrypt: int,
) -> None:
    logging.info("")
    logging.info("--------------- Test %d / %d: Starting APS mini app GST Test ---------------", idx, total_runs)
    stream_ids, listen_ports = [], []

    try:
        stream_ids, listen_ports, listen_ip = start_globus_streams(cfg, parallel, start_port, "mini_gst", idx, timeout)

        if not cfg.is_test:
            logging.info("MGST: Starting statkit monitoring")
            start_statkit(cfg, timeout, "mini_gst", output_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

        logging.info("MGST: Starting the APS mini app containers on the endpoints")
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
    encrypt: int,
) -> None:
    logging.info("")
    logging.info("--------------- Test %d / %d: Starting APS mini app GST Test ---------------", idx, total_runs)
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
    total_runs = (len(numactl) * len(blocks) * len(parallels) * len(args) * len(net_modes) * runs)
    tests_per_config = sum(
        (
            "iperf" in cfg.app,
            "ibase" in cfg.app,
            "sperf" in cfg.app,
            "rsync" in cfg.app and cfg.test == "transfer",
            "rbase" in cfg.app and cfg.test == "transfer",
            "gtr" in cfg.app and cfg.test == "transfer",
            "mini" in cfg.app and cfg.test == "stream",
            "mbase" in cfg.app and cfg.test == "stream",
        )
    )

    if tests_per_config == 0:
        raise ValueError(
            f"No applications can run for test mode {cfg.test!r}. "
            f"Selected applications: {list(cfg.app)}"
        )

    total_tests = total_runs * tests_per_config
    for idx, (numa, block, parallel, arg, splice, encrypt, run) in enumerate(test_config, start=1):
        created_files = False
        mode_dir = net_mode_dir(splice, encrypt)
        context = (
            f"test={cfg.test}, config={idx}/{total_runs}, "
            f"numa={numa}, block={block}, parallel={parallel}, "
            f"arg={arg}, splice={splice}, encrypt={encrypt}, run={run}"
        )
        try:
            test_idx, files = ((idx - 1) * tests_per_config), []
            logging.info("")
            logging.info(
                "--------------- Test: %s: No %d - %d / NUMA %s / blocksize %s / parallel %s / arg %s / splice %s / encrypt %s / run %s ---------------",
                cfg.test.capitalize(), test_idx, total_tests, numa, block, parallel, arg, splice, encrypt, run, 
            )
            logging.info("")

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

            timeout = (arg * 120) * 10

            if "iperf" in cfg.app:
                    test_idx += 1
                    last_block, last_splice, last_encrypt = check_gridftp_config(
                        cfg, block, splice, encrypt, last_block, last_splice, last_encrypt, output_dir
                    )
                    start_port = cfg.encrypt_port if encrypt else cfg.tunnel_port
                    run_iperf_gst(
                        cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                        parallel=parallel, arg=arg, files=files, start_port=start_port,
                        listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                        numa=numa, output_dir=output_dir,
                        encrypt=encrypt
                    )
                    time.sleep(cfg.sleep)

            if "ibase" in cfg.app:
                    test_idx += 1
                    start_port = cfg.direct_port
                    run_iperf_base(
                        cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                        parallel=parallel, arg=arg, files=files, start_port=start_port,
                        listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                        numa=numa, output_dir=output_dir, encrypt=encrypt
                    )
                    time.sleep(cfg.sleep)

            if "sperf" in cfg.app:
                    test_idx += 1   
                    run_iperf_scistream(
                        cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                        parallel=parallel, arg=arg, files=files, start_port=cfg.inbound_ports[0],
                        listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                        numa=numa, output_dir=output_dir, encrypt=encrypt, app_tag="iperf_sci"
                    )
                    time.sleep(cfg.sleep)
                
            if "rsync" in cfg.app and cfg.test == "transfer":
                test_idx += 1
                last_block, last_splice, last_encrypt = check_gridftp_config(
                    cfg, block, splice, encrypt, last_block, last_splice, last_encrypt, output_dir
                )
                run_rsync_gst(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files, start_port=cfg.rsync_port,
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
                time.sleep(cfg.sleep)

            if "gtr" in cfg.app and cfg.test == "transfer":
                test_idx += 1
                logging_gridftp(cfg, output_dir)
                logging.info("GTR: Changing the Gridftp log path")
                time.sleep(cfg.sleep)
                
                run_globus_transfer(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    parallel=parallel, arg=arg, files=files,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt,
                )
                time.sleep(cfg.sleep)

            if "mini" in cfg.app and cfg.test == "stream":
                test_idx += 1
                last_block, last_splice, last_encrypt = check_gridftp_config(
                    cfg, block, splice, encrypt, last_block, last_splice, last_encrypt, output_dir
                )

                start_port = cfg.mini_port
                run_mini_gst(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    tomo_file=cfg.tomo_file, parallel=parallel, arg=arg, start_port=start_port,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt,
                )
                
            if "mbase" in cfg.app and cfg.test == "stream":
                test_idx += 1
                start_port = cfg.mini_port
                run_mini_base(
                    cfg, idx=test_idx, total_runs=total_tests, timeout=timeout,
                    tomo_file=cfg.tomo_file, parallel=parallel, arg=arg, start_port=start_port,
                    listener_host=cfg.hosts.ep["listener"], initiator_host=cfg.hosts.ep["initiator"],
                    numa=numa, output_dir=output_dir, encrypt=encrypt,
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
            idx = 1
        #     if created_files:
        #         try:
        #             cleanup_file(cfg)
        #         except Exception:
        #             logging.exception(
        #                 "EXPERIMENT: File cleanup failed: %s",
        #                 context,
        #             )