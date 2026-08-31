# Formato del reporte Slack + reglas de PII

Dos piezas: un **canvas** con el detalle completo y un **mensaje de canal** corto (5–8 líneas) — lo único que alguien lee de verdad un martes a las 9 de la mañana.

## 1. Mensaje de canal

```
Reporte de calidad — 26 ago · 84 notas muestreadas de 812 · 11 asistentes
▲ 24 % con hallazgo major+ (ayer 17 %)

1. Dr. A. Ruiz — 5/6 notas · 🔴 blocking · plantilla `alta-hospitalaria`
2. Dra. M. Sanz — 3/5 notas · 🟠 major · plantilla `seguimiento`
3. Dr. J. León — 2/4 notas · 🟠 major · plantilla `seguimiento`

Patrón dominante: sección «Exploración física» vacía en 9 notas de 2 plantillas
> «EXPLORACIÓN FÍSICA:\nNo disponible.\n\nPRUEBAS COMPLEMENTARIAS:…»

@equipo-clinico umbral superado (15 %) · Ver canvas completo →
```

Reglas:

- **5–8 líneas.** Solo los tres peores asistentes, el delta, el patrón dominante.
- La línea de delta se lee: `▲ 24 %` (subió) / `▼ 17 %` (bajó) / `= 20 %` (igual). El delta se calcula contra el run anterior (misma rúbrica; si cambió → `delta n/a`).
- La mención (`@equipo-clinico` o el que elija el kickoff) **solo** cuando al menos un asistente supera el umbral (15 % de notas `major+`).
- El enlace "Ver canvas completo" apunta al canvas.
- Un asistente aparece en el top-3 con su severidad máxima y la plantilla que concentra el fallo.

## 2. Canvas (el detalle)

| Sección | Contenido |
|---|---|
| **Resumen** | Ventana, muestreadas/total, asistentes, % global `major+`, delta |
| **Tabla por asistente** | muestreadas / total en ventana / % con hallazgo `major+` / severidad máxima |
| **Top-3 patrones recurrentes** | Patrón + evidencia literal (cita ≤ 200 car., enmascarada) + plantillas afectadas |
| **Sugerencias de plantilla** | Cambio concreto **con el texto propuesto**, no «revisar el prompt» (ej: «hacer condicional la sección X porque el input nunca la contiene») |
| **Anexo** | Lista de notas revisadas con su veredicto (id, asistente, plantilla, hallazgos con cita) |

## 3. Gate de PII — bloqueante, reglas exactas

Son notas clínicas saliendo a Slack. **Nunca se publica una nota completa: solo citas ≤ 200 caracteres con identificadores enmascarados.** Antes de publicar, escanea cada cita que vaya a salir; si detecta un identificador sin enmascarar, **detente y no publiques** (re-enmascara el hallazgo y vuelve a renderizar). El gate es conservador: **ante la duda, falla** (seguro fallar de más que filtrar un NHC real).

### 3.1 Máscaras obligatorias (las aplica el juez al escribir la cita)

| Identificador | Máscara |
|---|---|
| Nombre completo | `[Nombre]` |
| NHC / historia clínica | `[NHC]` |
| Teléfono | `[Teléfono]` |
| Fecha de nacimiento | `[FechaNac]` |
| Email | `[Email]` |
| Dirección | `[Dirección]` |

### 3.2 Heurísticas de detección (las aplica el modelo antes de publicar)

Diseñadas para **no** disparar falsos positivos con texto clínico normal (terminología, fechas de consulta). Compila y aplica estas regex sobre cada cita; cualquier match fuera de corchetes `[...]` = hallazgo → bloquea la publicación:

| Identificador | Regex | Notas |
|---|---|---|
| Teléfono | `\+?\d{9,12}` **o** `\d{3}[-\s.]?\d{3}[-\s.]?\d{3,4}` | |
| Email | `[\w.+-]+@[\w-]+\.[\w.]+` | |
| NHC | `\b\d{7,12}\b` | Dígitos largos |
| Nombre | `(?:Paciente\|Dr\.?\|Dra\.?\|Sr\.?\|Sra\.?\|Dña\.?\|D\.?\|DNI\|NHC\|tel\.?\|teléfono)[^A-Za-zÁÉÍÓÚÑáéíóúñ]{0,30}\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+ [A-ZÁÉÍÓÚÑ][a-záéíóúñ]+` (case-insensitive en el marcador) | Solo con un **marcador de paciente/título** cerca — así "Insuficiencia Renal" o "Dolor Abdominal" no disparan |
| Fecha nacimiento | `(?:nacimiento\|nac\.?\|nacid[oa]\|born\|edad\|años)[^A-Za-zÁÉÍÓÚÑáéíóúñ0-9]{0,20}\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b` **o** `\b\d{1,2}[/-]\d{1,2}[/-](19[0-5]\d\|196[0-5])\b` | Contexto de nacimiento/edad, o año que implica edad ≥ 60 — una fecha de consulta reciente no dispara |

Reglas de aplicación:

1. Un match **dentro de corchetes** (`[Nombre]`, `[NHC]`, …) no cuenta — la cita ya está enmascarada.
2. Una cita con cualquier match fuera de corchetes → el gate **falla**: no se publica.
3. Si no estás seguro de si algo es un identificador → trata como hallazgo (conservador).

## 4. Flujo de publicación (Slack MCP)

1. **Dry-run**: producir canvas + mensaje en local, revisión humana de cada cita (sobre todo las del gate PII).
2. **Aplicar el gate PII** (§3.2) sobre cada cita que vaya a publicarse; si algo falla, re-enmascarar y repetir.
3. Crear/actualizar el **canvas** (nuevo canvas por run, o actualizar el del día anterior).
4. Publicar el **mensaje de canal** (5–8 líneas) con el enlace al canvas.
5. Si algún asistente supera el umbral → el mensaje lleva la mención configurada.

El primer envío real siempre lo revisa y aprueba un humano antes de armar el loop (ver SKILL.md §8).
