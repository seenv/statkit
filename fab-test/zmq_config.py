"""
CLI + Config for the ZeroMQ streaming experiments (SciStream paper replication).

Builds your existing `config.Config` (so gstreams/scistream/utils work
unchanged) plus a `ZmqOpts` with the ZeroMQ-specific settings.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from config import (
    Config, Hosts,
    _parse_int_list, _parse_str_list, _build_interface_map,
    _default_hosts_for_lease, _default_ips_for_lease, _default_interfaces_for_lease,
)

VALID_APPS = ("zgst", "zbase", "zsci")
APP_TAGS = {"zgst": "zmq_gst", "zbase": "zmq_base", "zsci": "zmq_sci"}
DEFAULT_SIZES = [2 ** k for k in range(9, 21)]   # 512 B .. 1 MiB
MIN_SAMPLE_SIZE = 256                            # control messages must stay distinguishable


def _parse_app_list(s: str) -> list[str]:
    apps = _parse_str_list(s)
    invalid = sorted(set(apps) - set(VALID_APPS))
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid app(s): {', '.join(invalid)}. Valid choices: {', '.join(VALID_APPS)}")
    return [a for a in VALID_APPS if a in apps]   # fixed order: gst -> base -> sci


def _parse_size_list(s: str) -> list[int]:
    sizes = _parse_int_list(s)
    if not sizes:
        raise argparse.ArgumentTypeError("Sample size list cannot be empty")
    bad = [x for x in sizes if x < MIN_SAMPLE_SIZE]
    if bad:
        raise argparse.ArgumentTypeError(f"Sample sizes must be >= {MIN_SAMPLE_SIZE} bytes: {bad}")
    return sizes


@dataclass(frozen=True)
class ZmqOpts:
    sizes: Sequence[int]
    total_size_gb: int
    per_stream: bool
    pattern: str
    hwm: int
    retries: int
    src_dir: str
    stream_timeout: int
    start_timeout: int
    idle_timeout: int
    statkit_pad: int
    gst_port: int
    gst_encrypt_port: int
    direct_port: int
    local_rsync: str

    def stream_bytes(self, parallel: int) -> int:
        total = self.total_size_gb * (1024 ** 3)
        return total if self.per_stream else total // parallel


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    p = argparse.ArgumentParser(description="ZeroMQ streaming experiments: GST vs Direct vs SciStream")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--is-test", action="store_true", help="Skip statkit, ping and system report")
    p.add_argument("--lease", type=str.lower, required=True, choices=["fabric", "chameleon", "guys"])
    p.add_argument("--tname", type=str, default="test")
    p.add_argument("--userhost", default="localhost")

    # labels only (kept in Config for compatibility with your helpers)
    p.add_argument("--numactl", choices=["numa", "no-numa"], default="no-numa")
    p.add_argument("--tcp-buffer", type=str, default="default")
    p.add_argument("--ring-buffer", type=str, default="default")

    p.add_argument("--initiator-ap", default=None)
    p.add_argument("--listener-ap", default=None)
    p.add_argument("--initiator-ep", default=None)
    p.add_argument("--listener-ep", default=None)
    p.add_argument("--initiator-ap-devs", type=_parse_str_list, default=None)
    p.add_argument("--listener-ap-devs", type=_parse_str_list, default=None)
    p.add_argument("--initiator-ep-devs", type=_parse_str_list, default=None)
    p.add_argument("--listener-ep-devs", type=_parse_str_list, default=None)

    p.add_argument("--listener-ip", default=None)
    p.add_argument("--listener-pub", default=None)
    p.add_argument("--initiator-ip", default=None)
    p.add_argument("--initiator-pub", default=None)
    p.add_argument("--listener-ap-ip", default=None)
    p.add_argument("--listener-ap-pub", default=None)
    p.add_argument("--initiator-ap-ip", default=None)
    p.add_argument("--initiator-ap-pub", default=None)

    # new ports (no overlap with your other experiments)
    p.add_argument("--gst-port", type=int, default=60000)
    p.add_argument("--gst-encrypt-port", type=int, default=60100)
    p.add_argument("--direct-port", type=int, default=60200)
    p.add_argument("--sci-inbound-port", type=int, default=60400)
    p.add_argument("--sci-outbound-port", type=int, default=60600)
    p.add_argument("--scisync-port", type=int, default=59999)

    p.add_argument("--sleep", type=int, default=5)

    p.add_argument("--app", type=_parse_app_list, default=list(VALID_APPS),
                   help="Comma-separated: zgst,zbase,zsci (default: all, run in that order)")
    p.add_argument("--encrypt", action="store_true", help="Add the E1 mode (encryption, no splice)")
    p.add_argument("--splice", type=_parse_int_list, default=None, help="Splice modes, e.g. 0,1")
    p.add_argument("--blocks", "-b", type=_parse_int_list, default=[32], help="GridFTP blocksizes (MB)")
    p.add_argument("--parallel", "-P", type=_parse_int_list, default=[10])
    p.add_argument("--sizes", "-s", type=_parse_size_list, default=DEFAULT_SIZES,
                   help="Sample sizes in bytes (default 512..1048576, powers of two)")
    p.add_argument("--total-size", type=int, default=10, help="GiB per run (split across streams)")
    p.add_argument("--per-stream", action="store_true", help="--total-size is per stream instead")
    p.add_argument("--run", "-r", type=int, default=10, help="Repetitions")
    p.add_argument("--retries", type=int, default=0, help="PUSH/PULL count-mismatch reruns")

    p.add_argument("--pattern", choices=["pubsub", "pushpull"], default="pubsub")
    p.add_argument("--hwm", type=int, default=1000, help="ZeroMQ SNDHWM/RCVHWM (messages)")
    p.add_argument("--src-dir", default="$HOME/fab-test", help="Dir with zmq_producer/consumer.py on EPs")
    p.add_argument("--stream-timeout", type=int, default=1800, help="Max seconds per stream test")
    p.add_argument("--start-timeout", type=int, default=300, help="Producer/consumer connect timeout")
    p.add_argument("--idle-timeout", type=int, default=120, help="Consumer max gap between messages")
    p.add_argument("--statkit-pad", type=int, default=2, help="Seconds of statkit before/after stream")
    p.add_argument("--local-rsync", default="/opt/homebrew/bin/rsync")

    p.add_argument("--output", "-o", default="/tmp/")
    return p, p.parse_args()


def build_config(args: argparse.Namespace) -> tuple[Config, ZmqOpts]:
    hosts = _default_hosts_for_lease(args.lease)
    devs = _default_interfaces_for_lease(args.lease)
    ips = _default_ips_for_lease(args.lease)

    for key in ("initiator_ap", "listener_ap", "initiator_ep", "listener_ep"):
        if getattr(args, key) is None:
            setattr(args, key, hosts[key])
        if getattr(args, f"{key}_devs") is None:
            setattr(args, f"{key}_devs", devs[key])
    ip = {k: (getattr(args, k) if getattr(args, k) is not None else v) for k, v in ips.items()}

    cfg = Config(
        verbose=args.verbose, is_test=args.is_test, lease=args.lease, test="stream", tname=args.tname,
        numactl=[args.numactl], tcp_buffer=args.tcp_buffer, ring_buffer=args.ring_buffer,
        localhost=args.userhost,
        hosts=Hosts(
            ap={"initiator": args.initiator_ap, "listener": args.listener_ap},
            ep={"initiator": args.initiator_ep, "listener": args.listener_ep},
        ),
        interfaces=_build_interface_map(args),
        listener_ip=ip["listener_ip"], listener_pub=ip["listener_pub"],
        initiator_ip=ip["initiator_ip"], initiator_pub=ip["initiator_pub"],
        listener_ap_ip=ip["listener_ap_ip"], listener_ap_pub=ip["listener_ap_pub"],
        initiator_ap_ip=ip["initiator_ap_ip"], initiator_ap_pub=ip["initiator_ap_pub"],
        tunnel_port=args.gst_port, encrypt_port=args.gst_encrypt_port, direct_port=args.direct_port,
        rsync_port=59300, mini_port=59400, mbase_port=59500, msci_port=59600,   # unused here
        scisync_port=args.scisync_port,
        inbound_ports=[args.sci_inbound_port], outbound_ports=[args.sci_outbound_port],
        tomo_file="",
        sleep=args.sleep,
        app=args.app, encrypt=bool(args.encrypt),
        splice=args.splice if args.splice is not None else [],
        parallels=args.parallel, time_frames=[], file_sizes=[],
        blocks=args.blocks, run_num=args.run,
        local_env="$HOME/Projects/gstreams",
        remote_env="$HOME/streams-cli/bin/activate",
        scistream_env="$HOME/scistream-proto/.seenvstream/bin/activate",
        report_dir=str(Path(args.output) / args.tname),
        proj_dir="$HOME/Projects/gstreams/statkit/results",
    )
    opts = ZmqOpts(
        sizes=args.sizes, total_size_gb=args.total_size, per_stream=args.per_stream,
        pattern=args.pattern, hwm=args.hwm, retries=args.retries, src_dir=args.src_dir,
        stream_timeout=args.stream_timeout, start_timeout=args.start_timeout,
        idle_timeout=args.idle_timeout, statkit_pad=args.statkit_pad,
        gst_port=args.gst_port, gst_encrypt_port=args.gst_encrypt_port,
        direct_port=args.direct_port, local_rsync=args.local_rsync,
    )
    return cfg, opts


def get_zmq_config() -> tuple[Config, ZmqOpts]:
    parser, args = parse_args()
    if not args.splice and not args.encrypt:
        parser.error("at least one of --splice or --encrypt is required")
    if args.run < 1:
        parser.error("--run must be >= 1")
    if args.retries < 0:
        parser.error("--retries must be >= 0")
    for p in args.parallel:
        per = args.total_size * 1024 ** 3 // (1 if args.per_stream else p)
        if per < max(args.sizes):
            parser.error(f"per-stream bytes ({per}) smaller than the largest sample size at P={p}")
    return build_config(args)
