#!/usr/bin/env python3
"""Coverage and drift between a workflow's pytest suite and its eval scenarios. Offline.

    python3 eval_plan.py --suite san_roque_citacion [--repo <maria-voice>] [--env prod]

Reads `tests/integration/customers/_evals/scenarios_<suite>.py` (without importing
maria-voice) and the test files it declares (`TEST_FILES`, default `test_<suite>.py`), and
reports:

  * each scenario, the tests it cites, and whether each exists;
  * the suite's tests that are neither covered by a scenario nor listed in DROPPED:
    decide each one (see references/scenario-design.md §1-§2);
  * drift: scenarios whose persona/evaluators/test_config or cited test source changed
    since the last bootstrap. The hash is computed exactly as bootstrap.py does, and
    compared with `evals_manifest_<env>.json`.

Nothing is sent anywhere. Exit status 1 when a scenario cites a test that does not exist.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import pathlib
import signal
import sys

signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # piping into head is normal


def find_repo(start: pathlib.Path) -> pathlib.Path:
    for base in (start, *start.parents):
        if (base / "maria_voice" / "assistant" / "session.py").is_file():
            return base
    fallback = pathlib.Path.home() / "omniloy" / "dev" / "maria-voice"
    if (fallback / "maria_voice").is_dir():
        return fallback
    sys.exit("✘ maria-voice checkout not found; pass --repo")


def load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sources(files: list[pathlib.Path]) -> dict[str, str]:
    """Same keys as bootstrap._load_test_sources_from: bare name and <file>::<name>."""
    out: dict[str, str] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        tree = ast.parse(text, filename=str(f))

        def add(key, node):
            src = "".join(lines[node.lineno - 1: node.end_lineno])
            out.setdefault(key, src)
            out[f"{f.name}::{key}"] = src
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                add(node.name, node)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                        add(f"{node.name}::{item.name}", item)
    return out


def scenario_hash(scenario: dict, sources: dict[str, str]) -> str:
    payload = {
        "slug": scenario["slug"],
        "sources": {n: sources.get(n, "") for n in scenario.get("source_tests", [])},
        "persona": scenario.get("persona"),
        "evaluators": scenario.get("evaluators"),
        "test_config": scenario.get("test_config"),
        "reuse_test_config_id": scenario.get("reuse_test_config_id"),
    }
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--env", default="prod", help="platform env of the manifest to compare (default prod)")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve() if a.repo else find_repo(pathlib.Path.cwd().resolve())
    cust = repo / "tests" / "integration" / "customers"
    mod_path = cust / "_evals" / f"scenarios_{a.suite}.py"
    if not mod_path.exists():
        sys.exit(f"✘ {mod_path} does not exist: generate it (SKILL.md, phase 2)")
    mod = load_module(mod_path)
    files = [cust / n for n in getattr(mod, "TEST_FILES", [f"test_{a.suite}.py"])]
    missing_files = [f.name for f in files if not f.exists()]
    if missing_files:
        sys.exit(f"✘ test files not found: {missing_files}")
    sources = test_sources(files)
    bare = {k for k in sources if not k.split("::")[0].endswith(".py")}
    scenarios, dropped = mod.SCENARIOS, getattr(mod, "DROPPED", [])
    manifest_path = cust / f"evals_manifest_{a.env}.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    recorded = ((manifest.get("suites") or {}).get(a.suite) or {}).get("scenarios") or {}

    print(f"# {a.suite}: {len(scenarios)} scenarios, {len(dropped)} dropped, "
          f"{len(bare)} tests in {[f.name for f in files]}\n")
    bad = 0
    cited: set[str] = set()
    for s in scenarios:
        h = scenario_hash(s, sources)
        state = ("not bootstrapped" if s["slug"] not in recorded else
                 "in sync" if recorded[s["slug"]].get("source_excerpt_hash") == h else "DRIFTED → re-bootstrap")
        print(f"- {s['slug']}  [{state}]")
        for t in s.get("source_tests", []):
            ok = t in sources
            bad += not ok
            head, _, rest = t.partition("::")
            cited.add(rest if head.endswith(".py") else t)
            print(f"    {'✔' if ok else '✘ MISSING'} {t}")
    dropped_names = {t for d in dropped for t in d.get("source_tests", [])}
    undecided = sorted(bare - cited - dropped_names)
    print(f"\n## {len(undecided)} test(s) neither evaluated nor dropped")
    for t in undecided:
        print(f"    ? {t}")
    orphans = sorted(set(recorded) - {s["slug"] for s in scenarios})
    if orphans:
        print(f"\n## in the {a.env} manifest but no longer in the module (resources left on the platform): {orphans}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
