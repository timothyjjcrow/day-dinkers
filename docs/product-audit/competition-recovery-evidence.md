# Competition recovery release evidence

Verified locally on September 8, 2026 (Pacific time). This records the working tree, not a production deployment. The competition source was frozen after the checks below; root owns the release build, PostgreSQL checks and deployment.

## Final behavior and contract

- `GET /api/competitions/next-matches` examines every active tournament participation and non-withdrawn league membership, independently of the paginated history/catalog lists. It returns each unresolved personal match in the current league round or active tournament. Playing/called matches and result reviews take priority. Default page size is 3; standard `limit`/`cursor` pagination reaches every remaining match. `total` is the actual match count, including multiple matches from the same league.
- Events leads with named personal matches and an **All active matches (N)** button. The full list opens the chosen match through its competition and retains a Back path to that list. Nearby filters apply date, level, singles/doubles and event type before catalog pagination. Organizer creation stays secondary.
- Tournament capacity reserves both held offers and queued priority. A read-only fetch cannot advertise a newly expired hold as available ahead of the queue. Expired offers retain the explanation and explicit signup/rejoin route.
- `TournamentEntry.partner_response_deadline_at` is nullable DateTime; `partner_history` is Text NOT NULL with application/server default `[]`. No additional fields, indexes or FKs were introduced in this final recovery slice. Legacy null deadlines remain unknown until an explicit new action. Root owns additive and production schema installation.
- Partner deadlines use the event-sensitive response window (24 hours, 4 hours or 30 minutes, capped at a future event start). Expiry does not accept a partner or expel the entry owner. The organizer previews then explicitly extends the deadline, or removes the incomplete entry. An extension never restores an expired invitation.
- Detail `my_partner_updates` is a recipient-only projection of existing entry history and archived removed-entry history: `{entry_id, status, at, next_step}`. It includes only requests whose recorded candidate was that viewer. No other candidate, private reason, or full organizer history is exposed. States distinguish expired, declined, cancelled and removed; next steps distinguish organizer review, a new partner offer, current signup options and registration closed. Current accepted/pending participation takes precedence over old status.
- Both sides still consent separately. A fresh offer waits for the entry owner; a fresh invitation waits for the invited partner. Waiting status and deadline are visible above the tabs. **Review request** opens Teams, and Accept/Decline physically reside in `td-players-panel`.
- League close counters now read **No result recorded / Marked not played / Results to review**. Unusual scores require explicit confirmation before either a current report or a closed-round review request is recorded.

## Executed tests

All backend test commands explicitly used `APP_ENV=testing TEST_DATABASE_URL=sqlite:///:memory: DATABASE_URL=sqlite:///:memory:`.

- Broad competition regression: `tests/test_competition*.py tests/test_tournament*.py tests/test_league*.py tests/test_community_me_compete_frontend.py`: **171 passed in 432.69s**. This included recipient privacy/recovery, history preservation, global next-match paging, league scheduling/round closure/correction, tournament operation/conflict/consent tests.
- Final focused frontend regression after waiting-card and Teams-panel corrections: `tests/test_competition_browse_frontend.py tests/test_tournament_partner_consent_frontend.py tests/test_league_round_operations_frontend.py`: **19 passed in 1.26s**. The broader focused frontend group also passed **43 tests** before the final wording adjustment.
- Earlier recovery-focused API/Node group: **21 passed**. This proves 35 newer history events do not hide an old active match, five active matches page without loss/duplicates, expiry does not register a player, organizer review precedes extension, consent is fresh, removed-entry history persists, and outsiders receive no private partner projection.

## Browser proof

Synthetic in-memory server on port 8056, separate organizer and partner accounts, Chrome at an instrumented 390 × 844 viewport. DOM `innerWidth` and document width were both 390. Interactions used buttons and keyboard where offscreen focus was needed; this is responsive browser verification, not physical iOS/Android touch-device certification.

1. Full doubles event → join queue → organizer removes an entry → held offer with fee, expiry and separate-partner-consent explanation.
2. Visible fixture clock expired only the synthetic offer and added another queued player. The next player received the hold; the expired player saw Rejoin waitlist and could rejoin at the back. Another opened place produced a new explicit offer. Acceptance created a pending invitation, not a ready team.
3. Synthetic partner deadline expired. The former recipient still saw **Your partner invitation expired** after maintenance cleared the pending invitation. Organizer saw **Resolve an incomplete team**.
4. Organizer reviewed and confirmed a new deadline. Recipient then saw **Send a new partner offer**, sent it, and saw **Waiting for Jamie Organizer**, the due time and no assumed entry.
5. Organizer selected **Review request**. DOM inspection confirmed Teams selected, `acceptPanel = td-players-panel`, and `acceptHidden = false`. Clicking the visible Accept button produced both player names and **Team ready**. No browser page errors were reported.
6. Events showed **All active matches (4)** although only three cards appeared in its preview. The full list included both league opponents. Opening the fourth match led to its scheduling screen; Back returned to the league, then Back returned to the complete four-match list.
7. Opening a tournament match from the same list and pressing Back returned to that tournament’s selected Final round and matchup. A second Back restored the full active-match list. No page-wide horizontal overflow appeared.

Inspected screenshots in `tmp/product-audit/`: `doubles-offer-390.png`, `partner-expired-390.png`, `partner-new-offer-390.png`, `partner-accepted-390.png`, `all-active-matches-390.png`.

## Requirement recommendations for the central ledger

| ID | Local status recommendation | Evidence |
|---|---|---|
| PL-26 | Implemented; release checks remain | Player-first hub, server filters, global next-match list; paging/API/Node/browser proof above |
| PL-27 | Implemented; release checks remain | Individual arrival and legacy honesty in tournament-operations-evidence.md |
| PL-28 | Implemented; release checks remain | Terms, explicit queue/holds, doubles consent, expiry/organizer recovery above |
| PL-29 | Implemented; release checks remain | Estimated/called/playing court board, conflicts, reviewed delays in tournament-operations-evidence.md |
| PL-30 | Implemented; release checks remain | Personal matchup and Back to correct round/list; responsive browser proof above |
| PL-31 | Implemented; release checks remain | Read-only bracket preview/fingerprint and downstream-result protection tests |
| PL-32 | Implemented; release checks remain | Structured proposals/acceptance/appointments in league-scheduling-evidence.md |
| PL-33 | Implemented; release checks remain | Distinct round/season standings, neutral ties and provisional movement in league-round-operations-evidence.md |
| PL-34 | Implemented; release checks remain | Absence/withdrawal, reviewed closure/extension and historical amendments in league-round-operations-evidence.md |
| PL-35 | Implemented; release checks remain | Scheduling-first opponent tasks and secondary score entry |
| PL-36 | Implemented; release checks remain | Compact personal cards and consent state above detail tabs; measured responsive layout |
| PL-37 | Root-owned integration | Shared agenda, calendar and invitation/offer attention are verified and reconciled by root |

Court assignments do not reserve a venue. Organizer fee/payment text does not process payment. No optional new competition formats were added. Production migration/concurrency, packaged assets, smoke checks and actual deployment are separate root-owned release gates.
