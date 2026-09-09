# Dated sessions, consent and attendance — local verification

Verified on 9 September 2026 UTC in the working tree. No deployment or production data changes.

## Behavior

- Weekly sessions materialize separate dated Game records, retaining stable URLs, rosters and results. Editing, cancellation and RSVP distinguish this date from following dates. Advancement uses session duration and the recurrence timezone. Legacy dates already overwritten by the old application are not invented.
- Waitlist vacancies reserve expiring offers. Only Accept adds a player to the roster. Expired or passed offers advance the queue; stale acceptance cannot take another person's reserved spot.
- Host handoffs require the recipient's acceptance. The requesting host remains responsible and on the roster until acceptance, including explicit future-date scope.
- Casual wrap-up requires an explicit attendee list. The participation ledger retains original RSVPs, departures, cancellation and attendance corrections separately from the played roster. A prior participant retains access to their completed private date; outsiders do not.

## Executed verification

`APP_ENV=testing TEST_DATABASE_URL=sqlite:///:memory: DATABASE_URL=sqlite:///:memory: python3 -m pytest tests/test_game*.py tests/test_ranked*.py tests/test_instant_rally*.py tests/test_dated_session_interactions_frontend.py tests/test_score_entry_frontend.py -q --disable-warnings --maxfail=6`

160 tests passed in 37.50 seconds. JavaScript syntax and `git diff --check` passed.

The disposable in-memory fixture `tests/e2e/fixtures/recurring_session_server.py` ran on port 8041. Through the browser:

1. Jamie requested that Casey host and opted to leave after acceptance. Jamie stayed host with two players while the request was pending.
2. Casey signed in separately and accepted. Casey became host; only then was Jamie removed.
3. Morgan viewed an offered place: the roster contained only Jamie and displayed Accept spot / Pass. Accept added Morgan to the specific dated roster.
4. Jamie explicitly selected two of three RSVPs during casual wrap-up. The page showed 3 signed up / 2 played. Correcting Morgan to played changed the played roster to three, retaining the RSVP history.

API tests also verify offer expiry, FIFO advancement, pass, duplicate acceptance, unauthorized handoff responses, decline, date-scoped future handoff, private historical access and attendance correction authorization. Executed Node tests dispatch the actual frontend event handlers rather than only asserting source strings.

## Remaining verification boundaries

- No production migration, deployment or PostgreSQL concurrency verification was performed.
- Consent cards did not receive a new mobile visual pass in this wave. Earlier dated-session browser evidence is in `tmp/product-audit/recurrence-ui/verification.md`.
- Durable attendance event coverage is complete for the tested scheduled casual flows. Ranked result finalization and every legacy instant-play creation path need separate review before claiming universal attendance-history coverage.
- The last roster wording improvement distinguishes held offers from a full confirmed roster. Shared agenda/card offer labels are owned by the main implementation task.

The fixture server was stopped and both verification tabs closed.
