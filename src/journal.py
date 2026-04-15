from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class Journal:
    def __init__(self, root: Path, max_bytes: int = 2_000_000, keep_files: int = 5):
        self.path = root / "logs" / "journal.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.keep_files = keep_files

    def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
        except OSError:
            return

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rotated = self.path.with_name(f"journal-{ts}.jsonl")
        try:
            self.path.rename(rotated)
        except OSError:
            return

        rotated_files = sorted(self.path.parent.glob("journal-*.jsonl"))
        excess = len(rotated_files) - self.keep_files
        for old in rotated_files[: max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass

    def log(self, event_type: str, payload: dict) -> None:
        self._rotate_if_needed()
        record = {
            "ts": datetime.now().isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
