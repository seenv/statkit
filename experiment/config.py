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


def _parse_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]

def _parse_int_list(name: str, default: list[int]) -> list[int]:
    return [int(x) for x in _parse_list(name, [str(d) for d in default])]

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", "-v", action="store_true", default=False)
    p.add_argument("--run", type=int, default=2, help="Number of times to test one config")
    p.add_argument("--parallel", "-P", type=int, default= 1, help="Number of parallel streams")
    p.add_argument("--time", "-t", type=int, default=5, help="Duration of each stream (seconds)")
    p.add_argument("--app", "-a", type=str, default="iperf", help="Number of times to test one config")
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
    run_num: int
    parallels: Sequence[int]
    time_frames: Sequence[int]
    app: str

    # hosts
    localhost: str
    hosts: Hosts
    listener_ip: str
    base_port: int

    # envs / paths
    report_dir: str
    remote_env: str
    local_env: str


TEST = Config(
    verbose=args.verbose,
    run_num=args.run,
    parallels=[args.parallel],
    time_frames=[args.time],  #20,
    app=args.app,   #"iperf",
    localhost="localhost",
    hosts=Hosts(
        ap={"initiator": "chi-c2cs", "listener": "chi-p2cs"},
        ep={"initiator": "chi-cons", "listener": "chi-prod"},
    ),
    listener_ip="10.140.82.103",
    base_port=50000,
    report_dir="/tmp",
    remote_env="/home/cc/streams-cli/bin/activate",
    local_env=str(Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser()),
)
