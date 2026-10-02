#!/usr/bin/env python3
"""Cliente de SOLO LECTURA de la API de ArgoCD, para leer logs de pods de prod.

Por qué existe: el Log Analytics de PRD no es accesible con la cuenta actual
(`InsufficientAccessError`), y Argo sí expone los logs de los pods **vivos**
(desde el último reinicio). No es un sustituto —no hay histórico— pero para un
incidente en curso o reciente es suficiente, y para lo demás sigue haciendo
falta el workspace.

Sin navegador: la UI de Argo habla con su propia API REST
(`POST /api/v1/session` → token, y `GET …/logs` para los logs), así que
Playwright solo haría de intermediario.

GET únicamente, salvo el POST de login. Nada de sincronizar, reiniciar ni tocar
un despliegue.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = "https://argocd-external.api.omniloy.com"
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))
from _paths import ARGO_ENV, ARGO_STATE  # noqa: E402

#: ARGO_USR / ARGO_PASS. Credentials live outside any repository (see _paths.py).
ENV_PATH = ARGO_ENV
STATE = ARGO_STATE
TIMEOUT = 90


class ArgoError(RuntimeError):
    pass


def credentials() -> Dict[str, str]:
    values = {}
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    user, password = values.get("ARGO_USR"), values.get("ARGO_PASS")
    if not user or not password:
        raise ArgoError(f"faltan ARGO_USR / ARGO_PASS en {ENV_PATH}")
    return {"username": user, "password": password}


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


RETRIES = 4
BACKOFF_S = 3.0


def _request(path: str, *, token: Optional[str] = None, data: Optional[dict] = None,
             raw: bool = False) -> Any:
    """GET/POST con reintentos: la red se cae a ratos y un log de varios MB es
    justo lo que se corta a medias. Los 4xx no se reintentan (no van a mejorar)."""
    last: Optional[Exception] = None
    for attempt in range(RETRIES):
        try:
            return _request_once(path, token=token, data=data, raw=raw)
        except ArgoError as exc:
            last = exc
            if " HTTP 4" in f" {exc}" and "HTTP 429" not in str(exc):
                raise
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * (attempt + 1))
    raise last  # type: ignore[misc]


def _request_once(path: str, *, token: Optional[str] = None, data: Optional[dict] = None,
                  raw: bool = False) -> Any:
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_ctx()) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise ArgoError(f"HTTP {exc.code} en {path}: {exc.read().decode('utf-8','replace')[:300]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ArgoError(f"{type(exc).__name__} en {path}: {exc}") from exc
    if raw:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def token() -> str:
    """Token de sesión, cacheado en disco mientras siga valiendo."""
    STATE.mkdir(parents=True, exist_ok=True)
    cache = STATE / "token.json"
    if cache.exists():
        cached = json.loads(cache.read_text()).get("token")
        if cached:
            try:
                _request("/api/v1/applications?fields=items.metadata.name", token=cached)
                return cached
            except ArgoError:
                pass
    data = _request("/api/v1/session", data=credentials())
    if not isinstance(data, dict) or "token" not in data:
        raise ArgoError(f"login sin token: {str(data)[:200]}")
    cache.write_text(json.dumps({"token": data["token"]}))
    return data["token"]


def applications(tok: str) -> List[dict]:
    data = _request("/api/v1/applications", token=tok)
    return data.get("items") or []


def resource_tree(tok: str, app: str) -> dict:
    return _request(f"/api/v1/applications/{urllib.parse.quote(app)}/resource-tree", token=tok)


def pods_of(tree: dict) -> List[dict]:
    return [
        {"name": n.get("name"), "namespace": n.get("namespace"), "health": (n.get("health") or {}).get("status"),
         "created": n.get("createdAt")}
        for n in (tree.get("nodes") or [])
        if n.get("kind") == "Pod"
    ]


def pod_logs(tok: str, app: str, pod: str, namespace: str, container: str = "",
             tail: int = 2000, since_seconds: Optional[int] = None) -> str:
    params = {"podName": pod, "namespace": namespace, "tailLines": tail, "follow": "false"}
    if container:
        params["container"] = container
    if since_seconds:
        params["sinceSeconds"] = since_seconds
    path = f"/api/v1/applications/{urllib.parse.quote(app)}/logs?{urllib.parse.urlencode(params)}"
    return _request(path, token=tok, raw=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", action="store_true", help="listar aplicaciones")
    parser.add_argument("--app", help="aplicación (p.ej. mariavoice-prd)")
    parser.add_argument("--pods", action="store_true", help="listar sus pods")
    parser.add_argument("--logs", metavar="POD", help="descargar los logs de un pod")
    parser.add_argument("--container", default="")
    parser.add_argument("--tail", type=int, default=2000)
    parser.add_argument("--since", type=int, help="segundos hacia atrás")
    parser.add_argument("--out")
    args = parser.parse_args()

    tok = token()
    print(f"  sesión de Argo: ok ({len(tok)} chars)")

    if args.apps:
        for app in applications(tok):
            meta, status = app.get("metadata", {}), app.get("status", {})
            print(f"    {meta.get('name'):28} salud={(status.get('health') or {}).get('status'):8} "
                  f"sync={(status.get('sync') or {}).get('status')}")
        return 0

    if not args.app:
        raise SystemExit("hace falta --app (o --apps)")

    tree = resource_tree(tok, args.app)
    pods = pods_of(tree)
    if args.pods or not args.logs:
        print(f"    pods de {args.app}: {len(pods)}")
        for pod in pods:
            print(f"      {pod['name']:52} ns={pod['namespace']:14} salud={pod['health']}")
        return 0

    pod = next((p for p in pods if p["name"] == args.logs), None)
    if not pod:
        raise SystemExit(f"pod {args.logs!r} no está en {args.app}")
    text = pod_logs(tok, args.app, pod["name"], pod["namespace"], args.container,
                    args.tail, args.since)
    lines = text.splitlines()
    print(f"    {len(lines)} líneas de {pod['name']}")
    if args.out:
        Path(args.out).write_text(text)
        print(f"    escrito: {args.out}")
    else:
        print("\n".join(lines[:15]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
