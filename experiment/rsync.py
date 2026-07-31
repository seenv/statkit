import logging
import shlex
import subprocess
from typing import Sequence

from remote import run_subprocess, popen_subprocess
from config import Config

#-------------------------------------------------------------------------------
# Rsync helpers
def stop_rsync_daemon(
    cfg: Config, host: str, parallel: int,
    module_path: str = "/tmp/temp_files",
    check: bool = True
) -> None:
    for i in range(parallel):
        cp = run_subprocess(
            host, None,
            #f"if [ -f {shlex.quote(module_path)}/rsyncd{i}.pid ] && "
            f"if [ -f {shlex.quote(module_path)}/rsyncd.pid ]; then "
            #f"kill -9 $(cat {shlex.quote(module_path)}/rsyncd{i}.pid) 2>/dev/null; then "
            #f" kill $(cat {shlex.quote(module_path)}/rsyncd.pid) "
            f"  echo 'stopping existing rsync daemon from pid file'; "
            f"  kill $(cat {shlex.quote(module_path)}/rsyncd{i}.pid) 2>/dev/null || true; "
            f"  sleep 1; "
            f"fi; "
            f"rm -f "
            f"{shlex.quote(module_path)}/rsyncd{i}.pid "
            f"{shlex.quote(module_path)}/rsyncd{i}.conf "
            f"{shlex.quote(module_path)}/rsyncd{i}.lock; ",
            localhost=cfg.localhost
        )

    if check and cp.returncode != 0:
        raise RuntimeError(
            f"RSYNC: Failed killing rsync daemon on {host.upper()}\n "
            f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr} "
        )
    logging.debug("RSYNC: Killed rsync daemon on %s %s", host.upper(), cp.stdout)


#-------------------------------------------------------------------------------
# Rsync GST
def start_rsync_daemon_gst(
    cfg: Config, src_host: str,
    rsync_port: int, stream_ids: Sequence[str], parallel:int,
    numa: str, out_dir: str, timeout: int, 
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
) -> list[subprocess.Popen[str]]:

    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    processes = []

    for i, stream_id in enumerate(stream_ids):
        cp = popen_subprocess(
            src_host, None,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(module_path)} && "

            f"cat > {shlex.quote(module_path)}/rsyncd{i}.conf <<'EOF'\n"
            f"use chroot = no\n"
            f"max connections = 64\n"
            f"pid file = {module_path}/rsyncd{i}.pid\n"
            #f"log file = {module_path}/rsyncd{i}.log\n"
            f"log file = {shlex.quote(out_dir)}/{app}-rsyncd{i}.log\n"
            f"lock file = {module_path}/rsyncd{i}.lock\n"
            f"timeout = 0\n"
            f"\n"
            f"[{module_name}{i}]\n"
            f"    path = {module_path}\n"
            f"    read only = false\n"
            f"    list = yes\n"
            f"EOF\n"

            f"globus-streams-launch -p {rsync_port + (i * 2)} {shlex.quote(stream_id)} "
            f"rsync --daemon -vvv --config={shlex.quote(module_path)}/rsyncd{i}.conf --port={rsync_port + (i * 2)} "
            f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-daemon{i}.log; ",
            localhost=cfg.localhost,
            #timeout=timeout,
        )
        processes.append(cp)
        logging.debug("RGST: Started rsync daemon on %s:%s", src_host.upper(), rsync_port + (i * 2))
        #logging.debug("RGST stdout:\n%s", cp.stdout)
    return processes


def start_rsync_transfer_gst(
    cfg: Config, src_host: str, dst_host: str, listen_ip: str,
    stream_ids: Sequence[str], listen_ports: Sequence[int], parallel: int,
    numa: str, arg: int,
    files: list[str], 
    out_dir: str, timeout: int,
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
) -> list[subprocess.CompletedProcess[str]]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")
    
    processes = []
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        rsync_url = f"rsync://globus.{stream_id}:{listen_port}/{module_name}{i}/{files[i]}"
        #rsync_files = " ".join(shlex.quote(f"rsync://globus.{stream_id}:{listen_port}/{module_name}/{file_name}")for file_name in files)
        logging.debug("RSYNC URL: %s", str(rsync_url))
        cp = popen_subprocess(
            dst_host, None, 
            f"set +x; mkdir -p {shlex.quote(out_dir)} && "
            f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}{i}-time.log "
            f"globus-streams-launch {shlex.quote(stream_id)} "  
            f"rsync -avvv --info=progress2,stats2 --no-compress --no-checksum "
            f"--whole-file --ignore-times --inplace --preallocate --numeric-ids "
            #f"{shlex.quote(rsync_url)} {shlex.quote(module_path)}/{shlex.quote(files[i])} "
            f"{shlex.quote(rsync_url)} {shlex.quote(module_path)}/ "
            f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-log.log; "
            f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\"; "
            f"}} 2>&1 | tr '\\r' '\\n' "
            f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
            f"| tee {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.log",
            localhost=cfg.localhost,
            #timeout=timeout,
        )
        processes.append(cp)
    for i, cp in enumerate(processes):
        stdout, stderr = cp.communicate()
        if cp.returncode != 0:
            logging.error("RGST: Process failed: %s", stderr)

        logging.debug("RGST: Completed rsync daemon transfer of file %s %s", files[i], stdout)


