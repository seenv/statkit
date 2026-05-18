"""
Main configuration file with experiments variables
"""
from __future__ import annotations

import getpass
import os, sys
from datetime import datetime
from os.path import expanduser
from pathlib import Path
import argparse
import logging


from dataclasses import dataclass, field
from typing import Sequence, TypedDict, Literal, Dict, Mapping

#from utils import _parse_list, _parse_int_list

# TODO: all the hardcoded stuff :/

# def _parse_flag(flag: str, default: bool = False) -> bool:
#     return flag in sys.argv if sys.argv else default

def _parse_int_list(s: str) -> list[int]:
    try:
        return [int(x.strip()) for x in s.split(",") if x.strip()]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid int list: {s}") from e

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="store_true", default=False)
    p.add_argument("--lease", type=str, required=True, help="Lease name on the testbed")
    p.add_argument("--baseline", action="store_true", default=False, help="Enable baseline tests")
    p.add_argument("--splice", type=int, default=1, help="Enabling splice splice (0: disable | 1: enable | 2: test both)")
    p.add_argument("--run", "-r", type=int, default=1, help="Total tests per each config")
    p.add_argument("--parallel", "-P", type=_parse_int_list, default= [1], help="Parallel streams value")
    p.add_argument("--time", "-t", type=_parse_int_list, default=[5], help="Duration of each stream (sec)")
    p.add_argument("--app", "-a", type=str, default="iperf", help="Application (iperf | )")
    p.add_argument("--blocks", "-b", type=_parse_int_list, default=[32], help="Gridftp blocksize")
    p.add_argument("--directport", type=int, default=49999, help="The default port for iperf3 baseline")
    p.add_argument("--tunnelport", type=int, default=50000, help="The default port for iperf3 through the tunnel")
    #p.add_argument("--port", type=int, default=49998, help="The default port")
    p.add_argument("--output", "-o", type=str, default="/tmp", help="Log file's base path (remote)")
    p.add_argument("--listen", "-ip",  type=str,required=True, help="The IP address of the listener") # default="192.168.10.2"
    #p.add_argument("--listenap", type=str, required=True, help="The IP address of the listener AP") #default="192.168.100.2"
    return p.parse_args()

args = parse_args()
Role = Literal["initiator", "listener"]

@dataclass(frozen=True)
class Hosts:
    ap: Mapping[Role, str]
    ep: Mapping[Role, str]

@dataclass(frozen=True)
class Config:
    # test params
    verbose: bool
    lease: str
    sleep: int
    baseline: bool
    splice: int
    run_num: int
    parallels: Sequence[int]
    time_frames: Sequence[int]
    app: str
    blocks: Sequence[int]

    # hosts
    localhost: str
    hosts: Hosts
    listener_ip: str
    #listener_ap_ip: str
    direct_port: int
    tunnel_port: int
    #port: int

    # envs / paths
    report_dir: str
    remote_env: str
    local_env: str

TEST = Config(
    verbose=args.verbose,
    lease=args.lease,
    sleep=10,
    baseline=args.baseline,
    splice=args.splice,
    run_num=args.run,
    parallels=args.parallel,
    time_frames=args.time,  #20,
    app=args.app,   #"iperf",
    blocks=args.blocks,
    localhost="localhost",
    hosts=Hosts(
        ap={"initiator": "fab-consap", "listener": "fab-prodap"},
        ep={"initiator": "fab-consep", "listener": "fab-prodep"},
    ),
    # hosts=Hosts(
    #     ap={"initiator": "fab-c2cs", "listener": "fab-p2cs"},
    #     ep={"initiator": "fab-cons", "listener": "fab-prod"},
    # ),
    listener_ip=args.listen,
    #listener_ap_ip=args.listenap,
    direct_port=args.directport,#49999,
    tunnel_port=args.tunnelport, #50000,
    #port=args.rsyncport, #49998,
    report_dir=args.output, #"/tmp",
    remote_env="/home/ubuntu/streams-cli/bin/activate",
    local_env=str(Path("~/Projects/fabric/.fabric/bin/activate").expanduser())
)

        cache/
        evaluate/
        experiment/archive/
        experiment/baseline.py
        experiment/hub/
        experiment/iperf.py.bkp
        experiment/utils.py_bkp
        reports/