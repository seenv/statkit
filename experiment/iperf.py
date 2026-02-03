from __future__ import annotations

import logging
#import os
import time
#from datetime import datetime
#from pathlib import Path
import shlex
import subprocess
from typing import Optional



from config import Config
#from congestion import congestion_change
#from proxy import proxy_change
#from utils import mkdir, run_dumpcap, run_stats, ssh_run, traffic_ctl

from utils import popen_subprocess, run_subprocess
from utils import restart_gridftp, get_stream_gateway_id, start_tunnel, stop_tunnel
from utils import init_listener_env, init_initiator_env
from utils import start_statkit, stop_statkit



def start_iperf_server(cfg: Config, tunnel_id: str) -> subprocess.Popen[str]:
    p = popen_subprocess(
        cfg.listener_host,
        cfg.remote_env,
        "globus-streams-launch "
        f"-p {cfg.base_port} {shlex.quote(tunnel_id)} "
        f"iperf3 -s -p {cfg.base_port} -1 ",
        localhost=cfg.localhost,
    )
    logging.info("LISTENER: started iperf3 server (local pid=%s)", p.pid)
    return p


def run_iperf_client(cfg: Config, tunnel_id: str, contact_port: int, parallel: int, run_idx: int) -> subprocess.CompletedProcess[str]:
    return run_subprocess(
        cfg.initiator_host,
        cfg.remote_env,
        "globus-streams-launch "
        f"{shlex.quote(tunnel_id)} "
        f"iperf3 -c globus.{shlex.quote(tunnel_id)} -p {contact_port} "
        f"-J --logfile /home/cc/statkit/reports/{parallel}/{run_idx}/iperf.json "
        f"-P {parallel} -t {cfg.time_frames} -O 3 -Z ",
        localhost=cfg.localhost,
        timeout=cfg.time_frames + 120,
    )


def iperf_main(cfg: Config) -> None:
    for parallel in cfg.parallels:
        for run_idx in range(cfg.run_num):
            logging.info("RUN %s / parallel=%s", run_idx + 1, parallel)

            # restart gridftp + get gateway IDs
            restart_gridftp(cfg, cfg.initiator_ap)
            time.sleep(1)
            initiator_stream_ap_id = get_stream_gateway_id(cfg, cfg.initiator_ap)
            logging.info("%s: gateway id=%s", cfg.initiator_ap.upper(), initiator_stream_ap_id)

            restart_gridftp(cfg, cfg.listener_ap)
            time.sleep(2)
            listener_stream_ap_id = get_stream_gateway_id(cfg, cfg.listener_ap)
            logging.info("%s: gateway id=%s", cfg.listener_ap.upper(), listener_stream_ap_id)
            
            # create tunnel (local)
            tunnel_id = start_tunnel(cfg, initiator_stream_ap_id, listener_stream_ap_id)
            logging.info("LOCAL: tunnel_id=%s", tunnel_id)
            time.sleep(1)

            iperf_srv: Optional[subprocess.Popen[str]] = None
            try:
                # launch statkit
                start_statkit(cfg, parallel, run_idx)
                time.sleep(1)
                
                # init listener env + start iperf server
                init_listener_env(cfg, tunnel_id)
                time.sleep(2)
                iperf_srv = start_iperf_server(cfg, tunnel_id)
                time.sleep(2)

                # init initiator env + discover contact port
                contact_port = init_initiator_env(cfg, tunnel_id)
                logging.info("INITIATOR: contact_port=%s", contact_port)
                time.sleep(1)
                
                # TODO: add pkill -9 iperf
                # run iperf client
                cp = run_iperf_client(cfg, tunnel_id, contact_port, parallel, run_idx)
                logging.info("INITIATOR: iperf3 stdout:\n%s", cp.stdout.strip())
                if cp.stderr.strip():
                    logging.info("INITIATOR: iperf3 stderr:\n%s", cp.stderr.strip())

                # wait for iperf server and log its output
                # if iperf_srv is not None:
                #     try:
                #         srv_out, srv_err = iperf_srv.communicate(timeout=cfg.time_frames + 120)
                #         if srv_out.strip():
                #             logging.info("LISTENER: iperf3 server stdout:\n%s", srv_out.strip())
                #         if srv_err.strip():
                #             logging.info("LISTENER: iperf3 server stderr:\n%s", srv_err.strip())
                #     except subprocess.TimeoutExpired:
                #         logging.warning("LISTENER: iperf3 server did not exit in time; terminating")
                #         iperf_srv.terminate()

            finally:
                #for proc in statkits:
                    #proc.wait()
                    #proc.communicate()
                # stop tunnel
                stop_statkit(cfg)
                stop_tunnel(cfg, tunnel_id)
                time.sleep(5)










# def stop_iperf(host: str) -> None:
#     ssh_run(host, "pkill -f iperf3 >/dev/null 2>&1 || true", check=False)


# def _start_iperf_server(host: str, port: int, log_file: str) -> None:
#     # Start in background; -1 exits after one client connects.
#     cmd = (
#         f"nohup iperf3 -s -1 -p {int(port)} -J --timestamp --logfile {log_file} "
#         f">/dev/null 2>&1 &"
#     )
#     ssh_run(host, cmd, check=True)
#     logging.info("IPERF: Server started on %s port=%d", host, port)


