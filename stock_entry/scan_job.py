"""Run one full scan and save it:  python -m stock_entry.scan_job

Used by the daily GitHub Action (.github/workflows/daily-scan.yml) so the deployed app always opens
with a fresh scan. Exit code 0 = scan saved, 1 = scan failed and the previous one was kept.
"""
from __future__ import annotations

import logging
import sys

from stock_entry.config import Settings
from stock_entry.services import Services, build_services

logger = logging.getLogger(__name__)


def run_once(services: Services) -> int:
    if not services.scanner.run_now():
        logger.error("Scan failed: %s", services.scanner.progress()["error"])
        return 1
    scan = services.store.load() or {}
    logger.info("Saved scan for %s: %d stocks", scan.get("data_date"), len(scan.get("rows", [])))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run_once(build_services(Settings())))


if __name__ == "__main__":
    main()
