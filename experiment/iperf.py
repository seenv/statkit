import logging
import shlex
import subprocess


from remote import run_subprocess, popen_subprocess
from utils import parse_size_to_bytes, get_numa_node, take_cpus
from config import Config

# -------------------------------------------------------------------------------
# iPerf3 GST
def start_iperf_server(cfg: Config, host: str, port: int, tunnel_id: str, file: str, app: str, numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
#def start_iperf_server(cfg: Config, host: str, port: int, tunnel_id: str, file: str, app: str, 
    # parallel: int, dev: str, 
    # numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
    extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)}" if cfg.test == "transfer" else ""
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
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "    #f"numactl --cpunodebind=0 --preferred=0 "
        f"globus-streams-launch "
        f"-p {port} {shlex.quote(tunnel_id)} "  #f"numactl --cpunodebind=0 --preferred=0 "
        f"iperf3 -s -p {port} -1 --timestamps --forceflush "    #f"iperf3 -s -B {cfg.listener_ip} -p {port} -1 --timestamps  --forceflush "
        f"{extra_arg} "
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json  & "
        "echo $! " ,
        localhost=cfg.localhost,
    )
    logging.info("IPERF: Started iperf3 server on host %s", host.upper())


def start_iperf_client(cfg: Config, host: str, tunnel_id: str, contact_port: int, 
    parallel: int, arg: int, file: str, app: str, numa: str, out_dir: str, timeout: int, 
    temp_dir: str = "/tmp/temp_files", check: bool = True) -> subprocess.CompletedProcess[str]:
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else 0
    #extra_arg = f"-Z -R -n {arg}G -F {shlex.quote(temp_dir)}/{shlex.quote(file)} " if cfg.test == "transfer" else f"-P {parallel} -i 10 -O 10 -Z -R -t {arg} "    
    # extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)} -n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    extra_arg = f"-n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    cp = run_subprocess(
        host, cfg.remote_env,
        f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "    #f"numactl --cpunodebind=0 --preferred=0 "
        "globus-streams-launch "
        f"{shlex.quote(tunnel_id)} "                                                    #f"numactl --cpunodebind=0 --preferred=0 "
        f"iperf3 -c globus.{shlex.quote(tunnel_id)} -p {contact_port} "                 #f"iperf3 -c globus.{shlex.quote(tunnel_id)} -B {cfg.initiator_ip} -p {contact_port} "
        f"-Z -R -P {parallel} --timestamps --forceflush "
        f"{extra_arg} "
        # f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json && "
        # f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}.json ",
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json; "
        f"rc=$?; "
        f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}.json 2>/dev/null || true; "
        f"exit $rc ",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    n = parallel * 2 + 6        # 2x lines per each direction, 2x sums + 4 extra
    #tail = "\n".join(cp.stdout.splitlines()[-n:])
    tail = "\n".join(cp.stdout.splitlines()[-29:-21])
    logging.info("IPERF: iPerf3 log on %s %s", host.upper(), tail)
    return cp


#-------------------------------------------------------------------------------
# iPerf3 Base
def start_iperf_server_base(cfg: Config, host: str, port: int, file: str, app: str, numa: str, out_dir: str, temp_dir: str = "/tmp/temp_files") -> subprocess.Popen[str]:
    extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)}" if cfg.test == "transfer" else ""
    cp = popen_subprocess(
        host, None,
        f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "    #f"numactl --cpunodebind=1 --preferred=1 "
        f"iperf3 -s -p {port} -1 --timestamps --forceflush "                            #f"iperf3 -s -B {cfg.listener_pub} -p {port} -1 --timestamps --forceflush "
        f"{extra_arg} "
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json  & "
        "echo $! " ,
        localhost=cfg.localhost,
    )
    logging.info("BASE: Started iperf3 server on %s", host.upper())


def start_iperf_client_base(
    cfg: Config, host: str, listener_pub: str, port: int, parallel: int, arg: int, 
    file: str, app: str, numa: str, out_dir: str, timeout: int, temp_dir: str = "/tmp/temp_files", check: bool = True
    ) -> subprocess.CompletedProcess[str]:  
    size = parse_size_to_bytes(arg) if cfg.test == "transfer" else 0
    #extra_arg = f"-F {shlex.quote(temp_dir)}/{shlex.quote(file)} -n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    extra_arg = f"-n {size} " if cfg.test == "transfer" else f"-i 10 -O 10 -t {arg} "
    cp = run_subprocess(
        host, cfg.remote_env,
        f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(temp_dir)} && "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "    #f"numactl --cpunodebind=0 --preferred=0 "
        f"iperf3 -c {listener_pub} -p {port} "                                          #f"iperf3 -c {listener_pub} -B {cfg.initiator_pub} -p {port} "
        f"-Z -R -P {parallel} --timestamps --forceflush "
        f"{extra_arg} "
        # f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json && "
        # f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}.json ",
        f"-J --logfile {shlex.quote(out_dir)}/{shlex.quote(app)}.json; "
        f"rc=$?; "
        f"cat {shlex.quote(out_dir)}/{shlex.quote(app)}.json 2>/dev/null || true; "
        f"exit $rc ",
        localhost=cfg.localhost,
        timeout= timeout,
    )
    n = parallel * 2 + 6        # 2x lines per each direction, 2x sums + 4 extra
    #tail = "\n".join(cp.stdout.splitlines()[-n:])
    tail = "\n".join(cp.stdout.splitlines()[-29:-21])
    logging.info("BASE: iPerf3 log on %s %s", host.upper(), tail)
    return cp

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