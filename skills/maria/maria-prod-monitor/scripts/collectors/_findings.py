#!/usr/bin/env python3
"""The failure record, the catalog it must agree with, and shared plumbing.

The record shape is the one fixed in `hierarchical_taxonomy.md` §3
(`conversation_id`, `verdict`, `layer`, `code`, `severity`, `detected_by`,
`evidence`, `aborted_layers`, `owner`) — with the code format of the workplan
§2.2 (`L<layer>-<DOMAIN>-<NNN>`).

Codes live in `catalog.yaml`, not in the detectors: every finding is validated
against it, so a detector cannot invent a code and no code can quietly change
meaning. A detector that emits an unknown id is a hard error, not a warning.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import yaml

from _paths import CATALOG  # noqa: E402
CATALOG_PATH = str(CATALOG)

SEVERITIES = ("low", "medium", "high", "critical")


class Catalog:
    """`catalog.yaml`, indexed by code id."""

    def __init__(self, path: str = CATALOG_PATH) -> None:
        with open(path) as handle:
            raw = yaml.safe_load(handle)
        self.version: str = raw["version"]
        self.codes: Dict[str, dict] = {c["id"]: c for c in raw["codes"]}

    def entry(self, code: str) -> dict:
        try:
            return self.codes[code]
        except KeyError:
            raise KeyError(
                f"{code} is not in catalog.yaml. Add it there (append-only) "
                "before a detector emits it."
            ) from None

    def of_layer(self, layer: str) -> List[dict]:
        return [c for c in self.codes.values() if c["layer"] == layer]


_CATALOG: Optional[Catalog] = None


def catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG


def finding(
    bundle: dict,
    code: str,
    *,
    evidence: Dict[str, Any],
    severity: Optional[str] = None,
    detected_by: str = "db_trace_assert",
    owner: Optional[str] = None,
) -> dict:
    """One failure record, with everything the catalog already knows filled in.

    `severity` is the only field a detector normally overrides: layer and owner
    are properties of the code, severity is a property of the occurrence
    (impact × recoverability — doc §4).
    """
    entry = catalog().entry(code)
    if entry.get("status") == "deprecated":
        raise ValueError(
            f"{code} está deprecado (sustituido por {entry.get('replaced_by')}): "
            "sigue resoluble para leer el histórico, pero no se emite. "
            f"Motivo: {(entry.get('deprecation_reason') or '').strip()[:200]}"
        )
    if severity is not None and severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    verdict = entry.get("verdict", "FAIL")
    layer = entry["layer"]
    # An L1 verdict stops L2 and L3 (doc §3, early exit). An INVALID call is
    # not an agent failure, so L2 still runs on it — only the semantic judge is
    # pointless.
    # Un código puede ser un fallo real y aun así no invalidar lo de arriba
    # (ver `blocks_upper` en el catálogo).
    blocks_upper = entry.get("blocks_upper", True)
    if verdict == "FAIL" and not blocks_upper:
        aborted = []
    elif verdict == "EXPECTED":
        # El resultado es el correcto dado el dato: no invalida nada y no
        # bloquea el juez (la pregunta «lo gestionó bien» sigue en pie).
        aborted = []
    elif verdict == "INVALID":
        aborted = ["L3"]
    elif layer == "L1":
        aborted = ["L2", "L3"]
    elif layer == "L2":
        aborted = ["L3"]
    else:
        aborted = []
    return {
        "conversation_id": bundle["call_id"],
        "blocks_upper": blocks_upper,
        "tenant": bundle.get("tenant"),
        "verdict": verdict,
        "layer": layer,
        "code": code,
        "title": entry["title"],
        "severity": severity or entry["severity_default"],
        "detected_by": detected_by,
        "evidence": evidence,
        "aborted_layers": aborted,
        "owner": owner or entry["owner"],
        "catalog_version": catalog().version,
    }


# -- io -------------------------------------------------------------------


def load_bundles(path: str) -> dict:
    with open(path) as handle:
        payload = json.load(handle)
    if "bundles" not in payload:
        raise ValueError(f"{path} does not look like a bundles file")
    return payload


def severity_rank(finding_: dict) -> int:
    return SEVERITIES.index(finding_["severity"])


def is_actionable(finding_: dict) -> bool:
    """Si hay algo que arreglar en nuestro lado.

    `EXPECTED` significa que el resultado es el correcto dado el dato que había
    (el paciente llama desde un teléfono que no está en su ficha y este HIS no
    puede identificar sin él), e `INVALID` que la llamada no dice nada del
    agente. Ninguno de los dos genera tarea; se cuentan aparte para que su
    volumen siga siendo visible.
    """
    return finding_.get("verdict", "FAIL") == "FAIL"


def primary(findings: List[dict]) -> Optional[dict]:
    """The one code a call is filed under: the first failure in the stack.

    Attribution rule (Hamel/Shankar, workplan §2.1): a failure upstream
    invalidates the layers above it, so the lowest layer wins, and within a
    layer the worst severity.
    """
    if not findings:
        return None
    return sorted(
        findings, key=lambda f: (f["layer"], -severity_rank(f), f["code"])
    )[0]


def summarize(findings: List[dict], total_calls: int) -> None:
    from collections import Counter

    print(f"  findings: {len(findings)} over {total_calls} calls")
    if not findings:
        return
    by_code = Counter((f["layer"], f["code"], f["title"]) for f in findings)
    width = max(len(c) for _, c, _ in by_code)
    for (layer, code, title), n in sorted(by_code.items(), key=lambda kv: -kv[1]):
        calls = len({f["conversation_id"] for f in findings if f["code"] == code})
        print(f"    {code:<{width}}  n={n:<4} calls={calls:<4} {title}")
    print("  by layer:", dict(Counter(f["layer"] for f in findings)))
    print("  by severity:", dict(Counter(f["severity"] for f in findings)))
    print("  by owner:", dict(Counter(f["owner"] for f in findings)))
