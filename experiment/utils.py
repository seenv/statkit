from __future__ import annotations

import logging
import os
import re
import uuid
import shlex
import subprocess
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, List, Dict, Tuple
from pathlib import PurePosixPath
from datetime import datetime
import socket
import traceback

from config import Config, Role
from remote import run_subprocess, popen_subprocess



# Helpers:
#-------------------------------------------------------------------------------
def setup_logging(verbose: bool, log_path: str = "/tmp/statkit.log") -> None:
    root = logging.getLogger()
    # removing existing handlers to avoid duplicate logs when re running
    # root.handlers.clear()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        #"%(asctime)s %(levelname)s %(message)s",
        "%(asctime)s %(message)s ",
        datefmt="%Y-%m-%d %H:%M:%S"
        )
    # always INFO in the console
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    #sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setLevel(logging.INFO)
    root.addHandler(sh)
    # DEBUG in file when verbose
    #if verbose:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path)#, mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)


def notify(topic: str, title: str, message: str) -> None:
    subprocess.run(["curl", "-H", f"Title: {title}", "-d", message, f"https://ntfy.sh/{topic}"], check=False)

def send_ntfy(success: bool, cfg: Config, msg: Exception | None = None) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if success:
        title = f"Experiment '{cfg.test.capitalize()}' finished"
        message = (f"\n Lease: {cfg.lease} \n Time: {now} \n Message:{msg}\n")
    else:
        title = f"Experiment '{cfg.test.capitalize()}' failed"
        message = (f"\n Lease: {cfg.lease} \n Time: {now} \n Message:{msg}\n")
    notify(topic=f"{cfg.test.replace(' ', '-')}", title=title, message=message)


#-------------------------------------------------------------------------------
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
    if splices:
        for splice in splices:
            # if splice not in (0, 1):
            #     raise ValueError(f"Invalid splice value: {splice}. Expected 0 or 1.")
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
    if encrypt == 1:
        return "E1"
    if splice == 0:
        return "A0"
    if splice == 1:
        return "A1"
    raise ValueError(f"Invalid mode: splice={splice}, encrypt={encrypt}")

def parse_size_to_bytes(size: str) -> int:
    # s = str(size).strip().upper()
    # units = {
    #     "B": 1, "K": 1024, "KB": 1024,
    #     "M": 1024**2, "MB": 1024**2, "G": 1024**3,
    #     "GB": 1024**3, "T": 1024**4, "TB": 1024**4,
    # }
    # for unit in sorted(units, key=len, reverse=True):
    #     if s.endswith(unit):
    #         number = float(s[:-len(unit)])
    #         return int(number * units[unit])
    # return int(float(s))
    return int(size) * (1024 ** 3)


def make_file(cfg: Config, parallel: int, arg: int, files: list[str], file_path: str = "/tmp/temp_files") -> None:
    host = cfg.hosts.ep.get("listener")
    size = parse_size_to_bytes(arg) #if cfg.test == "transfer" else 0
    chunk_size, remainder = divmod(size, parallel) if size else (0, 0)

    for idx, file_name in enumerate(files):
        file_size = chunk_size + (1 if idx < remainder else 0)
        cp = run_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(file_path)} && "
            f"fallocate -l {file_size} {shlex.quote(file_path)}/{shlex.quote(file_name)} && "
            f"test -f {shlex.quote(file_path)}/{shlex.quote(file_name)} && "
            f"du -h {shlex.quote(file_path)}/{shlex.quote(file_name)} ",
            localhost=cfg.localhost,
        )
    logging.debug("FILE: Created the source file(s) on %s: %s", host.upper(), cp.stdout.strip())


def create_output_dir(cfg: Config, output_dir: str, timeout: int):
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = run_subprocess(
            host, None,
            f"mkdir -p {shlex.quote(output_dir)} && "
            f"rm -f {shlex.quote(output_dir)}/* && "
            # f"find {shlex.quote(out_dir)} -mindepth 1 -delete && "
            f'ls {shlex.quote(output_dir)} ',
            localhost=cfg.localhost,
            timeout=timeout,
        )
        logging.debug("DIR: Creating and cleaning up the test directory on host: %s: %s", host.upper(), cp.stdout)
    logging.info("DIR: Created the test directory on the hosts")


