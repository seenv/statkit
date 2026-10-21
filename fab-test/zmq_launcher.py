"""
Experiment loop for the ZeroMQ streaming tests.

Per (block, parallel, sample size, net mode, run):  GST -> Direct -> SciStream
Output: <report_dir>/stream/zmq/B<blk>/P<p>/S<size>/<A0|A1|E1>/R<run>/
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from config import Config
from zmq_config import ZmqOpts, APP_TAGS
from utils import build_net_modes, net_mode_dir, create_output_dir, record_ping
from sysconf import system_state_report
from gstreams import check_gridftp_config
from scistream import start_scistream, stop_scistream
from zmqstream import (
    CountMismatchError, ProducerSpec, ConsumerSpec,
    run_stream, setup_gst, teardown_gst, delete_app_files,
)


# ------------------------------------------------------------------------------
# Globus Streams tunnel
def run_zmq_gst(cfg: Config, opts: ZmqOpts, *, app_tag: str, out_dir: str, label: str,
                parallel: int, sample_size: int, encrypt: int) -> None:
    start_port = opts.gst_encrypt_port if encrypt else opts.gst_port
    listener, initiator = cfg.hosts.ep["listener"], cfg.hosts.ep["initiator"]
    tunnels: list[str] = []
    try:
        gw_ips, gw_ports = setup_gst(cfg, parallel, start_port, label, tunnels, opts.stream_timeout)
        producers = [
            ProducerSpec(listener, start_port + (i * 2),
                         f"globus-streams-launch -p {start_port + (i * 2)} {t}")
            for i, t in enumerate(tunnels)
        ]
        consumers = [
            ConsumerSpec(initiator, gw_ips[i], gw_ports[i], f"globus-streams-launch {t}")
            for i, t in enumerate(tunnels)
        ]
        run_stream(cfg, opts, app_tag=app_tag, out_dir=out_dir, sample_size=sample_size,
                   stream_bytes=opts.stream_bytes(parallel), producers=producers, consumers=consumers)
        if not cfg.is_test:
            record_ping(cfg, initiator, cfg.listener_pub, app_tag, out_dir)
    finally:
        teardown_gst(cfg, tunnels)


# ------------------------------------------------------------------------------
# Direct (No-SciStream baseline)
def run_zmq_base(cfg: Config, opts: ZmqOpts, *, app_tag: str, out_dir: str, label: str,
                 parallel: int, sample_size: int, encrypt: int) -> None:
    listener, initiator = cfg.hosts.ep["listener"], cfg.hosts.ep["initiator"]
    ports = [opts.direct_port + (i * 2) for i in range(parallel)]
    producers = [ProducerSpec(listener, p) for p in ports]
    consumers = [ConsumerSpec(initiator, cfg.listener_pub, p) for p in ports]
    run_stream(cfg, opts, app_tag=app_tag, out_dir=out_dir, sample_size=sample_size,
               stream_bytes=opts.stream_bytes(parallel), producers=producers, consumers=consumers)
    if not cfg.is_test:
        record_ping(cfg, initiator, cfg.listener_pub, app_tag, out_dir)


# ------------------------------------------------------------------------------
# SciStream
def run_zmq_sci(cfg: Config, opts: ZmqOpts, *, app_tag: str, out_dir: str, label: str,
                parallel: int, sample_size: int, encrypt: int) -> None:
    listener, initiator = cfg.hosts.ep["listener"], cfg.hosts.ep["initiator"]
    try:
        logging.info("ZSCI: Creating %d SciStream tunnels (encrypt=%d)", parallel, encrypt)
        stream_ids, _, initiate_ap_ports, listen_ep_ports, _ = start_scistream(
            cfg, encrypt, parallel, opts.stream_timeout)
        if not (len(stream_ids) == len(initiate_ap_ports) == len(listen_ep_ports) == parallel):
            raise RuntimeError(
                f"ZSCI: expected {parallel} tunnels, got ids={stream_ids} "
                f"ap_ports={initiate_ap_ports} ep_ports={listen_ep_ports}")
        producers = [ProducerSpec(listener, p) for p in listen_ep_ports]
        consumers = [ConsumerSpec(initiator, cfg.initiator_ap_ip, p) for p in initiate_ap_ports]
        run_stream(cfg, opts, app_tag=app_tag, out_dir=out_dir, sample_size=sample_size,
                   stream_bytes=opts.stream_bytes(parallel), producers=producers, consumers=consumers)
        if not cfg.is_test:
            record_ping(cfg, initiator, cfg.listener_pub, app_tag, out_dir)
    finally:
        stop_scistream(cfg)


RUNNERS: dict[str, Callable[..., None]] = {
    "zgst": run_zmq_gst,
    "zbase": run_zmq_base,
    "zsci": run_zmq_sci,
}


# ------------------------------------------------------------------------------
# Retry wrapper (only PUSH/PULL count mismatches are retried)
def run_with_retries(cfg: Config, opts: ZmqOpts, app: str, *, out_dir: str, **kw) -> None:
    app_tag = APP_TAGS[app]
    attempts = opts.retries + 1
    for attempt in range(1, attempts + 1):
        try:
            logging.info("%s: attempt %d/%d", app_tag.upper(), attempt, attempts)
            RUNNERS[app](cfg, opts, app_tag=app_tag, out_dir=out_dir, **kw)
            return
        except CountMismatchError as e:
            if attempt == attempts:
                raise RuntimeError(f"{e} (after {attempt} attempt(s))") from e
            logging.warning("%s: %s. Rerunning (%d/%d)", app_tag.upper(), e, attempt + 1, attempts)
            delete_app_files(cfg, out_dir, app_tag)
            time.sleep(cfg.sleep)


# ------------------------------------------------------------------------------
# Main loop
def experiment_main(cfg: Config, opts: ZmqOpts) -> None:
    net_modes = build_net_modes(cfg.splice, cfg.encrypt)
    combos = [
        (block, parallel, size, splice, encrypt, run)
        for block in cfg.blocks
        for parallel in cfg.parallels
        for size in opts.sizes
        for splice, encrypt in net_modes
        for run in range(1, cfg.run_num + 1)
    ]
    total = len(combos)
    base_dir = Path(cfg.report_dir) / cfg.test / "zmq"
    logging.info("MAIN: %d configurations x %d app(s) = %d stream tests | apps=%s pattern=%s "
                 "total=%d GiB%s", total, len(cfg.app), total * len(cfg.app), list(cfg.app),
                 opts.pattern, opts.total_size_gb, " per stream" if opts.per_stream else " per run")

    if not cfg.is_test:
        sys_dir = str(base_dir / "sys-info")
        logging.info("SYS: Recording the system reports in %s", sys_dir)
        system_state_report(cfg, sys_dir)

    last_block = last_splice = last_encrypt = None
    for idx, (block, parallel, size, splice, encrypt, run) in enumerate(combos, start=1):
        mode = net_mode_dir(splice, encrypt)
        out_dir = str(base_dir / f"B{block}" / f"P{parallel}" / f"S{size}" / mode / f"R{run}")
        label = f"{cfg.tname.replace(' ', '_')}-zmq-B{block}-P{parallel}-S{size}-{mode}-R{run}"
        context = (f"config {idx}/{total}: block={block} parallel={parallel} size={size} "
                   f"mode={mode} run={run}")
        logging.info("")
        logging.info("--------------- Stream %s ---------------", context)

        try:
            create_output_dir(cfg, out_dir, timeout=120)
            for app in cfg.app:
                if app == "zgst":
                    last_block, last_splice, last_encrypt = check_gridftp_config(
                        cfg, block, splice, encrypt, last_block, last_splice, last_encrypt, out_dir)
                run_with_retries(cfg, opts, app, out_dir=out_dir, label=label,
                                 parallel=parallel, sample_size=size, encrypt=encrypt)
                time.sleep(cfg.sleep)
        except Exception as exc:
            logging.exception("EXPERIMENT: failed at %s", context)
            raise RuntimeError(f"Experiment failed at {context}: {exc}") from exc
