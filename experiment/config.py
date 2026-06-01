"""
Main configuration file for experiment variables.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence, cast


Role = Literal["initiator", "listener"]
TestMode = Literal["stream", "transfer"]


def _parse_int_list(s: str) -> list[int]:
    try:
        values = [int(x.strip()) for x in s.split(",") if x.strip()]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid integer list: {s}") from e

    if not values:
        raise argparse.ArgumentTypeError("Integer list cannot be empty")

    return values


def _parse_str_list(s: str) -> list[str]:
    values = [x.strip() for x in s.split(",") if x.strip()]

    if not values:
        raise argparse.ArgumentTypeError("String list cannot be empty")

    return values


def _parse_app_list(s: str) -> list[str]:
    valid_apps = {"iperf", "base", "rsync"}
    apps = _parse_str_list(s)

    invalid = sorted(set(apps) - valid_apps)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid app(s): {', '.join(invalid)}. "
            f"Valid choices: {', '.join(sorted(valid_apps))}"
        )

    return apps


@dataclass(frozen=True)
class Hosts:
    ap: Mapping[Role, str]
    ep: Mapping[Role, str]

@dataclass(frozen=True)
class Config:
    verbose: bool
    lease: str
    test: TestMode

    localhost: str
    hosts: Hosts
    listener_ip: str
    listener_pub: str
    initiator_ip: str
    initiator_pub: str

    tunnel_port: int
    direct_port: int
    rsync_port: int

    sleep: int

    app: Sequence[str]
    encrypt: Sequence[int]
    splice: Sequence[int]
    parallels: Sequence[int]
    time_frames: Sequence[int]
    file_sizes: Sequence[int]
    blocks: Sequence[int]
    run_num: int

    local_env: str
    remote_env: str
    report_dir: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stream or transfer experiments.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--lease", required=True, help="Lease name on the testbed")
    parser.add_argument("--test", required=True, choices=["stream", "transfer"], help="Experiment/test to perform")
    parser.add_argument("--userhost", default="localhost", help="Host where the command will execute")
    parser.add_argument("--remote-user", default="ubuntu", help="Remote username on the nodes")

    parser.add_argument("--initiator-ap", default="neat-guy")
    parser.add_argument("--listener-ap", default="that-guy")
    parser.add_argument("--initiator-ep", default="swell-guy")
    parser.add_argument("--listener-ep", default="this-guy")

    parser.add_argument("--listener-ip", default="128.135.24.117")
    parser.add_argument("--listener-pub", default="10.191.131.177")
    parser.add_argument("--initiator-ip", default="128.135.37.240")
    parser.add_argument("--initiator-pub", default="10.191.129.103")

    parser.add_argument("--tunnel-port", type=int, default=50000)
    parser.add_argument("--direct-port", type=int, default=49999)
    parser.add_argument("--rsync-port", type=int, default=49998)

    parser.add_argument("--sleep", type=int, default=15)

    parser.add_argument("--app", type=_parse_app_list, required=True, help="Comma-separated applications: iperf,base,rsync")
    parser.add_argument("--encrypt", type=_parse_int_list, default=[0])
    parser.add_argument("--splice", type=_parse_int_list, default=[1])
    parser.add_argument("--parallel", "-P", type=_parse_int_list, default=[1])
    parser.add_argument("--time", "-t", type=_parse_int_list, default=[10])
    parser.add_argument("--size", "-n", type=_parse_int_list, default=[1])
    parser.add_argument("--blocks", "-b", type=_parse_int_list, default=[32])
    parser.add_argument("--run", "-r", type=int, default=1)

    parser.add_argument("--output", "-o", default="/tmp")

    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    is_stream = args.test == "stream"

    return Config(
        verbose=args.verbose,
        lease=args.lease,
        test=cast(TestMode, args.test),

        localhost=args.userhost,
        hosts=Hosts(
            ap={
                "initiator": args.initiator_ap,
                "listener": args.listener_ap,
            },
            ep={
                "initiator": args.initiator_ep,
                "listener": args.listener_ep,
            },
        ),
        listener_ip=args.listener_ip,
        listener_pub=args.listener_pub,
        initiator_ip=args.initiator_ip,
        initiator_pub=args.initiator_pub,

        tunnel_port=args.tunnel_port,
        direct_port=args.direct_port,
        rsync_port=args.rsync_port,

        sleep=args.sleep,

        app=args.app,
        encrypt=args.encrypt,
        splice=args.splice,
        parallels=args.parallel,
        time_frames=args.time if is_stream else [],
        file_sizes=args.size if not is_stream else [],
        blocks=args.blocks,
        run_num=args.run,

        local_env=str(
            Path("~/Projects/globus_stream/streams-cli/bin/activate").expanduser()
        ),
        remote_env=f"/home/{args.remote_user}/streams-cli/bin/activate",
        report_dir=args.output,
    )


def get_config() -> Config:
    args = parse_args()
    return build_config(args)
