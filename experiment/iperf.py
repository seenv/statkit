import logging
import shlex
import re
import time
import json
import subprocess
from typing import Sequence

from remote import run_subprocess, popen_subprocess
from utils import parse_size_to_bytes, get_numa_node, take_cpus
from config import Config

# -------------------------------------------------------------------------------
# Helpers
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
    files: list[str],
    timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> list[subprocess.Popen[str]]:
    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"IGST: Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    processes = []

    for i, stream_id in enumerate(stream_ids):
        extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(files[i])}" if cfg.test == "transfer" else ""
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
            f"globus-streams-launch -p {start_port + (i * 2)} {shlex.quote(stream_id)} "                                    #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -s -p {start_port + (i * 2)} -1 --timestamps --forceflush "                      #f"iperf3 -s -B {cfg.listener_ip} -p {port} -1 --timestamps  --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        processes.append(cp)
        logging.debug("IGST: Started iperf3 server with tunnel ID %s and port of %d on host %s", stream_id, (start_port + (i * 2)), host.upper())


def start_iperf_client(
    cfg: Config, host: str, 
    stream_ids: Sequence[str], listen_ports: Sequence[int],
    parallel: int, numa: str, arg: int, file: str, app: str, out_dir: str, timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> list[subprocess.CompletedProcess[str]]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"IGST: Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")
    
    processes, results = [], []
    duration, throughput, bytes_transferred, retransmissions = 0, 0, 0, 0
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else 0
    chunk_size, remainder = divmod(size, parallel) if size else (0, 0)

    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        file_size = chunk_size + (1 if i < remainder else 0)
        extra_arg = f"-n {file_size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            
            f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
            
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-time.log "    #f"numactl --cpunodebind=0 --preferred=0 "
            "globus-streams-launch "
            f"{shlex.quote(stream_id)} "                                                    #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -c globus.{shlex.quote(stream_id)} -p {listen_port} "                 #f"iperf3 -c globus.{shlex.quote(tunnel_id)} -B {cfg.initiator_ip} -p {contact_port} "
            f"-Z -R -P 1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            
            f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\"; "
            f"}} 2>&1 | tr '\\r' '\\n' "
            f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
            f"| tee {shlex.quote(out_dir)}/{shlex.quote(app)}.log; "
            
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"sleep 1; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        processes.append((cp, listen_port))
    
    # for cp, listen_port in processes:
    #     stdout, stderr = cp.communicate()
    #     results.append((listen_port, stdout, stderr, cp.returncode))
    for cp, listen_port in processes:
        stdout, stderr = cp.communicate()
        time.sleep(cfg.sleep)
        results.append((listen_port, stdout, stderr, cp.returncode))

    for listen_port, stdout, stderr, returncode in results:
        if returncode != 0:
            logging.error("IGST: Process on port %s failed with return code %s: %s", listen_port, returncode, stderr)

        try:
            end = json.loads(stdout)["end"]
            sent, recv = end["sum_sent"], end["sum_received"]
            sent_seconds, sent_bytes = sent["seconds"], sent["bytes"]
            sent_bits_per_second = sent["bits_per_second"]
            sent_retransmits = sent.get("retransmits", 0)
            recv_seconds, recv_bytes = recv["seconds"], recv["bytes"]
            recv_bits_per_second = recv["bits_per_second"]
            # per_stream = [(s["sender"]["seconds"], s["receiver"]["seconds"]) for s in end["streams"]]
        except (json.JSONDecodeError, KeyError) as e:
            logging.error("IGST: No parseable summary on port %s (rc=%s): %s\n%s",
                        listen_port, returncode, e, stdout[-2000:])
            continue

        logging.debug("SENT: Gbps: %.6f | Sec: %.2f | Size: %.4f | Retrans: %s ", sent_bits_per_second / 1e9, sent_seconds, sent_bytes / 1e9, sent_retransmits)
        logging.debug("RECV: Gbps: %.6f | Sec: %.2f | Size: %.4f", recv_bits_per_second / 1e9, recv_seconds, recv_bytes / 1e9)

        sent_duration           = max(sent_duration, sent_seconds)          #max(sent_duration, sent.get("seconds", 0))
        sent_throughput         += (sent_bits_per_second / 1e9)     # Gbps  #sent.get("bits_per_second", 0) / 1e9
        sent_bytes_transferred  += (sent_bytes / 1e9)               # GB    #sent.get("bytes", 0) / 1e9
        sent_retransmissions    += sent_retransmits                         #sent.get("retransmits", 0)

        recv_duration           = max(recv_duration, recv_seconds)
        recv_throughput         += (recv_bits_per_second / 1e9)
        recv_bytes_transferred  += (recv_bytes / 1e9)

    logging.info(
        "IGST: iPerf3 log on %s: SUM of %d Stream(s): Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size(GB): %.4f",
        host.upper(), parallel, recv_throughput, recv_duration, sent_retransmissions, recv_bytes_transferred
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
    files: list[str],
    timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> list[subprocess.Popen[str]]:
    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"IBASE: Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    processes = []

    for i, stream_id in enumerate(stream_ids):
        extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(files[i])}" if cfg.test == "transfer" else ""
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
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-time.log "                #f"numactl --cpunodebind=1 --preferred=1 "
            f"iperf3 -s -p {start_port + (i * 2)} -1 --timestamps --forceflush "                              #f"iperf3 -s -B {cfg.listener_pub} -p {port} -1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        processes.append(cp)
        logging.debug("IBASE: Started iperf3 server on port %d on host %s", (start_port + (i * 2)), host.upper())


