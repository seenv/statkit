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
    # if not values:
    #     raise argparse.ArgumentTypeError("Integer list cannot be empty")
    return values

def _parse_str_list(s: str) -> list[str]:
    values = [x.strip() for x in s.split(",") if x.strip()]
    if not values:
        raise argparse.ArgumentTypeError("String list cannot be empty")
    return values

def _parse_test_list(s: str) -> list[TestMode]:
    valid_tests = {"stream", "transfer"}
    tests = _parse_str_list(s)

    invalid = sorted(set(tests) - valid_tests)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid test mode(s): {', '.join(invalid)}. "
            f"Valid choices: {', '.join(sorted(valid_tests))}"
        )

    return [cast(TestMode, test) for test in tests]

def _parse_proxy_list(s: str) -> list[str]:
    valid_proxies = {"apache", "stunnel", "haproxy"}
    proxies = _parse_str_list(s)
    invalid = sorted(set(proxies) - valid_proxies)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid proxy(s): {', '.join(invalid)}. "
            f"Valid choices: {', '.join(sorted(valid_proxies))}"
        )
    return proxies

def _parse_app_list(s: str) -> list[str]:
    valid_apps = {"iperf", "ibase", "sperf", "rsync", "rbase", "gtr", "mini", "mbase"}
    apps = _parse_str_list(s)
    invalid = sorted(set(apps) - valid_apps)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid app(s): {', '.join(invalid)}. "
            f"Valid choices: {', '.join(sorted(valid_apps))}"
        )
    return apps

def _parse_numa_list(s: str) -> list[str]:
    valid_numas = {"numa", "no-numa"}
    numas = _parse_str_list(s)
    invalid = sorted(set(numas) - valid_numas)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid numa(s): {', '.join(invalid)}. "
            f"Valid choices: {', '.join(sorted(valid_numas))}"
        )
    return numas

def _default_hosts_for_lease(lease: str) -> dict[str, str]:
    if lease.lower() == "chameleon":
        return {
            "initiator_ap": "chi-c2cs",
            "listener_ap": "chi-p2cs",
            "initiator_ep": "chi-cons",
            "listener_ep": "chi-prod",
        }
    elif lease.lower() == "fabric":
        return {
            "initiator_ap"  : "mfab-c2cs",
            "listener_ap"   : "mfab-p2cs",
            "initiator_ep"  : "mfab-cons",
            "listener_ep"   : "mfab-prod",
        }
    elif lease.lower() == "guys":
        return {
            "initiator_ap"  : "neat-guy",
            "listener_ap"   : "that-guy",
            "initiator_ep"  : "swell-guy",
            "listener_ep"   : "this-guy",
        }
    else:
        print("WTF in _default_hosts_for_lease")

    # raise ValueError(
    #     f"Unsupported lease {lease!r}. Expected 'fabric' or 'chameleon'"
    # )

def _default_ips_for_lease(lease: str) -> dict[str, str]:
    if lease.lower() == "chameleon":
        return {
            "listener_ip": "192.168.110.10",
            "listener_pub": "10.191.130.100",
            "initiator_ip": "192.168.120.10",
            "initiator_pub": "10.191.129.43",

            "listener_ap_ip": "128.135.24.119",
            "listener_ap_pub": "128.135.164.119",
            "initiator_ap_ip": "128.135.37.241",
            "initiator_ap_pub": "128.135.164.120",
        }
    elif lease.lower() == "fabric":
        return {
            "listener_ip": "192.168.10.10",
            "listener_pub": "192.168.10.10",
            "initiator_ip": "192.168.20.10",
            "initiator_pub": "192.168.20.10",

            "listener_ap_ip": "192.168.10.20",
            "listener_ap_pub": "192.168.100.10",
            "initiator_ap_ip": "192.168.20.20",
            "initiator_ap_pub": "192.168.100.20",
        }
    elif lease.lower() == "guys":
        return {
            "listener_ip": "128.135.24.117",
            "listener_pub": "128.135.24.117",
            "initiator_ip": "128.135.37.240",
            "initiator_pub": "128.135.37.240",
            
            "listener_ap_ip": "128.135.24.119",
            "listener_ap_pub": "128.135.164.119",
            "initiator_ap_ip": "128.135.37.241",
            "initiator_ap_pub": "128.135.164.120",
        }
    else:
        print("WTF in _default_ips_for_lease")

