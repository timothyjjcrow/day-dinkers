# Session planning metadata — September 12, 2026

Scope: PL-07, PL-08 and PL-09. Release r79 is prepared; production rollout and the full regression gate must be recorded separately before these findings become verified.

## Changes

Casual maximum players is independent of optional play style (rotating doubles, singles or mixed play). Three- and five-player capacities survive editing and recovery. Ranked matches keep their singles/doubles format. A rotating-doubles session needs at least four places.

Title, price/free status, meeting area and structured court access are in the main planner flow. The submit summary keeps price and access visible. Only a host-reported reservation exposes a reserved-court count. Public drop-in and booking-needed choices clear that count. Blank price and access remain unknown. Third Shot does not make the booking.

Create, edit, exact retries, weekly occurrences, public and private invitation previews, session details and calendar output preserve the same facts. Self-rating choices use the profile's concise descriptions and remain matching hints.

## Behavioral evidence

Synthetic local fixture on port 8062, Alex Discovery account, no production writes:

- Created a five-player mixed session with booking still needed and an explicit zero price.
- Closed with Keep draft, refreshed the app, resumed, and verified all those choices survived before creating the session.
- Edited it to rotating doubles, $12.50 per player and two host-reported reserved courts. Session detail and the Google Calendar event URL reflected all three changes.
- At 320px, reducing capacity to two rejected Save and focused Play style with the four-place requirement.
- Changed to singles, three players and public drop-in. The reserved count cleared and became disabled/hidden. Save succeeded; detail showed the price and host-reported public access.
- Checked at 320, 390 and 960px. Enlarging computed text sizes to 200% exposed clipped action labels; Directions and host/invite actions were adjusted to wrap. Rechecked with no overflowing visible button/link labels. This is a responsive desktop check, not physical-phone or assistive-technology certification.
- Browser reported no script errors in the exercised path. Local screenshots are in ignored `output/product-audit/evidence/planner-metadata/`; synthetic names/titles are test data, not claims about real activity.

## Automated and database evidence

- Executed JavaScript tests cover 2/3/4/5/100 capacity choices, draft and frozen retry metadata, escaped private invitation facts and escaped calendar output with correct duration, cost and access.
- API tests cover invalid enums, ranked/style compatibility, contradictory access/count rejection, no partial creation, exact keyed retries, title-only preservation, following-date propagation without changing earlier dates, public/private preview privacy and calendar subscription output.
- 42 planning/invitation tests passed on an isolated UTF-8 PostgreSQL database. An initial disposable database inherited SQL_ASCII from the local cluster and failed connection initialization; recreating this test target with explicit UTF-8 resolved the fixture issue.
- The additive production migration ran twice against a clone of the prior synthetic PostgreSQL schema. Both new fields are nullable; the legacy game remains NULL for both. All five core tables retained their one legacy record.
- A fresh private production custom-format backup was created and its archive directory verified. Pre-upgrade counts: 19 accounts, 18,510 courts, 95 games, 126 roster records and 11 messages. Neither new column existed yet. This record does not itself mean production migration or rollout completed.

Cost/access edits notify participants through the existing update path; explicit price-change reconfirmation is still part of the remaining commitment/roster review (PL-14). Completing these planner improvements does not close the whole-app audit.
