# Release checkpoint — September 12, 2026

The user explicitly resumed work and requested all current changes be pushed and deployed. Production deployment is now authorized. The earlier whole-app implementation goal remains incomplete; this release does not claim that every one of the 104 original requirement groups has passed every acceptance gate.

## September 12 continuation

r78 is live from code commit 5032ab9 after a fully green Linux run (1,723 passed, one PostgreSQL-only skip). Cost/access facts now precede Join; confirmed dated sessions expose calendar and optional Home Screen access; session Help is contextual. Private guest signup → explicit acceptance → host revocation was verified locally, including retention of the accepted roster place. Production health, asset hashes and mobile public entry passed. See release-r78.md and session-return-evidence.md.

The next planner wave is in progress locally: distinct play style and court access, main-flow practical details and clearer skill labels. It is not included in r78 and requires its own migration and verification. See remaining-implementation-review.md for the remaining work.

## Current release

The audit integration gaps are implemented: holiday/venue access continuity, durable venue-entry notes, following-date conflict projection and participant locking, saved overlapping plans, expired tournament partner recovery, and access to all active competition matches. Account export now produces a complete readable download, including after MFA step-up. Mobile selected-court controls and export layout were corrected from real browser findings.

Production schema upgrade completed and verified after a secure pre-upgrade backup. The same migration passed twice on an isolated PostgreSQL database containing the prior release schema and synthetic legacy records; profile, court, recurring session, roster and message data survived. New defaults and reply deletion semantics were checked. Twenty-nine scheduling/recurrence/agenda tests passed on PostgreSQL.

Release r77 is live, pushed to main, and passed live smoke checks. The full local suite passed 1,719 tests; all four real PostgreSQL concurrency cases passed. A fixture-only missing-Pillow issue in Linux CI was corrected without changing production files; its full CI rerun passed (verified September 12, 2026). Refer to release-r77.md for the receipt and verification boundaries.

## Verification records

- identity-account-chat-release-evidence.md: real download and MFA recovery, public-group preference persistence and scoped test evidence.
- competition-recovery-evidence.md: expired partner → extension → fresh consent and all-match navigation at 390px.
- venue-integrity-evidence.md: map search, mixed timeline, exact-date creation, durable entry note, refreshed return and accessible selected-card footer at 390px.
- The earlier detailed audit and frozen source snapshots remain in output/product-audit; their baseline/local distinctions are historical evidence, not claims about the new production release.

## Remaining boundaries

Browser push delivery is disabled in production. Actual background push receipt has not been verified. Do not infer delivery from queue insertion. Physical-device accessibility, large live tournament fields, and other unverified acceptance gates remain audit work.

The r77 application Brotli transfer is about 286 KiB, increased by the additional scheduling, competition and venue functionality. The release enforces a 300 KiB application cap and 370 KiB aggregate runtime-asset cap; moving code into helpers cannot conceal growth. Deferred feature loading remains an optimization opportunity.