# from typing import TypeVar
# T = TypeVar("T")
# def _use_default(value: T | None, default: T) -> T:
#     return default if value is None else value

def _default_interfaces_for_lease(
    lease: str,
) -> dict[str, list[str]]:
    if lease.lower() == "chameleon":
        return {
            "initiator_ap": ["eno1np0", "eno2np1"],
            "listener_ap": ["eno12399np0", "enp152s0np0"],
            "initiator_ep": ["eno1np0", "eno2np1"],
            "listener_ep": ["eno12399np0", "enp152s0np0"],
        }
    elif lease.lower() == "fabric":
        return {
            "initiator_ap": ["enp8s0np0", "enp9s0np1"],
            "listener_ap": ["enp8s0np0", "enp9s0np1"],
            "initiator_ep": ["enp7s0", "enp8s0"],
            "listener_ep": ["enp7s0", "enp8s0"],
        }
    elif lease.lower() == "guys":
        return {
            "initiator_ap": ["ens18", "ens19"],
            "listener_ap": ["ens18", "ens19"],
            "initiator_ep": ["ens18"],
            "listener_ep": ["ens18"],
        }
    else:
        print("WTF in _default_interfaces_for_lease")

def _build_interface_map(
    args: argparse.Namespace,
) -> dict[str, tuple[str, ...]]:
    entries = (
        (args.initiator_ap, args.initiator_ap_devs),
        (args.listener_ap, args.listener_ap_devs),
        (args.initiator_ep, args.initiator_ep_devs),
        (args.listener_ep, args.listener_ep_devs),
    )

    result: dict[str, list[str]] = {}

    for host, devices in entries:
        host_devices = result.setdefault(host, [])

        for dev in devices:
            dev = dev.strip()

            if dev and dev not in host_devices:
                host_devices.append(dev)

    return {
        host: tuple(devices)
        for host, devices in result.items()
    }

@dataclass(frozen=True)
class Hosts:
    ap: Mapping[Role, str]
    ep: Mapping[Role, str]

