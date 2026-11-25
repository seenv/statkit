import subprocess

# Each setting: the default value, plus the values to test.
# Multi-number settings (tcp_rmem/tcp_wmem) are strings, exactly as sysctl expects.
# All 27 settings: 14 that differ between default and tuned, then 13 that are identical.
SYSCTL_CONFS = {
    "net.ipv4.tcp_mtu_probing":           {"default": 0,        "values": [0, 1]},
    "net.ipv4.tcp_slow_start_after_idle": {"default": 1,        "values": [1, 0]},
    "net.core.optmem_max":                {"default": 131072,   "values": [131072, 1048576, 4194304, 8388608]},
    "net.core.rmem_max":                  {"default": 212992,   "values": [212992, 2147483647]},
    "net.core.wmem_max":                  {"default": 212992,   "values": [212992, 2147483647]},
    "net.ipv4.tcp_rmem": {
        "default": "4096 131072 6291456",
        "values": ["4096 131072 6291456", "4096 65536 1073741824", "4096 65536 2147483647"],
    },
    "net.ipv4.tcp_wmem": {
        "default": "4096 16384 4194304",
        "values": ["4096 16384 4194304", "4096 65536 1073741824", "4096 65536 2147483647"],
    },
    "net.ipv4.tcp_congestion_control":    {"default": "cubic",    "values": ["cubic", "bbr"]},
    "net.core.default_qdisc":             {"default": "fq_codel", "values": ["fq_codel", "fq"]},
    # "net.ipv4.tcp_no_metrics_save":       {"default": 0,        "values": [0, 1]},
    "net.ipv4.tcp_limit_output_bytes":    {"default": 1048576,  "values": [1048576, 4194304]},
    "net.core.netdev_budget":             {"default": 300,      "values": [300, 600]},
    "net.core.netdev_budget_usecs":       {"default": 2000,     "values": [2000, 8000]},
    "net.core.netdev_max_backlog":        {"default": 1000,     "values": [1000, 8192]},

    # Same value in default and tuned: pinned by reset_all_to_default(), skipped in the sweep.
    "net.ipv4.tcp_notsent_lowat":         {"default": 4294967295, "values": [4294967295]},
    "net.ipv4.tcp_autocorking":           {"default": 1,        "values": [1]},
    "net.ipv4.tcp_pacing_ss_ratio":       {"default": 200,      "values": [200]},
    "net.ipv4.tcp_pacing_ca_ratio":       {"default": 120,      "values": [120]},
    "net.ipv4.tcp_adv_win_scale":         {"default": 1,        "values": [1]},
    # "net.ipv4.tcp_window_scaling":        {"default": 1,        "values": [1]},
    "net.ipv4.tcp_app_win":               {"default": 31,       "values": [31]},
    "net.core.dev_weight":                {"default": 64,       "values": [64]},
    # "net.ipv4.tcp_sack":                  {"default": 1,        "values": [1]},
    # "net.ipv4.tcp_dsack":                 {"default": 1,        "values": [1]},
    # "net.ipv4.tcp_timestamps":            {"default": 1,        "values": [1]},
    "net.ipv4.tcp_ecn":                   {"default": 2,        "values": [2]},
    # "net.ipv4.tcp_moderate_rcvbuf":       {"default": 1,        "values": [1]},
}
"""
Leave at default
tcp_window_scaling, tcp_sack, tcp_dsack, tcp_timestamps, and tcp_moderate_rcvbuf should stay at 1
Turning any of them off only hurts performance. 
tcp_adv_win_scale and tcp_app_win are legacy; on kernel 6.6 and later, tcp_adv_win_scale is mostly 
ignored. dev_weight rarely matters unless using RPS.

"""
def set_sysctl(key, value):
    """Apply one sysctl setting (requires root)."""
    subprocess.run(["sysctl", "-w", f"{key}={value}"], check=True, capture_output=True)


def reset_all_to_default():
    for key, conf in SYSCTL_CONFS.items():
        set_sysctl(key, conf["default"])


def run_experiment(label):
    """Replace with your actual benchmark (iperf3, etc.). Return the result."""
    print(f"running experiment: {label}")
    return None


def one_at_a_time():
    """Baseline at all-defaults, then vary one setting at a time,
    restoring it to default before moving to the next."""
    results = []
    reset_all_to_default()
    results.append({"setting": "baseline", "value": None, "result": run_experiment("baseline")})

    for key, conf in SYSCTL_CONFS.items():
        for value in conf["values"]:
            if value == conf["default"]:
                continue  # already covered by the baseline
            set_sysctl(key, value)
            result = run_experiment(f"{key}={value}")
            results.append({"setting": key, "value": value, "result": result})
        set_sysctl(key, conf["default"])  # restore before the next setting

    return results


if __name__ == "__main__":
    for r in one_at_a_time():
        print(r)