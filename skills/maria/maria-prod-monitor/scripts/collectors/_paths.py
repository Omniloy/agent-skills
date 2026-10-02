"""Where the monitor finds its inputs, in one place.

Three kinds of location, deliberately separate:

* **DATA** — `catalog.yaml`, `known_errors.yaml`, `tasks.yaml`. Reviewed product knowledge,
  versioned in maria-voice (`docs/monitoring/`) and changed by PR. `MARIA_MONITOR_DATA`
  overrides it.
* **PRIVATE** — anything that carries patient data or credentials: Argo's login, the SINA
  review files (`auth_review_*.json`), the trend `state.json`. Never in a repository.
  `MARIA_MONITOR_PRIVATE` overrides the default `~/.config/maria-monitor`.
* **REPOS** — the sibling checkouts (maria-voice, maria-core-service, …) that
  `deployed.py` reads and whose `.env` holds the Supabase keys. `MARIA_REPOS_ROOT`
  overrides the autodetected parent of maria-voice.
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_voice_root() -> Path:
    env = os.environ.get("MARIA_VOICE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path.cwd().resolve()
    for base in (here, *here.parents):
        if (base / "maria_voice" / "assistant" / "session.py").is_file():
            return base
    return Path.home() / "omniloy" / "dev" / "maria-voice"


VOICE_ROOT = _find_voice_root()
REPOS_ROOT = Path(os.environ.get("MARIA_REPOS_ROOT") or VOICE_ROOT.parent).expanduser()
DATA_DIR = Path(os.environ.get("MARIA_MONITOR_DATA") or VOICE_ROOT / "docs" / "monitoring").expanduser()
PRIVATE_DIR = Path(os.environ.get("MARIA_MONITOR_PRIVATE") or Path.home() / ".config" / "maria-monitor").expanduser()

CATALOG = DATA_DIR / "catalog.yaml"
KEDB = DATA_DIR / "known_errors.yaml"
TASKS = DATA_DIR / "tasks.yaml"
STATE = PRIVATE_DIR / "state.json"
CORE_ENV = Path(os.environ.get("MARIA_CORE_ENV_PATH") or REPOS_ROOT / "maria-core-service" / ".env")
ARGO_ENV = Path(os.environ.get("ARGO_ENV_PATH") or PRIVATE_DIR / "argo.env")
ARGO_STATE = PRIVATE_DIR / "argo_state"
