import logging
from datetime import datetime

from config import TEST
from iperf import iperf_main
from utils import setup_logging

# TODO: define a class and create ctrl to run the setup pipeline 
def main() -> None:
    setup_logging(TEST.verbose)
    logging.info("MAIN: Starting the experiment: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

<<<<<<< Updated upstream
    if TEST.app == "iperf":
        iperf_main(TEST)
=======
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
>>>>>>> Stashed changes

if __name__ == "__main__":
    main()
