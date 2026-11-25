# from __future__ import annotations

# import logging
# import shlex
# import time
# from typing import Sequence

# from config import Config
# from remote import run_subprocess, popen_subprocess

# # The original dataset has shape (1500, 2160, 2560),
# # the sample one has shape (1500, 2, 2560).

# SERVICES = ("daq", "dist", "sirt")
# SAMPLE_FILE = "tomo_00058_all_subsampled1p_s1079s1081.h5"


# # --------------------------------------------------------------------------- #
# # Container command builders
# # --------------------------------------------------------------------------- #
# def _daq_service(port: int, file: str, i: int) -> str:
#     if file != SAMPLE_FILE:
#         simu_file_path = f"/mnt/{shlex.quote(file)}"
#         mount_drive = f"-v /home/ubuntu/mini/{shlex.quote(file)}:/mnt/{shlex.quote(file)}:ro"
#     else:
#         simu_file_path = f"/aps-mini-apps/data/{shlex.quote(file)}"
#         mount_drive = ""
#     return (
#         f"docker run --rm --network host "
#         f"--name daq-{i} "
#         f"{mount_drive} "
#         f"seenv/aps-mini-apps-daq:v1.1 "
#         f"python /aps-mini-apps/build/python/streamer-daq/DAQStream.py "
#         f"--mode 1 "
#         f"--simulation_file {simu_file_path} "
#         f"--d_iteration 10000 "
#         f"--publisher_addr tcp://*:{port} "
#         f"--iteration_sleep=0.5 "
#         f"--projection_sleep=0 "
#         f"--synch_count 1; "
#     )


# def _dist_service(ip: str, port: int, file: str, i: int) -> str:
#     num_sino = 2160 if file == "tomo_00058.h5" else 2
#     return (
#         f"docker run --rm --network host "
#         f"--name dist-{i} "
#         f"seenv/aps-mini-apps-dist:latest "
#         f"python /aps-mini-apps/build/python/streamer-dist/"
#         f"ModDistStreamPubDemo.py "
#         f"--data_source_addr tcp://{ip}:{port} "
#         f"--cast_to_float32 "
#         f"--normalize "
#         f"--my_distributor_addr tcp://127.0.0.1:{60000 + i} "
#         f"--beg_sinogram 1500 "
#         f"--num_sinograms {num_sino} "
#         f"--num_columns 2560; "
#     )


# def _sirt_service(i: int) -> str:
#     return (
#         f"docker run --rm --network host "
#         f"--name sirt-{i} "
#         f"seenv/aps-mini-apps-sirt:latest "
#         f"/aps-mini-apps/build/bin/sirt_stream "
#         f"--dest-host 127.0.0.1 "
#         f"--dest-port {60000 + i} "
#         f"--write-freq 4 "
#         f"--window-iter 1 "
#         f"--window-step 1 "
#         f"--window-length 4 "
#         f"-t 2 "
#         f"-c 1427 "
#         f"--pub-addr tcp://*:{60050 + i}; "
#     )


# # --------------------------------------------------------------------------- #
# # Container inspection helpers
# # --------------------------------------------------------------------------- #
# def _mini_names(parallel: int, services: Sequence[str] = SERVICES) -> set[str]:
#     return {f"{s}-{i}" for s in services for i in range(parallel)}


# def _container_names(cfg: Config, host: str, timeout: int, all_states: bool = False) -> set[str] | None:
#     """Names of containers on host. Running only, or all states if all_states=True.
#     Returns None if the docker command itself failed."""
#     flag = "-a " if all_states else ""
#     cp = run_subprocess(
#         host, None,
#         f"docker ps {flag}--format '{{{{.Names}}}}'",
#         localhost=cfg.localhost,
#         timeout=timeout,
#         check=False,
#     )
#     if cp.returncode != 0:
#         logging.warning("MINI: 'docker ps' failed on %s\nSTDERR:\n%s", host.upper(), cp.stderr)
#         return None
#     return set(cp.stdout.split())


