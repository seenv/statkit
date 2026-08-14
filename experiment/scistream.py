import sys
import logging
import shlex
import re
import time
import uuid
from typing import Sequence, Optional

from remote import run_subprocess, popen_subprocess
from utils import parse_size_to_bytes, get_numa_node, take_cpus
from config import Config

# -------------------------------------------------------------------------------
# Helpers
def _check_keys_exist(
    cfg,
    hosts: list[str],
    check: bool = True,
) -> bool:
    for host in hosts:
        cp = run_subprocess(
            host, None,
            "mkdir -p /tmp.scistream; "
            "test -f /tmp/.scistream/server.crt && echo yes || echo no ",
            localhost=cfg.localhost,
        )
        # if check and cp.returncode != 0:
        #     return False
        out = cp.stdout.decode() if isinstance(cp.stdout, bytes) else cp.stdout
        if out.strip() != "yes":
            return False
    return True

_KEY_RE = re.compile(r"(-----BEGIN PRIVATE KEY-----.+?-----END PRIVATE KEY-----)", re.DOTALL)
_CRT_RE = re.compile(r"(-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----)", re.DOTALL)
    # openssl req -x509 -nodes -days 365 \
    # -newkey rsa:2048 \
    # -keyout /tmp/.scistream/server.key \
    # -out /tmp/.scistream/server.crt \
    # -subj "/CN=128.135.37.241" \
    # -addext "subjectAltName=IP:128.135.37.241,IP:128.135.37.240,IP:128.135.164.120,IP:128.135.164.119,IP:128.135.24.119,IP:128.135.24.117,IP:128.135.11.192" \
    # 2>/dev/null
