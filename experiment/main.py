import logging
from datetime import datetime

from config import TEST
from iperf import iperf_main

def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("statkit.log")],
    )

def main() -> None:
    setup_logging(TEST.verbose)
    logging.info("MAIN: Starting the experiment: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if TEST.app == "iperf":
        iperf_main(TEST)

    #else:
    #    mini_apps_main()


if __name__ == "__main__":
    main()