# def _get_container_stats(cfg: Config, host: str, timeout: int, check: bool = False) -> int:
#     """Number of running containers on host (all containers, not just mini app)."""
#     cp = run_subprocess(
#         host, None,
#         "docker ps -q | wc -l",
#         localhost=cfg.localhost,
#         timeout=timeout,
#         check=check,
#     )
#     if cp.returncode != 0:
#         raise RuntimeError(
#             f"MINI: Failed checking the container stats on {host.upper()}\n"
#             f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
#     try:
#         return int(cp.stdout.strip())
#     except ValueError as e:
#         raise RuntimeError(f"MINI: Unexpected 'docker ps' output on {host.upper()}: {cp.stdout!r}") from e


# def _count_mini_running(cfg: Config, host: str, parallel: int, timeout: int) -> int | None:
#     names = _container_names(cfg, host, timeout)
#     if names is None:
#         return None
#     return len(names & _mini_names(parallel))


# def _wait_for_containers(
#     cfg: Config,
#     host: str,
#     service: str,
#     parallel: int,
#     timeout: int,
#     retries: int = 3,
#     delay: float = 1.0,
#     stable_checks: int = 2,
# ) -> bool:
#     """Wait until service-0 .. service-(parallel-1) are all running on host,
#     seen in `stable_checks` consecutive polls (catches crash-on-start)."""
#     expected = _mini_names(parallel, (service,))
#     missing = expected
#     streak = 0
#     for _ in range(retries):
#         names = _container_names(cfg, host, timeout)
#         if names is not None:
#             missing = expected - names
#             if not missing:
#                 streak += 1
#                 if streak >= stable_checks:
#                     logging.debug("MINI: Started %dx %s containers on %s",
#                                   parallel, service.upper(), host.upper())
#                     return True
#             else:
#                 streak = 0
#         time.sleep(delay)

#     logging.warning("MINI: %s containers missing on %s: %s | running: %s",
#                     service.upper(), host.upper(), sorted(missing),
#                     sorted(_container_names(cfg, host, timeout) or []))
#     return False


# def _cleanup_mini(cfg: Config, parallel: int, timeout: int, retries: int = 15) -> None:
#     """Force-remove all mini app containers and wait until their names are free,
#     so a relaunch with the same --name cannot conflict."""
#     names = sorted(_mini_names(parallel))
#     for host in set(cfg.hosts.ep.values()):
#         run_subprocess(
#             host, None,
#             f"docker rm -f {' '.join(names)} >/dev/null 2>&1 || true",
#             localhost=cfg.localhost,
#             timeout=timeout,
#             check=False,
#         )
#         for _ in range(retries):
#             existing = _container_names(cfg, host, timeout, all_states=True)
#             if existing is not None and not (existing & set(names)):
#                 break
#             time.sleep(1)
#         else:
#             raise RuntimeError(f"MINI: Mini app containers were not removed on {host.upper()}")


# # --------------------------------------------------------------------------- #
# # Launching
# # --------------------------------------------------------------------------- #
# def _start_mini_service(
#     cfg: Config,
#     host: str,
#     parallel: int,
#     numa: str,
#     app: str,
#     service: str,
#     start_port: int,
#     stream_ids: Sequence[str],
#     listen_ports: Sequence[int],
#     listener_ip: str,
#     timeout: int,
#     file: str,
#     out_dir: str,
#     attempt: int = 1,
#     module_path: str = "/tmp/temp_files",
#     check: bool = True,
# ) -> None:
#     delay = 0.2 if ("kans" in cfg.tname or "utah" in cfg.tname) else 0.15

#     for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
#         if app == "mini_gst":
#             if service == "daq":
#                 wrapper_cmd = f"globus-streams-launch -p {start_port + (i * 2)} {stream_id} "
#             else:
#                 wrapper_cmd = f"globus-streams-launch {stream_id} "
#         else:
#             wrapper_cmd = ""

#         if service == "daq":
#             if app in ("mini_gst", "mini_base"):
#                 cmd = _daq_service(start_port + (i * 2), file, i)
#             elif app == "mini_sci":
#                 cmd = _daq_service(cfg.inbound_ports[0] + (i * 5), file, i)
#             else:
#                 raise ValueError(f"MINI: Unknown app {app!r} for DAQ service")
#         elif service == "dist":
#             cmd = _dist_service(listener_ip, listen_port, file, i)
#         elif service == "sirt":
#             cmd = _sirt_service(i)
#         else:
#             raise ValueError(f"MINI: Unknown service {service!r}")

