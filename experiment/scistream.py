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
def _replace_haproxy_cfg(cfg, config_file, check: bool = True):
    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            f'cp $HOME/seenv-scistream-proto/src/s2ds/{config_file} '
            f'$HOME/seenv-scistream-proto/src/s2ds/haproxy.cfg.j2 && '
            f'sleep 1 && '
            f'cat $HOME/seenv-scistream-proto/src/s2ds/haproxy.cfg.j2 ',
            localhost=cfg.localhost,
        )
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"ISCI: Failed changing proxy config file on {host.upper()}\n "
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr} "
            )
        logging.debug('ISCI: Changed the proxy config file on %s: %s', host.upper(), '\n'.join((cp.stdout or '').splitlines()[-10:]))

def _check_keys_exist(cfg, hosts: list[str], check: bool = True) -> bool:
    for host in hosts:
        cp = run_subprocess(
            host, None,
            "mkdir -p /tmp.scistream; "
            "test -f /tmp/.scistream/server.crt && echo yes || echo no ",
            localhost=cfg.localhost,
        )

        out = cp.stdout.decode() if isinstance(cp.stdout, bytes) else cp.stdout
        if out.strip() != "yes":
            return False
    return True

_KEY_RE = re.compile(r"(-----BEGIN PRIVATE KEY-----.+?-----END PRIVATE KEY-----)", re.DOTALL)
_CRT_RE = re.compile(r"(-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----)", re.DOTALL)
def _key_gen(cfg: Config, host: str,check: bool = True,) -> tuple[str | None, str | None]:
    cp = run_subprocess(
        host, None,
        f"mkdir -p /tmp/.scistream; "
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

def _key_dist(cfg: Config, hosts: list[str], key: str, crt: str,check: bool = True) -> None:
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

def _check_proxy_config(cfg, stream_uid):
    for host in cfg.hosts.ap.values():
        cp = run_subprocess(
            host, None,
            f'cat /tmp/.scistream/{stream_uid}.conf ',
            localhost=cfg.localhost,
        )
        logging.debug("SCI: Proxy config file on %s:\n %s", host.upper(), cp.stdout)

# -------------------------------------------------------------------------------
# S2CS
def start_s2cs(
    cfg: Config, host: str, listener_ip: str, sync_port: int, port_range: str, 
    timeout: int, scistream_dir: str = "/tmp/.scistream", retries: int = 100, check: bool = True
) -> None:
    # have to run s2cs with timeout since it creates a zombie, and without timeout
    # the zombie process will remain and won't let the transfer to happen!!!
    # figure out why and how to fix it!?
    cp = run_subprocess(
        host, cfg.scistream_env,
        f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
        #f"timeout 60s "
        f"setsid stdbuf -oL -eL "
        f"timeout 15 s2cs --verbose --port={cfg.scisync_port} "#q--listener_ip={shlex.quote(cfg.listener_ip)} "     # f"s2cs --verbose --port={cfg.scisync_port} --listener_ip=128.135.37.241 "
        f"--port_range {port_range} "
        f"--server_crt=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        f"--server_key=\"$HAPROXY_CONFIG_PATH\"/server.key "
        f"--type=HaproxySubprocess > \"$HAPROXY_CONFIG_PATH\"/s2cs.log 2>&1 & "
        #f'sleep 5; '
        f"echo $! > \"$HAPROXY_CONFIG_PATH\"/s2cs.pid; "
        #f"sleep 1 && cat \"$HAPROXY_CONFIG_PATH\"/s2cs.log ",
        f"",
        localhost=cfg.localhost,
    )
    logging.debug(f"S2CS: Started stream tunnel on the {host.upper()}")
    #return id

# -------------------------------------------------------------------------------
# Inbound connection on Listener EP
def inbound(
    cfg: Config, host: str, receiver_ports: Sequence[int], remote_ip: str, s2cs_ip: str, sync_port: int, 
    parallel: int, timeout: int, scistream_dir: str = "/tmp/.scistream", retries: int = 100, check: bool = True,
) -> tuple[Optional[str], Sequence[str]]:
    ports_list = ",".join(str(p) for p in receiver_ports)
    listener_eps = ",".join(f"{cfg.listener_ap_ip}:{p}" for p in receiver_ports)
    cp = run_subprocess(
        host, cfg.scistream_env,
        f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
        f"cd \"$HAPROXY_CONFIG_PATH\"; "
        f"sleep 1; "

        f's2uc inbound-request --remote_ip {cfg.listener_ip} --num_conn 1 '    #f"s2uc inbound-request --remote_ip 128.135.24.117 --num_conn 5 "
        f'--receiver_ports={ports_list}  --s2cs {cfg.listener_ap_pub}:{cfg.scisync_port} --rate 1000000000 '    #f"--receiver_ports=5074,5075,5076,5077,5078  --s2cs 128.135.24.119:5007 --rate 100000 "
        f"--server_cert=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        f"> \"$HAPROXY_CONFIG_PATH\"/conin.log 2>&1 & echo $! >> \"$HAPROXY_CONFIG_PATH\"/inbound.pid; "
        # f'while ! grep -q "Listeners:" "$HAPROXY_CONFIG_PATH"/conin.log; do sleep 1; done; '
        f"sleep 1; "
        f"cat \"$HAPROXY_CONFIG_PATH\"/conin.log ",
        localhost=cfg.localhost,
    )
    
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"LOCAL: Failed creating the streams tunnel on {cfg.localhost.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    results = cp.stdout   # timeout=60) 
    _UUID = re.search(r'^([a-fA-F0-9-]{36})\s+.*INVALID_TOKEN PROD', results, re.MULTILINE)
    stream_uid = _UUID.group(1) if _UUID else None
    m = re.search(r'^Listeners:\s*\[(.*)\]$', results, re.MULTILINE)
    if m:
        listeners = re.findall(r"'(\d+\.\d+\.\d+\.\d+):(\d+)'", m.group(1))
        listen_ips = [ip for ip, _ in listeners]
        listen_ports = [int(port) for _, port in listeners]
    logging.debug(f"INBOUND: Started inbound connection on {host.upper()}: \n Stream ID: {stream_uid} \n Listener IPs: {listen_ips} \n Ports: {listen_ports}")
    return stream_uid, listen_ips, listen_ports

# -------------------------------------------------------------------------------
# Outbound connection on Initiator EP
def outbound(
    cfg: Config, host: str, stream_uid: str, remote_ip: str, receiver_ap_ip: str, 
    receiver_ports: Sequence[int], s2cs_ip: str, sync_port: int, parallel: int, 
    timeout: int, scistream_dir: str = "/tmp/.scistream", retries: int = 100, check: bool = True,
) -> None:
    #if not stream_uid or not (len(receiver_ports) == parallel):
    #    raise RuntimeError(f"OUTBOUND: Expected all lists to have length parallel={parallel}: {len(receiver_ports)},and a Stream UID: {stream_uid}")
    #ports = [receiver_port + (i) for i in range(parallel)]
    ports_list = ",".join(str(p) for p in receiver_ports)
    listener_eps = ",".join(f"{cfg.listener_ap_pub}:{p}" for p in receiver_ports)

    cp = run_subprocess(
        host, cfg.scistream_env,
        f"HAPROXY_CONFIG_PATH=/tmp/.scistream && mkdir -p \"$HAPROXY_CONFIG_PATH\"; "
        f"sleep 1; "
        
        f's2uc outbound-request --remote_ip {cfg.listener_ap_pub} --num_conn 1 --rate 1000000000 '    #f"s2uc outbound-request --remote_ip 128.135.164.119 --num_conn 5  --rate 100000 "
        f'--receiver_ports={ports_list} --s2cs {cfg.initiator_ap_ip}:{cfg.scisync_port} '   #f"--receiver_ports=5100,5101,5102,5103,5104 --s2cs 128.135.37.241:5007 "
        f'{stream_uid} {listener_eps} '      #f"128.135.164.119:5100,128.135.164.119:5101,128.135.164.119:5102,128.135.164.119:5103,128.135.164.119:5104 "
        f"--server_cert=\"$HAPROXY_CONFIG_PATH\"/server.crt "
        f"> \"$HAPROXY_CONFIG_PATH\"/conout.log 2>&1 & echo $! >> \"$HAPROXY_CONFIG_PATH\"/outbound.pid; "
        # f'while ! grep -q "Listeners:" "$HAPROXY_CONFIG_PATH"/conout.log; do sleep 1; done; '
        f"sleep 1; "
        f"cat \"$HAPROXY_CONFIG_PATH\"/conout.log ",
        localhost=cfg.localhost,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"LOCAL: Failed creating the streams tunnel on {cfg.localhost.upper()}\n"
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    results = cp.stdout   # timeout=60) 
    _UUID = re.search(r'^([a-fA-F0-9-]{36})\s+.*INVALID_TOKEN PROD', results, re.MULTILINE)
    stream_uid = _UUID.group(1) if _UUID else None
    m = re.search(r'^Listeners:\s*\[(.*)\]$', results, re.MULTILINE)
    if m:
        listeners = re.findall(r"'(\d+\.\d+\.\d+\.\d+):(\d+)'", m.group(1))
        listen_ips = [ip for ip, _ in listeners]
        listen_ports = [int(port) for _, port in listeners]
    logging.debug(f"OUTBOUND: Started inbound connection on {host.upper()}: \n Stream ID: {stream_uid} \n Listener IPs: {listen_ips} \n Ports: {listen_ports}")
    return listen_ips, listen_ports

# -------------------------------------------------------------------------------
# Control Service
def start_scistream(
    cfg: Config, encrypt: int, parallel: int, timeout: int,
) -> tuple[list[str], list[int], list[int], list[int], list[int]]:
    if encrypt:
        _replace_haproxy_cfg(cfg, 'haproxy.cfg.j2_encr')
    else:
        _replace_haproxy_cfg(cfg, 'haproxy.cfg.j2_no_encr')

    hosts = list(cfg.hosts.ap.values()) + list(cfg.hosts.ep.values())
    keys_exist = _check_keys_exist(cfg, hosts)
    if not keys_exist:
        key, crt = _key_gen(cfg, cfg.hosts.ap.get("listener"))
        _key_dist(cfg, hosts, key, crt)
    inbound_start_range = cfg.inbound_ports[0]
    outbound_start_range = cfg.outbound_ports[0]
    stream_ids, listen_ap_ports, initiate_ap_ports, listen_ep_ports, initiate_ep_ports = [], [], [], [], []
    for i in range(parallel):
        port_range = f'{outbound_start_range}-{outbound_start_range + parallel}' #(parallel) }'
        start_s2cs(cfg, host=cfg.hosts.ap.get("listener"), listener_ip=cfg.listener_ip, sync_port=cfg.scisync_port, port_range=port_range, timeout=timeout)
        start_s2cs(cfg, host=cfg.hosts.ap.get("initiator"), listener_ip=cfg.initiator_ap_ip, sync_port=cfg.scisync_port, port_range=port_range, timeout=timeout)
        time.sleep(cfg.sleep)
        # listen_ep_ports = [cfg.inbound_ports[0] + (i) for i in range(parallel)]
        #listen_ep_ports = [cfg.inbound_ports[0] + i]
        listen_ep_port = [inbound_start_range]
        inbound_start_range += parallel + 1
        stream_uid, listen_ap_ip, listen_ap_port = inbound(
            cfg, host=cfg.hosts.ep.get("listener"), 
            #receiver_port=cfg.inbound_ports[0], 
            receiver_ports=listen_ep_port,
            remote_ip=cfg.listener_ip,
            s2cs_ip=cfg.listener_ap_pub,
            sync_port=cfg.scisync_port,
            parallel=parallel, timeout=timeout,
            )

        initiate_ep_port =[outbound_start_range]
        outbound_start_range += parallel + 1
        initiate_ap_ip, initiate_ap_port = outbound(
            cfg, host=cfg.hosts.ep.get("initiator"), 
            stream_uid=stream_uid, # listen_ports,
            remote_ip=cfg.initiator_ap_pub, 
            receiver_ap_ip=cfg.listener_ap_pub, 
            #receiver_port=cfg.outbound_ports[0], 
            receiver_ports=initiate_ep_port, 
            s2cs_ip=cfg.initiator_ap_pub,
            sync_port=cfg.scisync_port,
            parallel=parallel, timeout=timeout,
        )
        _check_proxy_config(cfg, stream_uid)
        stream_ids.append(stream_uid)
        listen_ap_ports.extend(listen_ap_port)
        initiate_ap_ports.extend(initiate_ap_port)
        listen_ep_ports.extend(listen_ep_port)
        initiate_ep_ports.extend(initiate_ep_port)

    logging.info(f"SCI: Started Scistream tunnel")
    logging.debug(
        "SCI: SciStream tunnel configs: \n Stream IDS: %s \n Listener AP ports: %s \n Initiator AP ports: %s \n Listener EP ports: %s \n Initiator EP ports: %s",
        stream_ids, listen_ap_ports, initiate_ap_ports, listen_ep_ports, initiate_ep_ports
    )
    return stream_ids, listen_ap_ports, initiate_ap_ports, listen_ep_ports, initiate_ep_ports

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
            f'pkill -TERM -x haproxy || true; ',
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
        