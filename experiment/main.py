import logging
from pathlib import Path
from datetime import datetime
import traceback
import time

#from config import get_config
from config import get_configs
from launcher import experiment_main
from utils import setup_logging, send_ntfy, cleanup_file
from utils import initial_cleanup, copy_results
import getpass, socket, os, sys, shlex

def main() -> None:
    configs = get_configs()
    failures: list[tuple[str, Exception, Path, str]] = []

    for cfg in configs:
        #print(cfg)
        test_name = Path(cfg.report_dir).name
        log_dir = Path.home() / "Projects" / "globus_stream" / "statkit" / "results" / "reports" / cfg.lease.lower() / cfg.test / test_name
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{test_name}.log"     #log_path = log_dir / f"{datetime.now().strftime('%m-%d-%H-%M')}.log"

        # Separate this run from the previous run
        if log_path.exists() and log_path.stat().st_size > 0:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n------------------------------------------------------------------------------------------------------------\n" * 10)

        setup_logging(cfg.verbose, str(log_path))
        logging.info("MAIN: Command: %s", shlex.join(sys.argv))
        logging.info("MAIN: Host: %s | User: %s | CWD: %s | PID: %s", socket.gethostname(), getpass.getuser(), os.getcwd(), os.getpid())
        logging.info("MAIN: Python: %s", sys.version.split()[0])
        try:
            logging.info("MAIN: Starting the experiment at %s. Log file: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_path)
            initial_cleanup(cfg)
            time.sleep(cfg.sleep)
            experiment_main(cfg)

        except Exception as exc:
            traceback_text = traceback.format_exc()
            logging.exception("MAIN: %s experiment failed. Log file: %s", cfg.test, log_path)

            try:
                notif_err = RuntimeError(
                    f"{type(exc).__name__}: {exc}\n\n"
                    f"{traceback_text[-3000:]}"
                )
                send_ntfy(success=False, cfg=cfg, msg=notif_err)
            except Exception:
                logging.exception("MAIN: Failed to send failure notification for %s", cfg.test)
            
            failures.append((cfg.test, exc, log_path, traceback_text))

        else:
            notif_done = (
                f"Finished experiment successfully:\n"
                f"Logfile: {log_path}\n"
                f"Lease: {cfg.lease}\n"
                f"Apps: {cfg.app}\n"
                f"Splice: {cfg.splice}\n"
                f"Encrypt: {cfg.encrypt}\n"
                f"Parallels: {cfg.parallels}\n"
                f"Time Frames: {cfg.time_frames}\n"
                f"File sizes: {cfg.file_sizes}\n"
                f"Block sizes: {cfg.blocks}\n"
                f"Runs: {cfg.run_num}"
            )

            try: 
                send_ntfy(success=True, cfg=cfg, msg=notif_done)
            except Exception:
                logging.exception("MAIN: Failed to send success notification for %s", cfg.test)
            
            try:
                copy_results(cfg)
            except Exception:
                logging.exception("MAIN: Failed to copy results for %s", cfg.test)
            
            logging.info("MAIN: %s experiment completed successfully: %s", cfg.test, notif_done)

        finally:
            logging.info("MAIN: Finalizing %s experiment. Log file: %s", cfg.test, log_path)
            try:
                cleanup_file(cfg)
            except Exception:
                logging.exception("MAIN: File cleanup failed for %s", cfg.test)
            if cfg != configs[-1]:
                print("MAIN: Sleeping for 30 seconds before the next test")
                time.sleep(30)

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
