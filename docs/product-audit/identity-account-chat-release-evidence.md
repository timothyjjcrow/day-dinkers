# Identity, account and chat release verification

Verified locally on 9 September 2026 UTC. Source changes remain unshipped until the coordinating release deploys them. This note supersedes older statements that body search/reply-to and readable competition export are absent locally. It does not certify every identity requirement or production delivery.

## Closed release blocker: incomplete exports

The actual browser download exposed a failure missed by Flask test-client tests: request teardown detached the profile before streamed deferred `avatar_data` loading. The endpoint had already returned 200; the frontend then offered a `null` JSON file.

- The stream now owns a fresh app/ORM session and reloads the authenticated account by ID. Deferred profile photos and paged collections remain available through stream completion.
- The frontend checks format, account ID and required collections before offering Save. Null/incomplete data produces a retryable error, not a downloadable file.
- Prepare and Save are separate explicit actions. Password/code inputs clear after preparation. The download URL is revoked when the sheet closes.
- A final mobile screenshot caught an inline-anchor layout overlap. The export-ready area now uses grid spacing and a full-width 44px flex download link; the final screenshot and DOM dimensions verify the correction.

## Browser evidence

Only synthetic local accounts and an in-memory SQLite fixture on port 8051 were used. After shared CUA connection failures, verification used an independent `agent-browser` Chrome session `identity-release`, at a real 390×844 viewport. No production account or credential was changed.

1. **Player export:** Me → Settings → Account → Download player data → current password → Prepare → Save. The browser saved `tmp/product-audit/browser-player-export.json` (2,566 bytes). Parsing confirmed format `third-shot-player-data-v1`, the correct account, readable Easygoing doubles/Harbor Courts records and Harbor public group membership. Other authors' 107 chat messages and authentication secrets were excluded.
2. **Public group preferences:** Harbor public group → Group settings → Notifications. ArrowDown changed All to Mentions; Back opened the discard guard. Discard returned to All. Reopening, selecting Mentions and saving persisted after a complete page reload. The group still showed its next dated session and distinct Plan/Chat actions.
3. **Ordinary-account MFA recovery:** a dedicated synthetic account was provisioned through the real encrypted API setup/enable routes. Browser password login required an authenticator or recovery code. One recovery code completed login; ordinary Account → Account security showed MFA on and nine codes remaining. No new authentication credential was entered/activated through browser automation, which requires a human handoff under its policy.
4. **MFA export:** the recovered account's download sheet required current password plus a current authenticator code. Its second actual browser-saved file, `tmp/product-audit/browser-mfa-player-export.json`, parsed as the correct account with empty session history. This also verified the final export layout: document/scroll width 390px, Save link 354×44px. Page-error output was empty.
5. **Earlier chat browser proof:** in the same isolated fixture, body search found the North gate message older than the initial 100-message window. Reply attached the correct original. Reload restored the draft and revalidated the original reference. A synthetic 503 queued the reply with explicit retry state; restoring delivery and reopening produced exactly one delivered quoted message, with no pending duplicate.

Reviewed screenshots: `tmp/product-audit/account-export-ready-390.png`, `public-group-preferences-390.png`, and `account-recovery-success-390.png`. Screenshots taken mid-transition were replaced with settled views; there is no confirmed transparent-sheet defect.

## Automated verification

`APP_ENV=testing TEST_DATABASE_URL=sqlite:///:memory: DATABASE_URL=sqlite:///:memory: python3 -m pytest -q tests/test_player_data_export.py tests/test_chat_search_replies.py`

**14 passed.** Covers detached request-session export, deferred photo, 205 authored messages/photos, readable own session/league/tournament results, exclusion of unrelated matches and quoted originals, current password/MFA step-up, ordinary encrypted enrollment, single-use recovery and unchanged remaining codes on refused export; plus literal/cursor message search, no read-marker side effect, exact-conversation replies, all six room membership boundaries, blocking, redaction, deleted references and idempotent replay after deletion. A preceding selected suite had 40 passing tests and one incorrect new test payload assertion; the assertion was corrected and included in the 14 passing run. Do not add overlapping counts together. `node --check public/app-v15.js` passed.

## Per-ID recommendations for the central ledger

| Requirement | Local evidence/status recommendation |
|---|---|
| ID-20 | Body search and reply-to implemented; API privacy/durability tests and mobile interrupted-send proof complete. Optional message editing remains out of release scope. |
| ID-25 / ID-04 group portion | Public and private preference save/discard semantics and shared radio keyboard behavior now have browser evidence. This is not full assistive-technology certification. |
| ID-23 / ID-24 | Earlier public/private next-session and owner-first pending-invitation planning browser evidence remains valid. New public preference proof preserves the next-session card. No automatic RSVP. |
| ID-08 / ID-09 / ID-10 | Earlier focused editor/preview, honest rating attribution and Away-with-preserved-usual-slots browser evidence stands. Root owns planner suggestion integration; do not infer coverage of every suggestion from profile tests. |
| Account gaps associated with ID-35 | Ordinary MFA entry, recovery login, readable personal export and actual download verified locally as described. Per-device inventory is still absent; the interface honestly offers sign-out-all-other-sessions. |
| ID-31 / contextual ID-33 | Calendar/install actions and Help exist; after-join calendar/install placement and context-specific Help links remain optional follow-up work, not verified completion in this note. |
| ID-29 / ID-36 | Quiet hours/digest timing and physical-device/AT coverage are not claimed. Actual push/email receipt and platform authenticator enrollment remain production/manual verification items. |

No migration/deployment was performed by this worker. The final source freeze was communicated before root's release asset build. Test browser/fixture cleanup follows evidence capture.

## Release regression triage

The broad release run exposed older frontend assertions and extracted JavaScript harnesses that still assumed the previous UI. Test-only corrections now follow the current activity chooser, dated court entry, schedule-aware roster labels, focused profile drafts, neutral new-player history, retained live-refresh UI and shared `gameActivityLabel`/time-suggestion helpers. Existing action, privacy, focus, keyboard and retry assertions remain in place. No application-source change was needed for this subset.

The focused rerun passed **118 tests** (`tmp/product-audit-release/frontend-triage-focused.log`). Court-specific test updates were delegated to the court/venue worker; the coordinating release owns backend regressions, design-system fixes and final asset validation. These counts overlap the broader release suite and must not be added to it.
