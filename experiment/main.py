import logging
from pathlib import Path
from datetime import datetime
import traceback

from config import get_config
from launcher import experiment_main
from utils import setup_logging, send_ntfy


def main() -> None:
    
    cfg = get_config()
    #print(cfg)
    log_dir = Path("/tmp/statkit")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{cfg.test.replace(' ', '_')}-{datetime.now().strftime('%m-%d-%H-%M')}.log"
    setup_logging(cfg.verbose, str(log_path))
    logging.info("MAIN: Starting the experiment: %s Log file: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_path)
    try:
        if cfg.test in {"stream", "transfer"}:
            experiment_main(cfg)
        else:
            raise ValueError(f"Unknown test value: {cfg.test}")
    except Exception as e:
        #err = traceback.format_exc()
        logging.exception(
            f"MAIN: Experiment failed: \n"
            f"logfile: {log_path} \n"
            )
        send_ntfy(success=False, cfg=cfg, error=e)
        logging.info("MAIN: Log file: %s", log_path)
        raise

    send_ntfy(success=True, cfg=cfg)     
    print(
        f"\nFinished runing the experiment with vals: \n"
        f"logfile: {log_path} \n"
        f"Lease: {cfg.lease} \n",
        f"Test: {cfg.test} \n",
        f"Apps: {cfg.app} \n",
        f"Splice: {cfg.splice} \n",
        f"Parallels: {cfg.parallels} \n",
        f"Time Frames: {cfg.time_frames} \n",
        f"File sizes: {cfg.file_sizes} \n",
        f"Block sizes: {cfg.blocks} \n",
        f"Repeat runs: {cfg.run_num} \n",
        )

if __name__ == "__main__":
    main()
