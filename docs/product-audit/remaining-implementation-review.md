# Remaining implementation review — September 12, 2026

This narrows the stale checklist using current source. A source match is implementation evidence, not end-to-end verification.

## Next coherent change: clearer planning

- PL-07: `gameCapacityChoicesHtml` still conflates 2/4/6+ people with singles/doubles/group. Keep ranked sides explicit; casual sessions need maximum players and an optional separate play style. Carry that value through create/edit, retries, weekly dates, detail, invitations and calendar output.
- PL-08: title, cost, meeting area and court count remain inside the planner’s More options. Move decision-critical fields into the main flow. Add structured host-reported court access (reserved by host / public drop-in / booking needed / unknown); a count must never establish a reservation. Existing records stay unknown unless a real host claim is already present. Use additive nullable schema changes, production backup and migration verification before rollout.
- PL-09: minimum/maximum options still display only numeric self-ratings. Reuse the profile’s concise skill descriptions without implying Third Shot match rating or gatekeeping attendance.
- PL-03: exact date/time, court and open-spot filters already exist in `playDiscoveryQuery` and API tests. Audit the complete mobile discovery journey and level wording before advancing status. Saved delivery alerts are conditional on reliable delivery, not part of the initial discovery fix.
- PL-14: the scheduled Going count still uses all roster rows. Separate reconfirmation, accepted place, physical arrival and waitlist; compact large groups and preserve the primary action’s position.
- PL-10/11: exact-session creation sharing and revocable private links exist. The private guest → account → explicit RSVP → revoked-link journey still needs full browser evidence.

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