def start_iperf_client_base(
    cfg: Config, host: str, 
    listener_pub: str, 
    stream_ids: Sequence[str], listen_ports: Sequence[int],
    parallel: int, numa: str, arg: int, 
    file: str, 
    app: str,  out_dir: str, timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> list[subprocess.CompletedProcess[str]]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"IBASE: Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")

    processes,results = [], []
    duration, throughput, bytes_transferred, retransmissions = 0, 0, 0, 0
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else 0
    chunk_size, remainder = divmod(size, parallel) if size else (0, 0)
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        file_size = chunk_size + (1 if i < remainder else 0)
        extra_arg = f"-n {file_size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
        cp = popen_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-time.log "             #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -c {listener_pub} -p {listen_port} "                                                      #f"iperf3 -c {listener_pub} -B {cfg.initiator_pub} -p {port} "
            f"-Z -R -P 1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"sleep 1; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        processes.append((cp, listen_port))

    for cp, listen_port in processes:
        stdout, stderr = cp.communicate()
        time.sleep(cfg.sleep)
        results.append((listen_port, stdout, stderr, cp.returncode))
            
    for listen_port, stdout, stderr, returncode in results:
        if returncode != 0:
            logging.error("IBASE: Process on port %s failed with return code %s: %s", listen_port, returncode, stderr)

        try:
            end = json.loads(stdout)["end"]
            sent, recv = end["sum_sent"], end["sum_received"]
            sent_seconds, sent_bytes = sent["seconds"], sent["bytes"]
            sent_bits_per_second = sent["bits_per_second"]
            sent_retransmits = sent.get("retransmits", 0)
            recv_seconds, recv_bytes = recv["seconds"], recv["bytes"]
            recv_bits_per_second = recv["bits_per_second"]
            # per_stream = [(s["sender"]["seconds"], s["receiver"]["seconds"]) for s in end["streams"]]
        except (json.JSONDecodeError, KeyError) as e:
            logging.error("IBASE: No parseable summary on port %s (rc=%s): %s\n%s",
                        listen_port, returncode, e, stdout[-2000:])
            continue

        logging.debug("SENT: Gbps: %.6f | Sec: %.2f | Size: %.4f | Retrans: %s ", sent_bits_per_second / 1e9, sent_seconds, sent_bytes / 1e9, sent_retransmits)
        logging.debug("RECV: Gbps: %.6f | Sec: %.2f | Size: %.4f", recv_bits_per_second / 1e9, recv_seconds, recv_bytes / 1e9)

        sent_duration           = max(sent_duration, sent_seconds)          #max(sent_duration, sent.get("seconds", 0))
        sent_throughput         += (sent_bits_per_second / 1e9)     # Gbps  #sent.get("bits_per_second", 0) / 1e9
        sent_bytes_transferred  += (sent_bytes / 1e9)               # GB    #sent.get("bytes", 0) / 1e9
        sent_retransmissions    += sent_retransmits                         #sent.get("retransmits", 0)

        recv_duration           = max(recv_duration, recv_seconds)
        recv_throughput         += (recv_bits_per_second / 1e9)
        recv_bytes_transferred  += (recv_bytes / 1e9)

    logging.info(
        "IBASE: iPerf3 log on %s: SUM of %d Stream(s): Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size(GB): %.4f",
        host.upper(), parallel, recv_throughput, recv_duration, sent_retransmissions, recv_bytes_transferred
    )

#-------------------------------------------------------------------------------
# iPerf3 SciStream
def start_iperf_server_scistream(
    cfg: Config, 
    host: str,
    listen_ep_ports: Sequence[int],
    stream_ids: Sequence[str],
    parallel: int, 
    numa: str,
    out_dir: str, 
    app: str, 
    files: list[str],
    timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> list[subprocess.Popen[str]]:
    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"ISCI: Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    processes = []
    for i, (listen_port, stream_id) in enumerate(zip(listen_ep_ports, stream_ids)):
        extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(files[i])}" if cfg.test == "transfer" else ""
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
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-time.log "                #f"numactl --cpunodebind=1 --preferred=1 "
            f"iperf3 -s -p {listen_port} -1 --timestamps --forceflush "                                     #f"iperf3 -s -p {start_ports[i]} -1 --timestamps --forceflush "                              #f"iperf3 -s -B {cfg.listener_pub} -p {port} -1 --timestamps --forceflush "   f"iperf3 -s -p {listen_ep_ports[i]} -1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        #logging.info("ISCI: Started iperf3 server on %s", host.upper())
        processes.append(cp)
        logging.debug("ISCI: Started iperf3 server on port %d on host %s", (listen_ep_ports[i]), host.upper())


