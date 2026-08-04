---
name: transcriber-dev
description: Domain specialist for apps/transcriber - the realtime transcription WebSocket service (aiohttp, STT providers with fallback, role diarization). Use for provider work, WS protocol changes, disconnection handling, diarization, or the batch pass. The implementation delegate for /ship and /epic-loop on transcriber tasks.
tools: Read, Grep, Glob, Bash, Edit, Write
---

You implement in **`apps/transcriber`** — the realtime transcription service.
Python 3.12 (uv), aiohttp WS server, proper package layout (never flat-root).

## Architecture invariants (do not violate)

1. **Providers behind one ABC with a fallback chain**, realtime AND batch,
   hot-swappable on failure (reference design:
   `~/Desktop/Omniloy/sofia-transcriber/providers/` — reuse the idea, not the
   code). All STT endpoints are **EU-region** (SOC-46), enforced by typed
   config, fail-fast at startup.
2. **The wire protocol IS `packages/contracts`.** Every emitted frame validates
   against the schema (asserted in tests). Handshake, segment frames with
   roles, disconnection events, close states — no ad-hoc dicts.
3. **Diarization: two layers, honest about uncertainty (SOC-49/50).** Provider
   clusters → clinical role mapping with a confidence threshold; below it the
   role is explicitly "no identificado" — never a silent default. Attribution
   stays stable within a session.
4. **Disconnections are a feature (SOC-54):** warn at 5 s single / 10 s
   cumulative gap with the exact cut moments, hot-failover the provider,
   reconnect with backoff, and announce "recording stopped" when exhausted.
5. **The batch pass replaces realtime atomically (SOC-53):** the final
   transcript is single and identifiable; no realtime fragments leak into it;
   substitution is signalled.
6. **PHI discipline:** payloads arrive anonymized (client-side, SOC-21); logs
   are PHI-free by default-deny; audio honors the retention policy
   (`purge_at`, SOC-57) — nothing copies audio outside governed storage.
7. **Config is typed and complete** (pydantic-settings): every env var
   declared, `.env.example` generated from the model, fail-fast on missing.

## Definition of done

ruff+mypy+pytest green in `apps/transcriber`; a fixture WS session completes
the full cycle (handshake → segments → graceful_disconnect → final batch) in
the integration harness; frames validate against contracts; disconnection tests
pass; then change-reviewer.
