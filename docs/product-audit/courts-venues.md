# Courts and venue management: product audit

Read-only source audit, 9 September 2026 UTC. Current editable frontend and backend were inspected; the parent audit handles browser verification. Recommendations below are design judgments unless explicitly labelled a source-observed behavior or defect. No live writes, booking attempts, messages, claims or approvals were performed.

## Overall finding

The court experience now has a reasonable mobile foundation. It needs a clearer hierarchy around deciding **where and when to play**, consistent information across public/community/venue records, and reliable meanings for availability and presence. The owner workspace has also improved substantially: its next step is making ordinary updates predictable, with one save model and a useful schedule, rather than adding another dashboard.

Priority terminology: **P1** = resolve before presenting this as a dependable everyday workflow; **P2** = materially improves comprehension or efficiency; **P3** = useful later refinement. These are product priorities, not a security severity scale.

## Courts map, list, search and saved courts

**Present:** Map/List switch; 44px marker hit areas and keyboard labels; clustered markers; selected court identity, preview and highlighted pin; pan-to-reveal selection; list scroll restoration; three previews in map mode; paginated full list; recent court/city search; exact-name ranking and conservative typo fallback; saved courts; distance/rating/court-count sorting; Active now; Open now and facility filters; manual city search after denied location; stale-result preservation, retry and clear-filter recovery.

Evidence: [public/app-v15.js:4558](</Users/timothycrowley/pickleball local /public/app-v15.js:4558>), `:5046`, `:5185`, `:5230`, `:5986`, `:6080`, `:6376`, `:6513`, `:6734`, `:7583`; [backend/routes/courts.py:314](</Users/timothycrowley/pickleball local /backend/routes/courts.py:314>). Relevant regression coverage includes `tests/test_mobile_court_browsing_frontend.py`, `test_court_map_quality.py`, `test_court_discovery_frontend.py` and `test_court_filter_loader_residuals_frontend.py`.

**What is already fixed:** The old combination of a partly expanded, hard-to-scroll mobile list over an unusable map has been replaced by explicit views. Mobile full-list mode deliberately makes the map inert; switching to Map makes it interactive again. A selection now says “Selected court,” names the court, and reveals its pin above the dock. Map browsing and saving a home area are separated in an Area sheet. Do not call those historical issues uncorrected without a current browser reproduction.

**P1 — Make the map answer a player question.** A selected venue can show four equal-looking actions: venue action, Court details, Play options and Directions. The preview still makes the user decide how to navigate instead of showing the useful opportunity. Show the next joinable session or next official open play directly: date, time, level, price/access and remaining spots where reliable. Give one primary action appropriate to that result; retain Directions and full details as secondary. “Active now” must remain distinct from “court has posted opening hours.”

**P1 — Unify hours.** `applyCourtFilters` accepts Open now only when `court.open_status.is_open === true` (`app-v15.js:5187`). That state comes from the Court structured-hours record ([backend/models.py:360](</Users/timothycrowley/pickleball local /backend/models.py:360>)); venue managers edit separate free-text business hours (`app-v15.js:31694`). An owner can update displayed official hours without updating the data that drives map filtering. Use one authoritative structured weekly schedule, with venue timezone and holiday exceptions, and have filters, cards, detail and owner preview read it. Preserve “Hours unknown” rather than interpreting missing data as closed.

**P2 — Make search scope obvious.** Panning clears the typed query and reloads viewport results (`app-v15.js:4651`). That is intentional and avoids stale search results, but a small pan can appear to lose the search. Keep a persistent scope label (“Courts near …” / “Results for …”) and a visible “Search this area” transition or explicit search reset. Also label distance origin (“from map center”) because map distance is not always distance from the person (`:5040`).

**P2 — Add decision filters, not more cosmetic filters.** Existing facility filters are useful; the absent high-value dimensions are public/free versus fee/membership access, reservable versus drop-in, and scheduled play by day and skill. Only expose a filter once its data is reliable. Avoid a “no matches” experience caused by unknown fields silently treated as false.

