#!/usr/bin/env python3
"""L3: la capa semántica. Prepara el material para el juez y recoge sus veredictos.

Las capas L1 y L2 se deciden con aserciones sobre lo que la telemetría registró.
L3 no: «¿la conversación fue buena, dado que todo lo de abajo funcionó?» exige
leer lo que se dijo. Este módulo no juzga — **empaqueta** cada llamada con todo
lo que hace falta para juzgarla y luego **recoge** los veredictos, para que el
motor del juez sea intercambiable (hoy subagentes de Claude, mañana un modelo
por API) sin tocar el resto del pipeline.

Dos cosas que este diseño protege:

* **El juez no ve los veredictos deterministas de la llamada.** Si le dices «el
  pipeline cree que aquí falló la identificación», confirma. Se le da la
  conversación y el resultado, no la conclusión.
* **La cola tiene presupuesto y un suelo de muestreo aleatorio**
  (`run_detectors.build_judge_queue`): sin ese suelo, la cola se llena de
  llamadas que ya venían marcadas y el juez nunca mira lo que el pipeline
  considera sano — que es como un punto ciego de los detectores se vuelve
  permanente.

Uso:
    python3 judge_l3.py pack   findings.json --out /tmp/l3   # + residuo
    python3 judge_l3.py collect /tmp/l3 --findings findings.json --out l3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from _supabase import Supabase  # noqa: E402

#: Lo que se le pide al juez, en el orden en que debe pensarlo. Vive aquí y no
#: en el prompt de cada lote para que dos ejecuciones sean comparables.
RUBRIC = """Eres un revisor de calidad de llamadas de un asistente de voz sanitario.

Lees UNA llamada: para qué llamaba el paciente, qué se dijo y cómo acabó. Las
capas técnicas (transporte, voz, máquina de estados) ya se comprobaron aparte y
salieron bien, o el caso no llegaría a ti. Tu pregunta es la siguiente: **dado
que la máquina funcionó, ¿la conversación resolvió lo que el paciente quería?**

Responde SOLO con un JSON con esta forma:

{
  "resolvio": "si" | "no" | "parcial" | "no_aplica",
  "de_quien": "agente" | "paciente" | "dependencia" | "diseno_del_flujo" | "ninguno",
  "que_paso": "<una frase, concreta, citando lo que se dijo si ayuda>",
  "senales": ["<etiqueta corta>", ...],
  "cita": "<la intervención que mejor lo demuestra, literal y recortada>",
  "confianza": "alta" | "media" | "baja"
}

Reglas que evitan los errores típicos de un juez:

1. **No premies la cortesía.** Una despedida impecable sobre un objetivo sin
   cumplir es un `no`.
2. **No castigues lo que no es del agente.** Si el paciente colgó, cambió de
   tema o dio datos que no existen, `de_quien` es "paciente" y eso NO es un
   fallo nuestro. Si SINA no tenía huecos, es "dependencia".
3. **Distingue «no pudo» de «no supo».** Que no haya cita disponible es un
   resultado legítimo; que el agente no encuentre información que sí está en su
   documentación, no.
4. **`diseno_del_flujo`** es para cuando el agente hace exactamente lo que se le
   pidió y el resultado es malo de todas formas: el flujo no contempla el caso.
   Es el hallazgo más valioso y el que más se pasa por alto.
5. **Transferir no es fallar.** En varios clientes transferir ES el servicio. Si
   el paciente pidió una persona, o el caso está fuera de lo que el agente puede
   hacer, transferir es un `si`.
6. En `senales` usa SOLO etiquetas de esta lista cerrada, las que apliquen:

   - `transferencia_por_diseno` — se transfiere porque el asistente no gestiona
     eso, o porque el paciente pidió una persona. Es correcto.
   - `cierre_sin_salida` — política correcta y luego se cierra **sin** ofrecer
     transferencia ni un siguiente paso concreto.
   - `derivada_a_telefono_externo` — política correcta y SÍ se da una salida
     concreta (un teléfono del centro). No es lo mismo que
     `cierre_sin_salida`: aquí el paciente se va con algo que hacer, y por eso
     no es un fallo aunque no se resolviera en la llamada.
   - `llamada_saliente_sin_contexto` — el paciente devuelve una llamada del
     centro y no hay forma de saber por qué le llamaron.
   - `centro_no_reconocido` — se ofrece algo en un centro y luego resulta que
     ahí no se puede, o el centro no se identifica.
   - `servicio_descatalogado` — el servicio ya no se presta en ese sitio.
   - `agente_se_contradice` — cambia de versión al ser corregido.
   - `dato_asumido_sin_confirmar` — da por bueno un dato que no confirmó.
   - `sin_huecos` — la agenda no tenía disponibilidad. Es del proveedor.
   - `urgencia_no_reconocida` — se describe un síntoma que pide atención pronta
     y se trata como una gestión cualquiera.
   - `faq_resuelta` — se preguntó algo de la documentación y se respondió con
     un dato útil.
   - `consulta_resuelta` — se consultó un dato propio del paciente (sus citas,
     su historial) y se le dio. Distinta de `faq_resuelta`: esa es
     documentación, esta es la ficha.
   - `gestion_completada` — se cerró lo que el paciente venía a hacer (cita
     reservada, anulada, cambiada). La etiqueta de éxito completo.
   - `dependencia_deniega_operacion` — el backend rechaza una operación que el
     agente pidió bien (una anulación no autorizada, por ejemplo). No es
     `sin_huecos` ni fallo del agente.
   - `agente_no_puede_consultar_agenda` — el agente dice que no puede comprobar
     la disponibilidad de un profesional concreto.
   - `detalle_de_cita_no_soportado` — el paciente quiere un dato de su propia
     cita (la hora, el sitio) y el canal no lo contempla.
   - `menor_sin_documento` — la cita es para un menor sin DNI/NIE/pasaporte y la
     verificación no tiene alternativa.
   - `paciente_abandona` — cuelga o cambia de tema antes de resolver.

   Si de verdad ninguna encaja, usa `otro:<descripción en tres palabras>`. La
   lista es cerrada a propósito: en la primera pasada se dejó abierta y salieron
   176 etiquetas distintas en dos idiomas para 77 llamadas, con lo que ninguna
   se podía contar. Una taxonomía que no se puede contar no es una taxonomía.

   Las cinco últimas etiquetas se añadieron porque los jueces de la segunda
   pasada tuvieron que usar `otro:` para ellas: faltaba una etiqueta de éxito,
   faltaba distinguir la documentación de la ficha del paciente, y faltaban dos
   incapacidades concretas. Un `otro:` recurrente es una etiqueta que pide
   existir; si vuelve a pasar, se añade igual.
