#!/usr/bin/env python3
"""Read-only Supabase (PostgREST) access shared by every collector.

Three hard-won rules live here so no collector has to rediscover them:

1. **Keys are never printed and never passed on a command line.** They are read
   from `maria-core-service/.env` (where prod's are kept commented out), which
   also means no collector needs a secret of its own.
2. **Timestamps go out as `...Z`, never `+00:00`.** A `+` in a query string is
   decoded as a space and PostgREST answers 400.
3. **Everything is paginated.** PostgREST caps rows per response, so a window
   with 3k calls silently truncates without an offset loop.

GET only. Nothing in this package may write to Supabase.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from _paths import CORE_ENV  # noqa: E402  (MARIA_CORE_ENV_PATH overrides it)

ENV_PATH = str(CORE_ENV)

# Supabase project refs. dev/stg are branches of maria-db, so they do not show
# up in `supabase projects list`.
REFS = {
    "prod": "yavtkqacpiwaahzrejhk",
    "stg": "kvjdhbpkucvmnoczuoyy",
    "dev": "dfllbgtwfbfyiiayioeu",
}

PAGE = 1000


class AccessError(RuntimeError):
    """Something is missing in the environment, not in the data."""


def load_keys(env_path: str = ENV_PATH) -> Dict[str, str]:
    """Service-role keys by project ref, from the .env (commented lines included).

    The file pairs a `*SUPABASE_URL` with a `*SERVICE_ROLE_KEY` per environment;
    the ref in the URL is what tells us which key belongs to which project. The
    values are returned, never logged — callers must keep them out of output.
    """
    keys: Dict[str, str] = {}
    slots: Dict[str, str] = {}
    try:
        lines = open(env_path).read().splitlines()
    except OSError as exc:
        raise AccessError(f"cannot read {env_path}: {exc}") from exc

    for raw in lines:
        line = raw.strip().lstrip("#").strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if "SUPABASE_URL" in name and "supabase.co" in value:
            ref = value.split("//")[1].split(".")[0]
            slots[name.replace("URL", "KEYSLOT")] = ref
        elif "SERVICE_ROLE_KEY" in name and value.startswith("eyJ"):
            ref = slots.get(name.replace("SERVICE_ROLE_KEY", "KEYSLOT"))
            if ref:
                keys[ref] = value
    return keys


class Supabase:
    """One environment's PostgREST endpoint, read-only."""

    def __init__(self, env: str, keys: Optional[Dict[str, str]] = None) -> None:
        if env not in REFS:
            raise AccessError(f"unknown environment {env!r}; known: {sorted(REFS)}")
        self.env = env
        self.ref = REFS[env]
        key = (keys or load_keys()).get(self.ref)
        if not key:
            raise AccessError(
                f"no service-role key for {env} ({self.ref}) in {ENV_PATH}. "
                "For prod the line is normally commented out — uncomment it."
            )
        self._key = key

    # -- plumbing ---------------------------------------------------------

    def get(self, path: str, timeout: int = 60) -> Any:
        url = f"https://{self.ref}.supabase.co/rest/v1/{path}"
        req = urllib.request.Request(
            url,
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:500]
            # The path can carry patient-free filters only, so it is safe to
            # show; the key never appears in it.
            raise AccessError(f"GET {path} -> {exc.code}: {body}") from exc

    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: Iterable[str] = (),
        order: Optional[str] = None,
        limit: Optional[int] = None,
        page: int = PAGE,
    ) -> List[dict]:
        """A paginated select. `filters` are raw PostgREST predicates."""
        parts = [f"select={columns}", *filters]
        if order:
            parts.append(f"order={order}")
        base = f"{table}?" + "&".join(parts)

        rows: List[dict] = []
        offset = 0
        while True:
            size = page if limit is None else min(page, limit - len(rows))
            if size <= 0:
                break
            batch = self.get(f"{base}&limit={size}&offset={offset}")
            if not isinstance(batch, list):
                raise AccessError(f"unexpected payload for {table}: {type(batch)}")
            rows.extend(batch)
            if len(batch) < size:
                break
            offset += size
        return rows

    def in_list(self, column: str, values: Iterable[str]) -> str:
        """`col=in.(a,b,c)`, quoted so ids with odd characters survive."""
        quoted = ",".join('"' + str(v).replace('"', '') + '"' for v in values)
        return f"{column}=in.({quoted})"

    def select_by_chunks(
        self,
        table: str,
        *,
        column: str,
        values: List[str],
        columns: str = "*",
        filters: Iterable[str] = (),
        order: Optional[str] = None,
        chunk: int = 60,
    ) -> List[dict]:
        """Select rows for many ids without building a URL nobody accepts.

        60 uuids per `in.(...)` keeps the query string well under the limits a
        proxy will impose, and one window of calls is a handful of chunks.
        """
        rows: List[dict] = []
        for start in range(0, len(values), chunk):
            slice_ = values[start : start + chunk]
            if not slice_:
                continue
            rows.extend(
                self.select(
                    table,
                    columns=columns,
                    filters=[*filters, self.in_list(column, slice_)],
                    order=order,
                )
            )
        return rows


# -- time -----------------------------------------------------------------


def z(moment: datetime) -> str:
    """PostgREST-safe UTC timestamp. Never `+00:00` (see module docstring)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(hours: float = 24.0, until: Optional[datetime] = None) -> tuple[str, str]:
    """`(since, until)` as `Z` strings, `until` exclusive."""
    end = until or datetime.now(timezone.utc)
    return z(end - timedelta(hours=hours)), z(end)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """PostgREST timestamps back to aware datetimes (they arrive with `+00:00`)."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
