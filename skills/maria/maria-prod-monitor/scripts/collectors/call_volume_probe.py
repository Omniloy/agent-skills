#!/usr/bin/env python3
"""Read-only probe: shape of `calls` table + daily call volume per company.

Reads Supabase service-role keys from maria-core-service/.env (never prints them).
GET-only requests against PostgREST.
"""
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

from _paths import CORE_ENV  # noqa: E402
ENV_PATH = str(CORE_ENV)

REFS = {
    "prod": "yavtkqacpiwaahzrejhk",
    "stg": "kvjdhbpkucvmnoczuoyy",
}


def load_keys():
    """Collect every service-role key in the .env (commented or not), keyed by ref."""
    keys = {}
    current = {}
    for raw in open(ENV_PATH):
        line = raw.strip().lstrip("#")
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"')
        if "SUPABASE_URL" in name and "supabase.co" in value:
            ref = value.split("//")[1].split(".")[0]
            current[name.replace("URL", "KEYSLOT")] = ref
        if "SERVICE_ROLE_KEY" in name and value.startswith("eyJ"):
            slot = name.replace("SERVICE_ROLE_KEY", "KEYSLOT")
            ref = current.get(slot)
            if ref:
                keys[ref] = value
    return keys


def get(ref, key, path):
    url = f"https://{ref}.supabase.co/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_all(ref, key, base_path, page=1000, max_rows=50000):
    """Paginate a GET (PostgREST caps rows per request)."""
    rows = []
    offset = 0
    while offset < max_rows:
        batch = get(ref, key, f"{base_path}&limit={page}&offset={offset}")
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def looks_like_eval(room_name):
    rn = (room_name or "").lower()
    return "ai_generated" in rn or rn.startswith("test-") or "playground" in rn


def tenant_names(ref, key):
    """api_keys IS the companies table; find a name-ish column without touching secrets."""
    for col in ("name", "company_name", "client_name", "description"):
        try:
            rows = get(ref, key, f"api_keys?select=id,{col}&limit=200")
            return {r["id"]: r.get(col) for r in rows}
        except Exception:  # noqa: BLE001
            continue
    return {}


def main():
    keys = load_keys()
    # URL-safe UTC timestamp: 'Z' suffix, no '+' (a '+' in a query string becomes a space -> 400)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for env, ref in REFS.items():
        key = keys.get(ref)
        if not key:
            print(f"== {env} ({ref}): NO KEY FOUND in .env ==")
            continue
        print(f"== {env} ({ref}) ==")
        names = tenant_names(ref, key)
        cols = "id,created_at,api_key_id,room_name,call_direction,status"
        try:
            rows = fetch_all(ref, key, f"calls?select={cols}&created_at=gte.{since}")
        except Exception as e:  # noqa: BLE001
            print(f"  volume query failed: {e}")
            continue

        real = [r for r in rows if not looks_like_eval(r.get("room_name"))]
        evalish = len(rows) - len(real)
        print(f"  total last 7d: {len(rows)} ({evalish} look like evals/test by room_name; excluded below)")
        print("  per day:", dict(sorted(Counter(r["created_at"][:10] for r in real).items())))

        def label(ak):
            n = names.get(ak)
            return f"{n} ({ak[:8]})" if n else ak

        by_tenant = Counter(label(r["api_key_id"]) for r in real)
        print("  per tenant (real):", dict(by_tenant.most_common(15)))
        by_tenant_day = Counter((label(r["api_key_id"]), r["created_at"][:10]) for r in real)
        per_tenant_daily = {}
        for (tenant, day), n in sorted(by_tenant_day.items()):
            per_tenant_daily.setdefault(tenant, {})[day] = n
        for tenant, days in per_tenant_daily.items():
            print(f"    {tenant}: {days}")
        by_status = Counter(r["status"] for r in real)
        print("  status (real):", dict(by_status.most_common()))


if __name__ == "__main__":
    main()
