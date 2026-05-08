from __future__ import annotations

import logging
import shlex
import time
from pathlib import PurePosixPath

from config import Config
from utils import run_subprocess, start_statkit, stop_statkit, record_ping


def ensure_remote_file(cfg: Config, host: str, file_path: str) -> None:
    """
    Check that the source file exists on the sender.
    """
    cp = run_subprocess(
        host,
        None,
        f"test -f {shlex.quote(file_path)} && ls -lh {shlex.quote(file_path)} && du -h {shlex.quote(file_path)}",
        localhost=cfg.localhost,
    )
    logging.info("RSYNC: Source file on %s:\n%s", host.upper(), cp.stdout.strip())


def prepare_remote_dest(cfg: Config, host: str, dest_path: str) -> None:
    """
    Create the destination directory and remove the old destination file.
    This forces rsync to transfer the full file on every run.
    """
    dest_dir = str(PurePosixPath(dest_path).parent)

    run_subprocess(
        host,
        None,
        (
            f"mkdir -p {shlex.quote(dest_dir)} "#&& "
            #f"rm -f {shlex.quote(dest_path)}"
        ),
        localhost=cfg.localhost,
    )

    logging.info(
        "RSYNC: Prepared destination on %s: dir=%s file=%s",
        host.upper(),
        dest_dir,
        dest_path,
    )


def run_rsync(
    cfg: Config,
    src_host: str,
    #dst_ip: str,
    dst_host: str,
    src_file: str,
    dst_file: str,
    out_dir: str,
    timeout: int,
) -> None:
    """
    Run direct rsync from src_host to ubuntu@dst_ip:dst_file.
    """

    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"/usr/bin/time -v -o {shlex.quote(out_dir)}/rsync_time.log "
        f"rsync -a --info=progress2 --no-compress --stats "
        f"-e {shlex.quote('ssh -T -o Compression=no -o StrictHostKeyChecking=no')} "
        f"{shlex.quote(src_file)} "
        #f"ubuntu@{shlex.quote(dst_ip)}:{shlex.quote(dst_file)} "
        f"{shlex.quote(dst_host)}:{shlex.quote(dst_file)} "
        f"2>&1 | tee {shlex.quote(out_dir)}/rsync.log"
    )

    cp = run_subprocess(
        src_host,
        None,
        cmd,
        localhost=cfg.localhost,
        timeout=timeout,
    )

    #logging.info("RSYNC: Completed direct rsync from %s to %s", src_host.upper(), dst_ip)
    logging.info("RSYNC: Completed direct rsync from %s to %s", src_host.upper(), dst_host)
    logging.debug("RSYNC stdout:\n%s", cp.stdout)


def rsync_main(cfg: Config) -> None:
    """
    Direct rsync benchmark.

    Source:
        cfg.hosts.ep["initiator"]

    Destination:
        cfg.listener_ip

    No Globus Streams tunnel is created.
    """

    src_host = cfg.hosts.ep["listener"]
    dst_host = cfg.hosts.ep["initiator"]
    #dst_ip = cfg.listener_ip
    dst_ip = "192.168.20.10"

    src_file = "/tmp/rsync-test/1G.bin"
    dst_file = "/tmp/rsync-test/1G.bin"

    total_runs = len(cfg.time_frames) * len(cfg.parallels) * cfg.run_num * len(cfg.blocks)

    test_config = (
        (block, duration, parallel, run)
        for block in cfg.blocks
        for duration in cfg.time_frames
        for parallel in cfg.parallels
        for run in range(1, cfg.run_num + 1)
    )

    for idx, (block, duration, parallel, run) in enumerate(test_config, start=1):
        logging.info(
            "--------------- RSYNC DIRECT Test %d / %d : blocksize %s / duration %s / parallel %s / run %s ---------------",
            idx,
            total_runs,
            block,
            duration,
            parallel,
            run,
        )

        out_dir = f"{cfg.report_dir}/rsync-test/{block}/{parallel}/{run}"
        try:
            ensure_remote_file(cfg, src_host, src_file)
            prepare_remote_dest(cfg, dst_host, dst_file)

            logging.info("RSYNC: Starting statkit monitoring")
            start_statkit(cfg, duration, parallel, run, out_dir)
            time.sleep(cfg.sleep)

            logging.info("RSYNC: Starting direct rsync transfer")
            run_rsync(
                cfg=cfg,
                src_host=src_host,
                #dst_ip=dst_ip,
                dst_host=dst_host,
                src_file=src_file,
                dst_file=dst_file,
                out_dir=out_dir,
                timeout=max(duration * 120, 600),
            )

            logging.info("RSYNC: Recording RTT")
            record_ping(cfg, src_host, dst_ip, out_dir)

        except Exception as e:
            raise RuntimeError(f"RSYNC DIRECT ERROR: {e}") from e

        finally:
            logging.info("RSYNC: Stopping statkit monitoring")
            stop_statkit(cfg)
            time.sleep(cfg.sleep)