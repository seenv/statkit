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
    p.add_argument("--baseline", action="store_true", default=False, help="Enable baseline tests")
    p.add_argument("--run", "-r", type=int, default=1, help="Total tests per each config")
    p.add_argument("--parallel", "-P", type=_parse_int_list, default= [1], help="Parallel streams value")
    p.add_argument("--time", "-t", type=_parse_int_list, default=[5], help="Duration of each stream (sec)")
    p.add_argument("--app", "-a", type=str, default="rsync", help="Application (iperf | rsync)")
    p.add_argument("--blocks", "-b", type=_parse_int_list, default=[32], help="Gridftp blocksize")
    p.add_argument("--output", "-o", type=str, default="/tmp", help="Log file's base path (remote)")
    p.add_argument("--listen", "-ip",  type=str,required=True, help="The IP address of the listener") # default="10.52.2.167"
    p.add_argument("--listenap", type=str, required=True, help="The IP address of the listener AP") #default="129.114.108.91"
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
    sleep: int
    baseline: bool
    run_num: int
    parallels: Sequence[int]
    time_frames: Sequence[int]
    app: str
    blocks: Sequence[int]

    # hosts
    localhost: str
    hosts: Hosts
    listener_ip: str
    listener_ap_ip: str
    ap_port: int
    ep_port: int

    # envs / paths
    report_dir: str
    remote_env: str
    local_env: str

TEST = Config(
    verbose=args.verbose,
    sleep=10,
    baseline=args.baseline,
    run_num=args.run,
    parallels=args.parallel,
    time_frames=args.time,  #20,
    app=args.app,   #"iperf",
    blocks=args.blocks,
    localhost="localhost",
    hosts=Hosts(
        ap={"initiator": "initiator-ap", "listener": "listener-ap"},
        ep={"initiator": "initiator-ep", "listener": "listener-ep"},
    ),
    # hosts=Hosts(
    #     ap={"initiator": "fab-c2cs", "listener": "fab-p2cs"},
    #     ep={"initiator": "fab-cons", "listener": "fab-prod"},
    # ),
    listener_ip=args.listen,
    listener_ap_ip=args.listenap,
    ap_port=49999,
    ep_port=50000,
    report_dir=args.output, #"/tmp",
    remote_env="/home/cc/streams-cli/bin/activate",
    local_env=str(Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser())
)