@dataclass(frozen=True)
class Config:
    verbose: bool
    is_test: bool
    lease: str
    test: TestMode
    #proxy: Sequence[str]

    numactl: Sequence[str]
    tcp_buffer: str
    ring_buffer: str

    localhost: str
    hosts: Hosts

    interfaces: Mapping[str, Sequence[str]]

    listener_ip: str
    listener_pub: str
    initiator_ip: str
    initiator_pub: str

    listener_ap_ip: str
    listener_ap_pub: str
    initiator_ap_ip: str
    initiator_ap_pub: str

    tunnel_port: int
    encrypt_port: int
    direct_port: int
    rsync_port: int
    mini_port: int
    mbase_port: int
    scisync_port: int
    inbound_ports: Sequence[int]
    outbound_ports: Sequence[int]
    tomo_file: str

    sleep: int

    app: Sequence[str]
    # encrypt: Sequence[int]
    encrypt: bool
    splice: Sequence[int]
    parallels: Sequence[int]
    time_frames: Sequence[int]
    file_sizes: Sequence[int]
    blocks: Sequence[int]
    run_num: int

    local_env: str
    remote_env: str
    scistream_env: str
    report_dir: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stream or transfer experiments.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--is-test", action="store_true")
    parser.add_argument("--lease", type=str.lower, required=True, choices=["fabric", "chameleon", "guys"], help="Lease name on the testbed")
    parser.add_argument("--test", type=_parse_test_list, required=True, help="Comma-separated test modes: stream,transfer")
    #parser.add_argument("--proxy", type=_parse_proxy_list, required=True, help="Comma-separated proxy types: apache,stunnel,haproxy")
    parser.add_argument("--userhost", default="localhost", help="Host where the command will execute")

    parser.add_argument("--numactl", type=_parse_numa_list, required=True, help="Experimetns with numa tunning enabled")
    parser.add_argument("--tcp-buffer", type=str, required=True)#, help="Experimetns with numa tunning enabled")
    parser.add_argument("--ring-buffer", type=str, required=True)#, help="Experimetns with numa tunning enabled")

    parser.add_argument("--initiator-ap", type=str, default=None, help="Initiator access-point host; defaults according to --lease")
    parser.add_argument("--listener-ap", type=str, default=None, help="Listener access-point host; defaults according to --lease")
    parser.add_argument("--initiator-ep", type=str, default=None, help="Initiator endpoint host; defaults according to --lease")
    parser.add_argument("--listener-ep", type=str, default=None, help="Listener endpoint host; defaults according to --lease")

    parser.add_argument("--initiator-ap-devs", type=_parse_str_list, default=None, help="Comma-separated interfaces on the initiator access point")
    parser.add_argument("--listener-ap-devs", type=_parse_str_list, default=None, help="Comma-separated interfaces on the listener access point")
    parser.add_argument("--initiator-ep-devs", type=_parse_str_list, default=None, help="Comma-separated interfaces on the initiator endpoint")
    parser.add_argument("--listener-ep-devs", type=_parse_str_list, default=None, help="Comma-separated interfaces on the listener endpoint" )

    parser.add_argument("--listener-ip", default=None, help="Listener EP Private IP address")
    parser.add_argument("--listener-pub", default=None, help="Listener EP Public IP address (accessible by Initiator EP)")
    parser.add_argument("--initiator-ip", default=None, help="Initiator EP Private IP address")
    parser.add_argument("--initiator-pub", default=None, help="Initiator EP Public IP address (accessible by Listener EP)")
    
    parser.add_argument("--listener-ap-ip", default=None, help="Listener AP Private IP address")
    parser.add_argument("--listener-ap-pub", default=None, help="Listener AP Public IP address (accessible by Initiator EP)")
    parser.add_argument("--initiator-ap-ip", default=None, help="Initiator Ap Private IP address")
    parser.add_argument("--initiator-ap-pub", default=None, help="Initiator Ap Public IP address (accessible by Listener EP)")

    parser.add_argument("--tunnel-port", type=int, default=51000)
    parser.add_argument("--encrypt-port", type=int, default=51015)
    parser.add_argument("--direct-port", type=int, default=51030)
    parser.add_argument("--rsync-port", type=int, default=51045)
    parser.add_argument("--mini-port", type=int, default=51060)
    parser.add_argument("--mbase-port", type=int, default=51075)
    parser.add_argument("--scisync-port", type=int, default=51100)
    #parser.add_argument("--inbound-ports", type=_parse_int_list, default=[51115,51116,51117,51118,51119])
    #parser.add_argument("--outbound-ports", type=_parse_int_list, default=[51130,51131,51132,51133,51134])
    parser.add_argument("--inbound-ports", type=_parse_int_list, default=[51115])
    parser.add_argument("--outbound-ports", type=_parse_int_list, default=[51130])
    parser.add_argument("--tomo-file", type=str, default="tomo_00058_all_subsampled1p_s1079s1081.h5", choices=["tomo_00058_all_subsampled1p_s1079s1081.h5", "tomo_00058.h5"])

    parser.add_argument("--sleep", type=int, default=5)

    parser.add_argument("--app", type=_parse_app_list, required=True, help="Comma-separated applications: iperf,ibase,sperf,rsync,rbase,gtr,mini,mbase")
    parser.add_argument("--encrypt", action="store_true", help="Enabling encryption and disabling the splice")
    #parser.add_argument("--splice", type=_parse_int_list, help="Splice option, 0: disable, 1: enable")
    parser.add_argument("--splice", type=_parse_int_list, default=None, help="Splice option, 0: disable, 1: enable")
    parser.add_argument("--parallel", "-P", type=_parse_int_list, default=[1])
    parser.add_argument("--time", "-t", type=_parse_int_list, default=[15])
    parser.add_argument("--size", "-n", type=_parse_int_list, default=[1])
    parser.add_argument("--blocks", "-b", type=_parse_int_list, default=[32])
    parser.add_argument("--run", "-r", type=int, default=1)

    parser.add_argument("--output", "-o", default="/tmp/")

    #return parser.parse_args()
    return parser, parser.parse_args()


