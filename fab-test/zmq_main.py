"""
ZeroMQ streaming experiments (SciStream paper replication) - entry point.

Example:
  python zmq_main.py --lease fabric --tname zmq-wash-max --splice 0,1 --encrypt \
      --app zgst,zbase,zsci -P 10 -r 10
"""
import getpass
import logging
import os
import shlex
import socket
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from zmq_config import get_zmq_config
from zmq_launcher import experiment_main
from zmqstream import kill_zmq, copy_zmq_results
from utils import setup_logging, send_ntfy, initial_cleanup


def main() -> None:
    cfg, opts = get_zmq_config()

    log_dir = (Path.home() / "Projects" / "gstreams" / "statkit" / "results" / "reports"
               / cfg.lease.lower() / cfg.test / cfg.tname)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{cfg.tname}-zmq.log"
    if log_path.exists() and log_path.stat().st_size > 0:
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "-" * 108 + "\n")

    setup_logging(cfg.verbose, str(log_path))
    logging.info("MAIN: Command: %s", shlex.join(sys.argv))
    logging.info("MAIN: Host: %s | User: %s | CWD: %s | PID: %s",
                 socket.gethostname(), getpass.getuser(), os.getcwd(), os.getpid())
    logging.info("MAIN: Options: %s", opts)

    success = False
    try:
        logging.info("MAIN: Starting at %s. Log file: %s",
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_path)
        initial_cleanup(cfg)
        kill_zmq(cfg)
        time.sleep(cfg.sleep)
        experiment_main(cfg, opts)
        success = True
    except Exception as exc:
        tb = traceback.format_exc()
        logging.exception("MAIN: ZMQ experiment failed. Log file: %s", log_path)
        try:
            send_ntfy(success=False, cfg=cfg,
                      msg=RuntimeError(f"{type(exc).__name__}: {exc}\n\n{tb[-3000:]}"))
        except Exception:
            logging.exception("MAIN: Failed to send failure notification")
        raise
    finally:
        try:
            kill_zmq(cfg)
            initial_cleanup(cfg)
        except Exception:
            logging.exception("MAIN: Final cleanup failed")

    if success:
        copy_zmq_results(cfg, opts)     # only after the whole test succeeded
        msg = (f"Finished ZMQ experiment successfully:\nLogfile: {log_path}\nLease: {cfg.lease}\n"
               f"Name: {cfg.tname}\nApps: {cfg.app}\nPattern: {opts.pattern}\n"
               f"Splice: {cfg.splice}\nEncrypt: {cfg.encrypt}\nBlocks: {cfg.blocks}\n"
               f"Parallels: {cfg.parallels}\nSizes: {opts.sizes}\nRuns: {cfg.run_num}")
        try:
            send_ntfy(success=True, cfg=cfg, msg=msg)
        except Exception:
            logging.exception("MAIN: Failed to send success notification")
        logging.info("MAIN: %s", msg)


if __name__ == "__main__":
    main()
