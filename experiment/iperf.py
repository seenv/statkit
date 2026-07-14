import logging
import shlex
import re
import subprocess
from typing import Sequence

from remote import run_subprocess, popen_subprocess
from utils import parse_size_to_bytes, get_numa_node, take_cpus
from config import Config

# -------------------------------------------------------------------------------
# iPerf3 GST
def start_iperf_server(
    cfg: Config, 
    host: str,
    start_port: int,
    stream_ids: Sequence[str],
    parallel: int, 
    numa: str,
    out_dir: str, 
    app: str, 
    file: str,
    timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> subprocess.Popen[str]:
#def start_iperf_server(cfg: Config, host: str, port: int, tunnel_id: str, file: str, app: str, numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    launch_processes = []
    extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)}" if cfg.test == "transfer" else ""

    for i, stream_id in enumerate(stream_ids):
        # numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
        # numa_node, numa_cpus = get_numa_node(cfg, host, dev) if numa == "numa" else (None, "")
        # #cpu_count = max(2, parallel)
        # #selected_cpus = take_cpus(numa_cpus, cpu_count) if numa == "numa" else ""
        # numa_prefix = (
        #     f"numactl --physcpubind={shlex.quote(numa_cpus)} --membind={numa_node} "
        #     #f"numactl --physcpubind={shlex.quote(selected_cpus)} --membind={numa_node} "
        #     if numa == "numa"
        #     else ""
        # )
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-time.log "    #f"numactl --cpunodebind=0 --preferred=0 "
            f"globus-streams-launch "
            f"-p {start_port + i} {shlex.quote(stream_id)} "                                    #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -s -p {start_port + i} -1 --timestamps --forceflush "                      #f"iperf3 -s -B {cfg.listener_ip} -p {port} -1 --timestamps  --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        launch_processes.append(cp)
        logging.debug("IGST: Started iperf3 server with tunnel ID %s and port of %d on host %s", stream_id, (start_port + i), host.upper())


def start_iperf_client(
    cfg: Config, host: str, 
    stream_ids: Sequence[str], listen_ports: Sequence[int],
    parallel: int, numa: str, arg: int, file: str, app: str, out_dir: str, timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
# def start_iperf_client(cfg: Config, host: str, tunnel_id: str, contact_port: int, parallel: int, arg: int, file: str, app: str, numa: str, out_dir: str, timeout: int, temp_dir: str = "/tmp/temp_files", check: bool = True) -> subprocess.CompletedProcess[str]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")
    
    processes = []
    throughput, bytes_transferred, retransmissions = 0, 0, 0
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else 0
    #extra_arg = f"-Z -R -n {arg}G -F {shlex.quote(temp_dir)}/{shlex.quote(file)} " if cfg.test == "transfer" else f"-P {parallel} -i 10 -O 10 -Z -R -t {arg} "    
    # extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)} -n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    extra_arg = f"-n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        #cp = run_subprocess(
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}time.log "    #f"numactl --cpunodebind=0 --preferred=0 "
            "globus-streams-launch "
            f"{shlex.quote(stream_id)} "                                                    #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -c globus.{shlex.quote(stream_id)} -p {listen_port} "                 #f"iperf3 -c globus.{shlex.quote(tunnel_id)} -B {cfg.initiator_ip} -p {contact_port} "
            #f"-Z -R -P {parallel} --timestamps --forceflush "
            f"-Z -R -P 1 --timestamps --forceflush "
            f"{extra_arg} "
            # f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json && "
            # f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}.json ",
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
            # timeout=timeout,
        )
        processes.append(cp)

    for cp in processes:
        stdout, stderr = cp.communicate()
        if cp.returncode != 0:
            logging.error("IGST: Process failed: %s", stderr)

        tail = "\n".join(stdout.splitlines()[-29:-21])
        logging.debug("IGST: iPerf log on %s %s", host.upper(), tail)
        seconds = float(re.search(r'"seconds":\s*([\d.]+)', tail).group(1))
        size_bytes = int(re.search(r'"bytes":\s*(\d+)', tail).group(1))
        bits_per_second = float(re.search(r'"bits_per_second":\s*([\d.]+)', tail).group(1))
        retransmits = int(re.search(r'"retransmits":\s*(\d+)', tail).group(1))
        logging.debug(
            "iPerf3 log %s Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size: %.4f",
            host.upper(), bits_per_second / 1e9, seconds, retransmits, size_bytes / 1e9
        )
        throughput += (bits_per_second / 1e9)
        bytes_transferred += (size_bytes / 1e9)
        retransmissions += retransmits

    logging.info(
        "IGST: iPerf3 log on %s: SUM of %d Stream(s): Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size: %.4f",
        host.upper(), parallel, throughput, seconds, retransmissions, bytes_transferred
    )

