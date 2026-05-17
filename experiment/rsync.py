from __future__ import annotations

import logging
import shlex
import time

from config import Config
from utils import run_subprocess, make_temp_file, start_statkit, stop_statkit, record_ping
from utils import start_rsync, prepare_remote_dest


def rsync_main(cfg: Config) -> None:
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
            #"--------------- RSYNC Test: %d / %d : blocksize %s / duration %s / parallel %s / run %s ---------------",
            #"--------------- RSYNC Test: %d / %d : blocksize %s / size %sG / parallel %s / run %s ---------------",
            "--------------- RSYNC Tests: %d / %d : blocksize %s / size %sG / run %s ---------------",
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
            start_rsync(
                cfg=cfg,
                src_host=cfg.hosts.ep.get("listener"),
                dst_host=cfg.hosts.ep.get("initiator"),
                temp_file=temp_file,
                out_dir=out_dir,
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