---
name: sdk-release
description: Cut an SDK release - changesets version, build both variants, and open the automated PR to the releases repo (builds + changelog + README + dependency manifest, ZERO source files). Publication is always manual. Use when asked to release the SDK or prepare a distribution PR.
user-invocable: true
---

# SDK release (repo de releases, publicación manual)

El fuente del SDK **nunca sale del monorepo**. La distribución va por un repo
de releases separado (solo builds); el PR se automatiza, la publicación la hace
un humano.

> Referencia del patrón: el repo `sofia-sdk` actual. El repo de releases nuevo
> de sofia-care: nombre por confirmar (open question del plan).

## Flujo

1. **Versiona** con changesets (versionado coordinado react + webcomponent):
   ```bash
   pnpm changeset version && pnpm install --lockfile-only
   ```
2. **Build de ambas variantes** desde el core compartido:
   ```bash
   pnpm exec turbo run build --filter='@sofia-care/sdk-*'
   ```
3. **Ensambla el payload de distribución** (el workflow `sdk-release.yml` lo
   hace en CI; a mano para probar):
   - `dist/` de cada variante + checksums/provenance.
   - `package.json` solo-distribución (deps/peerDeps actualizadas, sin scripts
     de build del monorepo).
   - `CHANGELOG.md` — genera el título y el resumen con el skill
     **`release-title-changelog`** sobre los changesets/commits del release.
   - `README.md` de consumo regenerado (instalación, ambas variantes, ejemplo
     mínimo, matriz de compatibilidad `client_version`).
4. **Abre el PR al repo de releases** (con `gh`, requiere confirmación):
   - Rama `release/vX.Y.Z`, PR con el changelog como cuerpo.
   - La CI de ese repo corre el check bloqueante **"0 ficheros fuente"** (lista
     de extensiones/paths prohibidos: `*.ts` no-`.d.ts`, `src/`, tests, configs
     del monorepo) + smoke de instalación del tarball en una app limpia.
5. **Para** — la publicación (merge + `npm publish` / release) es **manual**:
   deja el PR listo, resume qué contiene, y recuerda el paso humano. Nunca
   merges ni publiques tú.

## Trazabilidad

Cada build queda enlazado a su commit de origen en el monorepo (SHA en el PR y
en el provenance). El sellado `x-sdk-version` + versión mínima soportada
(SOC-63) hacen el resto en runtime.
