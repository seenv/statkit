"""
Main configuration file with experiments variables
"""
from __future__ import annotations

import getpass
import os
from datetime import datetime
from os.path import expanduser
from pathlib import Path
from dataclasses import dataclass
from typing import Sequence

#from utils import _parse_list, _parse_int_list


@dataclass(frozen=True)
class Config:
    # envs
    local_env: str
    remote_env: str

    # hosts / endpoints
    localhost: str
    initiator_ap: str
    listener_ap: str
    initiator_host: str
    listener_host: str
    listener_ip: str

    # test params
    base_port: int
    run_num: int
    parallels: Sequence[int]
    time_frames: int
    
    app: str


TEST = Config(
    local_env="/home/seena/Projects/globus_stream/streams-cli/bin/activate",
    remote_env="/home/cc/streams-cli/bin/activate",
    localhost="localhost",
    initiator_ap="chi-c2cs",
    listener_ap="chi-p2cs",
    initiator_host="chi-cons",
    listener_host="chi-prod",
    listener_ip="10.140.82.103",
    base_port=50000,
    run_num=1,
    parallels= (1, 5), # (3, 4, 5),
    time_frames=5,
    app="iperf",
)





# #From Config
# def _parse_list(name: str, default: list[str]) -> list[str]:
#     raw = os.getenv(name)
#     if not raw:
#         return default
#     return [x.strip() for x in raw.split(",") if x.strip()]


# def _parse_int_list(name: str, default: list[int]) -> list[int]:
#     return [int(x) for x in _parse_list(name, [str(d) for d in default])]


# class Config:
#     loca_host = True
#     local_env="/home/seena/Projects/globus_stream/streams-cli/bin/activate"
#     remote_env="/home/cc/streams-cli/bin/activate"
#     local_host="localhost"
#     initiator_ap="chi-c2cs"
#     listener_ap="chi-p2cs"
#     initiator_host="chi-cons"
#     listener_host="chi-prod"
#     listener_ip="10.140.82.103"
#     base_port=50000
#     run_num=1
#     parallels=(3, 4, 5)
#     time_frames=20
    
#     # General Test Parameters
#     APP = os.getenv("APP", "iperf")                                        # iperf | mini
#     RUN_NUM = int(os.getenv("RUN_NUM", "1"))                              # Total runs per experiment point
#     WIN_SIZE = _parse_list("WIN_SIZE", ["0"])                              # iperf3 -w values; 0: disable
#     TIME_FRAMES = _parse_int_list("TIME_FRAMES", [20])#[10, 20, 30, 60])         # seconds
#     PARALLELS = _parse_int_list("PARALLELS", [1, 2, 3, 5])#, 10])
#     PROXY = _parse_list(
#         "PROXIES",
#         [
#             "Nginx",
#             "HaproxySubprocess_enull",
#             "StunnelSubprocess_enull",
#             "HaproxySubprocess.v1.2_crypt",
#             "StunnelSubprocess.v1.2_crypt",
#             "HaproxySubprocess.v1.3_crypt",
#             "StunnelSubprocess.v1.3_crypt",
#         ],
#     )
#     CONGESTIONS = _parse_list("CONGESTIONS", ["cubic", "bbr"])

#     # Local paths
#     USERNAME = getpass.getuser()
#     LOCAL_STREAMHUB = expanduser(os.getenv("_LOCAL_STREAMHUB", "~/Projects/globus_stream/streamhub/src/launcher.py"))
#     LOCAL_STATKIT = expanduser(os.getenv("LOCAL_STATKIT", "~/Projects/globus_stream/statkit/monitoring/launcher.py"))
#     MINI_APS_YML = expanduser(os.getenv("MINI_APS_YML", "~/Projects/streamhub/chameleon/mini-apps"))

#     # Remote endpoints
#     DEV = os.getenv("DEV", "eno1np0")
#     BASE_PORT = int(os.getenv("BASE_PORT", "5100"))
#     C2CS_IP = os.getenv("C2CS_IP", "10.52.1.30")
#     P2CS_IP = os.getenv("P2CS_IP", "192.5.87.71")

#     MINI_PATH = os.getenv("MINI_PATH", "~/mini-apps")
#     RMT_SYS_SCRIPT = os.getenv("RMT_SYS_SCRIPT", "~/statkit/monitoring/launcher.py")           # >>>>>>>>> have to change it to new version
#     SCISTREAM_PATH = os.getenv("SCISTREAM_PATH", "~/.venv/lib/python3.12/site-packages/src/s2ds")      # hardcoding scistream proxy file path
#     S2DS_TEMPLATE_DIR = os.getenv("S2DS_TEMPLATE_DIR", "~/.venv/lib/python3.12/site-packages/src/s2ds")

#     # Default experiment output root (on remote hosts)
#     HOME_DIR = os.getenv("HOME_DIR", f"~/running_tests")
#     #_HOME_DIR = f'~/DATE_TEST-{_APP.upper()}/{datetime.now().strftime("%Y-%m-%d")}' 

#     # Mini-app modules
#     MODULES = _parse_list("MODULES", ["daq", "dist", "sirt"])

#     # Host groups (names -> ssh host aliases)
#     S2CS_HOSTS = {"p2cs": "chi-p2cs", "c2cs": "chi-c2cs"}
#     ENDPOINTS = {"prod": "chi-prod", "cons": "chi-cons"}
#     HOSTS = {
#         "c2cs": "chi-c2cs",
#         "p2cs": "chi-p2cs",
#         "prod": "chi-prod",
#         "cons": "chi-cons",
#     }

#     # Remote execution 
#     REMOTE_PYTHON = os.getenv("REMOTE_PYTHON", "~/.venv/bin/python")
#     DUMPCAP_FILTER = os.getenv("DUMPCAP_FILTER", "portrange 5100-5110")
#     DUMPCAP_SNAPLEN = int(os.getenv("DUMPCAP_SNAPLEN", "96"))

#     # from version 2
#     MERROW = os.getenv("MERROW", "merrow")
#     ENV_ACTIVATE = os.getenv("ENV_ACTIVATE", "~/Projects/streamhub/.streamhub/bin/activate")
#     PROXY_FLAG = os.getenv("PROXY_FLAG", "--type")

#     # Helpers
#     @staticmethod
#     def now_tag() -> str:
#         return datetime.now().strftime("%Y%m%d_%H%M%S")

#     @staticmethod
#     def expand_remote(path: str | Path) -> str:
#         # remote expansion happens in bash -lc
#         return str(path)
