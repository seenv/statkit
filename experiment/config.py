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
    p.add_argument("--lease", type=str, required=True, help="Lease name on the testbed")
    p.add_argument("--test", type=str, required=True, choices=["stream", "transfer"], help="Experimet / test to perform (stream | transfer)")
    
    p.add_argument("--userhost", type=str, default="localhost", help="The host name where the command will execute")
    p.add_argument("--remote-user", type=str, default="ubuntu", help="The remot user name on the nodes")
    p.add_argument("--initiator-ap", type=str, default="neat-guy", help="Access point initiator host name")
    p.add_argument("--listener-ap", type=str, default="that-guy", help="Access point listener host name")
    p.add_argument("--initiator-ep", type=str, default="swell-guy", help="Endpoint initiator host name")
    p.add_argument("--listener-ep", type=str, default="this-guy", help="Endpoint listener host name")
    p.add_argument("--listener-ip",  type=str, default="128.135.24.117", help="The IP address of the listener (default: 192.168.10.10)") # default="10.52.2.167"
    p.add_argument("--initiator-ip",  type=str, default="128.135.37.240", help="The IP address of the initiator (default: 192.168.20.10)") # default="10.52.2.167"
    p.add_argument("--tunnel-port", type=int, default=50000, help="The default port for iperf3 through the tunnel (default: 50000)")
    p.add_argument("--direct-port", type=int, default=49999, help="The default port for iperf3 baseline (default: 49999)")
    p.add_argument("--rsync-port", type=int, default=49998, help="The default port for rsync (default: 49998)")
    p.add_argument("--sleep", type=int, default=10, help="DEBUG: Sleep time between between the commands (default: 10)")

    p.add_argument("--app", "-a", type=_parse_str_list, required=True, help="Application (iperf | base | rsync)")
    p.add_argument("--encrypt", type=_parse_int_list, default= [0], help="Enabling tunnel encryption (default: 0 | 0: disable | 1: enable)")
    p.add_argument("--splice", type=_parse_int_list, default= [1], help="Enabling splice splice (default: 1 | 0: disable | 1: enable)")
    p.add_argument("--parallel", "-P", type=_parse_int_list, default= [1], help="Parallel streams value; only in globus test(default: 1)")
    p.add_argument("--time", "-t", type=_parse_int_list, default=[5], help="Duration of each stream in second; only in globus test (default: 5s)")
    p.add_argument("--size", "-n", type=_parse_int_list, default=[1], help="File size to transfer in Gigabyte; only in transfer test (default: 1G)")
    p.add_argument("--blocks", "-b", type=_parse_int_list, default=[32], help="Gridftp blocksize (default: 32)")
    p.add_argument("--run", "-r", type=int, default=1, help="Total tests per each config")
    p.add_argument("--output", "-o", type=str, default="/tmp", help="Log file's base path (remote)")
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
    test: str

    # hosts
    localhost: str
    hosts: Hosts
    listener_ip: str
    initiator_ip: str
    tunnel_port: int
    direct_port: int
    rsync_port: int
    #port: int
    sleep: int
    
    # test configuration
    app: Sequence[str]
    encrypt: Sequence[int]
    splice: Sequence[int]
    parallels: Sequence[int]
    time_frames: Sequence[int]
    file_sizes: Sequence[int]
    blocks: Sequence[int]
    run_num: int

    # envs / paths
    local_env: str
    remote_env: str
    report_dir: str


TRANSFER = Config(
    # test params
    verbose=args.verbose,
    lease=args.lease,
    test=args.test,

    # hosts
    localhost=args.localhost,
    # hosts=Hosts(
    #     ap={
    #         "initiator": args.initiator_ap,
    #         "listener": args.listener_ap,
    #     },
    #     ep={
    #         "initiator": args.initiator_ep,
    #         "listener": args.listener_ep,
    #     },
    # ),
    hosts=Hosts(
        ap={"initiator": "fab-c2cs", "listener": "fab-p2cs"},
        ep={"initiator": "fab-cons", "listener": "fab-prod"},
    ),
    listener_ip=args.listener_ip,
    initiator_ip=args.initiator_ip,
    tunnel_port=args.tunnel_port,
    direct_port=args.direct_port,
    rsync_port=args.rsync_port,
    sleep=args.sleep,

    # test configuration
    app=args.app,
    encrypt=args.encrypt,
    splice=args.splice,
    parallels=args.parallel,
    time_frames=args.time,
    file_sizes=args.size,
    blocks=args.blocks,
    run_num=args.run,

    # envs / paths
    local_env=str(Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser()),
    remote_env=f"/home/{args.remote_user}/streams-cli/bin/activate",
    report_dir=args.output,
)

STREAM = Config(
    verbose=args.verbose,
    test=args.test,
    lease=args.lease,

    # hosts
    localhost=args.localhost,
    # hosts=Hosts(
    #     ap={
    #         "initiator": args.initiator_ap,
    #         "listener": args.listener_ap,
    #     },
    #     ep={
    #         "initiator": args.initiator_ep,
    #         "listener": args.listener_ep,
    #     },
    # ),
    hosts=Hosts(
        ap={"initiator": "fab-consap", "listener": "fab-prodap"},
        ep={"initiator": "fab-consep", "listener": "fab-prodep"},
    ),
    listener_ip=args.listener_ip,
    initiator_ip=args.initiator_ip,
    tunnel_port=args.tunnel_port,
    direct_port=args.direct_port,
    rsync_port=args.rsync_port,
    sleep=args.sleep,

    # test configuration
    app=args.app,
    encrypt=args.encrypt,
    splice=args.splice,
    parallels=args.parallel,
    time_frames=args.time,
    file_sizes=args.size,
    blocks=args.blocks,
    run_num=args.run,

    # envs / paths
    local_env=str(Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser()),
    remote_env=f"/home/{args.remote_user}/streams-cli/bin/activate",
    report_dir=args.output, 
)

TEST = STREAM if args.test == "stream" else TRANSFER
