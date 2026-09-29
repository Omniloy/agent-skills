#!/usr/bin/env python3
"""El código que corre en un entorno, no el de desarrollo.

La lección de la revisión del 2026-09-02: razoné sobre `dev` para explicar
llamadas de producción y llegué a una conclusión falsa (creí que la validación
parcial había corrido porque el `tool_input` llevaba el primer apellido, cuando
la versión desplegada pide ese dato de entrada por otra rama distinta). El
mensaje que delataba la diferencia no existía en `dev` siquiera.

Regla: **para diagnosticar un entorno se lee su rama.** Este script la resuelve
y saca el fichero o busca en él, para que no haya que acordarse.

Uso:
    python3 deployed.py --env prod --repo mcp --grep "y SOLO el PRIMER apellido"
    python3 deployed.py --env prod --repo voice --path maria_voice/assistant/agents/base_agent.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

#: Qué rama va a cada entorno. `main` es producción — no `dev`.
BRANCH = {"prod": "main", "stg": "stg", "dev": "dev"}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import REPOS_ROOT  # noqa: E402

REPOS = {
    "voice": str(REPOS_ROOT / "maria-voice"),
    "core": str(REPOS_ROOT / "maria-core-service"),
    # Deprecado y fusionado en maria-voice, pero PRODUCCIÓN todavía lo ejecuta:
    # mientras `main` de voice no lleve la fusión, el MCP es el código que corre.
    "mcp": str(REPOS_ROOT / "omniloy-mcp-server"),
}


def run(repo: Path, args: list) -> str:
    result = subprocess.run(["git", "-C", str(repo)] + args, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"git {' '.join(args)} falló: {result.stderr.strip()[:200]}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="prod", choices=sorted(BRANCH))
    parser.add_argument("--repo", required=True, choices=sorted(REPOS))
    parser.add_argument("--path", help="fichero a volcar")
    parser.add_argument("--grep", help="patrón a buscar en toda la rama")
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()

    repo = Path(REPOS[args.repo])
    branch = f"origin/{BRANCH[args.env]}"
    if not args.no_fetch:
        subprocess.run(["git", "-C", str(repo), "fetch", "origin", BRANCH[args.env], "--quiet"],
                       capture_output=True)
    head = run(repo, ["log", "-1", "--format=%h %ad %s", "--date=short", branch]).strip()
    print(f"# {args.repo} @ {branch} ({args.env}): {head}", file=sys.stderr)

    if args.grep:
        out = run(repo, ["grep", "-n", args.grep, branch])
        print(out or f"# sin coincidencias de {args.grep!r} en {branch}", end="")
    elif args.path:
        print(run(repo, ["show", f"{branch}:{args.path}"]), end="")
    else:
        raise SystemExit("hace falta --path o --grep")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