7. Si te falta información para decidir, `confianza: "baja"` y dilo en
   `que_paso`. Es infinitamente mejor que adivinar.
"""


def fetch_transcripts(call_ids: List[str], env: str = "prod") -> Dict[str, list]:
    """Transcripciones por `call_id`, en trozos para no pasarse con la URL."""
    db = Supabase(env)
    out: Dict[str, list] = {}
    for i in range(0, len(call_ids), 40):
        chunk = call_ids[i : i + 40]
        query = urllib.parse.urlencode(
            {"select": "id,transcription", "id": f"in.({','.join(chunk)})"}
        )
        for row in db.get(f"calls?{query}") or []:
            out[row["id"]] = row.get("transcription") or []
    return out


def pack(findings_path: str, out_dir: str, bundles_path: Optional[str] = None) -> int:
    payload = json.load(open(findings_path))
    queue = {q["call_id"]: q.get("reasons", []) for q in payload.get("judge_queue", [])}
    # El residuo entra siempre: es una llamada que acabó mal y que NINGÚN código
    # explica, o sea el sitio exacto donde el catálogo se queda corto.
    for item in payload.get("residue", []):
        queue.setdefault(item["call_id"], []).append("residuo_sin_codigo")

    bundles = {}
    if bundles_path:
        bundles = {
            b["call_id"]: b for b in json.load(open(bundles_path)).get("bundles", [])
        }

    transcripts = fetch_transcripts(sorted(queue))
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "RUBRICA.md").write_text(RUBRIC)

    written = 0
    for call_id, reasons in sorted(queue.items()):
        turns = transcripts.get(call_id) or []
        if not turns:
            continue  # sin transcripción no hay nada que juzgar
        bundle = bundles.get(call_id, {})
        (directory / f"{call_id}.json").write_text(
            json.dumps(
                {
                    "call_id": call_id,
                    "por_que_esta_en_la_cola": reasons,
                    "cliente": bundle.get("tenant"),
                    "para_que_llamaba": bundle.get("user_intent"),
                    "como_acabo": bundle.get("call_result"),
                    "estado": bundle.get("status"),
                    "duracion_s": bundle.get("duration_s"),
                    "tools_usadas": [t["name"] for t in bundle.get("tools", [])],
                    "conversacion": [
                        {"quien": t.get("speaker"), "dijo": t.get("content")}
                        for t in turns
                    ],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        written += 1

    print(f"  empaquetadas {written} llamadas de {len(queue)} en la cola")
    print(f"  sin transcripción (no juzgables): {len(queue) - written}")
    print(f"  rúbrica: {directory / 'RUBRICA.md'}")
    print(f"  destino: {directory}")
    return 0


def collect(pack_dir: str, out_path: str, findings_path: Optional[str] = None) -> int:
    """Lee los `*.verdict.json` y los deja en un solo fichero con su resumen."""
    from collections import Counter

    directory = Path(pack_dir)
    verdicts = []
    for path in sorted(directory.glob("*.verdict.json")):
        try:
            verdict = json.load(open(path))
        except json.JSONDecodeError as exc:
            print(f"  aviso: {path.name} no es JSON válido ({exc})")
            continue
        verdict.setdefault("call_id", path.name.replace(".verdict.json", ""))
        verdicts.append(verdict)

    print(f"  {len(verdicts)} veredictos leídos")
    if verdicts:
        print("  resolvió:", dict(Counter(v.get("resolvio") for v in verdicts)))
        print("  de quién:", dict(Counter(v.get("de_quien") for v in verdicts)))
        print("  confianza:", dict(Counter(v.get("confianza") for v in verdicts)))
        senales = Counter(s for v in verdicts for s in (v.get("senales") or []))
        print(f"  señales distintas: {len(senales)}")
        for tag, n in senales.most_common(15):
            print(f"     {n:3} {tag}")

    meta = {}
    if findings_path:
        meta = json.load(open(findings_path)).get("meta", {})
    json.dump(
        {"meta": {**meta, "stage": "l3"}, "verdicts": verdicts},
        open(out_path, "w"),
        ensure_ascii=False,
        indent=1,
    )
    print(f"  escrito: {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("findings")
    p.add_argument("--bundles")
    p.add_argument("--out", required=True)
    c = sub.add_parser("collect")
    c.add_argument("pack_dir")
    c.add_argument("--findings")
    c.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.cmd == "pack":
        return pack(args.findings, args.out, args.bundles)
    return collect(args.pack_dir, args.out, args.findings)


if __name__ == "__main__":
    raise SystemExit(main())
