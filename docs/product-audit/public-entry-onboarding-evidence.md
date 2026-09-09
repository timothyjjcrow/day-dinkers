# Public entry and focused profile evidence — 2026-09-09 UTC

Working-tree implementation only. Nothing in this note is a production deployment claim.

## Public browsing and privacy (ID-01)

- The signed-out entry starts with court search and safe public details. Login/signup opens when requested. Court search retains its query and pages results.
- Court details show address, amenities, available hours/fees and public upcoming sessions. A session shows its format, local time, duration, court, level, price and occupied/available places before account creation. Held waitlist offers count as occupied places. No public roster identities, email or chat are included.
- Both `/g/:id` Open Graph metadata and `/api/share-preview` now hide friends-only/private game facts. Public game facts at a closed or pending court are also hidden; closed/pending court previews are unavailable in both OG and API routes. Restricted responses use private/no-store caching.
- Baseline correction: the friends-only disclosure existed in preserved release `42b567c`. `pending_submission` did not exist in that baseline; pending-court protection is compatibility work for the new local venue/location model, not a proven production leak.
- Missing links have a clear unavailable state and court-browsing recovery. Failed preview requests expose Retry. Token invitation preview remains delegated to the root-owned helper and is not covered by this note's browser proof.

## Minimal setup and focused editing (ID-03/08/09)

- A generic first visit asks only for an optional area, then opens Play. The full profile, invitation and tour chain remains an explicit replay. Missing profile details remain resumable; skipping does not falsely mark the server profile complete.
- `maybeOfferPlayerDetailsAfterJoin(game, trigger)` offers a missing photo or self-rating after confirmed participation through an optional toast action. It checks current account/roster, offers each detail once per account and opens the focused editor. Root owns integration into successful joins; the helper has executed behavioral tests, but its integrated toast flow is not yet browser-proven here.
- Both Settings and the Me header use an editor hub with a compact player-card preview and About / Playing level / Photo / Usually plays / Primary court. Saves only submit the selected section. The preview refreshes when a saved child returns.
- Playing level shows self-rating first; optional DUPR details and the rating explanation are collapsed. Player-entered DUPR is attributed wherever the shared player identity is shown. Missing numeric self-ratings remain Not set; the registration default for the old coarse skill field no longer invents a numeric self-rating.

## Keyboard and group preference foundation (ID-04/25)

- Shared radio behavior provides one tab stop, arrow wrapping, Home/End and a real selection change for onboarding, profile and both group notification sheets.
- Both group notification sheets say Save to apply and guard unsaved dismissal. Save explicitly authorizes close after the API succeeds. Group browser save/discard flows remain to verify; this is not a full accessibility certification.

## Verification

59 focused tests passed across signed-out share previews, executed public/detail/onboarding/radio helpers, profile setup/resume, auth boot, settings, scoped profile saves, ratings, planner identity preservation and Activity decisions. `node --check public/app-v15.js` passed. This includes forbidden friends/private/instant/closed/pending combinations across OG/API, no-roster public facts and account-scoped prompt behavior.

The Activity tests also cover current tournament waitlist offers: offered and unexpired while registration remains open, even after reading; old offer notifications, queued/expired/closed/accepted/left requests, elapsed holds and existing entries do not remain actionable. The kind is in Games and is essential under the existing notification policy. No device delivery claim.

Synthetic browser verification used a separate in-memory SQLite server on port 8051 and a real 390×844 app iframe, with no production data:

1. Signed-out Harbor Courts displayed usable court facts and its public session; the friends-only session was absent.
2. Court search returned six matches, then Show more reached the remaining two while retaining the query.
3. Easygoing doubles displayed the actual time, 90-minute duration, 2.0–3.0 level, Free price, two players/six places and four available places. Join opened account creation.
4. Signup retained `#game/1` and returned to the specific game. It did not join automatically. Explicit Join changed the roster from two to three and showed You're in.
5. Closing that game offered only the optional area sheet. Maybe later opened Play with My plans · 1; no profile/invite/tour chain blocked it.
6. Me displayed Self-rating not set for the new account. Its header opened the focused section hub. Playing level initially had no selected radio and one tab stop. ArrowRight selected and focused 2.5; Save succeeded. The stale hub-preview issue found during that check was corrected with the standard modal resume hook; final recheck is tracked below.

At the completed public flow, browser error logs were empty and document width/scroll width both measured 390. This verifies responsive layout and keyboard semantics in a desktop browser iframe, not physical touch, screen-reader certification, real email delivery or production concurrency.

Remaining at this boundary: final saved-preview recheck; integrated after-join prompt; group preference save/discard and onboarding radio browser flows; physical-device and assistive-technology checks. Account export/MFA entry, temporary away times and the remaining group/history/help audit requirements belong to subsequent bounded work.