#         time.sleep(delay)
#         logging.debug("DEBUG: Command to run the containers: %s %s", wrapper_cmd, cmd)

#         # attempt number in the log names so earlier failed attempts are kept
#         base = f"{shlex.quote(out_dir)}/{shlex.quote(app)}-{service}-{i}-a{attempt}"
#         popen_subprocess(
#             host, None,
#             f"set +x; mkdir -p {shlex.quote(out_dir)} && "
#             f"{{ "
#             f'echo "START $(date \'+%Y-%m-%d %H:%M:%S\')"; '
#             f"/usr/bin/time -vvv -o {base}-time.log "
#             f"{wrapper_cmd} {cmd} "
#             f"rc=$?; "
#             f'echo "END $(date \'+%Y-%m-%d %H:%M:%S\') rc=$rc"; '
#             f"exit $rc; "
#             f"}} 2>&1 "
#             f"| tr '\\r' '\\n' "
#             f"| stdbuf -oL awk "
#             f"'NF {{ print $0; fflush(); }}' "
#             f"| tee {base}.log "
#             f"> /dev/null & ",
#             localhost=cfg.localhost,
#         )
#     logging.info("MINI: Launched %s containers on %s", service.upper(), host.upper())


# def start_mini_app(
#     cfg: Config,
#     parallel: int,
#     numa: str,
#     app: str,
#     start_port: int,
#     listen_ip: str,
#     listen_ports: Sequence[int],
#     stream_ids: Sequence[str],
#     out_dir: str,
#     file: str,
#     timeout: int,
#     module_path: str = "/tmp/temp_files",
#     retries: int = 3,
#     check: bool = True,
# ) -> None:
#     if not (len(listen_ports) == parallel and len(stream_ids) == parallel):
#         raise RuntimeError(
#             f"Expected all mini lists to have length parallel={parallel}, "
#             f"got ports={len(listen_ports)}, stream_ids={len(stream_ids)}")

#     stages = [
#         (cfg.hosts.ep["listener"], "daq"),
#         (cfg.hosts.ep["initiator"], "dist"),
#         (cfg.hosts.ep["initiator"], "sirt"),
#     ]

#     logging.info("MINI: Starting APS mini app containers on the endpoints")
#     for attempt in range(1, retries + 1):
#         _cleanup_mini(cfg, parallel, timeout)          # clean slate every attempt

#         for host, service in stages:
#             logging.debug("MINI: Starting APS mini app's %s service on %s (attempt %d/%d)",
#                           service.upper(), host.upper(), attempt, retries)
#             _start_mini_service(
#                 cfg, host, parallel, numa, app, service, start_port,
#                 stream_ids, listen_ports, listen_ip, timeout, file, out_dir,
#                 attempt=attempt,
#             )
#             if not _wait_for_containers(cfg, host, service, parallel, timeout):
#                 logging.warning("MINI: %s failed to start, attempt %d/%d",
#                                 service.upper(), attempt, retries)
#                 break                                   # restart the whole chain
#         else:
#             logging.info("MINI: APS mini app containers on the endpoints are online")
#             return                                      # every stage succeeded

#     _cleanup_mini(cfg, parallel, timeout)
#     raise RuntimeError(f"MINI: APS mini app containers failed to start after {retries} attempts")


# # --------------------------------------------------------------------------- #
# # Waiting / stopping / cleanup
# # --------------------------------------------------------------------------- #
# def wait_finish_transfer(
#     cfg: Config,
#     parallel: int,
#     app: str,
#     out_dir: str,
#     timeout: int,
#     retries: int = 10,
#     check: bool = False,
# ) -> None:
#     listener, initiator = cfg.hosts.ep["listener"], cfg.hosts.ep["initiator"]
#     listener_status = initiator_status = None
#     for retry in range(1, retries + 1):
#         listener_status = _count_mini_running(cfg, listener, parallel, timeout)
#         initiator_status = _count_mini_running(cfg, initiator, parallel, timeout)