def build_config(args: argparse.Namespace,  test_mode: TestMode) -> Config:

    is_stream = test_mode == "stream"

    default_hosts = _default_hosts_for_lease(args.lease)
    default_devs = _default_interfaces_for_lease(args.lease)
    default_ips = _default_ips_for_lease(args.lease)

    args.initiator_ap = args.initiator_ap if args.initiator_ap is not None else default_hosts["initiator_ap"]
    args.listener_ap = args.listener_ap if args.listener_ap is not None else default_hosts["listener_ap"]
    args.initiator_ep = args.initiator_ep if args.initiator_ep is not None else default_hosts["initiator_ep"]
    args.listener_ep = args.listener_ep if args.listener_ep is not None else default_hosts["listener_ep"]

    args.initiator_ap_devs = (args.initiator_ap_devs if args.initiator_ap_devs is not None else default_devs["initiator_ap"])
    args.listener_ap_devs = (args.listener_ap_devs if args.listener_ap_devs is not None else default_devs["listener_ap"])
    args.initiator_ep_devs = (args.initiator_ep_devs if args.initiator_ep_devs is not None else default_devs["initiator_ep"])
    args.listener_ep_devs = (args.listener_ep_devs if args.listener_ep_devs is not None else default_devs["listener_ep"])

    listener_ip = (args.listener_ip if args.listener_ip is not None else default_ips["listener_ip"])
    listener_pub = (args.listener_pub if args.listener_pub is not None else default_ips["listener_pub"])
    initiator_ip = (args.initiator_ip if args.initiator_ip is not None else default_ips["initiator_ip"])
    initiator_pub = (args.initiator_pub if args.initiator_pub is not None else default_ips["initiator_pub"])

    listener_ap_ip = (args.listener_ap_ip if args.listener_ap_ip is not None else default_ips["listener_ap_ip"])
    listener_ap_pub = (args.listener_ap_pub if args.listener_ap_pub is not None else default_ips["listener_ap_pub"])
    initiator_ap_ip = (args.initiator_ap_ip if args.initiator_ap_ip is not None else default_ips["initiator_ap_ip"])
    initiator_ap_pub = (args.initiator_ap_pub if args.initiator_ap_pub is not None else default_ips["initiator_ap_pub"])

    return Config(
        verbose=args.verbose,
        is_test=args.is_test,
        lease=args.lease,
        test=test_mode,
        # proxy=args.proxy,

        numactl=args.numactl,
        tcp_buffer=args.tcp_buffer,
        ring_buffer=args.ring_buffer,

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

        interfaces=_build_interface_map(args),

        listener_ip=listener_ip,
        listener_pub=listener_pub,
        initiator_ip=initiator_ip,
        initiator_pub=initiator_pub,

        listener_ap_ip=listener_ap_ip,
        listener_ap_pub=listener_ap_pub,
        initiator_ap_ip=initiator_ap_ip,
        initiator_ap_pub=initiator_ap_pub,

        tunnel_port=args.tunnel_port,
        encrypt_port=args.encrypt_port,
        direct_port=args.direct_port,
        rsync_port=args.rsync_port,
        mini_port=args.mini_port,
        mbase_port=args.mbase_port,
        scisync_port=args.scisync_port,
        inbound_ports=args.inbound_ports,
        outbound_ports=args.outbound_ports,
        tomo_file=args.tomo_file,

        sleep=args.sleep,

        app=args.app,
        encrypt=bool(args.encrypt),
        #splice=args.splice,
        splice=args.splice if args.splice is not None else [],
        parallels=args.parallel,
        time_frames=args.time if is_stream else [],
        file_sizes=args.size if not is_stream else [],
        blocks=args.blocks,
        run_num=args.run,

        local_env="$HOME/Projects/globus_stream/streams-cli/bin/activate",
        remote_env="$HOME/streams-cli/bin/activate",
        scistream_env="$HOME/seenv-scistream-proto/.seenvstream/bin/activate",
        report_dir=args.output,
    )

# def get_configs() -> list[Config]:
#     args = parse_args()

#     return [
#         build_config(args, test_mode)
#         for test_mode in args.test
#     ]

def get_configs() -> list[Config]:
    parser, args = parse_args()
    if not args.splice and not args.encrypt:
        parser.error("at least one of --splice or --encrypt is required")
    return [build_config(args, test_mode) for test_mode in args.test]