import logging
import shlex

from remote import run_subprocess
from config import Config

#-------------------------------------------------------------------------------
# Rsync helpers
def stop_rsync_daemon(
    cfg: Config, host: str,
    module_path: str = "/tmp/temp_files",
    check: bool = True) -> None:
    cp = run_subprocess(
        host, None,
        f"if [ -f {shlex.quote(module_path)}/rsyncd.pid ] && "
        f"kill -0 $(cat {shlex.quote(module_path)}/rsyncd.pid) 2>/dev/null; then "
        f"  echo 'stopping existing rsync daemon from pid file'; "
        f"  kill $(cat {shlex.quote(module_path)}/rsyncd.pid) 2>/dev/null || true; "
        f"  sleep 1; "
        f"fi; "
        f"rm -f {shlex.quote(module_path)}/rsyncd.pid "
        f"{shlex.quote(module_path)}/rsyncd.lock; ",
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
    port: int, tunnel_id: str, 
    numa: str, out_dir: str, timeout: int, 
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
    ) -> None:

    cp = run_subprocess(
        src_host, None,
        f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(module_path)} && "

        f"cat > {shlex.quote(module_path)}/rsyncd.conf <<'EOF'\n"
        f"use chroot = no\n"
        f"max connections = 64\n"
        f"pid file = {module_path}/rsyncd.pid\n"
        f"log file = {module_path}/rsyncd.log\n"
        f"lock file = {module_path}/rsyncd.lock\n"
        f"timeout = 0\n"
        f"\n"
        f"[{module_name}]\n"
        f"    path = {module_path}\n"
        f"    read only = false\n"
        f"    list = yes\n"
        f"EOF\n"

        f"globus-streams-launch -p {port} {shlex.quote(tunnel_id)} "
        f"rsync --daemon vvv --config={shlex.quote(module_path)}/rsyncd.conf --port={port} "
        f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-daemon.log; ",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    logging.info("RGST: Started rsync daemon on %s:%s", src_host.upper(), port)
    logging.debug("RGST stdout:\n%s", cp.stdout)


