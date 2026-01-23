from __future__ import annotations
import csv
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


def now_wall_s() -> float:
    return time.time()


def now_mono_s() -> float:
    return time.monotonic()


@dataclass
class CsvLogger:
    path: str
    fieldnames: Iterable[str]
    flush_every: int = 1

    def __post_init__(self) -> None:
        self._f = open(self.path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._f, fieldnames=list(self.fieldnames))
        if os.stat(self.path).st_size == 0:
            self._writer.writeheader()
            self._f.flush()
        self._n = 0

    def write(self, row: Dict[str, Any]) -> None:
        self._writer.writerow(row)
        self._n += 1
        if self.flush_every > 0 and (self._n % self.flush_every == 0):
            self._f.flush()

    def close(self) -> None:
        try:
            self._f.flush()
        finally:
            self._f.close()


# def percentile(sorted_vals, p: float) -> float:
#     """
#     p in [0, 100]. Uses linear interpolation between closest ranks.
#     Expects sorted_vals already sorted.
#     """
#     if not sorted_vals:
#         return float("nan")
#     if p <= 0:
#         return float(sorted_vals[0])
#     if p >= 100:
#         return float(sorted_vals[-1])
#     k = (len(sorted_vals) - 1) * (p / 100.0)
#     f = int(k)
#     c = min(f + 1, len(sorted_vals) - 1)
#     if f == c:
#         return float(sorted_vals[f])
#     d0 = sorted_vals[f] * (c - k)
#     d1 = sorted_vals[c] * (k - f)
#     return float(d0 + d1)


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None
