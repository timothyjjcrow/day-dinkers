# Pass 6 — Reviewing recurring hosting

September 15, 2026. Source follows 5180ec1. The full-app goal and pass 6 remain in progress.

## What improved

Requesting future hosting now shows the actual dated plans and the standing schedule. Each date identifies its time range, court, price and roster size. The first two are visible, with the rest under a keyboard-operable disclosure. Optional session details contain format, level, access, duration, audience and notes. The schedule has its own card, so a moved date or different price is not mistaken for a change to every future session. Unknown costs, duration and bookings remain explicitly unknown. Cross-midnight ranges retain the end date.

The owner chooses a player and sees a counted action, such as Ask Sam Rivera to host 5 dates. The recipient sees Review hosting, then a dedicated sheet naming the session and requesting host. It explains that acceptance joins the listed dates and future sessions on the schedule, and whether the old host will leave. This replaces an immediate acceptance with no combined view of the plans. A full date is named in the error, expanded and marked; acceptance is disabled with a reload action.

Both reviews handle loading and retry. Changed terms refresh the review without losing the owner's selected player or silently resubmitting. The changed-plan notice stays next to the refreshed information. Pending acceptance locks the actions and details and prevents dismissal; a failed write restores them. On mobile, the content and final actions remain in normal document flow, with wrapped labels and vertical scrolling. Optional detail replaces a long always-visible description, while the actual dated plans remain available before the decision.

## Behavior and compatibility

New owner and recipient preview endpoints are read-only and role-checked. The review token covers the displayed date terms, membership counts, recipient membership, standing rule and request identity. Acceptance of recurring hosting requires the current token. Missing or stale reviews fail before changing the hosts or rosters. A current review also acknowledges any changed playing plans for the new host; other players' reconfirmation remains outstanding. Ownership and capacity are checked again. Existing earlier dates remain outside the selected future scope. Legacy weekly rules get a readable day and clock without initializing the series during preview.

The owner's token remains optional for older request clients. Recurring acceptance now requires the review protocol; the backend and new browser must ship together. Older clients cannot silently accept future hosting. Single-date acceptance keeps its existing plan-confirmation behavior. No schema migration is needed.

## Evidence

Screenshots and logs are in the primary workspace at `output/product-audit/evidence/ui-ux-pass-06/hosting-review/`. All people, invitations and sessions were synthetic, in-memory fixtures.

- `hosting-owner-before390.png` and `hosting-recipient-before390.png` show the original future-hosting controls on fixture 8078, without an integrated date review. Final recipient views are `hosting-recipient390-final-light.png` and `hosting-recipient390-final-dark.png`; the owner is shown in `hosting-owner390-final-dark.png` and the 320px action captures.
- Fresh fixture 8080 ran the final backend. Morgan created games 8–12, Tuesday/Thursday at 11 AM Pacific, ending October 8; Sam joined the first. Changing game 9 from $5 to $8 after the owner's review produced a real host_review_changed rejection. Sam remained selected; the new review separated the $8 occurrence from $5 schedule defaults. Submitting again created request 2 with departure selected.
- As Sam, opening Review hosting performed only the preview. Moving game 10 to October 2 while that review was open made acceptance fail with host_review_changed. The refreshed list contained October 2, focused the error and required another decision (`hosting-recipient-stale390.png`). That screenshot predates the final session-name header and shorter consequence wording. Keyboard Enter expanded all five dates (`hosting-recipient-all390.png`).
- Synthetic 503 preview failures disabled acceptance/sending and exposed Try again. Restoring fetch and retrying loaded the real plans. A 1.5-second delayed owner lookup released after switching to This date only was ignored: the panel stayed hidden and the single-date action stayed available. The final owner stale-message placement was verified on fixture 8079 (`hosting-owner-stale-final390.png`); this fixture predates only the final backend legacy normalization and authorization-order refinements.
- Casey joined game 12 after its capacity was reduced to two. Sam's review refreshed into a disabled state naming October 8; the full card was expanded and marked (`hosting-recipient-full320.png`, `hosting-recipient-full-date320.png`). Casey left through the API; Reload dates restored acceptance.
- A five-second simulated 503 on acceptance showed Accepting, disabled Back and inert details. Escape did not close the sheet. Failure restored the controls and focused the error (`hosting-recipient-pending390.png`, `hosting-recipient-write-failure390.png`). This tests a bounded simulated response, not an ambiguous production timeout.
- With native fetch restored, keyboard acceptance succeeded. The sheet closed and the session showed You're hosting (`hosting-recipient-success390.png`). Read-back confirmed Sam as host and confirmed player on exactly games 8–12, Morgan absent as requested, no pending request, standing RSVP enabled, game 9 still $8 and game 10 still October 2.
- Inspected owner and recipient layouts at 320/390px in both themes; recipient desktop at 1280px in both themes. Independently doubled computed fonts stayed within a 320px page and modal, with the final actions reachable. The `large320` screenshots show both top and bottom states. Keyboard expansion and final acceptance were exercised. No physical-device or screen-reader verification was performed.

## Checks and next scope

863 targeted frontend/design, host-consent, recurring-date, editing and reconfirmation checks passed. After the last full-date visual refinement, six renderer/design checks passed again, along with JavaScript syntax and diff whitespace checks. New tests cover read-only previews, identity/expiry/ownership, legacy rules, missing and stale reviews, changed date/price/duration/notes/defaults/membership/cancellation, capacity, unchanged earlier dates and other players, idempotent acceptance, escaped markup and cross-midnight times.

These are SQLite and local-browser checks, not new PostgreSQL concurrency coverage. Review tokens do not fingerprint the identities of other roster members, and this is not a projected calendar of unmaterialized future dates. Held waitlist offers, schedule conflicts during host acceptance, future-date blocks, all role/expiry/account-switch browser states, uncertain writes and the previously recorded planning/editing gaps remain open for later coverage.

Next broaden to pass 7: immediate play, on-the-way, arrival and completion. Retain the remaining pass 6 items for cross-feature verification. Source only: no release build, database export, migration or production deployment. The existing backup-approval boundary is unchanged.