# def _iperf_client(host: str, dst: str, port: int, window: str, parallel: int, duration: int, log_file: str) -> None:
#     w_arg = "" if (not window or window == "0") else f"-w {window}"
#     cmd = (
#         f"iperf3 -c {dst} -p {int(port)} -R -J -Z "
#         f"{w_arg} -P {int(parallel)} -t {int(duration)} "
#         f"--timestamp --logfile {log_file}"
#     )
#     cp = ssh_run(host, cmd, check=True, timeout=duration + 60)
#     logging.debug("IPERF: client stdout=%s", cp.stdout)


# def _run_pair(
#     *,
#     label: str,
#     server_host: str,
#     server_port: int,
#     client_host: str,
#     client_dst: str,
#     duration: int,
#     parallel: int,
#     window: str,
#     out_dir: Path,
#     stats_hosts: dict[str, str],
# ) -> None:
#     """
#     Run one iperf test on emd points and then APs
#     """
#     out_dir.mkdir(parents=True, exist_ok=True)
#     server_log = str(out_dir / f"{label}_server_{server_host}_{server_port}.json")
#     client_log = str(out_dir / f"{label}_client_{client_host}_{server_port}.json")

#     # Start monitoring on selected hosts (one file per host)
#     stats_procs = []
#     for name, h in stats_hosts.items():
#         host_dir = out_dir / "stats"
#         host_dir.mkdir(parents=True, exist_ok=True)
#         stats_file = str(host_dir / f"{label}_{name}_P{parallel}_T{duration}.jsonl")
#         stats_procs.append(run_stats(h, duration + 8, 0, stats_file, Config._RMT_SYS_SCRIPT))

#     _start_iperf_server(server_host, server_port, server_log)
#     time.sleep(1)

#     _iperf_client(client_host, client_dst, server_port, window, parallel, duration, client_log)

#     # Ensure monitors finish
#     for p in stats_procs:
#         try:
#             p.wait(timeout=duration + 30)
#         except Exception:
#             p.kill()


# def iperf_main() -> None:
#     logging.info("IPERF: Starting iperf main: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

#     prev_congestion: str | None = None
#     prev_proxy: str | None = None

#     combos = (
#         (congestion, proxy, duration, parallel, window, run)
#         for congestion in Config._CONGESTIONS
#         for proxy in Config._PROXY
#         for duration in Config._TIME_FRAMES
#         for parallel in Config._PARALLELS
#         for window in Config._WIN_SIZE
#         for run in range(1, Config._RUN_NUM + 1)
#     )
#     total_runs = len(Config._TIME_FRAMES) * len(Config._CONGESTIONS) * len(Config._PROXY) * len(Config._PARALLELS) * Config._RUN_NUM * len(Config._WIN_SIZE)

#     for idx, (congestion, proxy, duration, parallel, window, run) in enumerate(combos, start=1):
#         if congestion != prev_congestion:
#             prev_congestion = congestion = congestion_change(Config._HOSTS, Config._CONGESTIONS, congestion)
#             time.sleep(1)

#         if proxy != prev_proxy:
#             prev_proxy = proxy = proxy_change(Config._LOCAL_STREAMHUB, Config._S2CS_HOSTS, proxy)
#             time.sleep(1)

#         logging.info("IPERF: [%d/%d] congestion=%s proxy=%s T=%s P=%s w=%s run=%d",
#                      idx, total_runs, congestion, proxy, duration, parallel, window, run)        

#         # Stop any leftover iperf3 servers
#         for host in Config._HOSTS.values():
#             stop_iperf(host)

#         # Create remote dirs & apply traffic shaping
#         base_dir = Path(Config._HOME_DIR).expanduser() / proxy / congestion / f"P{parallel}" / f"T{duration}" / f"run{run:02d}"
#         for name, host in Config._HOSTS.items():
#             mkdir(host, base_dir)
#             traffic_ctl(name, host, parallel, base_dir)

#         # end-to-end chi-cons -> C2CS_IP:BASE_PORT, server on chi-prod listening on BASE_PORT
#         endpoint_dir = base_dir / "endpoints"
#         _run_pair(
#             label="endpoints",
#             server_host="chi-prod",
#             server_port=Config._BASE_PORT,
#             client_host="chi-cons",
#             client_dst=Config._C2CS_IP,
#             duration=duration,
#             parallel=parallel,
#             window=window,
#             out_dir=endpoint_dir,
#             stats_hosts=Config._ENDPOINTS,
#         )
#         time.sleep(5)

#         # gateway-to-gateway chi-c2cs -> P2CS_IP:6666, server on chi-p2cs listening on 6666
#         s2cs_dir = base_dir / "s2cs"
#         _run_pair(
#             label="s2cs",
#             server_host="chi-p2cs",
#             server_port=6666,
#             client_host="chi-c2cs",
#             client_dst=Config._P2CS_IP,
#             duration=duration,
#             parallel=parallel,
#             window=window,
#             out_dir=s2cs_dir,
#             stats_hosts=Config._S2CS_HOSTS,
#         )

#         if run == Config._RUN_NUM:
#             logging.info("IPERF: Completed run %d/%d for this experiment point", run, Config._RUN_NUM)
#             time.sleep(2)

#     logging.info("IPERF: All experiments complete.")
