# My plans agenda: September 14, 2026

Source review following17ac71a on `codex/mobile-auth-clarity`. Pass3 remains in progress. Not deployed.

## What improved

The host fixture previously showed19 overlap pairs ahead of the schedule, followed by a large “Next up” card and then the remaining dates. At390px the first actual plan began below the visible screen. The revised view shows a compact, explicitly labeled overlap warning and puts all scheduled games into a consistent date-grouped agenda. The same fixture now shows two full agenda entries above the bottom navigation.

Overlap Review expands every original pair and keeps every game, league and tournament route, including estimated-time notices. Closing a session retains disclosure state. No saved plan is cancelled or moved by opening or closing this warning.

To do contains invitation replies, active spot/hosting offers, score confirmation, changed-plan confirmation and session wrap-up. Ordinary pending invitations are now counted in the tab's task count. Scheduled plans are excluded from the agenda only when shown in To do; waiting/recent work has its own section. Cancelled, ended, joined, hosting, waitlisted and skipped states use distinct labels. Joined describes membership without claiming confirmed attendance. An expired session that the user can still complete retains its wrap-up task.

Rows show the time and status above a full-width name and court. This avoids squeezing a long title between a narrow time column and a large badge. Shared competition rows use the same responsive structure. Replaced the unrelated profile checklist with a small setup link and its completion count; the existing onboarding action remains available. Calendar entry also remains available when all plans need decisions.

## Verified journeys

Local in-memory fixture port8065, synthetic Sam and Alex accounts only. Browser checks included:

- Host agenda with11 plans, all original overlap pairs and dates retained.
- Expand overlap, open the selected session, return with disclosure intact.
- Accept Alex's invitation to After-work doubles: task count2→1; session appears once under Upcoming plans as Joined.
- Accept the pending hosting request: task count1→0; session appears under Upcoming plans as Hosting. The fixture's stated leave-on-accept behavior removed the previous host as expected.
- Return focus after both a plain drill-in and a mutation that replaces the original row. The latter exposed the rendering container being detached; fallback now resolves the current visible row and is guarded by account and tab identity.
- 390px dark,320px light/dark,1280px light desktop; doubled element text at320px in both themes. Checked panel scroll width296 against client width296. Names, court text, dates and status badges wrap.
- Calendar subscription sheet opens from the agenda. External Google/Apple/Outlook providers were not opened. No provider-sync claim is made.

761 frontend/design checks passed in14.76s after the layout, state and focus changes. A final56-test focused run passed in1.60s after adding explicit expired-session status handling. New executable checks cover status distinctions, complete overlap routes and visibility/account-safe focus restoration. Existing overlap escaping checks remain intact. Syntax and whitespace checks passed. Browser error inspection reported no uncaught errors.

Evidence: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-03/plans/`. Compare `before-390-dark.png` with `host-agenda-390-dark.png`. Player transition screenshots show the accepted invitation and host request. Narrow, enlarged-text, expanded-overlap and desktop screenshots are alongside `frontend-tests.log`.

## Remaining work

Review mixed tournament/league decisions visually with a richer fixture, large paginated agendas, date boundaries, empty/error states for My plans, and calendar subscription copy/sync/revocation flows. Profile setup navigation retains its existing handler but the full onboarding journey is a separate open pass. Physical devices and assistive technology remain unverified. The floating invitation banner is still a separate competing surface to review in the shared navigation/activity pass.

The production release still requires new immutable assets and its existing deployment prerequisites. This source pass does not resolve the pending production backup approval boundary.