#-------------------------------------------------------------------------------
# Rsync Base
def start_rsync_daemon_base(
    cfg: Config, src_host: str,
    rsync_port: int, stream_ids: Sequence[str], parallel:int,
    numa: str, out_dir: str, timeout: int, 
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
) -> list[subprocess.Popen[str]]:

    if not (len(stream_ids) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got tunnel ids={len(stream_ids)}")
    processes = []
    for i, stream_id in enumerate(stream_ids):
        cp = popen_subprocess(
            src_host, None,
            f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(module_path)} && "

            f"cat > {shlex.quote(module_path)}/rsyncd{i}.conf <<'EOF'\n"
            f"use chroot = no\n"
            f"max connections = 64\n"
            f"pid file = {module_path}/rsyncd{i}.pid\n"
            #f"log file = {module_path}/rsyncd{i}.log\n"
            f"log file = {shlex.quote(out_dir)}/{app}-rsyncd{i}.log\n"
            f"lock file = {module_path}/rsyncd{i}.lock\n"
            f"timeout = 0\n"
            f"\n"
            f"[{module_name}{i}]\n"
            f"    path = {module_path}\n"
            f"    read only = false\n"
            f"    list = yes\n"
            f"EOF\n"
        
            f"rsync --daemon -vvv --config={shlex.quote(module_path)}/rsyncd{i}.conf --port={rsync_port + (i * 2)} "
            f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-daemon{i}.log; ",
            localhost=cfg.localhost,
            #timeout=timeout,
        )
        processes.append(cp)
        logging.debug("RBASE: Started rsync daemon on %s:%s", src_host.upper(), rsync_port + (i * 2))
        #logging.debug("RGST stdout:\n%s", cp.stdout)
    return processes


def start_rsync_transfer_base(
    cfg: Config, src_host: str, dst_host: str, listen_ip: str,
    stream_ids: Sequence[str], listen_ports: Sequence[int], parallel: int,
    numa: str, arg: int,
    files: list[str], 
    out_dir: str, timeout: int,
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
) -> list[subprocess.CompletedProcess[str]]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")

    processes = []
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        rsync_url = f"rsync://{listen_ip}:{listen_port}/{module_name}{i}/{files[i]}"
        logging.debug("RSYNC URL: %s", str(rsync_url))
        cp = popen_subprocess(
            dst_host, None, 
            f"set +x; mkdir -p {shlex.quote(out_dir)} && "
            f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}{i}-time.log "
            f"rsync -avvv --info=progress2,stats2 --no-compress --no-checksum "
            f"--whole-file --ignore-times --inplace --preallocate --numeric-ids "
            f"{shlex.quote(rsync_url)} {shlex.quote(module_path)}/{shlex.quote(files[i])} "
            f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-{i}-log.log; "
            f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\"; "
            f"}} 2>&1 | tr '\\r' '\\n' "
            f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
            f"| tee {shlex.quote(out_dir)}/{shlex.quote(app)}-{i}.log",
            localhost=cfg.localhost,
            #timeout=timeout,
        )
        processes.append(cp)
    for i, cp in enumerate(processes):
        stdout, stderr = cp.communicate()
        if cp.returncode != 0:
            logging.error("RBASE: Process failed: %s", stderr)

        logging.debug("RBASE: Completed rsync daemon transfer of file %s %s", files[i], stdout)


# -------------------------------------------------------------------------------
# Rsync SSH 
def start_rsync_ssh(
    cfg: Config, src_host: str, dst_host: str, listen_ip: str,
    stream_ids: Sequence[str], listen_ports: Sequence[int], parallel: int,
    numa: str, arg: int,
    files: list[str], 
    out_dir: str, timeout: int,
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
) -> list[subprocess.CompletedProcess[str]]:
    if not (len(listen_ports) == parallel):
        raise RuntimeError(f"Expected all lists to have length parallel={parallel}, but got listen ports={len(listen_ports)}")

    processes = []
    # for i, file_name in enumerate(files):
    for i, (listen_port, stream_id) in enumerate(zip(listen_ports, stream_ids)):
        file_path = f"{module_path}/{files[i]}"
        cp = popen_subprocess(
            #src_host, None,
            dst_host, None,
            f"set +x; mkdir -p {shlex.quote(out_dir)} && "
            f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
            f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/rsync_ssh{i}-time.log "
            f"rsync -avvv --info=progress2,stats2 --mkpath --no-compress --no-checksum "
            f"--whole-file --ignore-times --inplace --preallocate --numeric-ids "
            ##f"{shlex.quote(file_path)} "
            f"-e {shlex.quote('ssh -T -o Compression=no -o StrictHostKeyChecking=no')} "
            #f"-e {shlex.quote('ssh -p {port} -T -o Compression=no -o StrictHostKeyChecking=no')} "
            #f"{shlex.quote(file_path)} {shlex.quote(dst_host)}:{shlex.quote(file_path)} "
            ##f"{shlex.quote(dst_host)}:{shlex.quote(file_path)} "
            
            f"{shlex.quote(src_host)}:{shlex.quote(file_path)} "
            f"{shlex.quote(file_path)} "
            
            f"--log-file={shlex.quote(f'{out_dir}/rsync_ssh-{i}-log.log')}; "
            f"rc=$?; "
            f"echo \"END $(date '+%Y-%m-%d %H:%M:%S') RC=$rc\"; "
            f"exit \"$rc\"; "
            f"}} 2>&1 | tr '\\r' '\\n' "
            f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
            f"| tee {shlex.quote(f'{out_dir}/rsync_ssh-{i}.log')}",
            localhost=cfg.localhost,
        )
        processes.append(cp)
    for i, cp in enumerate(processes):
        stdout, stderr = cp.communicate()
        if cp.returncode != 0:
            logging.error("RSSH: Process failed: %s", stderr)
        
        logging.debug("RSSH: Completed rsync ssh transfer of file %s %s", files[i], stdout)