**P2 — Saved courts should be actionable.** Keep the existing Save behavior, but use the saved list to show changes worth knowing—next session, closure or updated hours—rather than only a geographically sorted set. Clearly separate Save court from primary court and home area. These three concepts exist and are not interchangeable.

## Court detail and planning a visit

**Present:** Photo hero, directions, save/share, fees/access/hours/facility facts, conditions and weather, presence, open player sessions, recurring sessions, verified venue information, reviews, checked-in players, communities, regulars, local ranked history, court-edit suggestions and closure reporting. Detail opens a loading shell immediately and preserves the previous view/context on refresh failure (`app-v15.js:11879`).

**P1 — Reorder by intent.** The primary card is “Playing and forming now,” with “Check in to find players” as the default CTA; the venue slot and future player-organized sessions follow below (`app-v15.js:12120–12255`). That suits an arrival but not someone at home choosing tomorrow’s game. Use three clear sections or tabs: **Play here / Visit / Photos & reviews**. Lead Play here with the next relevant dated opportunities; show the existing arrival action prominently only when physically nearby or explicitly selected. Keep owner administration out of normal player scanning.

**P1 — One timeline, explicit organizers.** Community open-play notes, community-verified recurring open-play rows, player-created recurring sessions, official venue schedule and imported venue schedule are separate concepts and separate UI blocks. A player needs “Tuesday, 6 PM, Beginner open play, Venue-run, register with provider,” versus “Tuesday, 6 PM, Doubles with Alex, 3/4 players, Join.” Present one dated schedule with source badges and action-specific buttons. Do not merge participant counts or claim a reservation between these systems. “Plan this session” on a community-maintained schedule currently starts a new player plan (`app-v15.js:7300`); rename it “Plan to go with others” and explain the venue entry requirements at confirmation.

**P2 — Reduce the visit uncertainty.** The existing Before you go sheet already contains hours, fees, open play, amenities, directions and contact (`app-v15.js:7339`). Expand the data model with entrance/parking instructions, court identifiers, paddle rotation/queue rules, guest eligibility and accessibility where supplied. Put missing information in a compact “Not listed” treatment with a correction action. A full screen of unknown facts and zero check-ins should not imply the physical court is empty or unsuitable.

**P2 — Better hierarchy for secondary activity.** Court regulars, “Mayor,” ranked leaders, tournaments and results already live under More at this court (`:12300`), which is preferable to foregrounding everything. Keep those secondary. The compelling court image is a recognizable entrance/court photo plus useful schedule; a generic hero and numbers cannot substitute for that.

## Presence, check-in and current conditions

**Present:** Quiet versus looking-for-game presence; visible aggregate count versus privacy-controlled identities; check-out; foreground-only optional automatic check-in; GPS accuracy/range errors; expiring presence and periodic pings; friend discovery; live match formation. Exact device coordinates are not retained for the returned short-lived presence proof. Anonymous court API viewers receive aggregate activity, not physically present identities ([backend/routes/courts.py:903](</Users/timothycrowley/pickleball local /backend/routes/courts.py:903>)).

**P1 — Reconcile presence enforcement. Source-observed contract gap.** The current UI calls `freshCourtPresenceLocation` and requires a fresh, precise GPS fix (`app-v15.js:6978`); `check_in` validates location only if optional `presence_intent` or `presence_location` fields are supplied ([backend/routes/courts.py:2117](</Users/timothycrowley/pickleball local /backend/routes/courts.py:2117>)). A legacy request without those fields can still create presence. Decide whether all “here now” data must be verified, then enforce that policy on the server or explicitly label unverified self-reports. No live abuse or presence mutation was attempted in this audit.

**P1 — Presence is a signal, not guaranteed play.** Show “2 people shared a check-in · updated X minutes ago,” with an obvious duration/leave control. Keep “I am here,” “I want a game,” and “I reserved a place in this session” visually separate. Existing privacy copy is not fully consistent: automatic-check-in consent broadly says players viewing the court can see you (`app-v15.js:6904`), while detail and server apply friend/looking/privacy restrictions. Generate short audience copy from the actual visibility setting.

