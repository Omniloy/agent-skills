#!/usr/bin/env python3
"""Detectores de VENTANA: lo que no se ve mirando una llamada.

Los detectores L1/L2 son por llamada, y hay causas que por definición no caben
ahí. Que 96 llamadas de HCB fallaran al identificar el 25 de agosto no era mala
suerte de 96 pacientes: era **una** dependencia mal configurada, y eso solo se
ve comparando la ventana entera.

Lo que la ventana puede decidir sola es el **síntoma**: esta tool de este
cliente lleva un rato fallando de forma sostenida y no es mala suerte. Lo que
NO puede decidir es de quién es el fallo. La tentación era compararlo con otro
cliente —«si a Premium le va bien, el proveedor está bien y lo que está mal es
la configuración de HCB»—, y es inválido: **cada cliente tiene su propio
despliegue de SINA**, con host y mecanismo de autenticación distintos
(`sinasuite.clinicabenidorm.com` con password grant vs `sina.premiumhs.es` con
ticket). Son servicios diferentes; que uno esté sano no dice nada del otro.

El dueño lo decide el log de maria-core, con el código HTTP de la
autenticación contra el despliegue **propio** del cliente: 4xx son nuestras
credenciales (`L1-HIS-001`), 5xx o timeout es su servicio (`L1-HIS-002`). Sin
log que cubra la ventana, el código es `L1-HIS-003` y el dueño queda
`unattributed` — que es la respuesta honesta, no un hueco que rellenar a ojo.

Los hallazgos se emiten sobre las llamadas afectadas (para que el triaje siga
teniendo sus enlaces), pero el código dice «dependencia», no «tool con error».
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from _findings import finding, load_bundles, summarize

#: Tools que hablan con el HIS. Una tool local que falle no dice nada del
#: proveedor, así que la lista es explícita en vez de «todas».
HIS_TOOLS = {
    "login_and_onboard",
    "start_scheduling_process",
    "schedule_appointment",
    "get_available_dates",
    "get_scheduled_appointments",
    "select_appointment_to_reschedule",
    "cancel_appointment",
    "get_professionals",
    "save_center",
    "save_service",
    "save_professional",
    "save_insurance_company",
    "save_benefit",
}

#: Umbrales, calibrados con datos reales (ver `L1-HIS-001` en el catálogo): la
#: línea base de un día normal es 0% de error, y el día del incidente fue 63%
#: para el cliente afectado y 0% para el otro. Con esa separación, 10% distingue
#: sin lugar a dudas, y 2% deja margen a un fallo puntual en el cliente "sano".
ERROR_RATE_TRIGGER = 0.10
MIN_TOOL_CALLS = 10

#: Cómo se traduce lo que dice el log al dueño del fallo.
VERDICT_CODES = {
    "credentials": "L1-HIS-001",   # 4xx autenticando: configuración nuestra
    "provider": "L1-HIS-002",      # 5xx / timeout: su servicio
    None: "L1-HIS-003",            # sin log en la ventana: sin determinar
}


def _rates(bundles: List[dict]) -> Dict[tuple, Counter]:
    """Éxitos y errores por (cliente, tool) en la ventana."""
    rates: Dict[tuple, Counter] = defaultdict(Counter)
    for bundle in bundles:
        for tool in bundle["tools"]:
            if tool["name"] in HIS_TOOLS:
                rates[(bundle["tenant"], tool["name"])][tool["status"]] += 1
    return rates


def detect(bundles: List[dict], deps: Optional[Dict[str, dict]] = None) -> List[dict]:
    """Hallazgos de ventana.

    `deps` es lo que el log de maria-core dice de cada cliente, por nombre:
    `{"HCB": {"verdict": "credentials", "evidence": "...", "at": "..."}}`, tal y
    como lo devuelve `parse_core_log.dependency_verdicts`. Si no se pasa, el
    síntoma se reporta sin atribuir.
    """
    rates = _rates(bundles)
    deps = deps or {}
    findings: List[dict] = []

    for (tenant, tool), counts in rates.items():
        total = sum(counts.values())
        if total < MIN_TOOL_CALLS:
            continue
        rate = counts["error"] / total
        if rate < ERROR_RATE_TRIGGER:
            continue

        dep = deps.get(tenant) or {}
        code = VERDICT_CODES[dep.get("verdict")]

        # Se conserva la tasa de los demás clientes: ya no decide el dueño, pero
        # sigue diciendo si el incidente es de uno o de todos, que es lo primero
        # que se quiere saber al abrir el informe.
        others = {
            other_tenant: (c["error"] / max(1, sum(c.values())))
            for (other_tenant, other_tool), c in rates.items()
            if other_tool == tool and other_tenant != tenant and sum(c.values()) >= MIN_TOOL_CALLS
        }

        affected = [
            b for b in bundles
            if b["tenant"] == tenant and any(
                t["name"] == tool and t["status"] == "error" for t in b["tools"]
            )
        ]
        times = sorted(t["at"] for b in affected for t in b["tools"]
                       if t["name"] == tool and t["status"] == "error")
        message = next((t["error"] for b in affected for t in b["tools"]
                        if t["name"] == tool and t["status"] == "error" and t["error"]), None)
        for bundle in affected:
            findings.append(
                finding(
                    bundle,
                    code,
                    evidence={
                        "tool": tool,
                        "tasa_de_error": round(rate, 3),
                        "llamadas_a_la_tool": total,
                        "errores": counts["error"],
                        "desde": times[0] if times else None,
                        "hasta": times[-1] if times else None,
                        "otros_clientes_con_la_misma_tool": {t: round(r, 3) for t, r in others.items()},
                        "mensaje": (message or "")[:200] or None,
                        "veredicto_del_log": dep.get("verdict") or "sin log en la ventana",
                        "evidencia_del_log": dep.get("evidence"),
                    },
                    detected_by="window_error_rate",
                )
            )
    return findings


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    payload = load_bundles(sys.argv[1])
    findings = detect(payload["bundles"])
    print(f"== ventana {payload['meta']['env']} "
          f"{payload['meta']['since']} → {payload['meta']['until']} ==")
    summarize(findings, len(payload["bundles"]))
    if findings:
        first = findings[0]["evidence"]
        print(f"  detalle: tool={first['tool']} tasa={first['tasa_de_error']:.0%} "
              f"({first['errores']}/{first['llamadas_a_la_tool']}) "
              f"otros={first['otros_clientes_con_la_misma_tool']}")
        print(f"  dueño según el log: {first['veredicto_del_log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
