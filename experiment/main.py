import logging
from pathlib import Path
from datetime import datetime
import traceback

from config import TEST
from launcher import experiment_main
from utils import setup_logging, send_ntfy


def main() -> None:
    log_dir = Path("/tmp/statkit")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{TEST.test.replace(" ", "_")}-{datetime.now().strftime('%m-%d-%H-%M')}.log"
    setup_logging(TEST.verbose, str(log_path))
    logging.info("MAIN: Starting the experiment: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        if TEST.test in {"globus", "transfer"}:
            experiment_main(TEST)
        else:
            raise ValueError(f"Unknown test value: {TEST.test}")
    except Exception as e:
        #err = traceback.format_exc()
        logging.exception("MAIN: Experiment failed")
        send_ntfy(success=False, cfg=TEST, error=e)
        raise

    send_ntfy(success=True, cfg=TEST)     
    print(
        f"\nFinished runing the experiment with vals: \n"
        f"Parallels: {TEST.parallels} \n"
        f"File sizes: {TEST.file_sizes} \n"
        f"Apps: {TEST.app} \n"
        f"Block sizes: {TEST.blocks} \n"
        f"Repeat runs: {TEST.run_num} \n"
        )

if __name__ == "__main__":
    main()