#-------------------------------------------------------------------------------
# iPerf3 Base
def start_iperf_server_base(
    cfg: Config, 
    host: str,
    start_port: int,
    stream_ids: Sequence[str],
    parallel: int, 
    numa: str,
    out_dir: str, 
    app: str, 
    file: str,
    timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> subprocess.Popen[str]:
#def start_iperf_server_base(cfg: Config, host: str, port: int, file: str, app: str, numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    launch_processes = []
    extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)}" if cfg.test == "transfer" else ""

    for i, stream_id in enumerate(stream_ids):
        # numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
        # numa_node, numa_cpus = get_numa_node(cfg, host, dev) if numa == "numa" else (None, "")
        # #cpu_count = max(2, parallel)
        # #selected_cpus = take_cpus(numa_cpus, cpu_count) if numa == "numa" else ""
        # numa_prefix = (
        #     f"numactl --physcpubind={shlex.quote(numa_cpus)} --membind={numa_node} "
        #     #f"numactl --physcpubind={shlex.quote(selected_cpus)} --membind={numa_node} "
        #     if numa == "numa"
        #     else ""
        # )
        cp = popen_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "                #f"numactl --cpunodebind=1 --preferred=1 "
            f"iperf3 -s -p {start_port + i} -1 --timestamps --forceflush "                              #f"iperf3 -s -B {cfg.listener_pub} -p {port} -1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        #logging.info("IBASE: Started iperf3 server on %s", host.upper())
        launch_processes.append(cp)
        logging.debug("IBASE: Started iperf3 server on port %d on host %s", (start_port + i), host.upper())


def start_iperf_client_base(
    cfg: Config, host: str, 
    listener_pub: str, 
    stream_ids: Sequence[str], listen_ports: Sequence[int],
    parallel: int, numa: str, arg: int, 
    file: str, app: str,  out_dir: str, timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")

    processes = []
    throughput, bytes_transferred, retransmissions = 0, 0, 0
    # size = parse_size_to_bytes(arg) if cfg.test == "transfer" else None
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else None
    #extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)} -n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    extra_arg = f"-n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        #cp = run_subprocess(
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}time.log "             #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -c {listener_pub} -p {listen_port} "                                                      #f"iperf3 -c {listener_pub} -B {cfg.initiator_pub} -p {port} "
            f"-Z -R -P 1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
            #timeout= timeout,
        )
        processes.append(cp)

    for cp in processes:
        stdout, stderr = cp.communicate()
        if cp.returncode != 0:
            logging.error("IBASE: Process failed: %s", stderr)
        #tail = "\n".join(stdout.splitlines()[-29:-21])
        #logging.info("IGST: iPerf3 log on %s %s", host.upper(), tail)
        #return cp
        tail = "\n".join(stdout.splitlines()[-29:-21])
        logging.debug("IGST: iPerf log on %s %s", host.upper(), tail)
        seconds = float(re.search(r'"seconds":\s*([\d.]+)', tail).group(1))
        size_bytes = int(re.search(r'"bytes":\s*(\d+)', tail).group(1))
        bits_per_second = float(re.search(r'"bits_per_second":\s*([\d.]+)', tail).group(1))
        retransmits = int(re.search(r'"retransmits":\s*(\d+)', tail).group(1))
        logging.debug(
            "iPerf3 log %s Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size: %.4f",
            host.upper(), bits_per_second / 1e9, seconds, retransmits, size_bytes / 1e9
        )
        throughput += (bits_per_second / 1e9)
        bytes_transferred += (size_bytes / 1e9)
        retransmissions += retransmits

    logging.info(
        "IBASE: iPerf3 log on %s: SUM of %d Stream(s): Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size: %.4f",
        host.upper(), parallel, throughput, seconds, retransmissions, bytes_transferred
    )



def cleanup_iperf(cfg: Config, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = run_subprocess(
            host, None,
            "pkill -TERM -f '[i]perf3' || true ", 
            localhost=cfg.localhost
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"IPERF: Failed killing iperf on {host.upper()}\n "
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr} "
            )
        logging.debug("IPERF: Killed iperf on %s %s", host.upper(), cp.stdout)