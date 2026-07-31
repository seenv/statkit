import logging
from pathlib import Path
from datetime import datetime
import traceback
import time

#from config import get_config
from config import get_configs
from launcher import experiment_main
from utils import setup_logging, send_ntfy, cleanup_file, report_log_dir


def main() -> None:
    configs = get_configs()
    failures: list[tuple[str, Exception, Path, str]] = []

    for cfg in configs:
        #print(cfg)
        #log_dir = Path(f"/tmp/{cfg.lease}/{cfg.test}/statkit")
        log_dir = report_log_dir(cfg)
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / f"{datetime.now().strftime('%m-%d-%H-%M')}.log"
        setup_logging(cfg.verbose, str(log_path))

        try:
            logging.info("MAIN: Starting the experiment at %s. Log file: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_path)
            experiment_main(cfg)

        except Exception as exc:
            traceback_text = traceback.format_exc()
            logging.exception("MAIN: %s experiment failed. Log file: %s", cfg.test, log_path)

            try:
                notif_err = RuntimeError(
                    f"{type(exc).__name__}: {exc}\n\n"
                    f"{traceback_text[-3000:]}"
                )
                send_ntfy(success=False, cfg=cfg, error=notif_err)
            except Exception:
                logging.exception("MAIN: Failed to send failure notification for %s", cfg.test)
            
            failures.append((cfg.test, exc, log_path, traceback_text))

        else:
            logging.info("MAIN: %s experiment completed successfully", cfg.test)
            try: 
                send_ntfy(success=True, cfg=cfg)
    
            except Exception:
                logging.exception("MAIN: Failed to send success notification for %s", cfg.test)

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
            logging.info("MAIN: Finalizing %s experiment. Log file: %s", cfg.test, log_path)
            try:
                cleanup_file(cfg)
            except Exception:
                logging.exception("MAIN: File cleanup failed for %s", cfg.test)
            logging.info("MAIN: Sleeping for 60 seconds before the next test")
            time.sleep(60)

    if failures:
        details = "\n".join(
            (
                f"- Test: {test}\n"
                f"  Error: {type(exc).__name__}: {exc}\n"
                f"  Log: {log_path}"
            )
            for test, exc, log_path, _ in failures
        )
        first_exception = failures[0][1]
        raise RuntimeError(
            "One or more experiment modes failed:\n"
            f"{details}"
        ) from first_exception


if __name__ == "__main__":
    main()
