"""Start the Stock Entry server:  .venv\\Scripts\\python.exe run.py  then open http://127.0.0.1:8765"""
from __future__ import annotations

import logging

from stock_entry.app import create_app
from stock_entry.config import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    app = create_app(settings)
    logging.getLogger(__name__).info("Stock Entry running at http://%s:%d", settings.host, settings.port)
    app.run(host=settings.host, port=settings.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
