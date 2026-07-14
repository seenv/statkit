import logging
from pathlib import Path
from datetime import datetime
import traceback

#from config import get_config
from config import get_configs
from launcher import experiment_main
from utils import setup_logging, send_ntfy


def main() -> None:
    configs = get_configs()
    failures: list[tuple[str, Exception]] = []

    for cfg in configs:
        #print(cfg)
        log_dir = Path(f"/tmp/{cfg.lease}/{cfg.test}/statkit")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{datetime.now().strftime('%m-%d-%H-%M')}.log"
        setup_logging(cfg.verbose, str(log_path))

        try:
            logging.info("MAIN: Starting the experiment: %s Log file: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_path)
            experiment_main(cfg)

        except Exception as exc:
            #err = traceback.format_exc()
            logging.exception("MAIN: %s experiment failed. Log file: %s", cfg.test, {log_path})
            send_ntfy(success=False, cfg=cfg, error=exc)
            failures.append((cfg.test, exc))
        else:
            logging.info("MAIN: %s experiment completed successfully", cfg.test)
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

        finally:
            logging.info("MAIN: Log file: %s", log_path)

    if failures:
        failed_tests = ", ".join(test for test, _ in failures)
        raise RuntimeError(f"One or more experiment modes failed: {failed_tests}")

if __name__ == "__main__":
    main()