**P2 — Help when GPS fails.** Keep integrity checks, but offer a clear scheduled-session attendance path and a retry panel explaining inaccurate versus denied location. The existing suggestion to “move into the open” is reasonable but is not a universal solution for indoor venues. Do not silently classify a failed GPS check as a no-show.

**P1 — Court data moderation needs impact tiers.** Two distinct accounts can auto-apply suggested facts, including permanent closure ([backend/routes/courts.py:1406](</Users/timothycrowley/pickleball local /backend/routes/courts.py:1406>)). No verified-visit or trust threshold is shown in that consensus path. A closure disables new play and check-in. Require staff/verified owner review or stronger evidence for closure and other disruptive changes, while allowing low-impact amenities to use community confirmation. Show last confirmed date/source and allow a clear dispute/undo workflow.

**P2 — Conditions and crowd estimates need confidence labels.** Conditions are a latest-report signal, with freshness expiry ([backend/routes/courts.py:1697](</Users/timothycrowley/pickleball local /backend/routes/courts.py:1697>)). “Busiest” is computed from Third Shot check-ins in 90 days, including buckets with as few as two check-ins (`:2024`), and the detail shows only a Busiest label. Rename to “Recent Third Shot check-ins,” show sample/date range, and suppress confident popularity language with tiny samples. Allow contradictory reports to be visible rather than replacing truth with a single report.

## Reviews, photos and court contributions

**Present:** One editable star review per account, optional 500-character comment, all reviews, delete/report; gallery/lightbox with uploads, attribution, likes, deletion and reporting; suggestions and new-court submission. Existing empty photo copy asks for courts/nets/entrance—useful guidance.

**P2 — Make contributions useful to a visit.** Encourage tagged photos (entrance, parking, courts, nets), photo date and a nominated helpful cover; sort reviews by useful/recent details rather than only star value. Add small structured prompts for access, maintenance and beginner friendliness rather than a long review form. Do not represent ratings as verified visits: the review route requires login, not a completed visit ([backend/routes/courts.py:1273](</Users/timothycrowley/pickleball local /backend/routes/courts.py:1273>)). Clearly indicate review provenance if verified-attendance badges are introduced.

**P2 — Keep contributor progress visible.** An edit should show Submitted → Needs confirmation → Applied/rejected, with the old and proposed value. Closure decisions should explain what happens to existing sessions. The implementation already has suggestions/decisions; improve their visibility rather than building a second feedback system.

## Venue discovery, claim and verification

**Present:** Manage your venue entry, multi-location list, existing-court search, two-step claim, owner/manager/staff attestation, private work email, optional reference/evidence, private draft workspace, verification challenges/reviewer feedback, resubmission and publish. Owner, admin, editor and viewer states are differentiated; errors include retry and capability-unavailable states. See `app-v15.js:30522`, `:31321`, `:31447`; `venue-workspace-v15.js:12`.

**P1 — Finish the missing-venue journey.** “Can’t find your venue?” closes the workspace and sends the user to the map with a toast telling them to add a court (`app-v15.js:31331`). The owner must discover another form and then resume claiming. Keep this inside the claim flow: identify location/address, check duplicates, create a pending listing, continue the claim with progress preserved.

**P2 — Verification should have a clear next action.** The current state model is much clearer than the old management card. Add submission time, expected review window if operations can support one, outstanding evidence and one contact/support path. Do not offer made-up approval times. Prepare listing content while waiting; distinguish “role verified” from “content approved” and “published” with a short progress strip.

## Venue editor, publishing and player preview

**Present:** Venue/Schedule/Lessons/Booking tabs; clear edit entries; About/Visit/Contact editor; mobile Edit/Preview switch and live preview; local drafts; changed-field PATCH; save/error/discard handling; logo upload feedback; actual public-card preview; role-sensitive tools; publication status. These are meaningful improvements already implemented (`venue-workspace-v15.js:26`, `:118`, `:225`; `app-v15.js:30245`, `:30417`, `:31579`).

