import logging
from datetime import datetime

#from config import Config
from config import TEST
from iperf import iperf_main
#from mini import mini_apps_main
#from utils import scp_sys_script

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("app.log")],
)


def main() -> None:
    logging.info("MAIN: Starting the experiment: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logging.info("MAIN: Test configurations:")
    # for attr in dir(Config):
    #     if attr.startswith("_") and not attr.startswith("__") and not callable(getattr(Config, attr)):
    #         logging.info("  %s: %r", attr, getattr(Config, attr))

    #  sys_monitor.py is on remote hosts
    # TODO: change it to statkit
    #scp_sys_script()

    if TEST.app == "iperf":
        iperf_main(TEST)
    #else:
    #    mini_apps_main()


if __name__ == "__main__":
    main()