#         if listener_status == 0 and initiator_status == 0:
#             logging.info("MINI: Finished APS mini app's transfers on the endpoints")
#             return
#         if retry < retries:
#             time.sleep(cfg.sleep)

#     raise RuntimeError(
#         f"MINI: Containers didn't finish the transfer after {retries * cfg.sleep} seconds. "
#         f"listener={listener_status}, initiator={initiator_status}")


# def stop_mini_containers(
#     cfg: Config,
#     parallel: int,
#     app: str,
#     out_dir: str,
#     timeout: int,
#     check: bool = False,
# ) -> None:
#     """Kill only the mini app containers (daq-*, dist-*, sirt-*)."""
#     names = " ".join(sorted(_mini_names(parallel)))
#     for host in set(cfg.hosts.ep.values()):
#         cp = run_subprocess(
#             host, None,
#             f"docker kill {names} >/dev/null 2>&1 || true",
#             localhost=cfg.localhost,
#             timeout=timeout,
#             check=check,
#         )
#         if check and cp.returncode != 0:
#             raise RuntimeError(
#                 f"MINI: Failed stopping the containers on {host.upper()}\n"
#                 f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
#         logging.debug("MINI stdout on %s:\n%s", host.upper(), cp.stdout)
#     logging.info("MINI: Stopped APS mini app's containers on the endpoints")


# def prune_containers(
#     cfg: Config,
#     parallel: int,
#     app: str,
#     out_dir: str,
#     timeout: int,
#     retries: int = 5,
#     check: bool = False,
# ) -> None:
#     """Remove mini app containers, wait until gone, then prune stopped containers."""
#     _cleanup_mini(cfg, parallel, timeout, retries=max(retries, 1) * max(int(cfg.sleep), 1))
#     for host in set(cfg.hosts.ep.values()):
#         cp = run_subprocess(
#             host, None,
#             "docker container prune -f",
#             localhost=cfg.localhost,
#             timeout=timeout,
#             check=check,
#         )
#         if check and cp.returncode != 0:
#             raise RuntimeError(
#                 f"MINI: Failed pruning the containers on {host.upper()}\n"
#                 f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
#     logging.info("MINI: Pruned APS mini app's containers on the endpoints")

from __future__ import annotations

import logging
import shlex
import time
from typing import Sequence

from config import Config
from remote import run_subprocess, popen_subprocess

# The original dataset has shape (1500, 2160, 2560),
# the sample one has shape (1500, 2, 2560).

SERVICES = ("daq", "dist", "sirt")
SAMPLE_FILE = "tomo_00058_all_subsampled1p_s1079s1081.h5"


# --------------------------------------------------------------------------- #
# Container command builders
# --------------------------------------------------------------------------- #
def _daq_service(port: int, file: str, i: int) -> str:
    if file != SAMPLE_FILE:
        simu_file_path = f"/mnt/{shlex.quote(file)}"
        mount_drive = f"-v /home/ubuntu/mini/{shlex.quote(file)}:/mnt/{shlex.quote(file)}:ro"
    else:
        simu_file_path = f"/aps-mini-apps/data/{shlex.quote(file)}"
        mount_drive = ""
    return (
        f"docker run --rm --network host "
        f"--name daq-{i} "
        f"{mount_drive} "
        f"seenv/aps-mini-apps-daq:v1.1 "
        f"python /aps-mini-apps/build/python/streamer-daq/DAQStream.py "
        f"--mode 1 "
        f"--simulation_file {simu_file_path} "
        f"--d_iteration 10000 "
        f"--publisher_addr tcp://*:{port} "
        f"--iteration_sleep=0.5 "
        f"--projection_sleep=0 "
        f"--synch_count 1; "
    )


def _dist_service(ip: str, port: int, file: str, i: int) -> str:
    num_sino = 2160 if file == "tomo_00058.h5" else 2
    return (
        f"docker run --rm --network host "
        f"--name dist-{i} "
        f"seenv/aps-mini-apps-dist:latest "
        f"python /aps-mini-apps/build/python/streamer-dist/"
        f"ModDistStreamPubDemo.py "
        f"--data_source_addr tcp://{ip}:{port} "
        f"--cast_to_float32 "
        f"--normalize "
        f"--my_distributor_addr tcp://127.0.0.1:{60000 + i} "
        f"--beg_sinogram 1500 "
        f"--num_sinograms {num_sino} "
        f"--num_columns 2560; "
    )


