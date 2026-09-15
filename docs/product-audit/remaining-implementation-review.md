# Remaining implementation review — September 12, 2026

This narrows the stale checklist using current source. A source match is implementation evidence, not end-to-end verification.

## Current focus after r79: roster commitments

- PL-07/08/09: implemented and deployed in r79. Casual capacity/play style, main-flow essentials, explicit host-reported access and descriptive self-ratings have focused browser/API/PostgreSQL evidence. Wider acceptance verification stays open; see planner-metadata-evidence.md.
- PL-03: exact date/time, court and open-spot filters already exist in `playDiscoveryQuery` and API tests. Audit the complete mobile discovery journey and level wording before advancing status. Saved delivery alerts are conditional on reliable delivery, not part of the initial discovery fix.
- PL-14: the scheduled Going count still uses all roster rows. Separate reconfirmation, accepted place, physical arrival and waitlist; compact large groups and preserve the primary action’s position.
- PL-10/11: exact-session creation sharing and revocable private links exist. The private guest → account → explicit RSVP → revoked-link journey was exercised for r78. Retain the narrower PL-10 public/copy-fallback review.

## Other remaining work

- PL-17/21: verify immediate-play state continuity and that RSVP history stays separate from actual attendance across legacy and ranked paths.
- ID-16: Messages / Groups / Players tabs exist; public-group creation/planning still uses “community” terminology. Complete the visible-label pass without adding navigation layers.
- ID-33/35: session Help is linked; other relevant controls and common detail ordering still need a contextual pass.
- ID-29/36: actual delivery is disabled in production; quiet hours/digests remain conditional. Physical-phone/assistive-technology certification is still unavailable from the desktop responsive checks.
- CV-04: useful access/play filters require explicit unknown handling and reliable source data. Current filters cover hours and facilities, not all proposed access/scheduled-play dimensions.
- CV-09: secondary court activity remains secondary and real court-view photo preference exists. Verify the whole useful-visit hierarchy before closing.
- CV-11/12: provenance and explicit unconfirmed check-in recovery exist. Verify denied/inaccurate location, privacy audience, leave/expiry and scheduled attendance together.
- CV-15: photo categories/dates and unknown-date preservation exist. Structured practical review prompts and useful/recent ordering are not yet demonstrated.

See requirements.json for all original acceptance text and the boundaries attached to each implemented item.