def start_iperf_client_scistream(
    cfg: Config, host: str, 
    listener_pub: str, 
    stream_ids: Sequence[str], 
    initiate_ap_ports: Sequence[int],
    parallel: int, numa: str, arg: int, 
    file: str, 
    app: str,  out_dir: str, timeout: int, 
    temp_dir: str = "/tmp/temp_files",
    retries: int = 100,
    check: bool = True,
) -> list[subprocess.CompletedProcess[str]]:
    if not (len(initiate_ap_ports) == parallel):
        raise RuntimeError(f"ISCI: Expected all lists to have length parallel={parallel}, but got listen ports={len(initiate_ap_ports)}")

    processes, results = [], []
    sent_duration, sent_throughput, sent_bytes_transferred, sent_retransmissions = 0, 0, 0, 0
    recv_duration, recv_throughput, recv_bytes_transferred = 0, 0, 0
    
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else 0
    chunk_size, remainder = divmod(size, parallel) if size else (0, 0)
    for i, (listen_port, stream_id) in enumerate(zip(initiate_ap_ports, stream_ids, strict=True)):
        file_size = chunk_size + (1 if i < remainder else 0)
        extra_arg = f"-n {file_size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
        #cp = run_subprocess(
        cp = popen_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-time.log "             #f"numactl --cpunodebind=0 --preferred=0 "
            f"iperf3 -c {listener_pub} -p {listen_port} "                                                #f"iperf3 -c {listener_pub} -B {cfg.initiator_pub} -p {port} " #f"iperf3 -c 128.135.164.120 -p 5100 "
            f"-Z -R -P 1 --timestamps --forceflush "
            f"{extra_arg} "
            f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json; "
            f"rc=$?; "
            f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.json 2>/dev/null || true; "
            f"sleep 1; "
            f"exit $rc ",
            localhost=cfg.localhost,
        )
        processes.append((cp, listen_port))
        logging.debug("ISCI: Started iperf3 client on port %d on host %s", (initiate_ap_ports[i]), host.upper())
    #time.sleep(int(arg)+int(cfg.sleep)*2)
    for cp, listen_port in processes:
        stdout, stderr = cp.communicate()
        time.sleep(cfg.sleep)
        results.append((listen_port, stdout, stderr, cp.returncode))
            
    for listen_port, stdout, stderr, returncode in results:
        if returncode != 0:
            logging.error("ISCI: Process on port %s failed with return code %s: %s", listen_port, returncode, stderr)

        try:
            end = json.loads(stdout)["end"]
            sent, recv = end["sum_sent"], end["sum_received"]
            sent_seconds, sent_bytes = sent["seconds"], sent["bytes"]
            sent_bits_per_second = sent["bits_per_second"]
            sent_retransmits = sent.get("retransmits", 0)
            recv_seconds, recv_bytes = recv["seconds"], recv["bytes"]
            recv_bits_per_second = recv["bits_per_second"]
            # per_stream = [(s["sender"]["seconds"], s["receiver"]["seconds"]) for s in end["streams"]]
        except (json.JSONDecodeError, KeyError) as e:
            logging.error("ISCI: No parseable summary on port %s (rc=%s): %s\n%s",
                        listen_port, returncode, e, stdout[-2000:])
            continue

        logging.debug("SENT: Gbps: %.6f | Sec: %.2f | Size: %.4f | Retrans: %s ", sent_bits_per_second / 1e9, sent_seconds, sent_bytes / 1e9, sent_retransmits)
        logging.debug("RECV: Gbps: %.6f | Sec: %.2f | Size: %.4f", recv_bits_per_second / 1e9, recv_seconds, recv_bytes / 1e9)

        sent_duration           = max(sent_duration, sent_seconds)          #max(sent_duration, sent.get("seconds", 0))
        sent_throughput         += (sent_bits_per_second / 1e9)     # Gbps  #sent.get("bits_per_second", 0) / 1e9
        sent_bytes_transferred  += (sent_bytes / 1e9)               # GB    #sent.get("bytes", 0) / 1e9
        sent_retransmissions    += sent_retransmits                         #sent.get("retransmits", 0)

        recv_duration           = max(recv_duration, recv_seconds)
        recv_throughput         += (recv_bits_per_second / 1e9)
        recv_bytes_transferred  += (recv_bytes / 1e9)

    logging.info(
        "ISCI: iPerf3 log on %s: SUM of %d Stream(s): Throughput(Gbps): %.6f | Duration(sec): %.2f | Retransmits: %s | Size(GB): %.4f",
        host.upper(), parallel, recv_throughput, recv_duration, sent_retransmissions, recv_bytes_transferred
    )