def _sirt_service(i: int) -> str:
    return (
        f"docker run --rm --network host "
        f"--name sirt-{i} "
        f"seenv/aps-mini-apps-sirt:latest "
        f"/aps-mini-apps/build/bin/sirt_stream "
        f"--dest-host 127.0.0.1 "
        f"--dest-port {60000 + i} "
        f"--write-freq 4 "
        f"--window-iter 1 "
        f"--window-step 1 "
        f"--window-length 4 "
        f"-t 2 "
        f"-c 1427 "
        f"--pub-addr tcp://*:{60050 + i}; "
    )


# --------------------------------------------------------------------------- #
# Container inspection helpers
# --------------------------------------------------------------------------- #
def _mini_names(parallel: int, services: Sequence[str] = SERVICES) -> set[str]:
    return {f"{s}-{i}" for s in services for i in range(parallel)}


def _container_names(cfg: Config, host: str, timeout: int, all_states: bool = False) -> set[str] | None:
    """Names of containers on host. Running only, or all states if all_states=True.
    Returns None if the docker command itself failed."""
    flag = "-a " if all_states else ""
    cp = run_subprocess(
        host, None,
        f"docker ps {flag}--format '{{{{.Names}}}}'",
        localhost=cfg.localhost,
        timeout=timeout,
        check=False,
    )
    if cp.returncode != 0:
        logging.warning("MINI: 'docker ps' failed on %s\nSTDERR:\n%s", host.upper(), cp.stderr)
        return None
    return set(cp.stdout.split())


def _get_container_stats(cfg: Config, host: str, timeout: int, check: bool = False) -> int:
    """Number of running containers on host (all containers, not just mini app)."""
    cp = run_subprocess(
        host, None,
        "docker ps -q | wc -l",
        localhost=cfg.localhost,
        timeout=timeout,
        check=check,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"MINI: Failed checking the container stats on {host.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    try:
        return int(cp.stdout.strip())
    except ValueError as e:
        raise RuntimeError(f"MINI: Unexpected 'docker ps' output on {host.upper()}: {cp.stdout!r}") from e


def _count_mini_running(cfg: Config, host: str, parallel: int, timeout: int) -> int | None:
    names = _container_names(cfg, host, timeout)
    if names is None:
        return None
    return len(names & _mini_names(parallel))


def _wait_for_containers(
    cfg: Config,
    expected: dict[str, set[str]],
    timeout: int,
    retries: int = 5,
    delay: float = 1.0,
    stable_checks: int = 2,
) -> bool:
    """Wait until every expected container is running on its host.

    `expected` maps host -> container names that must be up there. It is
    cumulative: once SIRT is launched, the initiator must have dist-* AND
    sirt-* running (2 x parallel), and the listener must still have daq-*.
    All of them must be seen in `stable_checks` consecutive polls
    (catches containers that crash right after starting)."""
    missing: dict[str, set[str]] = dict(expected)
    streak = 0
    for _ in range(retries):
        missing = {}
        for host, names_needed in expected.items():
            running = _container_names(cfg, host, timeout)
            gone = names_needed - (running or set())
            if gone:
                missing[host] = gone
        if not missing:
            streak += 1
            if streak >= stable_checks:
                for host, names_needed in expected.items():
                    logging.debug("MINI: %d mini containers running on %s: %s",
                                  len(names_needed), host.upper(), sorted(names_needed))
                return True
        else:
            streak = 0
        time.sleep(delay)

    for host, gone in missing.items():
        logging.warning("MINI: Expected %d containers on %s, missing: %s | running: %s",
                        len(expected[host]), host.upper(), sorted(gone),
                        sorted(_container_names(cfg, host, timeout) or []))
    return False


def _cleanup_mini(cfg: Config, parallel: int, timeout: int, retries: int = 5) -> None:
    """Force-remove all mini app containers and wait until their names are free,
    so a relaunch with the same --name cannot conflict."""
    names = sorted(_mini_names(parallel))
    for host in set(cfg.hosts.ep.values()):
        run_subprocess(
            host, None,
            f"docker rm -f {' '.join(names)} >/dev/null 2>&1 || true",
            localhost=cfg.localhost,
            timeout=timeout,
            check=False,
        )
        for _ in range(retries):
            existing = _container_names(cfg, host, timeout, all_states=True)
            if existing is not None and not (existing & set(names)):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"MINI: Mini app containers were not removed on {host.upper()}")


