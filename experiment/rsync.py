from __future__ import annotations

import logging
import shlex
import time
from pathlib import PurePosixPath

from config import Config
from utils import run_subprocess, make_temp_file, start_statkit, stop_statkit, record_ping


# def ensure_remote_file(cfg: Config, host: str, size: int, file_path: str) -> None:
#     """
#     Check that the source file exists on the sender.
#     """
#     cp = run_subprocess(
#         host,
#         None,
#         f"mkdir -p /tmp/{cfg.app}/ && "
#         f"fallocate -l {size}G {shlex.quote(file_path)} && "
#         f"test -f {shlex.quote(file_path)} && "
#         f"ls -lh {shlex.quote(file_path)} && "
#         f"du -h {shlex.quote(file_path)}",
#         localhost=cfg.localhost,
#     )
#     logging.info("RSYNC: Source file on %s:\n%s", host.upper(), cp.stdout.strip())


def prepare_remote_dest(cfg: Config, host: str, dest_path: str) -> None:
    """
    Create the destination directory and remove the old destination file.
    This forces rsync to transfer the full file on every run.
    """
    dest_dir = str(PurePosixPath(dest_path).parent)

    run_subprocess(
        host, None,
        (
            f"mkdir -p {shlex.quote(dest_dir)} "#&& "
            #f"rm -f {shlex.quote(dest_path)}"
        ),
        localhost=cfg.localhost,
    )

    logging.info(
        "RSYNC: Prepared destination on %s: temp_file=%s",
        host.upper(),
        #dest_dir,
        dest_path,
    )


def run_rsync(
    cfg: Config,
    src_host: str,
    #dst_ip: str,
    dst_host: str,
    temp_file: str,
    #temp_file: str,
    out_dir: str,
    timeout: int,
) -> None:
    """
    Run direct rsync from src_host to ubuntu@dst_ip:temp_file.
    --block-size=SIZE, -B    force a fixed checksum block-size
    --log-file=FILE          log what we're doing to the specified FILE
    #                  create destination's missing path components

    """

    cmd = (
        f"mkdir -p {shlex.quote(out_dir)} && "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/rsync_time.log "
        f"rsync -avvv --info=progress2,stats2 --no-compress --stats "
        f"--mkpath --no-checksum --whole-file --ignore-times "
        f"-e {shlex.quote('ssh -T -o Compression=no -o StrictHostKeyChecking=no')} "
        f"{shlex.quote(temp_file)} "
        #f"ubuntu@{shlex.quote(dst_ip)}:{shlex.quote(temp_file)} "
        f"{shlex.quote(dst_host)}:{shlex.quote(temp_file)} "
        f"--log-file {shlex.quote(out_dir)}/rsync_log.log "
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
    #dst_ip = "192.168.20.10"

    #src_file = f"/tmp/{cfg.file_size}G.bin"
    #dst_file = f"/tmp/{cfg.file_size}G.bin"

    test_config = (
        #(block, duration, parallel, run)
        #(block, size, parallel, run)
        (block, size, run)
        for block in cfg.blocks
        #for duration in cfg.time_frames
        #for parallel in cfg.parallels
        for size in cfg.file_size
        for run in range(1, cfg.run_num + 1)
    )
    
    #total_runs = len(cfg.time_frames) * len(cfg.parallels) * cfg.run_num * len(cfg.blocks)
    #total_runs = len(cfg.blocks) * len(cfg.file_size) * len(cfg.parallels) * cfg.run_num
    total_runs = len(cfg.blocks) * len(cfg.file_size) * cfg.run_num
    
    #for idx, (block, duration, parallel, run) in enumerate(test_config, start=1):
    for idx, (block, size, run) in enumerate(test_config, start=1):
        logging.info(
            #"--------------- RSYNC DIRECT Test %d / %d : blocksize %s / duration %s / parallel %s / run %s ---------------",
            #"--------------- RSYNC DIRECT Test %d / %d : blocksize %s / size %sG / parallel %s / run %s ---------------",
            "--------------- RSYNC DIRECT Test %d / %d : blocksize %s / size %sG / run %s ---------------",
            idx, total_runs, block,
            #duration, #parallel,
            size, run)

        #out_dir = f"{cfg.report_dir}/rsyncs/{block}/{parallel}/{duration}/{run}"
        #out_dir = f"{cfg.report_dir}/rsyncs/{block}/{size}/{parallel}/{run}"
        out_dir = f"{cfg.report_dir}/{block}/{size}/{run}"
        try:
            temp_file = f"/tmp/rsync/{size}G.bin"
            make_temp_file(cfg, cfg.hosts.ep.get("listener"), size, temp_file)
            #prepare_remote_dest(cfg, dst_host, temp_file)

            logging.info("RSYNC: Starting statkit monitoring")
            #start_statkit(cfg, duration, parallel, run, out_dir)
            start_statkit(cfg, size, "rsync", out_dir)   #size as duration which will be * 60s
            time.sleep(cfg.sleep)

            logging.info("RSYNC: Starting direct rsync transfer")   #it will run on server and log it there
            run_rsync(
                cfg=cfg,
                src_host=cfg.hosts.ep.get("listener"),
                #dst_ip=dst_ip,
                dst_host=cfg.hosts.ep.get("initiator"),
                temp_file=temp_file,
                #temp_file=temp_file,
                out_dir=out_dir,
                #timeout=max(duration * 120, 600),
                timeout=(size * 60)
            )

            logging.info("RSYNC: Recording RTT")        # it will run on the client
            record_ping(cfg, cfg.hosts.ep.get("initiator"), cfg.listener_ip, "rsync", out_dir)

        except Exception as e:
            raise RuntimeError(f"RSYNC ERROR: {e}") from e

        finally:
            logging.info("RSYNC: Stopping statkit monitoring")
            stop_statkit(cfg)
            time.sleep(cfg.sleep)