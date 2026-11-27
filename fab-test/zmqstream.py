"""
Remote helpers for the ZeroMQ streaming experiments.
All remote work goes through your remote.run_subprocess / popen_subprocess.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from config import Config
from remote import run_subprocess, popen_subprocess
from gstreams import (
    get_stream_id, start_tunnel, status_tunnel, stop_tunnel, delete_tunnel,
    init_listener_env, init_initiator_env, start_globus_streams, stop_globus_streams
)
from zmq_config import ZmqOpts

EXIT_MISMATCH = 3


class CountMismatchError(Exception):
    """PUSH/PULL consumer received a different number of samples than sent."""


@dataclass(frozen=True)
class ProducerSpec:
    host: str
    port: int
    wrapper: str = ""      # e.g. "globus-streams-launch -p <port> <tunnel_id>"


@dataclass(frozen=True)
class ConsumerSpec:
    host: str
    ip: str
    port: int
    wrapper: str = ""      # e.g. "globus-streams-launch <tunnel_id>"


def _all_hosts(cfg: Config) -> list[str]:
    return list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())


# ------------------------------------------------------------------------------
# Command builders
def _wrap_remote(out_dir: str, tag: str, inner: str) -> str:
    q = shlex.quote(out_dir)
    return (
        f"set +x; mkdir -p {q} && "
        f"{{ set +e; "
        f'echo "START $(date \'+%Y-%m-%d %H:%M:%S\')"; '
        f"{inner}; "
        f"rc=$?; "
        f'echo "END $(date \'+%Y-%m-%d %H:%M:%S\') rc=$rc"; '
        f"exit $rc; "
        f"}} 2>&1 "
        f"| tr '\\r' '\\n' "
        f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
        f"| tee {q}/{shlex.quote(tag)}.log; "
        # the remote shell does not run with pipefail (ssh joins argv into one string),
        # so return the exit code of the wrapped command, not the one of `tee`
        f'exit "${{PIPESTATUS[0]}}"'
    )


def sync_dir(out_dir: str, app_tag: str) -> str:
    """Folder for the start barrier files of one app (ready-<i> on the consumer host,
    go on the producer host)."""
    return f"{out_dir}/.sync-{app_tag}"


def producer_cmd(opts: ZmqOpts, spec: ProducerSpec, *, tag: str, out_dir: str,
                 sample_size: int, stream_bytes: int, go_file: str) -> str:
    q = shlex.quote(out_dir)
    inner = (
        f'cd "{opts.src_dir}" && '
        f"PYTHONUNBUFFERED=1 /usr/bin/time -vvv -o {q}/{shlex.quote(tag)}-time.log "
        f"{spec.wrapper} python zmq_producer.py "
        f"--port {spec.port} --pattern {opts.pattern} "
        f"--sample-size {sample_size} --total-bytes {stream_bytes} "
        f"--hwm {opts.hwm} --start-timeout {opts.start_timeout} "
        f"--go-file {shlex.quote(go_file)} --go-timeout {opts.ready_timeout + 60} "
        f"--out {q} --tag {shlex.quote(tag)}"
    )
    return _wrap_remote(out_dir, tag, inner)


def consumer_cmd(opts: ZmqOpts, spec: ConsumerSpec, *, tag: str, out_dir: str,
                 sample_size: int, ready_file: str) -> str:
    q = shlex.quote(out_dir)
    inner = (
        f'cd "{opts.src_dir}" && '
        f"PYTHONUNBUFFERED=1 /usr/bin/time -vvv -o {q}/{shlex.quote(tag)}-time.log "
        f"{spec.wrapper} python zmq_consumer.py "
        f"--connect-ip {shlex.quote(spec.ip)} --port {spec.port} --pattern {opts.pattern} "
        f"--sample-size {sample_size} --hwm {opts.hwm} "
        f"--start-timeout {opts.start_timeout} --idle-timeout {opts.idle_timeout} "
        f"--ready-file {shlex.quote(ready_file)} --first-data-timeout {opts.ready_timeout + 60} "
        f"--out {q} --tag {shlex.quote(tag)}"
    )
    return _wrap_remote(out_dir, tag, inner)


# ------------------------------------------------------------------------------
# Cleanup
def kill_zmq(cfg: Config) -> None:
    for host in cfg.hosts.ep.values():
        run_subprocess(
            host, None,
            r"pkill -TERM -f '[z]mq_producer\.py' || true; "
            r"pkill -TERM -f '[z]mq_consumer\.py' || true; "
            r"sleep 1; "
            r"pkill -KILL -f '[z]mq_producer\.py' || true; "
            r"pkill -KILL -f '[z]mq_consumer\.py' || true",
            localhost=cfg.localhost,
        )
    logging.debug("ZMQ: Killed leftover producer/consumer processes on the EPs")


def delete_app_files(cfg: Config, out_dir: str, app_tag: str) -> None:
    q, t = shlex.quote(out_dir), shlex.quote(app_tag)
    for host in _all_hosts(cfg):
        run_subprocess(host, None, f"rm -f {q}/{t}-* {q}/{t}.log", localhost=cfg.localhost)
    logging.info("ZMQ: Deleted %s files in %s on all hosts", app_tag, out_dir)


# ------------------------------------------------------------------------------
# Statkit (no --duration; started/stopped explicitly around each stream)
_STATKIT_PATTERN = "[g]lobus-gridftp-server|[h]aproxy|[i]perf3|[r]sync|[d]ocker"

# def start_statkit_zmq(cfg: Config, app: str, out_dir: str) -> None:
#     q, a = shlex.quote(out_dir), shlex.quote(app)
#     for host in _all_hosts(cfg):

#         run_subprocess(
#             host, cfg.remote_env,
#             f"mkdir -p {q} && "
#             f"pids=$(pgrep -d, -f {shlex.quote(_STATKIT_PATTERN)} || true); "
#             f"nohup python ~/statkit/monitor/launcher.py --pids \"$pids\" "
#             f"--out {q} --app {a} > {q}/{a}-launcher.out 2>&1 < /dev/null & "
#             f"echo $! > {q}/{a}-launcher.pid",
#             localhost=cfg.localhost,
#         )
#     logging.info("ZMQ: Started statkit (%s) on all hosts", app)

def start_statkit_zmq(cfg: Config, app: str, out_dir: str) -> None:
    q, a = shlex.quote(out_dir), shlex.quote(app)
    for host in _all_hosts(cfg):
        pattern = "[g]lobus-gridftp-server|[h]aproxy|[i]perf3|[r]sync|[d]ocker"
        names = "globus-gridftp-server,haproxy,iperf3,rsync,docker"
        cp = popen_subprocess(
            host, cfg.remote_env,
            f"mkdir -p {q} && "
            f"pids=$(pgrep -d, -f {shlex.quote(pattern)} || true); "
            f"python ~/statkit/monitor/launcher.py --pids \"$pids\" "
            f"--process-names {shlex.quote(names)} "
            f"--out {q} --app {a} > {q}/{a}-launcher.out 2>&1 < /dev/null & "
            f"echo $! > {q}/{a}-launcher.pid",
            localhost=cfg.localhost,
        )
        logging.info("ZMQ: Started statkit (%s) on all hosts", app)


def check_statkit_alive(cfg: Config, app: str, out_dir: str) -> None:
    pidfile = shlex.quote(f"{out_dir}/{app}-launcher.pid")
    for host in _all_hosts(cfg):
        cp = run_subprocess(host, None, f'kill -0 "$(cat {pidfile})"',
                            localhost=cfg.localhost, check=False)
        if cp.returncode != 0:
            raise RuntimeError(f"STATKIT: launcher is not running on {host.upper()} ({pidfile})")


def stop_statkit_zmq(cfg: Config) -> None:
    for host in _all_hosts(cfg):
        run_subprocess(host, None, r"pkill -TERM -f 'monitor/launcher\.py' || true",
                       localhost=cfg.localhost)
    time.sleep(1)
    logging.info("ZMQ: Stopped statkit on all hosts")


# ------------------------------------------------------------------------------
# Globus Streams tunnels (uses your gstreams.py building blocks)
def setup_gst(cfg: Config, parallel: int, start_port: int, label: str,
              tunnels: list[str], timeout: int) -> tuple[list[str], list[int]]:
    """Creates `parallel` tunnels and fills `tunnels` in place (so a partial setup
    can still be torn down by the caller). Returns connector IPs and ports."""
    ids = get_stream_id(cfg)
    logging.info("ZGST: Creating %d tunnels", parallel)
    for i in range(parallel):
        tunnels.append(start_tunnel(cfg, ids["initiator"], ids["listener"], f"{label}-p{i}", timeout))
    for t in tunnels:
        status_tunnel(cfg, t, "AWAITING_LISTENER")

    logging.info("ZGST: Bringing up %d tunnels on the endpoints", parallel)
    gw_ips, gw_ports = [], []
    for i, t in enumerate(tunnels):
        init_listener_env(cfg, cfg.listener_ip, t, start_port + (i * 2))
        status_tunnel(cfg, t, "ACTIVE")
        _contact_port, gw_ip, gw_port = init_initiator_env(cfg, t)
        gw_ips.append(gw_ip)
        gw_ports.append(gw_port)

    for t in tunnels:                       # all must still be ACTIVE before streaming
        status_tunnel(cfg, t, "ACTIVE")
    logging.info("ZGST: All %d tunnels ACTIVE", parallel)
    return gw_ips, gw_ports


def teardown_gst(cfg: Config, tunnels: list[str]) -> None:
    if not tunnels:
        return
    logging.info("ZGST: Stopping %d tunnel(s)", len(tunnels))
    # for t in tunnels:
    #     stop_tunnel(cfg, t)
    # for t in tunnels:
    #     status_tunnel(cfg, t, "STOPPED")
    #     delete_tunnel(cfg, t)
    stop_globus_streams(cfg, tunnels)


# ------------------------------------------------------------------------------
# One stream test: statkit -> producers -> consumers -> wait -> statkit stop
def _tail(p: subprocess.Popen[str]) -> str:
    try:
        out, err = p.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        return "<no output>"
    return f"STDOUT:\n{(out or '')[-1500:]}\nSTDERR:\n{(err or '')[-1500:]}"


TunnelWatch = Callable[[], dict]   # returns {tunnel_id: state} for tunnels that are NOT ACTIVE


def _check_procs(procs: list[tuple[str, int, str, subprocess.Popen[str]]], done: set[int],
                 mismatches: list[str]) -> None:
    """Record finished processes; raise on any failure other than a PUSH/PULL mismatch."""
    for k, (role, i, host, p) in enumerate(procs):
        if k in done:
            continue
        rc = p.poll()
        if rc is None:
            continue
        done.add(k)
        if rc == 0:
            continue
        if role == "consumer" and rc == EXIT_MISMATCH:
            mismatches.append(f"{role}-{i}@{host}")
            continue
        raise RuntimeError(f"ZMQ: {role} {i} on {host.upper()} failed (rc={rc})\n{_tail(p)}")


def _tunnels_ok(tunnel_watch: Optional[TunnelWatch], when: str) -> None:
    if tunnel_watch is None:
        return
    bad = tunnel_watch()
    if bad:
        raise RuntimeError(f"ZMQ: tunnel(s) not ACTIVE {when}: "
                           + ", ".join(f"{t} = {st}" for t, st in bad.items()))


def _wait_all(procs: list[tuple[str, int, str, subprocess.Popen[str]]], timeout: int,
              tunnel_watch: Optional[TunnelWatch] = None, watch_every: float = 10.0) -> list[str]:
    deadline = time.monotonic() + timeout
    next_watch = time.monotonic() + watch_every
    mismatches: list[str] = []
    done: set[int] = set()
    while len(done) < len(procs):
        _check_procs(procs, done, mismatches)
        if len(done) == len(procs):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"ZMQ: stream did not finish within {timeout}s")
        if tunnel_watch is not None and time.monotonic() >= next_watch:
            _tunnels_ok(tunnel_watch, "while streaming")
            next_watch = time.monotonic() + watch_every
        time.sleep(0.5)
    return mismatches


def _ready_count(cfg: Config, host: str, sync: str) -> list[int]:
    """Indices of consumers whose ready file exists."""
    cp = run_subprocess(host, None, f"ls {shlex.quote(sync)} 2>/dev/null || true",
                        localhost=cfg.localhost, check=False)
    return sorted(int(n.split("-", 1)[1]) for n in cp.stdout.split() if n.startswith("ready-"))


def _barrier(cfg: Config, opts: ZmqOpts, procs, done, mismatches, *, cons_host: str, sync: str,
             n: int, app_tag: str) -> None:
    """Wait until every consumer has received its HEADER (whole path works)."""
    deadline = time.monotonic() + opts.ready_timeout
    ready: list[int] = []
    while time.monotonic() < deadline:
        _check_procs(procs, done, mismatches)          # a process that died before GO is an error
        if done:
            raise RuntimeError(f"ZMQ: {app_tag}: a process exited before the start barrier")
        ready = _ready_count(cfg, cons_host, sync)
        if len(ready) == n:
            return
        time.sleep(1.0)
    missing = sorted(set(range(n)) - set(ready))
    raise TimeoutError(f"ZMQ: {app_tag}: only {len(ready)}/{n} streams ready after "
                       f"{opts.ready_timeout}s; not ready: {missing}")


def _send_go(cfg: Config, prod_host: str, sync: str, delay_s: float = 1.0) -> None:
    """Write the start time (now + delay, CLOCK_REALTIME of the producer host) atomically."""
    q = shlex.quote(sync)
    run_subprocess(prod_host, None,
                   f"mkdir -p {q} && echo $(( $(date +%s%N) + {int(delay_s * 1e9)} )) > {q}/go.tmp "
                   f"&& mv {q}/go.tmp {q}/go",
                   localhost=cfg.localhost)


def _leftovers(cfg: Config) -> dict[str, int]:
    out = {}
    for host in cfg.hosts.ep.values():
        cp = run_subprocess(host, None, r"pgrep -fc '[z]mq_(producer|consumer)\.py' || true",
                            localhost=cfg.localhost, check=False)
        try:
            n = int(cp.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            n = 0
        if n:
            out[host] = n
    return out


def run_stream(cfg: Config, opts: ZmqOpts, *, app_tag: str, out_dir: str, sample_size: int,
               stream_bytes: int, producers: list[ProducerSpec], consumers: list[ConsumerSpec],
               tunnel_watch: Optional[TunnelWatch] = None) -> None:
    """statkit -> all producers + consumers in parallel -> wait until every consumer has its
    HEADER -> (GST) tunnels ACTIVE -> GO: every producer starts sending at the same instant
    -> wait (tunnels watched) -> no process may outlive the stream -> statkit stop."""
    if len(producers) != len(consumers):
        raise RuntimeError(f"ZMQ: {len(producers)} producers vs {len(consumers)} consumers")
    prod_hosts, cons_hosts = {p.host for p in producers}, {c.host for c in consumers}
    if len(prod_hosts) != 1 or len(cons_hosts) != 1:
        raise RuntimeError(f"ZMQ: producers/consumers must each be on one host: {prod_hosts} {cons_hosts}")
    prod_host, cons_host = prod_hosts.pop(), cons_hosts.pop()
    sync = sync_dir(out_dir, app_tag)
    n = len(producers)

    procs: list[tuple[str, int, str, subprocess.Popen[str]]] = []
    done: set[int] = set()
    statkit_on = False
    mismatches: list[str] = []
    try:
        for host in (prod_host, cons_host):          # no stale barrier files from a retry
            run_subprocess(host, None, f"rm -rf {shlex.quote(sync)} && mkdir -p {shlex.quote(sync)}",
                           localhost=cfg.localhost)
        if not cfg.is_test:
            start_statkit_zmq(cfg, app_tag, out_dir)
            statkit_on = True
            time.sleep(opts.statkit_pad)
            check_statkit_alive(cfg, app_tag, out_dir)

        logging.info("ZMQ: %s launching %d producer(s) and %d consumer(s): %d B x %d samples each",
                     app_tag, n, n, sample_size, stream_bytes // sample_size)
        for i, spec in enumerate(producers):
            cmd = producer_cmd(opts, spec, tag=f"{app_tag}-{i}", out_dir=out_dir, sample_size=sample_size,
                               stream_bytes=stream_bytes, go_file=f"{sync}/go")
            procs.append(("producer", i, spec.host, popen_subprocess(spec.host, cfg.remote_env, cmd,
                                                                   localhost=cfg.localhost)))
        for i, spec in enumerate(consumers):
            cmd = consumer_cmd(opts, spec, tag=f"{app_tag}-{i}", out_dir=out_dir, sample_size=sample_size,
                               ready_file=f"{sync}/ready-{i}")
            procs.append(("consumer", i, spec.host, popen_subprocess(spec.host, cfg.remote_env, cmd,
                                                                   localhost=cfg.localhost)))

        t_launch = time.monotonic()
        _barrier(cfg, opts, procs, done, mismatches, cons_host=cons_host, sync=sync, n=n, app_tag=app_tag)
        logging.info("ZMQ: %s all %d streams ready after %.1fs", app_tag, n, time.monotonic() - t_launch)
        _tunnels_ok(tunnel_watch, "at the start barrier")
        _send_go(cfg, prod_host, sync)
        logging.info("ZMQ: %s GO sent: all producers start together", app_tag)

        mismatches += _wait_all(procs, opts.stream_timeout, tunnel_watch)
        logging.info("ZMQ: %s stream finished", app_tag)
        left = _leftovers(cfg)
        if left:
            raise RuntimeError(f"ZMQ: {app_tag}: processes still running after the stream finished: {left}")
        if statkit_on:
            time.sleep(opts.statkit_pad)
    finally:
        for _, _, _, p in procs:
            if p.poll() is None:
                p.kill()
        kill_zmq(cfg)
        if statkit_on:
            stop_statkit_zmq(cfg)

    if mismatches:
        raise CountMismatchError(f"{app_tag}: sample count mismatch on {', '.join(mismatches)}")


# ------------------------------------------------------------------------------
# Copy results back (only called after the whole test succeeded)
# def copy_zmq_results(cfg: Config, opts: ZmqOpts) -> None:
#     hosts = [
#         (cfg.hosts.ap["initiator"], "cons-ap"),
#         (cfg.hosts.ep["initiator"], "cons-ep"),
#         (cfg.hosts.ap["listener"], "prod-ap"),
#         (cfg.hosts.ep["listener"], "prod-ep"),
#     ]
#     # remote_dir = f"{cfg.report_dir.rstrip('/')}/{cfg.test}/zmq/"
#     # local_root = f"{cfg.proj_dir}/reports/{cfg.lease.lower()}/{cfg.test}/{Path(cfg.report_dir).name}/zmq"
#     #remote_dir = f"{cfg.report_dir.rstrip('/')}/{cfg.pattern}"
#     remote_dir = f"{cfg.report_dir.rstrip('/')}/"
#     local_root = f"{cfg.proj_dir}/reports/{cfg.lease.lower()}/{cfg.test}/{cfg.tname}"
#     for host, dest in hosts:
#         cp = run_subprocess(
#             cfg.localhost, None,
#             # rsync -av --mkpath --ignore-existing -e ssh max-ap:/tmp/max-wash-zmq/zmq-pushpull 
#             # $HOME/Projects/gstreams/statkit/results/reports/fabric/stream/max-wash-zmq/cons-ap/
#             f"{opts.local_rsync} -av --mkpath --ignore-existing -e ssh "
#             f"{host}:{remote_dir} {local_root}/{dest}/",
#             localhost=cfg.localhost,
#         )
#         logging.debug("COPY: %s -> %s/%s\n%s", host, local_root, dest, cp.stdout[-500:])
#     logging.info("COPY: Results copied to %s", local_root)
def copy_zmq_results(cfg: Config, opts: ZmqOpts) -> None:
    try:
        hosts = [
            (cfg.hosts.ap["initiator"], "cons-ap"),
            (cfg.hosts.ep["initiator"], "cons-ep"),
            (cfg.hosts.ap["listener"], "prod-ap"),
            (cfg.hosts.ep["listener"], "prod-ep"),
        ]
        # remote_dir = f"{cfg.report_dir.rstrip('/')}/{cfg.test}/zmq/"
        # local_root = f"{cfg.proj_dir}/reports/{cfg.lease.lower()}/{cfg.test}/{Path(cfg.report_dir).name}/zmq"
        #remote_dir = f"{cfg.report_dir.rstrip('/')}/{cfg.pattern}"
        remote_dir = f"{cfg.report_dir.rstrip('/')}/"
        local_root = f"{cfg.proj_dir}/reports/{cfg.lease.lower()}/{cfg.test}/{cfg.tname}"
        for host, dest in hosts:
            cp = run_subprocess(
                cfg.localhost, None,
                # rsync -av --mkpath --ignore-existing -e ssh max-ap:/tmp/max-wash-zmq/zmq-pushpull 
                # $HOME/Projects/gstreams/statkit/results/reports/fabric/stream/max-wash-zmq/cons-ap/
                f"{opts.local_rsync} -av --mkpath --ignore-existing -e ssh "
                f"{host}:{remote_dir} {local_root}/{dest}/",
                localhost=cfg.localhost,
            )
            if cp.returncode != 0:
                raise RuntimeError(f"rsync from {host} failed (rc={cp.returncode}): {cp.stderr[-500:]}")
            logging.debug("COPY: %s -> %s/%s\n%s", host, local_root, dest, cp.stdout[-500:])
        logging.info("COPY: Results copied to %s", local_root)
    except Exception as e:
        raise RuntimeError(f"COPY: Runtime Error: {e}") from e