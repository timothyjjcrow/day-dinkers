# r78 — clearer session commitments and return actions

Live on September 12, 2026: https://third-shot.vercel.app/

- Cost and host-reported court booking appear before Join.
- Confirmed dated sessions expose calendar and optional Home Screen actions.
- One-time calendar copies are distinct from subscriptions; instructions state refresh limitations.
- Session Help opens a relevant answer and preserves Back navigation.
- Audit records reconcile shipped r77 work and retain incomplete implementation/verification separately.

## Release receipt

- Code commit: `5032ab9b257ccc1a72ae46bc33d304a17c321486`, pushed to main after its branch CI passed.
- Linux CI: https://github.com/timothyjjcrow/day-dinkers/actions/runs/34705139556 — **1,723 passed, one PostgreSQL-only skip**, plus successful deterministic asset rebuild and container build.
- Production: `dpl_ACSkfupHgpmegTjEVZJow4qM4tV6`, https://third-shot-66lytfxhd-timothyjjcrows-projects.vercel.app — READY.
- No schema changes. Immutable runtime assets are r78; service-worker cache is `thirdshot-v15-r80`. Earlier assets, including r77, remain readable.
- Live `/health`: 200 with database healthy and production environment. Public court search: 200. Live JS/CSS hashes exactly match committed r78 files. The service worker serves cache r80. The 390px public entry loads without browser errors.
- Error-level deployment log query since 30 minutes returned no log entries; this is not certification of every production journey.

Verification detail: [session-return-evidence.md](session-return-evidence.md). The initial local full run exposed five asset-version mismatches and four outdated test assertions; all 99 affected-file tests passed after correction, followed by the completely green Linux run above.

The whole-app goal remains incomplete. In particular, the next planner metadata/UI changes require their own tests and additive database migration; they are not part of r78.
