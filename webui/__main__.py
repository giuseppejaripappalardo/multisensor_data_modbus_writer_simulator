"""Convenience entry point: `python -m webui` starts uvicorn on 0.0.0.0:8000."""
import os

import uvicorn


def main() -> None:
    host = os.environ.get("UI_HOST", "0.0.0.0")
    port = int(os.environ.get("UI_PORT", "8000"))
    uvicorn.run("webui.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
