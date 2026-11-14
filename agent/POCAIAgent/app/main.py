"""Entrypoint module for `app.main:app`."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure 'src' is in python path
root_dir = Path(__file__).resolve().parent.parent
src_path = str(root_dir / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Set default AGENTS_CONFIG if not set
if "AGENTS_CONFIG" not in os.environ:
    default_config = root_dir / "config" / "agents.yaml"
    if default_config.exists():
        os.environ["AGENTS_CONFIG"] = str(default_config)

from presentation.api import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
