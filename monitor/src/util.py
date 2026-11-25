from __future__ import annotations
import csv
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
 
 
def now_wall_s() -> float:
    return time.time()
 
 
def now_mono_s() -> float:
    return time.monotonic()
 
 
def _read_header(path: str) -> Optional[list[str]]:
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            return next(csv.reader(f), None)
    except (OSError, StopIteration):
        return None
 
 
@dataclass
class CsvLogger:
    """
    Appends to <path>. If the file already exists with a different header
    (e.g. from an older version with fewer columns), the old file is renamed to
    <name>.<YYYYmmdd-HHMMSS>.old.csv and a fresh file is started, so rows never
    end up under the wrong header.
    """
    path: str
    fieldnames: Iterable[str]
    flush_every: int = 1
 
    def __post_init__(self) -> None:
        self.fieldnames = list(self.fieldnames)
 
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            if _read_header(self.path) != self.fieldnames:
                root, ext = os.path.splitext(self.path)
                moved = f"{root}.{time.strftime('%Y%m%d-%H%M%S')}.old{ext or '.csv'}"
                os.replace(self.path, moved)
                print(f"[CsvLogger] header changed; moved {self.path} -> {moved}", file=sys.stderr)
 
        self._f = open(self.path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._f, fieldnames=self.fieldnames)
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
 
 
def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None
 