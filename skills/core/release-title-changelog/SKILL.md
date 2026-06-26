---
name: release-title-changelog
description: Generar el título de una release y su changelog en una o dos frases explicativas (nunca una lista) a partir de los commits de git de un rango o de una PR. Usar cuando el usuario pida "el changelog y el título de la release", "dame el changelog breve", pase un número/URL de PR (típicamente la PR de release hacia main), o pegue un listado de commits/PRs pidiendo resumen de release.
user-invocable: true
---

# Título de release + changelog en prosa

Convierte el contenido de una release en un **título tipo titular** + un **changelog de 1–2 frases en prosa** (nunca una lista). La sustancia sale siempre de los commits reales — nunca se inventa alcance.

## Entrada

Tres formas de darle el material, en orden de preferencia:

1. **Un nº o URL de PR** (el flujo habitual: la PR de release hacia `main`). Saca el rango de commits de la propia PR:
   ```bash
   gh pr view <n> --json title,url,baseRefName,headRefName,commits
   # solo los títulos de commit (la señal que se resume):
   gh pr view <n> --json commits --jq '.commits[].messageHeadline'
   ```
   Si solo dan el número, infiere el repo del remoto (`gh repo view --json nameWithOwner -q .nameWithOwner`). Si la URL es completa, extrae `owner/repo` + número de ella. Los commits de la PR **son** el contenido de la release — ese es el rango.

2. **Un listado de commits/PRs pegado** por el usuario → úsalo tal cual.

3. **Un rango del repo** (por tag o por merges) cuando no haya PR ni pegado:
   ```bash
   git log --oneline <ultima-release>..HEAD          # por tag
   git log --oneline --merges <rango>                # solo merges de PR, suele ser la señal limpia
   ```

## Proceso

1. **Filtrar ruido**: ignorar commits de merge sin contenido propio ("Merge pull request #N from..." cuenta solo como agrupador de su feature), fixes triviales, typos y commits de formato. Quedarse con los cambios con valor de producto.
2. **Agrupar por tema**: los nombres de branch (`feat/codify-metrics`, `fix/...`) y los PRs agrupan mejor que los commits sueltos. Identificar los 1–3 temas dominantes de la release.
3. **Jerarquizar**: el tema más importante (más PRs, más visible para el usuario final) define el título; el resto se subordina en el changelog.

## Formato de salida

**Título:** corto (4–8 palabras), nombra los 1–2 temas dominantes, sin verbos de commit ("added", "fixed") — estilo titular: "Métricas de Codify y observabilidad de transcripción".

**Changelog:** una o dos frases explicativas en prosa — **NUNCA una lista, nunca bullets**. Estructura típica: frase 1 = tema principal con su alcance concreto entre paréntesis si aporta; frase 2 (opcional) = temas secundarios. Mencionar detalles técnicos solo si son la sustancia del cambio (p. ej. "percentiles p90/p95", "tabla precalculada"), no mecánica interna (nombres de columnas, migraciones).

## Ejemplo (real)

Entrada: ~15 PRs sobre métricas de Codify (tabla precalculada, columna en fact_doctor_usage_daily, allowed products, panel en dashboard) + métricas del asistente de transcripción (grabación, latencias, percentiles de notas e informes).

Salida:

> **Título de la release:** Métricas de Codify y observabilidad de transcripción
>
> **Changelog:** Se incorporan las métricas de Codify al dashboard general (con tabla precalculada y su columna en el uso diario por doctor) y se amplía la observabilidad del asistente de transcripción con métricas de grabación, notas e informes, incluyendo latencias y percentiles (mediana, p90, p95).

## Reglas

- Idioma: el del usuario (normalmente español).
- Brevedad máxima: si cabe en una frase, una frase.
- No inventar alcance: solo lo que está en los commits. Si un tema es ambiguo por el mensaje del commit, omitirlo antes que adivinarlo.
- Si el usuario pide variantes (más corto, en inglés, tono marketing), regenerar solo el formato, no reinterpretar los commits.
- Solo lee (git log / `gh pr view`); no commitea, no crea releases ni tags. La salida vive en el chat salvo que pidan otra cosa.