# --------------------------------------------------------------------------- #
# Launching
# --------------------------------------------------------------------------- #
def _start_mini_service(
    cfg: Config,
    host: str,
    parallel: int,
    numa: str,
    app: str,
    service: str,
    start_port: int,
    stream_ids: Sequence[str],
    listen_ports: Sequence[int],
    listener_ip: str,
    timeout: int,
    file: str,
    out_dir: str,
    attempt: int = 1,
    module_path: str = "/tmp/temp_files",
    check: bool = True,
) -> None:
    delay = 0.2 #if ("kans" in cfg.tname or "utah" in cfg.tname) else 0.15

    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        if app == "mini_gst":
            if service == "daq":
                wrapper_cmd = f"globus-streams-launch -p {start_port + (i * 2)} {stream_id} "
            else:
                wrapper_cmd = f"globus-streams-launch {stream_id} "
        else:
            wrapper_cmd = ""

        if service == "daq":
            if app in ("mini_gst", "mini_base"):
                cmd = _daq_service(start_port + (i * 2), file, i)
            elif app == "mini_sci":
                cmd = _daq_service(cfg.inbound_ports[0] + (i * 5), file, i)
            else:
                raise ValueError(f"MINI: Unknown app {app!r} for DAQ service")
        elif service == "dist":
            cmd = _dist_service(listener_ip, listen_port, file, i)
        elif service == "sirt":
            cmd = _sirt_service(i)
        else:
            raise ValueError(f"MINI: Unknown service {service!r}")

        time.sleep(delay)
        logging.debug("DEBUG: Command to run the containers: %s %s", wrapper_cmd, cmd)

        # attempt number in the log names so earlier failed attempts are kept
        base = f"{shlex.quote(out_dir)}/{shlex.quote(app)}-{service}-{i}-a{attempt}"
        popen_subprocess(
            host, None,
            f"set +x; mkdir -p {shlex.quote(out_dir)} && "
            f"{{ "
            f'echo "START $(date \'+%Y-%m-%d %H:%M:%S\')"; '
            f"/usr/bin/time -vvv -o {base}-time.log "
            f"{wrapper_cmd} {cmd} "
            f"rc=$?; "
            f'echo "END $(date \'+%Y-%m-%d %H:%M:%S\') rc=$rc"; '
            f"exit $rc; "
            f"}} 2>&1 "
            f"| tr '\\r' '\\n' "
            f"| stdbuf -oL awk "
            f"'NF {{ print $0; fflush(); }}' "
            f"| tee {base}.log "
            f"> /dev/null & ",
            localhost=cfg.localhost,
        )
    logging.info("MINI: Launched %s containers on %s", service.upper(), host.upper())


