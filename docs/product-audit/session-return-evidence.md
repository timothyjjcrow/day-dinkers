# Session return actions — September 12, 2026

Scope: PL-12/13, ID-30/31, and the session portion of ID-33. This is a specific implementation/verification receipt, not whole-audit completion.

Confirmed future dated sessions expose Add to calendar and optional Home Screen access alongside their court and date. The same renderer covers normal Join and accepted waitlist offers. Queued/offered places, cancelled/completed dates, instant play and past start times do not receive these controls. There is one calendar action, outside Session options. Installed/standalone clients omit Home Screen access. Undo is now at least 44×44 pixels.

Cost and court access precede the roster and Join. Missing cost remains unknown; zero means Free session. A court count is attributed to the host (“Host says … reserved”), rather than presented as proof of a reservation. These facts are no longer buried in the optional details disclosure.

Calendar choices distinguish a one-time copy, which does not receive changes, from a private subscription. Subscription timing belongs to the provider; the sheet points to Third Shot for last-minute changes. No calendar is added automatically.

Home Screen opens instructions, not an automatic installation or notification permission prompt. A retained native installation offer requires another explicit click. Accepted, declined and failed browser prompts receive different truthful outcomes; failure retains browser/bookmark instructions. Account settings remains a fallback. Instructions follow Apple’s Safari guide (https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios) and Google’s Chrome help (https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid&hl=en). Actual native installation/phone alert delivery is outside this browser check.

Session Help uses the existing Help & safety sheet and expands joining, changing a plan, or ranked-score guidance according to the current context. Existing parent/child modal navigation preserves the session. Contextual help on other product surfaces remains tracked in ID-33.

## Evidence

- `tests/test_session_return_tools_frontend.py` executes eligibility, standalone behavior, explicit prompt consent/decline/failure, help topic selection and honest cost/booking labels.
- The full local run exercised 1,724 cases: 1,714 passed, nine failed, one PostgreSQL-only case skipped. Five failures exposed the missed Flask release-version bump; four were assertions for the old component placement/cache version. After corrections, all 99 tests in the affected files passed. Final Linux CI is tracked in the r78 release receipt.
- Disposable in-memory fixture: `tests/e2e/fixtures/discovery_server.py`, port 8061. All accounts and sessions synthetic; no production writes.
- Actual browser: login → dated session → Join → overlapping-plan confirmation → confirmed roster → calendar choices → Back → Home Screen instructions → Back → session Help. The relevant change/cancel topic opens; Back preserves the session.
- Waitlist offer: no return actions before acceptance; Go back from the conflict warning retains the offer; explicit Keep both plans completes acceptance, shows You’re in and both return controls.
- Layout checked at 320/390/960 pixels. The 320-pixel Help sheet also had computed text sizes doubled: no horizontal overflow. These are desktop-browser responsive checks, not physical-phone or screen-reader certification.
- Screenshots are retained locally under `output/product-audit/evidence/session-return/`. Browser CLI clicks required scrolling offscreen controls into view; assertions inspect the top dialog because retained parent dialogs intentionally remain in the DOM.

## Reconciliation

The audit ledger was reconciled against r77 source, tests and existing journey evidence. Implementation and verification remain separate: 87 findings have implementation recorded; five are in progress and twelve pending. This does not certify 87 complete user flows. Conditional delivery/device checks and unverified journeys remain explicit in each requirement.
