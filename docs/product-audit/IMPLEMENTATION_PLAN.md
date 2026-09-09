# Complete product audit implementation

Goal: implement the complete audit so a player or venue manager can finish the intended task easily from entry to outcome. The goal remains active until the implementation, production release and requirement-by-requirement verification are complete.

## Scope control

- Baseline: `42b567c`, branch `codex/product-audit-implementation`.
- The three audit reports are preserved here as the source specification.
- [CHECKLIST.md](CHECKLIST.md) lists all 104 extracted findings. [requirements.json](requirements.json) stores each full requirement, status, implementation and verification evidence.
- States: pending → in_progress → implemented → verified. A test passing does not alone prove a complete user flow. Deployment and release checks are separate from local verification.
- Later refinements and conditional recommendations remain tracked. If a requirement is better satisfied by an equivalent implementation, record the concrete rationale and verify the original user need; do not silently omit it.
- Keep existing data, private audiences, consent, review controls, chat durability, calendar support and corrected score history intact. No fabricated attendance or reconstructed historic occurrences where records do not exist.

## Work sequence

1. **Interaction and truth corrections.** Preserve open/focused Play controls during refresh; complete upcoming lists; fix exact-plan invitations, history/Activity filters, privacy state, rank eligibility, score correction, accurate delivery/category wording and consistent account validation.
2. **Dated plans and participation.** Durable series/occurrences, explicit date/future changes, complete agenda including pending invitations/waitlists and competition commitments, accepted waitlist offers and host transfers, distinct RSVP and actual attendance.
3. **Competition workflow.** Structured league time/court proposals and acceptance, round/season standings and absences, tournament individual arrival, estimate/called/playing states, court/player conflict checks, delay controls, organizer preview and mobile next-match hierarchy.
4. **Public discovery and planning.** Useful public detail before signup, concise generic onboarding, task-based creation, access/cost/format clarity, date/court/level/open-spot filters and pagination, private invite links, map search scope and court timeline.
5. **Court data and presence.** Shared structured hours/timezone/exceptions, visiting information, confidence/freshness, safe presence enforcement and failure recovery, useful photos/reviews and impact-appropriate edit review.
6. **Venue workflow.** Approved live version plus reviewable draft, predictable save behavior, conflict-safe updates/readable history, complete missing-venue claim, date-specific schedules/timezone, service templates, clear booking handoffs, usable integrations/analytics/team/operator tools.
7. **Identity and social flow.** Focused profile edits, useful player actions and filters, correctly scoped history/ratings, real request semantics, conversation search/replies/planning, consistent public/private group planning and notification preferences.
8. **Shared polish and support.** Compact mobile layouts, coherent Back/Close/save/action placement, contextual help, keyboard/assistive technology/text-size checks, phone keyboards and weak-network recovery.
9. **Release and completion audit.** Reconcile all 104 requirements against current code, meaningful tests and browser flows; migrate additive schemas safely; build versioned assets; deploy; verify production build/schema/health and read-only public journeys. Any incomplete or weak evidence keeps the goal active.

## Parallel ownership for the first implementation wave

- Root: Play rendering, task entry, planner/share and agenda frontend; shared migration wiring; requirement ledger, integration and browser verification.
- Recurrence worker: Game/GamePlayer/series preference model sections and recurrence routes. Identity worker reserves game_history_payload and its history route. No unrelated model changes.
- Identity worker: Me/history/profile/privacy/Activity/settings frontend, scoped account validation and notification/history API filtering.
- Venue worker: Business model sections, public reviewed projection, concurrency/versioning, revision UX and venue frontend sections.
- Shared files use targeted patches, with function ownership agreed before editing. Root alone manages release assets and deployment.

## Verification gates

- Use isolated test databases. Never import a locally configured production application to run destructive fixtures.
- Add tests for behavior and state transitions where meaningful: recurrence date identity/DST/duration, invitations and access, pagination before filtering, waitlist acceptance, stale-edit conflicts, reviewed public projection, result correction and match scheduling.
- Browser checks cover the whole task: invited guest → correct session → RSVP → calendar/arrival; waitlisted → offer → accept; proposed league match → accepted → agenda → score; weekly occurrence → completed history → next date; owner draft → preview → review → live without unintended disappearance.
- Exercise relevant negative states: no data, loading/failure, expired/private/full/cancelled, blocked identity, denied/imprecise GPS, offline/retry, role changes and simultaneous edits.
- Responsive checks at 320/390/960px, long names, 200% text, keyboard-only and phone keyboard behavior where tooling permits. Actual push receipt requires configured delivery and a real subscription; queue insertion alone is not receipt.
- A release receipt records commit, build version, schema verification, test results, deployment URL and current-state completion audit. No blanket claim of perfection from a narrow green check.

## Progress log

- 2026-09-08 Pacific: active goal confirmed; clean baseline inspected; implementation branch created; all 104 audit requirements recorded; first correction/foundation wave started.
