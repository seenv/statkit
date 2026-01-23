# resource_monitor
Monitoring CPU, memory, disk, and network.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python monitor.py --out ./mon --interval 1.0 --duration 60
```

per-process:

```bash
python monitor.py --out ./mon --interval 1.0 --duration 60 --pids 1234,5678
```

Network backend:

- `psutil` (default): NIC byte/packet deltas and Mbps
- `bpftrace`: per-second NIC bytes via tracepoints (requires `bpftrace` + privileges)

```bash
python monitor.py --out ./mon --duration 30 --net-backend psutil
sudo -E python monitor.py --out ./mon --duration 30 --net-backend bpftrace
```

Outputs:
- cpu.csv
- mem.csv
- disk.csv
- net.csv
