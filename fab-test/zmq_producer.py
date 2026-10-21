#!/usr/bin/env python3
"""
ZeroMQ streaming producer (runs on the listener EP).

Binds, waits for the consumer, sends one HEADER control message, then
num_samples = total_bytes // sample_size data messages back-to-back
(no sampling delay), then one STOP control message carrying the sent count.

  PUB/SUB   : XPUB socket so the subscription is detected before sending
              (avoids the slow-joiner loss). Data sends are lossy like PUB;
              STOP is sent with XPUB_NODROP so it is never dropped.
  PUSH/PULL : PUSH socket, reliable, blocks on HWM.

Exit codes: 0 ok, 2 error/timeout.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

import zmq

CTRL = b"ZMQCTRL:"
EXIT_OK, EXIT_ERR = 0, 2
ZERO_COPY_MIN = 65536


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ZeroMQ streaming producer")
    p.add_argument("--bind-ip", default="*")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--pattern", choices=["pubsub", "pushpull"], default="pubsub")
    p.add_argument("--sample-size", type=int, required=True, help="Bytes per message")
    p.add_argument("--total-bytes", type=int, required=True, help="Bytes this stream sends")
    p.add_argument("--hwm", type=int, default=1000)
    p.add_argument("--start-timeout", type=int, default=300, help="Seconds to wait for the consumer")
    p.add_argument("--stop-timeout", type=int, default=120, help="Seconds to wait for STOP to be queued")
    p.add_argument("--linger-ms", type=int, default=60000)
    p.add_argument("--out", required=True)
    p.add_argument("--tag", required=True)
    return p.parse_args()


def ctrl(payload: dict) -> bytes:
    return CTRL + json.dumps(payload).encode()


def write_json(a: argparse.Namespace, info: dict) -> None:
    with open(os.path.join(a.out, f"{a.tag}.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)


def main() -> int:
    a = parse_args()
    os.makedirs(a.out, exist_ok=True)
    ss = a.sample_size
    n = a.total_bytes // ss
    info = {
        "role": "producer", "tag": a.tag, "host": socket.gethostname(),
        "pattern": a.pattern, "endpoint": f"tcp://{a.bind_ip}:{a.port}",
        "sample_size": ss, "total_bytes": a.total_bytes, "num_samples": n,
        "hwm": a.hwm, "clock": "time.perf_counter_ns",
        "wall_ref_ns": time.time_ns(), "mono_ref_ns": time.perf_counter_ns(),
        "status": "started",
    }
    if n <= 0:
        info["status"] = "error: total_bytes < sample_size"
        write_json(a, info)
        print(f"PRODUCER {a.tag}: {info['status']}", file=sys.stderr)
        return EXIT_ERR

    ctx = zmq.Context()
    sock = ctx.socket(zmq.XPUB if a.pattern == "pubsub" else zmq.PUSH)
    sock.setsockopt(zmq.SNDHWM, a.hwm)
    sock.setsockopt(zmq.LINGER, a.linger_ms)
    try:
        sock.bind(info["endpoint"])
        print(f"PRODUCER {a.tag}: bound {info['endpoint']} pattern={a.pattern} "
              f"sample_size={ss} num_samples={n}", flush=True)

        # Wait for the subscriber (PUB/SUB only); PUSH blocks on send instead.
        if a.pattern == "pubsub":
            if not sock.poll(a.start_timeout * 1000, zmq.POLLIN):
                raise TimeoutError(f"no subscriber after {a.start_timeout}s")
            sub = sock.recv()
            if not sub or sub[0] != 1:
                raise RuntimeError(f"unexpected subscription frame: {sub!r}")
        info["t_subscribed_ns"] = time.perf_counter_ns()

        sock.setsockopt(zmq.SNDTIMEO, a.start_timeout * 1000)
        sock.send(ctrl({"type": "HEADER", "tag": a.tag, "pattern": a.pattern,
                        "sample_size": ss, "num_samples": n}))
        sock.setsockopt(zmq.SNDTIMEO, -1)

        payload = bytes(ss)
        copy = ss < ZERO_COPY_MIN
        send = sock.send
        clock = time.perf_counter_ns
        t0 = clock()
        for _ in range(n):
            send(payload, copy=copy)
        t1 = clock()

        if a.pattern == "pubsub":
            sock.setsockopt(zmq.XPUB_NODROP, 1)
        sock.setsockopt(zmq.SNDTIMEO, a.stop_timeout * 1000)
        sock.send(ctrl({"type": "STOP", "tag": a.tag, "sent": n}))

        info.update({"t_first_send_ns": t0, "t_last_send_ns": t1, "sent": n, "status": "ok"})
        print(f"PRODUCER {a.tag}: sent {n} x {ss} B in {(t1 - t0) / 1e9:.3f}s", flush=True)
        rc = EXIT_OK
    except zmq.Again:
        info["status"] = "error: send timed out (consumer not connected or stalled)"
        rc = EXIT_ERR
    except Exception as e:  # noqa: BLE001
        info["status"] = f"error: {type(e).__name__}: {e}"
        rc = EXIT_ERR
    finally:
        write_json(a, info)
        sock.close()          # honours LINGER so queued data is flushed
        ctx.term()
    if rc != EXIT_OK:
        print(f"PRODUCER {a.tag}: {info['status']}", file=sys.stderr, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
