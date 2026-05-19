import logging
from pathlib import Path
from datetime import datetime

from config import TEST
from iperf import iperf_main
#from rsync import rsync_main
from launcher import experiment_main
from utils import setup_logging

# TODO: define a class and create ctrl to run the setup pipeline 
def main() -> None:
    log_dir = Path("/tmp/statkit")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{TEST.test.replace(" ", "_")}-{datetime.now().strftime('%m-%d-%H-%M-%S')}.log"
    setup_logging(TEST.verbose, str(log_path))
    # setup_logging(TEST.verbose, f"/tmp/statkit/{shlex(TEST.test)}-{shlex(datetime.now().strftime('%Y-%m-%d-%H:%M:%S'))}.log")
    logging.info("MAIN: Starting the experiment: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # try:
    #     if TEST.app == "iperf":
    #         iperf_main(TEST)
    #     elif TEST.app == "rsync":
    #         rsync_main(TEST)
    #     elif TEST.app == "all":
    #         experiment_main(TEST)
    #     else:
    #         raise ValueError(f"Unknown TEST.app value: {TEST.app}")
    # except Exception:
    #     logging.exception("MAIN: Experiment failed")
    #     raise
    try:
        if TEST.test in {"globus", "transfer"}:
            experiment_main(TEST)
        else:
            raise ValueError(f"Unknown test value: {TEST.test}")
    except Exception:
        logging.exception("MAIN: Experiment failed")
        raise
            
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
