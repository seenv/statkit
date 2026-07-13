import logging
import shlex
from typing import Dict

from config import Config, Role
from utils import parse_size_to_bytes, parse_collection_uid
from remote import run_subprocess


#-------------------------------------------------------------------------------
# Globus Transfer
# _UUID_CANDIDATE = re.compile(r"[0-9a-fA-F-]{32,36}")
# def parse_collection_uid(output: str,  parts: list[str], *, exact: bool = False) -> str:
#     for line in output.splitlines():
#         if "|" not in line:
#             continue
#         if line.strip().startswith("---"):
#             continue
#         # first column is Display Name
#         display = line.split("|", 1)[1].strip()
#         if not all(part in display for part in parts):
#             continue
#         m = _UUID_CANDIDATE.search(line)
#         if not m:
#             raise RuntimeError(f"Matched name but no ID found on line:\n{line}")
#         return str(uuid.UUID(m.group(0)))
#     raise RuntimeError(f"No collection row matched name={parts!r}.\nOutput:\n{output}")


def get_collection_id(cfg: Config, check: bool = True) -> Dict[Role, str]:
    out: dict[Role, str] = {}
    for role, host in cfg.hosts.ep.items():
        cp = run_subprocess(host, None, "gcs collection list \n", localhost=cfg.localhost)
        if check and cp.returncode != 0:
            raise RuntimeError(
                f"GTR: Failed getting the collection id on {host.upper()}\n"
                f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
            )
        out[role] = parse_collection_uid(cp.stdout + "\n" + cp.stderr, [cfg.lease, role.capitalize()], exact=False)
        logging.debug("GTR: Collection id on %s %s", host.upper(), cp.stdout.strip())
    missing = {"initiator", "listener"} - set(out.keys())
    if missing:
        raise RuntimeError(f"Missing collection ids for roles: {sorted(missing)}")
    return out




#task_id="$(globus transfer $source_ep $dest_ep     --jmespath 'task_id' --format=UNIX     --batch my_file_batch.txt)"
# echo "Waiting on 'globus transfer' task '$task_id'"
# globus task wait "$task_id" --timeout 30
# if [ $? -eq 0 ]; then
#     echo "$task_id completed successfully";
# else
#     echo "$task_id failed!";
# fi

def start_globus_transfer(
    cfg: Config, src_cid: str, dst_cid: str, label: str, 
    # contact_port: int, parallel: int, 
    size: int, encr: int, file: str, app: str, out_dir: str,  timeout: int,
    # module_name: str = "transfer", module_path: str = "/tmp/temp_files", 
    check: bool = True,
    ) -> None:
    extra_arg = f"--encrypt" if encr == 1 else ""
    size_bytes = parse_size_to_bytes(size)
    cp = run_subprocess(
        cfg.localhost, None,
        f"set +x; mkdir -p {shlex.quote(out_dir)} && "
        f"SUBMISSION_ID=$(globus task generate-submission-id) && "
        f"echo \"$SUBMISSION_ID\" > {shlex.quote(out_dir)}/{shlex.quote(app)}_submission_id.txt && "
        f"echo \"SUBMISSION_ID=$SUBMISSION_ID\" && "
        f"echo \"START $(date '+%Y-%m-%d %H:%M:%S')\" | tee {shlex.quote(out_dir)}/{shlex.quote(app)}.log && "
        f"/usr/bin/time -vvv -o {shlex.quote(out_dir)}/{shlex.quote(app)}-time.log "
        f"globus transfer -v --submission-id \"$SUBMISSION_ID\" "
        #f"--source-local-user cc --destination-local-user cc "
        f"{shlex.quote(src_cid)}:{shlex.quote(file)} "
        f"{shlex.quote(dst_cid)}:{shlex.quote(file)} "
        f"--label {shlex.quote(label)} {extra_arg} "
        f"--no-verify-checksum --fail-on-quota-errors "
        f"--format unix --jmespath task_id --notify off "
        f"> {shlex.quote(out_dir)}/{shlex.quote(app)}_task_id.txt 2> {shlex.quote(out_dir)}/{shlex.quote(app)}_submit_stderr.log && "
        #f"> {shlex.quote(out_dir)}/{shlex.quote(app)}_task_id.txt "
        #f"2> >(tee {shlex.quote(out_dir)}/{shlex.quote(app)}_submit_stderr.log >&2) && "
        f"TASK_ID=$(cat {shlex.quote(out_dir)}/{shlex.quote(app)}_task_id.txt | tr -d '\"[:space:]') && "
        f"echo \"TASK_ID=$TASK_ID\" | tee -a {shlex.quote(out_dir)}/{shlex.quote(app)}.log && "
        f"globus task wait \"$TASK_ID\" 2>&1 | tee -a {shlex.quote(out_dir)}/{shlex.quote(app)}.log && "
        f"globus task show \"$TASK_ID\" "
        f"> {shlex.quote(out_dir)}/{shlex.quote(app)}_task_show.log && "
        f"echo \"END $(date '+%Y-%m-%d %H:%M:%S')\" | tee -a {shlex.quote(out_dir)}/{shlex.quote(app)}.log",
        localhost=cfg.localhost,
        timeout=timeout,
    )
    logging.info(
        "GTR: Completed Globus transfer of %s from %s to %s", file, src_cid, dst_cid)
    logging.debug("GTR stdout:\n%s", cp.stdout)