def initial_cleanup(
    cfg, timeout: int = 30, check: bool = True,
):
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = run_subprocess(
            host, None,
            f'pkill -TERM haproxy || true; '
            f'pkill -TERM stunnel || true; '
            f'pkill -TERM nginx || true; '
            f'pkill -TERM s2cs || true; '
            f'pkill -TERM s2uc || true; '
            
            f'pkill -TERM iperf3 || true; '
            f'pkill -TERM rsync || true; '
            
            r'pkill -TERM -f "monitor/launcher\.py" || true; '
            # f'docker ps -q | xargs -r docker stop; '
            # f'docker ps -aq | xargs -r docker rm; ',
            f'docker ps -q | xargs --no-run-if-empty docker stop && docker container prune -f; ',
            localhost=cfg.localhost,
            timeout=timeout,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"INITCLN: Failed initial node's cleaning up the reports on {host.upper()}"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
    logging.info("INITCLN: Initial nodes cleanup")


def cleanup_file(cfg: Config, file_path: str = "/tmp/temp_files") -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        #for file_name in files:
        cp = run_subprocess(
            host, None,
            f"rm -f {shlex.quote(file_path)}/file*  ",
            #f"du -h {shlex.quote(file_path)}/ || true ",
            localhost=cfg.localhost,
        )
        logging.debug("FILE: Cleaning up the temp files on %s: %s", host.upper(), cp.stdout.strip())
    logging.info("FILE: Cleaned up the temp files")


def copy_results(cfg, check: bool = True) -> None:

    hosts = [
        (cfg.hosts.ap["initiator"], "cons-ap"),
        (cfg.hosts.ep["initiator"], "cons-ep"),
        (cfg.hosts.ap["listener"],  "prod-ap"),
        (cfg.hosts.ep["listener"],  "prod-ep"),
    ]

    try:
        for numa in cfg.numactl:
            test_type = cfg.test
            test_bed = cfg.lease.lower()
            test_dir = Path(cfg.report_dir).name
            child_dir = f'{numa}/{cfg.tcp_buffer}/{cfg.ring_buffer}'
            # proj_dir		= f"/home/seena/Projects/globus_stream/statkit/results"  
            proj_dir      = cfg.proj_dir
            base_dir      = f"{proj_dir}/reports/{test_bed}/{test_type}/{test_dir}/{child_dir}"   # root where node folders live
            output_dir    = f"{proj_dir}/analysis/{test_bed}/{test_type}/{test_dir}/{child_dir}"
            report_dir		= f"{proj_dir}/reports/{test_bed}/{test_type}"

            print(f"TEST_TYPE  : {test_type}")
            print(f"BASE_DIR    : {base_dir}")
            print(f"OUTPUT_DIR : {output_dir}")
        
            for host, dest_name in hosts:
                cp = run_subprocess(
                    cfg.localhost, None,
                    f"/opt/homebrew/bin/rsync -av --mkpath --ignore-existing "
                    #f"rsync -avznc --itemize-changes "
                    #f"-e ssh {host}:/tmp/exps/{test_bed}/{test_dir}/{test_type}/{child_dir}/ "
                    f"-e ssh {host}:/tmp/{test_dir}/{test_type}/{child_dir}/ "
                    f"{report_dir}/{test_dir}/{child_dir}/{dest_name}/ " ,
                    localhost=cfg.localhost,
                )
                if check and cp.returncode != 0:
                    raise RuntimeError(
                        f"COPY: Failed copying the reports on {host.upper()}"
                        f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
                    )

            if test_type == "transfer" and "gtr" in cfg.app:
                cp = run_subprocess(
                    cfg.localhost, None,
                    f"rsync -av --mkpath --ignore-existing "
                    #f"rsync -avznc --itemize-changes "
                    f"/tmp/exps/{test_bed}/{test_dir}/{test_type}/{child_dir}/ "
                    f"/tmp/{test_dir}/{test_type}/{child_dir}/ "
                    f"{report_dir}/{test_dir}/{child_dir}/cons-ep/ ",
                    localhost=cfg.localhost,
                )
                if check and cp.returncode != 0:
                    raise RuntimeError(
                        f"COPY: Failed copying the reports on {cfg.localhost.upper()}"
                        f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
                    )
    except Exception as e:
        raise RuntimeError(f"COPY: Runtime Error: {e}") from e

#-------------------------------------------------------------------------------
# Statkit monitor
def start_statkit(cfg: Config, timeout : int , app: str, out_dir: str, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        pattern = "[g]lobus-gridftp-server|[h]aproxy|[i]perf3|[r]sync|[d]ocker"
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"pids=$(pgrep -d, -f {shlex.quote(pattern)} || true); "
            f"python ~/statkit/monitor/launcher.py --pids \"$pids\" "
            f"--out {shlex.quote(out_dir)} --app {shlex.quote(app)} "
            f"--duration {timeout} & "
            f"echo $! > {shlex.quote(out_dir)}/{shlex.quote(app)}-launcher.pid ",
            localhost=cfg.localhost,
        )
        logging.debug("SYS: Started on statkit on %s %s", host.upper(), cp.stdout)


def stop_statkit(cfg: Config) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = popen_subprocess(
            host, None,
            r"pkill -TERM -f 'monitor/launcher\.py' || true",
            localhost=cfg.localhost,
        )
        logging.debug("SYS: Stopped statkit on %s", host.upper())


def get_numa_node(cfg: Config, host: str, dev: str) -> tuple[int, str]:
    cp = run_subprocess(
        host,
        None,
        f"dev={shlex.quote(dev)}; "
        f"numa_file=/sys/class/net/$dev/device/numa_node; "
        f"if [ ! -f \"$numa_file\" ]; then "
        f"  echo \"NUMA: missing $numa_file\" >&2; "
        f"  exit 1; "
        f"fi; "
        f"node=$(cat \"$numa_file\"); "
        f"if [ \"$node\" -lt 0 ]; then "
        f"  echo \"NUMA: device $dev has unknown numa_node=$node\" >&2; "
        f"  exit 1; "
        f"fi; "
        f"cpulist_file=/sys/devices/system/node/node$node/cpulist; "
        f"if [ ! -f \"$cpulist_file\" ]; then "
        f"  echo \"NUMA: missing $cpulist_file\" >&2; "
        f"  exit 1; "
        f"fi; "
        f"cpus=$(cat \"$cpulist_file\"); "
        f"if [ -z \"$cpus\" ]; then "
        f"  echo \"NUMA: empty CPU list for node $node\" >&2; "
        f"  exit 1; "
        f"fi; "
        f"echo \"$node $cpus\"",
        localhost=cfg.localhost,
    )
    lines = cp.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(
            f"NUMA: Empty NUMA output on {host} for dev={dev}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    last_line = lines[-1]
    parts = last_line.split(maxsplit=1)
    if len(parts) != 2:
        raise RuntimeError(
            f"NUMA: Could not parse NUMA output on {host} for dev={dev}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    numa_node = int(parts[0])
    numa_cpus = parts[1].strip()
    logging.debug(
        "NUMA: host=%s dev=%s node=%s cpus=%s",
        host.upper(),
        dev,
        numa_node,
        numa_cpus,
    )
    return numa_node, numa_cpus


def take_cpus(cpulist: str, count: int) -> str:
    cpus: list[int] = []

    for part in cpulist.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = map(int, part.split("-", 1))
            cpus.extend(range(start, end + 1))
        else:
            cpus.append(int(part))
    if count <= 0:
        raise ValueError(f"CPU count must be positive, got {count}")
    if len(cpus) < count:
        raise RuntimeError(
            f"Not enough CPUs in NUMA cpulist. requested={count}, available={len(cpus)}, cpulist={cpulist}"
        )
    selected = cpus[:count]
    return ",".join(str(cpu) for cpu in selected)


#-------------------------------------------------------------------------------
# Globus Transfer
_UUID_CANDIDATE = re.compile(r"[0-9a-fA-F-]{32,36}")
def parse_collection_uid(output: str,  parts: list[str], *, exact: bool = False) -> str:
    for line in output.splitlines():
        if "|" not in line:
            continue
        if line.strip().startswith("---"):
            continue
        # first column is Display Name
        display = line.split("|", 1)[1].strip()
        if not all(part in display for part in parts):
            continue
        m = _UUID_CANDIDATE.search(line)
        if not m:
            raise RuntimeError(f"Matched name but no ID found on line:\n{line}")
        return str(uuid.UUID(m.group(0)))
    raise RuntimeError(f"No collection row matched name={parts!r}.\nOutput:\n{output}")

#-------------------------------------------------------------------------------
# Ping
def record_ping(cfg: Config, host: str, dest_ip: str, app: str, out_dir: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = run_subprocess(
        host, None,
        f"ping -4 -n -q -i 0.5 -c 20 {dest_ip} | tee {shlex.quote(out_dir)}/{shlex.quote(app)}-ping.log ",
        localhost=cfg.localhost,
    )
    logging.debug("%s: Ping log %s", host.upper(), cp.stdout)
    return cp

def extract_connector_contact_string(conf_text: str) -> tuple[str, int]:
    for line in conf_text.splitlines():
        if line.startswith("connector_contact_string="):
            value = line.split("=", 1)[1].strip()
            ip, port = value.rsplit(":", 1)
            return ip, int(port)
    raise ValueError("connector_contact_string not found")