**P1 — Ordinary editing must not unexpectedly remove a live venue.** Any sensitive change—including name, phone/email, logo or booking link—sets the entire verified listing unpublished pending review ([backend/services/business_governance.py:25](</Users/timothycrowley/pickleball local /backend/services/business_governance.py:25>), `:139–170`). Registration URL changes on a schedule or offering do the same ([backend/routes/businesses.py:751](</Users/timothycrowley/pickleball local /backend/routes/businesses.py:751>), `:1124`). The UI discloses this, but disclosure does not make the workflow convenient. Store a reviewed public version and separate draft; review changed links/content while continuing to show the last approved listing. If a specific link is unsafe, hold that link immediately. Present “Saved draft / Awaiting review / Live” consistently.

**P1 — One save model.** Text saves when Save is pressed; logo upload/removal saves immediately; direct schedule-card edits persist immediately on Save session; the bulk schedule editor requires Update list and then Save schedule (`app-v15.js:31586`, `:31628`, `:31967`, `:32253`). All paths have explanatory copy, but people still have to learn multiple transaction boundaries. Stage everything in a draft with one save/publish checkpoint, or make each operation explicitly self-contained with a persistent saved-status row and undo.

**P2 — Preview the full player journey.** The owner overview preview is a stylized venue card; the Player listing preview uses the real public business renderer, which is good. Add a “View in court page” preview including the surrounding court details and schedule. This reveals conflicting hours, duplicated booking links and missing instructions before publishing.

## Venue schedule and services

**Present:** One-time, weekly and date-range sessions; start/end, audience, host, capacity, manually entered spots, registration URL, sold out/cancelled/completed/hidden states; CSV preview/import; card edit/remove; service/lesson descriptions, price text and duration; server validation and role checks.

**P1 — Manage dated occurrences, not abstract rows.** The owner schedule renders each stored pattern as a card and counts saved rows (`venue-workspace-v15.js:128–147`). Give a Week/Agenda view with actual upcoming dates, Today shortcut, past archive, visible status and one-tap duplicate/cancel. Add “this occurrence / this and future” editing and holiday exceptions; the current schedule model accepts weekly/dated/date-range records but has no per-occurrence exception field ([backend/routes/businesses.py:798](</Users/timothycrowley/pickleball local /backend/routes/businesses.py:798>)). Avoid treating a recurring cancelled row as a permanent cancellation when the owner meant this Tuesday.

**P1 — Fix timezone setup.** New session timezone defaults to the manager’s browser timezone and is editable as a raw IANA string (`app-v15.js:31971`, `:32012`). A remote manager can create the wrong venue time. Store and inherit timezone from venue location, use a human-readable selector when correction is needed, and show local date/time in previews. Source observation; no wrong-time live event was created.

**P1 — Be honest about availability.** Spots remaining is editable, not a live inventory guarantee. Show “Venue updated X ago” next to that number; when stale, say “Check availability.” Owner cards should expose spots/capacity and registration destination so an owner can spot errors without reopening every form. Current owner card displays capacity but not remaining spots (`venue-workspace-v15.js:132`).

**P2 — Separate services from scheduled sessions.** A tab named Lessons also includes memberships, clinics and other services. Rename it Services or Programs, then give templates: private lesson, group clinic, membership, court rental. A service should link to its available dates or external inquiry/booking flow instead of becoming an isolated description. Do not promise payments or real-time booking that the app does not implement.

## Booking links, connections and analytics

**Present:** Secure outbound booking/membership/lesson/open-play actions; click tracking; schedule source/freshness; link checks; generic structured JSON catalog upload; sync status, degraded/disconnected states and reports; provider integration requests; 7/30/90-day views/clicks/reported conversions. The current integration form explicitly says the executable contract is a generic JSON catalog (`app-v15.js:30789`).