def start_rsync_transfer_gst(
    cfg: Config, src_host: str, dst_host: str, 
    tunnel_id, port: int, parallel, arg,
    file: str, numa: str, out_dir: str, timeout: int,
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
    ) -> None:
    #rsync_url = f"rsync://{cfg.initiator_pub}:{port}/{module_name}/{file}"
    rsync_url = f"rsync://globus.{tunnel_id}:{port}/{module_name}/{file}"
    logging.info("\n\n RSYNC URL: %s\n\n", rsync_url)
    cp = run_subprocess(
        dst_host, None, 
        f"set +x; mkdir -p {shlex.quote(out_dir)} && "
        f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "
        f"globus-streams-launch {shlex.quote(tunnel_id)} "  
        f"rsync -avvv --info=progress2,stats2 --no-compress --no-checksum "
        f"--whole-file --ignore-times --inplace --preallocate --numeric-ids "
        f"{shlex.quote(rsync_url)} {shlex.quote(module_path)}/{shlex.quote(file)} "
        f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-log.log; "
        f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\"; "
        f"}} 2>&1 | tr '\\r' '\\n' "
        f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
        f"| tee {shlex.quote(out_dir)}/{shlex.quote(app)}.log",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    logging.info(
        "RGST: Completed rsync daemon transfer from %s to %s/%s",
        #src_host.upper(), dst_host.upper(), port, module_name, file,
        rsync_url, module_path, file,
    )
    logging.debug("RSYNC stdout:\n%s", cp.stdout)


#-------------------------------------------------------------------------------
# Rsync Base
def start_rsync_daemon_base(
    cfg: Config, src_host: str, numa: str, out_dir: str, port: int, timeout: int, 
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_files", check: bool = True,
    ) -> None:
    cp = run_subprocess(
        src_host, None,
        f"mkdir -p {shlex.quote(out_dir)} {shlex.quote(module_path)} && "
        
        # f"if [ -f {shlex.quote(module_path)}/rsyncd.pid ] && "
        # f"kill -0 $(cat {shlex.quote(module_path)}/rsyncd.pid) 2>/dev/null; then "
        # f"  echo 'stopping existing rsync daemon from pid file'; "
        # f"  kill $(cat {shlex.quote(module_path)}/rsyncd.pid) 2>/dev/null || true; "
        # f"  sleep 1; "
        # f"fi; "
        # f"rm -f {shlex.quote(module_path)}/rsyncd.pid "
        # f"{shlex.quote(module_path)}/rsyncd.lock; "
    
        f"cat > {shlex.quote(module_path)}/rsyncd.conf <<'EOF'\n"
        f"use chroot = no\n"
        f"max connections = 64\n"
        f"pid file = {module_path}/rsyncd.pid\n"
        f"log file = {module_path}/rsyncd.log\n"
        f"lock file = {module_path}/rsyncd.lock\n"
        f"timeout = 0\n"
        #f"socket options = SO_SNDBUF=134217728 SO_RCVBUF=134217728\n"
        f"\n"
        f"[{module_name}]\n"
        f"    path = {module_path}\n"
        f"    read only = false\n"
        f"    list = yes\n"
        f"EOF\n"
        
        #f"rsync --daemon --config={shlex.quote(module_path)}/rsyncd.conf --port={port}; ",
        f"rsync --daemon -vvv --config={shlex.quote(module_path)}/rsyncd.conf --port={port} "
        f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-daemon.log; ",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    logging.info("RSYNC: Started rsync daemon on %s:%s", src_host.upper(), port)
    logging.debug("RSYNC stdout:\n%s", cp.stdout)


def start_rsync_transfer_base(
    cfg: Config, src_host: str, dst_host: str, file: str, numa: str, out_dir: str, port: int, timeout: int,
    app: str, 
    module_name: str = "transfer", module_path: str = "/tmp/temp_file", check: bool = True,
    ) -> None:
    #rsync_url = f"rsync://{cfg.initiator_ip}:{port}/{module_name}/{file}"
    rsync_url = f"rsync://{cfg.listener_pub}:{port}/{module_name}/{file}"
    cp = run_subprocess(
        dst_host, None, 
        f"set +x; mkdir -p {shlex.quote(out_dir)} && "
        f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "
        f"rsync -avvv --info=progress2,stats2 --no-compress --no-checksum "
        f"--whole-file --ignore-times --inplace --preallocate --numeric-ids "
        #f"{shlex.quote(module_path)}/{shlex.quote(file)} {shlex.quote(rsync_url)} " # rsync://globus.${TUNNEL_ID}:3096/transfer/20G.bin /tmp/temp_files/20G.bin 
        f"{shlex.quote(rsync_url)} {shlex.quote(module_path)}/{shlex.quote(file)} "
        f"--log-file={shlex.quote(out_dir)}/{shlex.quote(app)}-log.log; "
        f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\"; "
        f"}} 2>&1 | tr '\\r' '\\n' "
        f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
        f"| tee {shlex.quote(out_dir)}/{shlex.quote(app)}.log",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    logging.info(
        "RSYNC: Completed rsync daemon transfer of %s from %s to rsync://%s:%s/%s/%s",
        file, src_host.upper(), dst_host.upper(), port, module_name, file,
    )
    logging.debug("RSYNC stdout:\n%s", cp.stdout)


# -------------------------------------------------------------------------------
# Rsync SSH 
def start_rsync_ssh(
    cfg: Config, src_host: str, dst_host: str, file: str, numa: str, out_dir: str, port: int, timeout: int,
    module_path: str = "/tmp/temp_file", check: bool = True,
    ) -> None:
    file_path = f"{module_path}/{file}"
    cp = run_subprocess(
        #src_host, None,
        dst_host, None,
        f"set +x; mkdir -p {shlex.quote(out_dir)} && "
        f"{{ echo \"START $(date '+%Y-%m-%d %H:%M:%S')\"; "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/rsync_ssh-time.log "
        f"rsync -avvv --info=progress2,stats2 --mkpath --no-compress --no-checksum "
        f"--whole-file --ignore-times --inplace --preallocate --numeric-ids "
        ##f"{shlex.quote(file_path)} "
        f"-e {shlex.quote('ssh -T -o Compression=no -o StrictHostKeyChecking=no')} "
        #f"-e {shlex.quote('ssh -p {port} -T -o Compression=no -o StrictHostKeyChecking=no')} "
        #f"{shlex.quote(file_path)} {shlex.quote(dst_host)}:{shlex.quote(file_path)} "
        ##f"{shlex.quote(dst_host)}:{shlex.quote(file_path)} "
        
        f"{shlex.quote(src_host)}:{shlex.quote(file_path)} "
        f"{shlex.quote(file_path)} "
        
        f"--log-file={shlex.quote(out_dir)}/rsync_ssh-log.log; "
        f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\"; "
        f"}} 2>&1 | tr '\\r' '\\n' "
        f"| stdbuf -oL awk 'NF {{ print $0; fflush(); }}' "
        f"| tee {shlex.quote(out_dir)}/rsync_ssh.log",
        localhost=cfg.localhost,
        timeout=timeout
    )
    logging.info("RSYNC: Completed rsync ssh transfer of %s from %s to %s/%s",
        file, src_host.upper(), dst_host, file_path,
    )
    logging.debug("RSYNC stdout:\n%s", cp.stdout)
