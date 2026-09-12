# r78 — clearer session commitments and return actions

Production promotion pending final Linux CI.

- Cost and host-reported court booking appear before Join.
- Confirmed dated sessions expose calendar and optional Home Screen actions.
- One-time calendar copies are distinct from subscriptions; instructions state refresh limitations.
- Session Help opens a relevant answer and preserves Back navigation.
- Audit records reconcile shipped r77 work and retain incomplete implementation/verification separately.

No database schema changes. Historical release assets remain available; current immutable assets are r78 and service-worker cache is thirdshot-v15-r80.

Verification detail: [session-return-evidence.md](session-return-evidence.md). The local full run plus corrected affected-file rerun covers 1,723 passing cases, with one PostgreSQL-only skip; this is not a claim that the initial full run was green. Fresh Linux CI and production smoke results will be recorded here before closure.
