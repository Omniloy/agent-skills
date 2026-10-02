#!/usr/bin/env bash
# Una pasada completa del monitor de producción (MAR-1504), de punta a punta.
#
# El orden NO es arbitrario: los logs se bajan ANTES de consultar las llamadas,
# y la ventana se alinea a lo que el log cubre. Al revés, la mitad de los casos
# se quedan sin causa — Argo solo sirve el log de los pods vivos y maria-voice
# autoescala, así que cada minuto que pasa se pierde cobertura.
#
# Todo es de solo lectura sobre producción. Lo único que escribe fuera son las
# tareas de Jira, y eso lo hace una persona.
#
# Uso:
#   ./monitor.sh prod 2026-09-03T12:15:00Z 4.75 /tmp/pasada
set -euo pipefail

ENV="${1:-prod}"
UNTIL="${2:?falta el fin de la ventana, ISO con Z}"
HOURS="${3:-24}"
# El destino por defecto es el scratchpad de la sesión y NO /tmp: el sistema
# limpia /tmp sin avisar y se llevó por delante una pasada entera con sus 77
# veredictos del juez. Los entregables (parte y aviso) se copian donde pidan.
SCRATCH="${CLAUDE_SCRATCHPAD:-$HOME/.cache/maria-monitor}"
OUT="${4:-$SCRATCH/monitor_$(date -u +%Y%m%d_%H%M)}"

HERE="$(cd "$(dirname "$0")" && pwd)"
# Patient data and credentials (Argo login, SINA reviews): outside any repository.
PRIVATE="${MARIA_MONITOR_PRIVATE:-$HOME/.config/maria-monitor}"
mkdir -p "$OUT"/{logs,out}

echo "== 1/7 · logs de producción (lo primero: la ventana se pierde con el tiempo)"
for app in mariacore mariavoice; do
  python3 "$HERE/argo/argo_logs.py" --app "${app}-${ENV/prod/prd}" \
    --tail 500000 --out "$OUT/logs/$app" | tail -2
done
CORE_LOG=$(ls "$OUT"/logs/mariacore/maria-core-*[0-9a-z].log 2>/dev/null | grep -v sync-sina | head -1)
VOICE_LOGS="$OUT/logs/mariavoice/*.log"

echo "== 2/7 · llamadas de la ventana"
python3 "$HERE/collectors/fetch_bundles.py" --env "$ENV" --until "$UNTIL" --hours "$HOURS" \
  --out "$OUT/out/bundles.json" | tail -3

echo "== 3/7 · detectores L1/L2 + ventana + latencia del log"
python3 "$HERE/collectors/run_detectors.py" "$OUT/out/bundles.json" \
  --core-log "$CORE_LOG" --voice-log $VOICE_LOGS \
  --out "$OUT/out/findings.json" | tail -20

echo "== 4/7 · identificación: la verdad de SINA desde el log"
python3 "$HERE/collectors/auth_cases.py" "$OUT/out/findings.json" --env "$ENV" \
  --out "$OUT/out/auth_cases.json" | tail -2
python3 "$HERE/sina/reclassify_auth.py" --cases "$OUT/out/auth_cases.json" \
  --reviews "$PRIVATE/auth_review_*.json" --core-log "$CORE_LOG" \
  --bundles "$OUT/out/bundles.json" --out "$OUT/out/auth.json" | tail -3

echo "== 5/7 · cola del juez semántico (L3)"
python3 "$HERE/collectors/judge_l3.py" pack "$OUT/out/findings.json" \
  --bundles "$OUT/out/bundles.json" --out "$OUT/l3" | tail -3
# El juez es externo: lee $OUT/l3/RUBRICA.md y escribe *.verdict.json ahí.
# Cuando haya veredictos, `judge_l3.py collect` los recoge.
if compgen -G "$OUT/l3/*.verdict.json" > /dev/null; then
  python3 "$HERE/collectors/judge_l3.py" collect "$OUT/l3" \
    --findings "$OUT/out/findings.json" --out "$OUT/out/l3.json" | tail -4
  # Segunda pasada de los detectores, ahora con los veredictos: así los
  # hallazgos de L3 entran en `findings.json` y no solo en el parte.
  echo "   segunda pasada, con la capa semántica dentro:"
  python3 "$HERE/collectors/run_detectors.py" "$OUT/out/bundles.json" \
    --core-log "$CORE_LOG" --voice-log $VOICE_LOGS --l3 "$OUT/out/l3.json" \
    --out "$OUT/out/findings.json" | grep -E "juez:|residuo de L3|findings:" || true
  L3_ARG=(--l3 "$OUT/out/l3.json")
else
  echo "   (sin veredictos todavía: el parte sale sin capa semántica)"
  L3_ARG=()
fi

echo "== 6/7 · el parte: markdown para la terminal, HTML para compartir"
python3 "$HERE/collectors/report.py" "$OUT/out/findings.json" --bundles "$OUT/out/bundles.json" \
  --review "$OUT/out/auth.json" --no-state --out "$OUT/parte.md" | tail -1
python3 "$HERE/collectors/artifact.py" "$OUT/out/findings.json" --bundles "$OUT/out/bundles.json" \
  --review "$OUT/out/auth.json" "${L3_ARG[@]}" --out "$OUT/parte.html" | tail -1

echo "== 7/7 · el aviso"
python3 "$HERE/collectors/notify.py" "$OUT/out/findings.json" --review "$OUT/out/auth.json" \
  "${L3_ARG[@]}" --out "$OUT/slack.txt" > /dev/null
echo "   escrito: $OUT/slack.txt"

echo "== 8/8 · el historial compartido (monitor.sqlite)"
if python3 -c "import sys; sys.path.insert(0, '$HERE/collectors'); import monitor_store; sys.exit(0 if monitor_store.exists() else 1)"; then
  SINCE=$(python3 -c "import datetime as d; u=d.datetime.strptime('$UNTIL','%Y-%m-%dT%H:%M:%SZ'); print((u-d.timedelta(hours=float('$HOURS'))).strftime('%Y-%m-%dT%H:%M:%SZ'))")
  python3 "$HERE/collectors/monitor_store.py" record-run --findings "$OUT/out/findings.json" \
    --env "$ENV" --since "$SINCE" --until "$UNTIL" --by "${MONITOR_USER:-$USER}"
else
  echo "   (sin monitor.sqlite: la pasada no queda en el historial; ver monitor_store.py init / import-yaml)"
fi
echo
echo "Listo. Publicar $OUT/parte.html como artifact y pegar su enlace en el aviso."
