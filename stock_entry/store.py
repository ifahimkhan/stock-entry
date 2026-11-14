"""Persist the latest scan as a JSON file so the page loads instantly without rescanning."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_SECONDS = 0.1


class ScanStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        """Return the saved scan, or None when there is none yet (or the file is unreadable)."""
        if not self._path.exists():
            return None
        try:
            with self._path.open(encoding="utf-8") as handle:
                scan = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Saved scan %s could not be read: %s", self._path, exc)
            return None
        if not isinstance(scan, dict) or not isinstance(scan.get("rows"), list):
            logger.error("Saved scan %s has an unexpected shape; ignoring it", self._path)
            return None
        return scan

    def save(self, scan: dict[str, Any]) -> None:
        """Write atomically: a crash mid-write never leaves a half-written scan file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(scan, handle, allow_nan=False)
        # On Windows the swap fails while another thread is reading the old file, so retry briefly.
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temp_path, self._path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_RETRY_SECONDS)
