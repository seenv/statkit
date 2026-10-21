#!/usr/bin/env python3
"""
ZeroMQ streaming consumer (runs on the initiator EP).

Connects, reads the HEADER, then timestamps every data message on arrival
(time.perf_counter_ns, i.e. CLOCK_MONOTONIC) until STOP arrives.

Outputs in --out:
  <tag>.npy   int64 arrival timestamps (ns), one per received data message
  <tag>.json  sample size, sent/received/dropped counts, t_first, t_stop, ...

Goodput  = received * sample_size / (t_stop - t_first)
Delay    = mean(diff(ts)),  Jitter = std(diff(ts))   (computed later, offline)

Exit codes: 0 ok, 2 error/timeout, 3 count mismatch (PUSH/PULL only;
PUB/SUB drops are recorded in the JSON and exit 0).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

import numpy as np
import zmq

CTRL = b"ZMQCTRL:"
EXIT_OK, EXIT_ERR, EXIT_MISMATCH = 0, 2, 3
ZERO_COPY_MIN = 65536


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ZeroMQ streaming consumer")
    p.add_argument("--connect-ip", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--pattern", choices=["pubsub", "pushpull"], default="pubsub")
    p.add_argument("--sample-size", type=int, required=True, help="Expected bytes per message")
    p.add_argument("--hwm", type=int, default=1000)
    p.add_argument("--start-timeout", type=int, default=300, help="Seconds to wait for HEADER")
    p.add_argument("--idle-timeout", type=int, default=120, help="Max seconds between messages")
    p.add_argument("--out", required=True)
    p.add_argument("--tag", required=True)
    return p.parse_args()


def parse_ctrl(body: bytes) -> dict | None:
    if not body.startswith(CTRL):
        return None
    return json.loads(body[len(CTRL):])


def main() -> int:
    a = parse_args()
    os.makedirs(a.out, exist_ok=True)
    endpoint = f"tcp://{a.connect_ip}:{a.port}"
    info: dict = {
        "role": "consumer", "tag": a.tag, "host": socket.gethostname(),
        "pattern": a.pattern, "endpoint": endpoint, "hwm": a.hwm,
        "clock": "time.perf_counter_ns",
        "wall_ref_ns": time.time_ns(), "mono_ref_ns": time.perf_counter_ns(),
        "status": "started",
    }
    ts = np.empty(0, dtype=np.int64)
    received = 0
    rc = EXIT_ERR

    ctx = zmq.Context()
    if a.pattern == "pubsub":
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"")
    else:
        sock = ctx.socket(zmq.PULL)
    sock.setsockopt(zmq.RCVHWM, a.hwm)
    sock.setsockopt(zmq.LINGER, 0)

    try:
        sock.setsockopt(zmq.RCVTIMEO, a.start_timeout * 1000)
        sock.connect(endpoint)
        print(f"CONSUMER {a.tag}: connected {endpoint} pattern={a.pattern}", flush=True)

        try:
            header = parse_ctrl(sock.recv())
        except zmq.Again:
            raise TimeoutError(f"no HEADER after {a.start_timeout}s") from None
        if not header or header.get("type") != "HEADER":
            raise RuntimeError(f"first message is not a HEADER: {header!r}")
        ss, n = int(header["sample_size"]), int(header["num_samples"])
        if ss != a.sample_size:
            raise RuntimeError(f"sample size mismatch: header={ss} expected={a.sample_size}")
        info.update({"sample_size": ss, "num_samples": n})

        ts = np.empty(n, dtype=np.int64)
        sock.setsockopt(zmq.RCVTIMEO, a.idle_timeout * 1000)
        copy = ss < ZERO_COPY_MIN
        recv = sock.recv
        clock = time.perf_counter_ns
        stop = None
        t_stop = 0
        extra = 0
        try:
            while True:
                m = recv(copy=copy)
                t = clock()
                if len(m) == ss:
                    if received < n:
                        ts[received] = t
                        received += 1
                    else:
                        extra += 1
                    continue
                msg = parse_ctrl(m if copy else m.bytes)
                if msg and msg.get("type") == "STOP":
                    stop, t_stop = msg, t
                    break
                raise RuntimeError(f"unexpected message of {len(m)} bytes")
        except zmq.Again:
            raise TimeoutError(
                f"no message for {a.idle_timeout}s after {received} samples") from None

        sent = int(stop["sent"])
        info.update({
            "sent": sent, "received": received, "dropped": sent - received,
            "extra": extra, "bytes_received": received * ss,
            "t_first_ns": int(ts[0]) if received else None,
            "t_stop_ns": t_stop,
        })
        mismatch = received != sent or extra != 0
        if mismatch and a.pattern == "pushpull":
            info["status"] = "mismatch"
            rc = EXIT_MISMATCH
        elif extra != 0:
            info["status"] = "error: received more samples than announced"
            rc = EXIT_ERR
        else:
            info["status"] = "ok"
            rc = EXIT_OK
        print(f"CONSUMER {a.tag}: received {received}/{sent} x {ss} B "
              f"(dropped {sent - received}) status={info['status']}", flush=True)
    except Exception as e:  # noqa: BLE001
        info["status"] = f"error: {type(e).__name__}: {e}"
        info["received"] = received
        rc = EXIT_ERR
    finally:
        np.save(os.path.join(a.out, f"{a.tag}.npy"), ts[:received])
        with open(os.path.join(a.out, f"{a.tag}.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        sock.close()
        ctx.term()
    if rc == EXIT_ERR:
        print(f"CONSUMER {a.tag}: {info['status']}", file=sys.stderr, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
