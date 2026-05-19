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

def _parse_str_list(s: str) -> list[str]:
    try:
        return [str(x.strip()) for x in s.split(",") if x.strip()]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid str list: {s}") from e

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="store_true", default=False)
<<<<<<< Updated upstream
    p.add_argument("--baseline", action="store_true", default=False, help="Enable baseline tests")
    p.add_argument("--run", "-r", type=int, default=1, help="Total tests per each config")
    p.add_argument("--parallel", "-P", type=_parse_int_list, default= [1], help="Parallel streams value")
    p.add_argument("--time", "-t", type=_parse_int_list, default=[5], help="Duration of each stream (sec)")
    p.add_argument("--app", "-a", type=str, default="iperf", help="Application (iperf | )")
=======
    p.add_argument("--test", type=str, required=True, choices=["globus", "transfer"], help="Experimet / test to perform (globus | transfer)")
    p.add_argument("--lease", type=str, required=True, help="Lease name on the testbed")
    p.add_argument("--baseline", action="store_true", default=False, help="Enable baseline tests")
    p.add_argument("--splice", type=_parse_int_list, default= [1], help="Enabling splice splice (0: disable | 1: enable)")
    p.add_argument("--run", "-r", type=int, default=1, help="Total tests per each config")
    p.add_argument("--parallel", "-P", type=_parse_int_list, default= [1], help="Parallel streams value")
    p.add_argument("--time", "-t", type=_parse_int_list, default=[5], help="Duration of each stream (sec)")
    p.add_argument("--size", "-n", type=_parse_int_list, default=[1], help="File size to transfer")
    p.add_argument("--app", "-a", type=_parse_str_list, required=True, choices=["iperf", "base", "rsync"], help="Application (iperf | base | rsync)")
>>>>>>> Stashed changes
    p.add_argument("--blocks", "-b", type=_parse_int_list, default=[32], help="Gridftp blocksize")
    p.add_argument("--output", "-o", type=str, default="/tmp", help="Log file's base path (remote)")
    p.add_argument("--listen", "-ip",  type=str,required=True, help="The IP address of the listener") # default="10.52.2.167"
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
<<<<<<< Updated upstream
=======
    test: str
    lease: str
>>>>>>> Stashed changes
    sleep: int
    baseline: bool
    run_num: int
    parallels: Sequence[int]
    time_frames: Sequence[int]
<<<<<<< Updated upstream
    app: str
=======
    file_sizes: Sequence[int]
    app: Sequence[str]
>>>>>>> Stashed changes
    blocks: Sequence[int]

    # hosts
    localhost: str
    hosts: Hosts
    listener_ip: str
<<<<<<< Updated upstream
    listener_ap_ip: str
    ap_port: int
    ep_port: int
=======
    direct_port: int
    tunnel_port: int
    #port: int
>>>>>>> Stashed changes

    # envs / paths
    report_dir: str
    remote_env: str
    local_env: str

TRANSFER = Config(
    verbose=args.verbose,
<<<<<<< Updated upstream
    sleep=10,
    baseline=args.baseline,
    run_num=args.run,
    parallels=args.parallel,
    time_frames=args.time,  #20,
=======
    test=args.test,
    lease=args.lease,
    sleep=10,
    baseline=args.baseline,
    splice=args.splice,
    run_num=args.run,
    parallels=args.parallel,
    time_frames=args.time,  #20,
    file_sizes=args.size,
>>>>>>> Stashed changes
    app=args.app,   #"iperf",
    blocks=args.blocks,
    localhost="localhost",
    hosts=Hosts(
        ap={"initiator": "chi-cons-ap", "listener": "chi-prod-ap"},
        ep={"initiator": "chi-cons-ep", "listener": "chi-prod-ep"},
    ),
    listener_ip=args.listen,
<<<<<<< Updated upstream
    listener_ap_ip=args.listenap,
    ap_port=49999,
    ep_port=50000,
=======
    direct_port=args.directport,#49999,
    tunnel_port=args.tunnelport, #50000,
    #port=args.rsyncport, #49998,
>>>>>>> Stashed changes
    report_dir=args.output, #"/tmp",
    remote_env="/home/cc/streams-cli/bin/activate",
    local_env=str(Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser())
)

GLOBUS = Config(
    verbose=args.verbose,
    test=args.test,
    lease=args.lease,
    sleep=10,
    baseline=args.baseline,
    splice=args.splice,
    run_num=args.run,
    parallels=args.parallel,
    time_frames=args.time,  #20,
    file_sizes=args.size,
    app=args.app,   #"iperf",
    blocks=args.blocks,
    localhost="localhost",
    hosts=Hosts(
        ap={"initiator": "fab-consap", "listener": "fab-prodap"},
        ep={"initiator": "fab-consep", "listener": "fab-prodep"},
    ),
    listener_ip=args.listen,
    direct_port=args.directport,#49999,
    tunnel_port=args.tunnelport, #50000,
    #port=args.rsyncport, #49998,
    report_dir=args.output, #"/tmp",
    remote_env="/home/ubuntu/streams-cli/bin/activate",
    local_env=str(Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser())
)

# if args.test == "globus":
#     TEST = GLOBUS
# elif args.test == "transfer":
#     TEST = TRANSFER
# else:
#     raise ValueError(f"Unknown test value: {args.test}")
TEST = GLOBUS if args.test == "globus" else TRANSFER
