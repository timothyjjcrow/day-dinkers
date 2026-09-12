# r79 — practical session planning

Live September 12, 2026: https://third-shot.vercel.app/

Casual sessions have a maximum player count and a separate play style. Price, meeting area and court access are in the main planning flow. Invitations, session details and calendars carry the same facts. Self-rating choices include short descriptions. Directions and session actions wrap when text grows.

## Release receipt

- Runtime change: `72c242d2b88c1d5e865ebb9ca8908424d4c90425`.
- Released commit: `1b80e9bb2486d78515015786f33fb9673f8d5531`, which corrects the standalone invitation-rendering test's missing helper imports without changing application bytes. Pushed to main after CI succeeded.
- Linux CI: https://github.com/timothyjjcrow/day-dinkers/actions/runs/34708529410 — **1,740 passed, one PostgreSQL-only skip**, deterministic asset rebuild and container build successful.
- Initial CI run: 1,739 passed and one test-harness failure. All 16 affected checks passed after its correction. The redundant local full run was stopped after reproducing that same failure; it is not counted as a full passing run.
- PostgreSQL: 42 planning/invitation checks passed. The additive schema migration ran twice on prior synthetic data before production; two nullable fields were added with legacy values preserved as NULL.
- Production migration and a separate schema check passed after a verified private backup. Core counts stayed at 19 accounts, 18,510 courts, 95 games, 126 roster records and 11 messages.
- Deployment: `dpl_BNpnfXTG6xveSevB2novzpUMhm38`, https://third-shot-djx5e6ims-timothyjjcrows-projects.vercel.app — READY, production alias assigned.
- The public alias serves r79 and service-worker cache `thirdshot-v15-r81`. Live identity and Brotli JavaScript/CSS bytes match the committed artifacts. Earlier r78 assets remain available.
- `/health` reports production and a healthy database. Public court search and public court previews return 200. A fresh 390px browser shows the public entry, no horizontal overflow and no script errors.
- The error-level deployment log query for the preceding 30 minutes returned no entries; it does not certify unexercised production journeys.

The first HTTP validator advertised Brotli despite its Python client lacking a decoder. It was corrected to verify identity content and raw Brotli artifacts separately. Both passed; the browser also loaded r79 successfully.

Evidence and boundaries: [planner-metadata-evidence.md](planner-metadata-evidence.md). The whole-app goal remains active. The separate roster/reconfirmation work under development is not included in r79.
