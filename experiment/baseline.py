from __future__ import annotations

import logging
import time
import shlex
import subprocess

from config import Config, Role

from utils import popen_subprocess, run_subprocess
from utils import cleanup_iperf


def _base_start_statkit(cfg: Config, t : int, parallel: int, run_idx: int, out_dir: str, check: bool = True) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {shlex.quote(out_dir)} && "
            "pids=$(pgrep -d, -f globus-gridftp-server || true); "
            "python ~/statkit/monitor/launcher.py  --pids \"$pids\" "
            f"--out {shlex.quote(out_dir)} --duration {t + 120} & "
            f"echo $! > {shlex.quote(out_dir)}/launcher.pid ", 
            localhost=cfg.localhost,
        )
        logging.debug("BASELINE: Started on statkit on %s: %s", host.upper(), cp.stdout)

def _base_start_iperf_server(cfg: Config, host: str, out_dir: str) -> subprocess.Popen[str]:
    #host = cfg.hosts.ep.get("listener")
    cp = popen_subprocess(
        host, None,
        f"iperf3 -s -p 49000 -1 --timestamps "
        f"-J --logfile {shlex.quote(out_dir)}/iperf.json --forceflush & "
        "echo $! " ,
        localhost=cfg.localhost,
    )
    logging.info("BASELINE: Started iperf3 server on %s", host.upper())

def _base_run_iperf_client(cfg: Config, host: str, listener_ip: str, t : int, parallel: int, run: int, out_dir: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    #host = cfg.hosts.ep.get("initiator")
    cp = run_subprocess(
        host, cfg.remote_env,
        f"iperf3 -c {listener_ip} -p 49000 "
        f"-J --logfile {shlex.quote(out_dir)}/iperf.json --forceflush "
        f"-P {parallel} -O 3 -Z -R -t {t} --timestamps ",
        localhost=cfg.localhost,
        timeout= t + 120,
    )
    n = parallel * 2 + 6        # 2x lines per each direction, 2x sums + 4 extra
    tail = "\n".join(cp.stdout.splitlines()[-n:])
    logging.info("BASELINE: iPerf3 log (when -J is not set)on %s: \n%s", host.upper(), tail)
    return cp

def _base_stop_statkit(cfg: Config) -> None:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    for host in hosts:
        cp = popen_subprocess(
            host, None,
            r"pkill -TERM -f 'monitor/launcher\.py' || true",
            localhost=cfg.localhost,
        )
        logging.debug("BASELINE: Stopped statkit on %s", host)
        

        
def baseline_main(cfg: Config, duration: int, parallel: int, run: int, block: int):
    out_dir = f"{cfg.report_dir}/direct/{block}/{parallel}/{run}"
    cleanup_iperf(cfg)
    time.sleep(5)

    try:
    # launch statkit
        logging.info("BASELINE: Starting the statkit monitoring on the hosts")
        _base_start_statkit(cfg, duration, parallel, run, out_dir)
        time.sleep(9)
        
        logging.info("BASELINE: Starting iperf server")
        _base_start_iperf_server(cfg, cfg.hosts.ap.get("listener"), out_dir)
        time.sleep(5)           # it takes more for them to initiates! TODO: find a better way
        # TODO: add pkill -9 iperf
        
        # run iperf client
        logging.info("BASELINE: Starting iperf client")
        _base_run_iperf_client(cfg, cfg.hosts.ap.get("initiator"), "192.5.86.197", duration, parallel, run, out_dir)
        time.sleep(5)

    finally:
        logging.info("BASELINE: Stopping the tunnel and statkit")
        _base_stop_statkit(cfg)
        time.sleep(2)
    