def start_mini_app(
    cfg: Config,
    parallel: int,
    numa: str,
    app: str,
    start_port: int,
    listen_ip: str,
    listen_ports: Sequence[int],
    stream_ids: Sequence[str],
    out_dir: str,
    file: str,
    timeout: int,
    module_path: str = "/tmp/temp_files",
    retries: int = 5,
    check: bool = True,
) -> None:
    if not (len(listen_ports) == parallel and len(stream_ids) == parallel):
        raise RuntimeError(
            f"Expected all mini lists to have length parallel={parallel}, "
            f"got ports={len(listen_ports)}, stream_ids={len(stream_ids)}")

    listener, initiator = cfg.hosts.ep["listener"], cfg.hosts.ep["initiator"]

    # Strict order: DAQ (listener) -> DIST (initiator) -> SIRT (initiator).
    # DIST and SIRT share the initiator, so after SIRT starts the initiator
    # must have 2 x parallel mini containers (dist-* + sirt-*).
    stages = [
        (listener, "daq"),
        (initiator, "dist"),
        (initiator, "sirt"),
    ]

    logging.info("MINI: Starting APS mini app containers on the endpoints")
    for attempt in range(1, retries + 1):
        _cleanup_mini(cfg, parallel, timeout)          # clean slate every attempt
        expected: dict[str, set[str]] = {}             # host -> names launched so far

        for host, service in stages:
            logging.debug("MINI: Starting APS mini app's %s service on %s (attempt %d/%d)",
                          service.upper(), host.upper(), attempt, retries)
            _start_mini_service(
                cfg, host, parallel, numa, app, service, start_port,
                stream_ids, listen_ports, listen_ip, timeout, file, out_dir,
                attempt=attempt,
            )
            expected.setdefault(host, set()).update(_mini_names(parallel, (service,)))
            # daq: listener = parallel
            # dist: listener = parallel (daq still up), initiator = parallel
            # sirt: listener = parallel (daq still up), initiator = 2 x parallel
            if not _wait_for_containers(cfg, expected, timeout):
                logging.warning("MINI: %s failed to start, attempt %d/%d",
                                service.upper(), attempt, retries)
                break                                   # restart the whole chain
        else:
            logging.info("MINI: APS mini app containers on the endpoints are online")
            return                                      # every stage succeeded

    _cleanup_mini(cfg, parallel, timeout)
    raise RuntimeError(f"MINI: APS mini app containers failed to start after {retries} attempts")


# --------------------------------------------------------------------------- #
# Waiting / stopping / cleanup
# --------------------------------------------------------------------------- #
def wait_finish_transfer(
    cfg: Config,
    parallel: int,
    app: str,
    out_dir: str,
    timeout: int,
    retries: int = 10,
    check: bool = False,
) -> None:
    listener, initiator = cfg.hosts.ep["listener"], cfg.hosts.ep["initiator"]
    listener_status = initiator_status = None
    for retry in range(1, retries + 1):
        listener_status = _count_mini_running(cfg, listener, parallel, timeout)
        initiator_status = _count_mini_running(cfg, initiator, parallel, timeout)

        if listener_status == 0 and initiator_status == 0:
            logging.info("MINI: Finished APS mini app's transfers on the endpoints")
            return
        if retry < retries:
            time.sleep(cfg.sleep)

    raise RuntimeError(
        f"MINI: Containers didn't finish the transfer after {retries * cfg.sleep} seconds. "
        f"listener={listener_status}, initiator={initiator_status}")


def stop_mini_containers(
    cfg: Config,
    parallel: int,
    app: str,
    out_dir: str,
    timeout: int,
    check: bool = False,
) -> None:
    """Kill only the mini app containers (daq-*, dist-*, sirt-*)."""
    names = " ".join(sorted(_mini_names(parallel)))
    for host in set(cfg.hosts.ep.values()):
        cp = run_subprocess(
            host, None,
            f"docker kill {names} >/dev/null 2>&1 || true",
            localhost=cfg.localhost,
            timeout=timeout,
            check=check,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"MINI: Failed stopping the containers on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
        logging.debug("MINI stdout on %s:\n%s", host.upper(), cp.stdout)
    logging.info("MINI: Stopped APS mini app's containers on the endpoints")


def prune_containers(
    cfg: Config,
    parallel: int,
    app: str,
    out_dir: str,
    timeout: int,
    retries: int = 5,
    check: bool = False,
) -> None:
    """Remove mini app containers, wait until gone, then prune stopped containers."""
    _cleanup_mini(cfg, parallel, timeout, retries=max(retries, 1) * max(int(cfg.sleep), 1))
    for host in set(cfg.hosts.ep.values()):
        cp = run_subprocess(
            host, None,
            "docker container prune -f",
            localhost=cfg.localhost,
            timeout=timeout,
            check=check,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"MINI: Failed pruning the containers on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")
    logging.info("MINI: Pruned APS mini app's containers on the endpoints")
