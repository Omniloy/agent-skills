#!/usr/bin/env python3
"""Access to conversation flows: read through PostgREST, write through core-service.

Three rules live here so no edit script has to rediscover them:

1. **Reads are PostgREST GETs; writes go through core-service.** A flow document written
   directly (PostgREST, SQL, the Supabase UI) changes while `conversation_flows.version` does
   not, so every worker's flow cache keeps serving the old document with no error anywhere.
   `PUT /api/v1/conversation-flows/:id` moves the version row, `version` and
   `active_version_id` together. The one exception is creating a clone, where there is no
   flow to PUT yet (see `clone_flow.py`).
2. **Keys are never printed.** They are read from maria-core-service's `.env`, where the
   service-role key of each environment sits in its own block (prod's commented out), and
   are matched to an environment by the project ref inside the URL next to them.
3. **Health is checked after every write**: `version` must equal the highest
   `version_number`, and `active_version_id` must be set.

Environment:
    MARIA_CORE_ENV_PATH   path to maria-core-service/.env
                          (default: <workspace>/maria-core-service/.env, see `_default_env_path`)
    MARIA_CORE_URL_<ENV>  core-service base URL used for writes to <ENV> (dev/stg/prod);
                          MARIA_CORE_URL is the fallback. There is no default on purpose:
                          writing to the wrong environment is the expensive mistake.
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

#: Supabase project refs. dev and stg are BRANCHES of maria-db, so they do not appear in
#: `supabase projects list`.
REFS = {
    "prod": "yavtkqacpiwaahzrejhk",
    "stg": "kvjdhbpkucvmnoczuoyy",
    "dev": "dfllbgtwfbfyiiayioeu",
}
PAGE = 1000


class FlowDBError(RuntimeError):
    """Something is missing or inconsistent: the caller must stop, not retry."""


def _default_env_path() -> str:
    """maria-core-service/.env next to the maria-voice checkout, wherever this runs from."""
    here = pathlib.Path.cwd().resolve()
    for base in (here, *here.parents):
        candidate = base / "maria-core-service" / ".env"
        if candidate.is_file():
            return str(candidate)
        sibling = base.parent / "maria-core-service" / ".env"
        if (base / "maria_voice").is_dir() and sibling.is_file():
            return str(sibling)
    return str(pathlib.Path.home() / "omniloy" / "dev" / "maria-core-service" / ".env")


ENV_PATH = os.environ.get("MARIA_CORE_ENV_PATH") or _default_env_path()


def load_keys(env_path: str = ENV_PATH) -> Dict[str, str]:
    """Service-role keys by project ref, commented lines included. Never log the values."""
    keys: Dict[str, str] = {}
    slots: Dict[str, str] = {}
    try:
        lines = open(env_path, encoding="utf-8").read().splitlines()
    except OSError as exc:
        raise FlowDBError(f"cannot read {env_path}: {exc} (set MARIA_CORE_ENV_PATH)") from exc
    for raw in lines:
        line = raw.strip().lstrip("#").strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if "SUPABASE_URL" in name and "supabase.co" in value:
            slots[name.replace("URL", "KEYSLOT")] = value.split("//")[1].split(".")[0]
        elif "SERVICE_ROLE" in name and value.startswith("eyJ"):
            slot = name.replace("SERVICE_ROLE_KEY", "KEYSLOT").replace("SERVICE_ROLE", "KEYSLOT")
            ref = slots.get(slot)
            if ref:
                keys[ref] = value
    return keys


class DB:
    """One environment, read-only over PostgREST."""

    def __init__(self, env: str, keys: Optional[Dict[str, str]] = None) -> None:
        if env not in REFS:
            raise FlowDBError(f"unknown environment {env!r}; known: {sorted(REFS)}")
        self.env, self.ref = env, REFS[env]
        key = (keys or load_keys()).get(self.ref)
        if not key:
            raise FlowDBError(
                f"no service-role key for {env} ({self.ref}) in {ENV_PATH}. "
                "For prod the line is normally commented out; it is read anyway."
            )
        self._key = key

    def get(self, path: str, timeout: int = 60) -> Any:
        req = urllib.request.Request(
            f"https://{self.ref}.supabase.co/rest/v1/{path}",
            headers={"apikey": self._key, "Authorization": f"Bearer {self._key}",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise FlowDBError(f"GET {path} -> {exc.code}: "
                              f"{exc.read().decode('utf-8', 'replace')[:400]}") from exc

    def select(self, table: str, *, columns: str = "*", filters: Iterable[str] = (),
               order: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
        parts = [f"select={columns}", *filters]
        if order:
            parts.append(f"order={order}")
        base = f"{table}?" + "&".join(parts)
        rows: List[dict] = []
        while True:
            size = PAGE if limit is None else min(PAGE, limit - len(rows))
            if size <= 0:
                break
            batch = self.get(f"{base}&limit={size}&offset={len(rows)}")
            rows.extend(batch)
            if len(batch) < size:
                break
        return rows

    # -- flows ---------------------------------------------------------------

    def flow(self, flow_id: str, columns: str = "*") -> dict:
        rows = self.select("conversation_flows", columns=columns, filters=[f"id=eq.{flow_id}"])
        if not rows:
            raise FlowDBError(f"flow {flow_id} does not exist in {self.env}")
        return rows[0]

    def active_flow_id(self, api_key_id: str) -> Optional[str]:
        """What the customer is actually served. Never trust the id quoted in a task."""
        rows = self.select("api_keys", columns="default_conversation_flow_id,voice_mode",
                           filters=[f"id=eq.{api_key_id}"])
        if not rows:
            raise FlowDBError(f"api_key {api_key_id} does not exist in {self.env}")
        return rows[0]["default_conversation_flow_id"]

    def collections(self, flow_id: str) -> List[str]:
        rows = self.select("conversation_flow_collections", columns="collection_id",
                           filters=[f"conversation_flow_id=eq.{flow_id}"])
        return [r["collection_id"] for r in rows]

    def health(self, flow_id: str) -> tuple[bool, str]:
        """AGENTS.md health query: version == max(version_number), active_version_id set."""
        f = self.flow(flow_id, columns="version,active_version_id")
        top = self.select("conversation_flows_versions", columns="version_number",
                          filters=[f"conversation_flow_id=eq.{flow_id}"],
                          order="version_number.desc", limit=1)
        highest = top[0]["version_number"] if top else None
        ok = bool(f["active_version_id"]) and f["version"] == highest
        return ok, (f"version={f['version']} max(version_number)={highest} "
                    f"active_version_id={'set' if f['active_version_id'] else 'NULL'}")


def core_url(env: str) -> str:
    url = os.environ.get(f"MARIA_CORE_URL_{env.upper()}") or os.environ.get("MARIA_CORE_URL")
    if not url:
        raise FlowDBError(
            f"set MARIA_CORE_URL_{env.upper()} (or MARIA_CORE_URL) to the core-service that "
            f"writes to {env}. There is no default: a write to the wrong database is the "
            "mistake this refuses to make for you."
        )
    return url.rstrip("/")


def put_flow(env: str, flow_id: str, api_key: str, *, flow_document: dict,
             change_description: str, change_type: str = "edit") -> dict:
    """Save a new version through core-service. The only supported way to edit a flow.

    `change_type` is CHECK-constrained: create | edit | promote_from_sandbox | activate |
    backfill. `X-User-Id: CLAUDE` is what lands in `created_by_user_id`.
    """
    allowed = {"create", "edit", "promote_from_sandbox", "activate", "backfill"}
    if change_type not in allowed:
        raise FlowDBError(f"change_type {change_type!r} violates the CHECK; use one of {allowed}")
    body = json.dumps({"flowDocument": flow_document, "changeDescription": change_description,
                       "changeType": change_type}).encode()
    req = urllib.request.Request(
        f"{core_url(env)}/api/v1/conversation-flows/{flow_id}", data=body, method="PUT",
        headers={"Content-Type": "application/json", "X-Api-Key": api_key, "X-User-Id": "CLAUDE"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise FlowDBError(f"PUT -> {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}") from exc


# -- document helpers shared by edit scripts, lint and promotion ------------------------

def node(doc: dict, node_id: str) -> dict:
    for n in doc.get("nodes", []):
        if n["node_id"] == node_id:
            return n
    raise FlowDBError(f"node {node_id} does not exist")


def edge(n: dict, edge_id: str) -> dict:
    for e in n.get("edges", []) or []:
        if e["edge_id"] == edge_id:
            return e
    raise FlowDBError(f"edge {edge_id} does not exist on {n['node_id']}")


def text(value: Any, lang: Optional[str] = None) -> str:
    """A localized field as plain text: the requested language, else the first one."""
    if isinstance(value, dict):
        if lang and value.get(lang):
            return value[lang]
        return next((v for v in value.values() if isinstance(v, str) and v), "")
    return value or ""


def set_text(n: dict, field: str, new: str, lang: str = "es") -> None:
    if isinstance(n.get(field), dict):
        n[field][lang] = new
    else:
        n[field] = new


def iter_webhooks(doc: dict):
    """Every HTTP definition in the document: `tools[]` AND each node's `prefetch_tools`.

    Prefetch tools live outside `tools[]`; a promotion that rewrites only `tools[]` leaves
    production calling the test backend on every transition into that node.
    """
    for t in doc.get("tools", []) or []:
        if t.get("url"):
            yield "tools[]", t
    for n in doc.get("nodes", []) or []:
        for t in n.get("prefetch_tools") or []:
            if t.get("url"):
                yield f"{n['node_id']}.prefetch_tools", t


def host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


def _core(env: str, method: str, path: str, api_key: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{core_url(env)}{path}", data=json.dumps(body).encode(), method=method,
        headers={"Content-Type": "application/json", "X-Api-Key": api_key, "X-User-Id": "CLAUDE"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise FlowDBError(f"{method} {path} -> {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}") from exc


def create_flow(env: str, api_key: str, *, name: str, description: str, flow_document: dict,
                default_language: str = "es", metadata: Optional[dict] = None,
                llm_config: Optional[dict] = None) -> dict:
    """POST /api/v1/conversation-flows: a new flow with its version-1 row (changeType create).
    The tenant is the one the api key belongs to."""
    body = {"name": name, "description": description, "flowDocument": flow_document,
            "defaultLanguage": default_language, "metadata": metadata or {}}
    if llm_config:
        body["llmConfig"] = llm_config
    return _core(env, "POST", "/api/v1/conversation-flows", api_key, body)


def set_collections(env: str, api_key: str, flow_id: str, collection_ids: List[str]) -> dict:
    """PUT /:id/collections — REPLACES the set of KB collections linked to the flow."""
    return _core(env, "PUT", f"/api/v1/conversation-flows/{flow_id}/collections", api_key,
                 {"collection_ids": collection_ids})
