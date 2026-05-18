import logging
from datetime import datetime

from config import TEST
from iperf import iperf_main
from utils import setup_logging

# TODO: define a class and create ctrl to run the setup pipeline 
def main() -> None:
    setup_logging(TEST.verbose)
    try:
        logging.info("MAIN: Starting the experiment: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        if TEST.app == "iperf":
            iperf_main(TEST)
    except Exception :
        logging.exception("Unhandled error on local host")
        
    print(
        f"\nFinished runing the experiment with vals: \n"
        f"Parallels: {TEST.parallels} \n"
        #f"File sizes: {TEST.file_size} \n"
        f"Time frames: {TEST.time_frames} \n"
        f"Apps: {TEST.app} \n"
        f"Block sizes: {TEST.blocks} \n"
        f"Repeat runs: {TEST.run_num} \n"
    )

if __name__ == "__main__":
    main()