def _key_gen(
    cfg: Config, 
    host: str,
    check: bool = True,
) -> tuple[str | None, str | None]:
        # f"openssl req -x509 -nodes -days 365 -newkey rsa:2048 "
        # f"-keyout \"$HAPROXY_CONFIG_PATH\"/server.key -out \"$HAPROXY_CONFIG_PATH\"/server.crt "
        # f"-subj '/CN={cfg.p2cs_listener} -addext subjectAltName="
        # f"IP:{cfg.p2cs_ip}, "
        # f"IP:{cfg.prod_ip}, "
        # f"IP:{cfg.c2cs_listener}, "
        # f"IP:{cfg.c2cs_ip}, "
        # f"IP:{cfg.cons_ip}, "
        # f"IP:{cfg.inbound_ip}, "
        # f"IP:{cfg.outbound_ip}, "
        # f"IP:128.135.11.192 "
        # f"2>/dev/null; "
        
        # '-subj "/CN=128.135.37.241" '
        #         '-addext "subjectAltName=IP:128.135.37.241,IP:128.135.37.240,'
        #         'IP:128.135.164.120,IP:128.135.164.119,IP:128.135.24.119,'
        #         'IP:128.135.24.117,IP:128.135.11.192"; '
        
        #
    cp = run_subprocess(
        host, None,
        f"mkdir -p /tmp/.scistream; "
        #f'openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout $HAPROXY_CONFIG_PATH/server.key -out \"$HAPROXY_CONFIG_PATH\"/server.crt -subj "/CN={cfg.listener_ap_pub}" -addext "subjectAltName=IP:{cfg.listener_ip}, IP:{cfg.listener_pub}, IP:{cfg.initiator_ip}, IP:{cfg.initiator_pub}, IP:{cfg.listener_ap_ip}, IP:{cfg.listener_ap_pub}, IP:{cfg.initiator_ap_ip}, IP:{cfg.initiator_ap_pub}" 2>/dev/null; '
        'openssl req -x509 -nodes -days 365 '
        '-newkey rsa:2048 '
        '-keyout /tmp/.scistream/server.key '
        '-out /tmp/.scistream/server.crt '
        f'-subj "/CN={cfg.initiator_ap_ip}" '
        '-addext "subjectAltName='
        f'IP:{cfg.listener_ip},'
        f'IP:{cfg.listener_pub},'
        f'IP:{cfg.initiator_ip},'
        f'IP:{cfg.initiator_pub},'
        f'IP:{cfg.listener_ap_ip},'
        f'IP:"{cfg.listener_ap_pub}",'
        f'IP:{cfg.initiator_ap_ip},'
        f'IP:{cfg.initiator_ap_pub},'
        f'IP:128.135.11.192'
        '"; '
        'cat /tmp/.scistream/server.crt /tmp/.scistream/server.key > /tmp/.scistream/stream.pem; '
        #ls.f"chmod 600 \"$HAPROXY_CONFIG_PATH\"/stream.pem; "
        f"cat /tmp/.scistream/stream.pem; ",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"KEYGEN: Failed generating the keys on {host.upper()}"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    #results = cp.result()   # (timeout=30)    
    pem = cp.stdout
    key_match = _KEY_RE.search(pem)
    crt_match = _CRT_RE.search(pem)
    key = key_match.group(1) if key_match else None
    crt = crt_match.group(1) if crt_match else None
    logging.debug(f"KEYGEN: The Keys are generate on  {host.upper()}")
    return key, crt

def _key_dist(
    cfg: Config, 
    hosts: list[str],
    key: str, crt: str,
    check: bool = True,
) -> None:
    for host in hosts:
        cp = run_subprocess(
            host, None,
            #f"[[ -z \"$HAPROXY_CONFIG_PATH\" ]] && "
            f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
            f"sleep 1; "

            f"echo {shlex.quote(crt)} > \"$HAPROXY_CONFIG_PATH\"/server.crt; "   # printf '%s' 
            f"echo {shlex.quote(key)} > \"$HAPROXY_CONFIG_PATH\"/server.key; "
            f"cat \"$HAPROXY_CONFIG_PATH\"/server.crt \"$HAPROXY_CONFIG_PATH\"/server.key > \"$HAPROXY_CONFIG_PATH\"/stream.pem; "
            f"chmod 600 \"$HAPROXY_CONFIG_PATH\"/stream.pem ",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"KEYDIST: Failed generating the keys on {host.upper()}"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
    logging.debug(f"KEYDIST: The Keys are distributed on all nodes")

# -------------------------------------------------------------------------------
# S2CS
def start_s2cs(
    cfg: Config, 
    host: str, 
    listener_ip: str, sync_port: int,
    timeout: int, 
    scistream_dir: str = "/tmp/.scistream",
    retries: int = 100,
    check: bool = True,
) -> None:
    # have to run s2cs with timeout since it creates a zombie, and without timeout
    # the zombie process will remain and won't let the transfer to happen!!!
    # figure out why and how to fix it!?
    cp = run_subprocess(
        host, cfg.scistream_env,
        #f"[[ -z \"$HAPROXY_CONFIG_PATH\" ]] && "
        f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
        
        # f"if [[ -z $(ps -ef | grep [ ]$(cat /tmp/.scistream/s2cs.pid)) ]]; then "
        #f"if ! kill -0 $(cat '\"$HAPROXY_CONFIG_PATH\"/s2cs.pid' 2>/dev/null) 2>/dev/null; then "
        #f'if ! kill -0 \"$(cat \"$HAPROXY_CONFIG_PATH\"/s2cs.pid 2>/dev/null)\" 2>/dev/null; then '
        #f"sleep 5; "
        
        #f"timeout 60s "
        f"setsid stdbuf -oL -eL "
        f"timeout 15 s2cs --verbose --port=5007 --listener_ip={shlex.quote(listener_ip)} "     # f"s2cs --verbose --port=5007 --listener_ip=128.135.37.241 "
        f"--server_crt=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        f"--server_key=\"$HAPROXY_CONFIG_PATH\"/server.key "
        #f"--type=StunnelSubprocess > \"$HAPROXY_CONFIG_PATH\"/p2cs.log 2>&1 & "
        f"--type=HaproxySubprocess > \"$HAPROXY_CONFIG_PATH\"/s2cs.log 2>&1 & "
        #f'sleep 5; '
        f"echo $! > \"$HAPROXY_CONFIG_PATH\"/s2cs.pid; "
        # f"else "
        # f"timeout 60s "
        # f"s2cs --verbose --port=5007 --listener_ip=128.135.24.119 "
        # f"--server_crt=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        # f"--server_key=\"$HAPROXY_CONFIG_PATH\"/server.key "
        # f"--type=StunnelSubprocess > \"$HAPROXY_CONFIG_PATH\"/p2cs.log 2>&1 & "
        # f"echo $! > \"$HAPROXY_CONFIG_PATH\"/s2cs.pid; ""
        #f"fi; "
        
        #f"sleep 1 && cat \"$HAPROXY_CONFIG_PATH\"/s2cs.log ",
        f"",
        localhost=cfg.localhost,
    )
    # if check and cp.returncode != 0:
    #     raise RuntimeError(
    #         f"S2CS: Failed creating the stream tunnel on {host.upper()}\n"
    #         f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
    #     )
    logging.debug(f"S2CS: Started stream tunnel on the {host.upper()}")
    #return id

# -------------------------------------------------------------------------------
# Inbound connection on Listener EP
_UUID_CANDIDATE = re.compile(r"[0-9a-fA-F-]{32,36}")
def _parse_uid(output: str) -> str:
    for m in _UUID_CANDIDATE.finditer(output):
        try:
            return str(uuid.UUID(m.group(0)))
        except ValueError:
            pass
    raise RuntimeError(f"Could not find UUID in output:\n{output}")

def inbound(
    cfg: Config, host: str, 
    receiver_ports: Sequence[int], #receiver_ports: list[int]
    remote_ip: str, s2cs_ip: str,
    sync_port: int, 
    parallel: int, timeout: int, 
    scistream_dir: str = "/tmp/.scistream",
    retries: int = 100,
    check: bool = True,
) -> tuple[Optional[str], Sequence[str]]:
    cp = run_subprocess(
        host, cfg.scistream_env,
        #f"[[ -z \"$HAPROXY_CONFIG_PATH\" ]] && "
        f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
        f"cd \"$HAPROXY_CONFIG_PATH\"; "
        f"sleep 1; "

        #f"timeout 1000 "
        #f"s2uc inbound-request --remote_ip 128.135.24.117 --num_conn 5 "
        f's2uc inbound-request --remote_ip {cfg.listener_ip} --num_conn 1 '
        #f"--receiver_ports=5074,5075,5076,5077,5078  --s2cs 128.135.24.119:5007 --rate 100000 "
        f'--receiver_ports=5074  --s2cs "{cfg.listener_ap_ip}":5007 --rate 100000 '
        f"--server_cert=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        f"> \"$HAPROXY_CONFIG_PATH\"/conin.log 2>&1 & echo $! >> \"$HAPROXY_CONFIG_PATH\"/inbound.pid; "
        f"while ! grep -q 'Listeners:' \"$HAPROXY_CONFIG_PATH\"/conin.log; do "
        f"    sleep 1; "
        f"done; "
        f"sleep 1; "
        f"cat \"$HAPROXY_CONFIG_PATH\"/conin.log ",
        localhost=cfg.localhost,
    )
    
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"LOCAL: Failed creating the streams tunnel on {cfg.localhost.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    # this-guy | .scistream $ cat conin.log 
    # uid; s2cs; access_token; role
    # 47b56221-9295-11f1-958b-f6df7d7542c9 128.135.164.119:5007 INVALID_TOKEN PROD
    # sending client request message
    # started client request
    # waiting for hello message
    # Another GRPC error occurred: failed to connect to all addresses; last error: UNKNOWN: ipv4:128.135.164.119:5007: Failed to connect to remote host: Connection refused
    # sending for hello message
    # Error sending hello message: failed to connect to all addresses; last error: UNKNOWN: ipv4:128.135.164.119:5007: Failed to connect to remote host: Connection refused
    # Request Failed, no Hello response
    results = cp.stdout   # timeout=60) 
    # id = _parse_uid(results)
    #_UUID = re.search(r'^([a-f0-9-]{36})\s+.*INVALID_TOKEN PROD', results, re.MULTILINE)
    _UUID = re.search(r'^([a-fA-F0-9-]{36})\s+.*INVALID_TOKEN PROD', results, re.MULTILINE)
    _LISTENERS = re.findall(r'(?im)^listeners:\s*"([^"]+)"', results)
    _PROD_LISTENERS = re.findall(r'(?im)^prod_listeners:\s*"([^"]+)"', results)
        
    stream_uid = _UUID.group(1) if _UUID else None
    listen_ports = [port.split(":")[-1] for port in _LISTENERS]
    prod_ports = [port.split(":")[-1] for port in _PROD_LISTENERS]
        
    logging.debug(f"INBOUND: Started inbound connection on {host.upper()}: \n Stream ID: {id} \n Listener Ports: {listen_ports} \n Producer Ports: {prod_ports}")
    return stream_uid, listen_ports


# -------------------------------------------------------------------------------
# Outbound connection on Initiator EP
def outbound(
    cfg: Config, host: str, 
    stream_uid: str, #listen_ports: Sequence[int],
    remote_ip: str, 
    receiver_ap_ip: str, 
    receiver_ports: list[int], 
    s2cs_ip: str,
    sync_port: int, 
    parallel: int, timeout: int, 
    scistream_dir: str = "/tmp/.scistream",
    retries: int = 100,
    check: bool = True,
) -> None:
    if not stream_uid or not (len(receiver_ports) == parallel):
        raise RuntimeError(f"OUTBOUND: Expected all lists to have length parallel={parallel}: {len(receiver_ports)},and a Stream UID: {stream_uid}")
    #receiver_port = receiver_ports[0]
    cp = run_subprocess(
        host, cfg.scistream_env,
        #f"[[ -z \"$HAPROXY_CONFIG_PATH\" ]] && "
        f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
        f"sleep 1; "
        
        #f"timeout 60s "
        #f"s2uc outbound-request --remote_ip 128.135.164.119 --num_conn 5  --rate 100000 "
        f's2uc outbound-request --remote_ip {cfg.listener_ap_pub} --num_conn 1  --rate 100000 '
        #f"--receiver_ports=5100,5101,5102,5103,5104 --s2cs 128.135.37.241:5007 "
        f'--receiver_ports=5100 --s2cs {cfg.initiator_ap_ip}:5007 '
        f"4f8583bc-a4d3-11ee-9fd6-034d1fcbd7c3 "
        #f"128.135.164.119:5100,128.135.164.119:5101,128.135.164.119:5102,128.135.164.119:5103,128.135.164.119:5104 "
        f'{cfg.listener_ap_pub}:5100 '
        f"--server_cert=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        f"> \"$HAPROXY_CONFIG_PATH\"/conout.log & echo $! >> \"$HAPROXY_CONFIG_PATH\"/outbound.pid; "
        f"while ! grep -q 'Hello message sent successfully' \"$HAPROXY_CONFIG_PATH\"/conout.log; do "
        f"    sleep 1; "
        f"done; "
        f"sleep 1; "
        f"cat \"$HAPROXY_CONFIG_PATH\"/conout.log ",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"LOCAL: Failed creating the streams tunnel on {cfg.localhost.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    logging.debug(f"OUTBOUND: Started outbound connection on {host.upper()}")

# -------------------------------------------------------------------------------
# Control Service
def start_scistream(
    cfg: Config, 
    parallel: int, timeout: int,
) -> tuple[Optional[str], Sequence[str]]:
    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    keys_exist = _check_keys_exist(cfg, hosts)
    if not keys_exist:
        key, crt = _key_gen(cfg, cfg.hosts.ap.get("listener"))
        _key_dist(cfg, hosts, key, crt)

    start_s2cs(cfg, host=cfg.hosts.ap.get("listener"), listener_ip=cfg.listener_ap_ip, sync_port=cfg.scisync_port, timeout=timeout)
    start_s2cs(cfg, host=cfg.hosts.ap.get("initiator"), listener_ip=cfg.initiator_ap_ip, sync_port=cfg.scisync_port, timeout=timeout)
    time.sleep(cfg.sleep)
    
    stream_uid, listen_ports = inbound(
        cfg, host=cfg.hosts.ep.get("listener"), 
        receiver_ports=cfg.inbound_ports, 
        remote_ip=cfg.listener_ip,
        s2cs_ip=cfg.listener_ap_pub,
        sync_port=cfg.scisync_port,
        parallel=parallel, timeout=timeout,
        )
    
    outbound(
        cfg, host=cfg.hosts.ep.get("initiator"), 
        stream_uid=stream_uid, # listen_ports,
        remote_ip=cfg.initiator_ap_pub, 
        receiver_ap_ip=cfg.listener_ap_pub, 
        receiver_ports=cfg.outbound_ports, 
        s2cs_ip=cfg.initiator_ap_pub,
        sync_port=cfg.scisync_port,
        parallel=parallel, timeout=timeout,
    )

    logging.info(f"SCI: Started Scistream tunnel")
    return stream_uid, listen_ports


def stop_scistream(
    cfg,
    check: bool = True,
) -> None:

    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            # f"[[ -z \"$HAPROXY_CONFIG_PATH\" ]] && "
            # f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
            # f"sleep 1; "
            # f"sudo pkill haproxy && sleep 1 && echo \"$(ps -ef | grep haproxy)\" >> \"$HAPROXY_CONFIG_PATH\"/kill.log; "
            # f"sudo pkill stunnel && sleep 1 && echo \"$(ps -ef | grep '[s]tunnel')\" >> \"$HAPROXY_CONFIG_PATH\"/kill.log; "
            # f"sudo pkill nginx && sleep 1 && echo \"$(ps -ef | grep '[n]ginx')\" >> \"$HAPROXY_CONFIG_PATH\"/kill.log; ",
            # f"sudo pkill haproxy && sleep 1; "
            # f"sudo pkill stunnel && sleep 1; "
            # f"sudo pkill nginx && sleep 1; ",
            #f'pkill -TERM -f "[s]2cs" || true; '
            #f'sudo pkill -TERM -f "[s]tunnel" || true; '
            #f'sudo pkill -TERM -f "[h]aproxy" || true; ',
            f'pkill -TERM -x haproxy || true;',
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"LOCAL: Failed stopping the streams tunnel on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug(f"SCI: Stopped SciStream S2CS tunnels on {host.upper()}") 

    for host in cfg.hosts.ep.values():
        cp = run_subprocess(
            host, None,
            #f"[[ -z \"$HAPROXY_CONFIG_PATH\" ]] && "
            # f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
            # f"[[ -f \"$HAPROXY_CONFIG_PATH\"/inbound.pid ]] && pid=\"$(cat \"$HAPROXY_CONFIG_PATH\"/inbound.pid)\" && [[ $pid =~ ^[0-9]+$ ]] && kill -9 \"$pid\" > \"$HAPROXY_CONFIG_PATH\"/kill.log 2>&1; "
            # f"[[ -f \"$HAPROXY_CONFIG_PATH\"/outbound.pid ]] && pid=\"$(cat \"$HAPROXY_CONFIG_PATH\"/outbound.pid)\" && [[ $pid =~ ^[0-9]+$ ]] && kill -9 \"$pid\" >> \"$HAPROXY_CONFIG_PATH\"/kill.log 2>&1; ",
            # f"find \"$HAPROXY_CONFIG_PATH\" ! -name kill.log -delete ",
            f"pkill -TERM -f '[s]2uc' || true ",
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"LOCAL: Failed stopping the streams tunnel on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        logging.debug(f"SCI: Stopped SciStream S2US on {host.upper()}") 
    logging.info(f"SCI: Stopped SciStream on nodes")
        