**P1 — Make the handoff self-explanatory.** Use “Book with [provider]” and “Registration opens [provider]” with a clear destination. Preserve the specific event/date when a link supports it. Return users to a useful court/session context; do not mark a reservation confirmed because a link was clicked. Existing analytics correctly say they do not infer completed bookings (`app-v15.js:30954`); keep that distinction.

**P2 — Translate connections into manager tasks.** “Feed contract,” “Sync JSON,” health enums and publication capabilities are developer vocabulary. Hide the raw contract under Advanced. Main choices should be “Add booking link,” “Import schedule,” “Connect supported provider” only when available, and “Get setup help.” Status should say what players currently see and how to fix a failure, with last successful update. Avoid adding unsupported provider logos as implied partnerships.

**P2 — Simplify analytics.** Lead with listing views, booking clicks and top clicked sessions; show trend and date range. Show confirmed bookings only when a validated connected source supplies them. The current numeric formatter can turn null/missing values into misleading zeroes because `Number(null)` is finite (`app-v15.js:30963`); represent unavailable measurements as “Not connected” or “—.” Guard fast range changes against older requests overwriting a newer range.

## Team, revisions, ownership, security and operator tools

**Present:** Invitation/revoke, roles, step-up/MFA, ownership transfer/release, suspension/publishing states, change-history restoration; operator queue with claims, evidence, sensitive changes, reports, integration requests and link health; assignment and audited review actions. `app-v15.js:30599`, `:30969`, `:30988`, `:31082`, `:31123`.

**P1 — Human-readable change history.** Owner revision history prints `change_summary`; the backend generates that as two shortened hashes (`business_governance.py:157`, `app-v15.js:30979`). The owner cannot confidently restore an earlier version from a hash and actor/time. Show changed field names and before/after values; the reusable `VenueWorkspace.revisionDiff` helper already exists. Require a preview of what will change before restore, especially links and publication effects.

**P1 — Prevent simultaneous edit loss at the server.** Frontend item edits re-fetch and compare collections, which is useful (`venue-workspace-v15.js:81–103`). Schedule and service APIs still replace the whole collection without a submitted version/ETag check ([backend/routes/businesses.py:751](</Users/timothycrowley/pickleball local /backend/routes/businesses.py:751>), `:1124`). Two managers can both pass client checks and then overwrite each other. Add revision-aware conditional writes and a recovery UI that keeps the user's edits while showing the newer values. Source-inferred race; not reproduced by mutating data.

**P2 — Make permissions and pending invitations legible.** Keep the present confirmations for access changes. Show “Can edit content / can publish / can invite staff” in plain words next to each role; pending invitations need expiry and resend status. The operator page is functional but a stack of seven queues; add filters, search, assignment and overdue sorting before queue volume grows. Keep operator vocabulary and governance internals inside that privileged surface.

## Suggested acceptance walkthroughs

1. A remote player finds a court, understands access and tomorrow’s playable options, opens the correct organizer/registration flow, and returns to the same map result and scroll position.
2. An arriving player with precise GPS, denied GPS and indoor/inaccurate GPS understands what can and cannot be confirmed; quiet versus looking status matches the actual audience.
3. A manager claims an existing and a missing venue, adds hours and one event, verifies, previews the full player page and publishes without restarting steps.
4. A verified manager corrects a phone or booking link while the last approved listing stays available; draft and live versions are visibly different.
5. A remote manager changes only next Tuesday’s event, with correct venue time, updated availability and no unintended recurring cancellation.
6. Two managers edit the same schedule; the second sees a conflict with their work preserved. A viewer never sees a working destructive control.
7. An external booking link is clicked and a provider is unavailable; Third Shot never calls the booking confirmed, and the player has a clear recovery path.

These walkthroughs are proposed verification criteria. Existing source-level tests demonstrate coverage of many contracts, but they do not by themselves establish that a first-time mobile user understands the complete journey.
