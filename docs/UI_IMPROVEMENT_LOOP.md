# Player experience improvement loop

Started September 4, 2026. Continue until the user stops the loop.

## Priorities

Make the next useful action easy to find. Strengthen the path from a first game
to familiar players, friendships, and repeat play. Address observed friction
before adding more controls. Check phone layouts and existing flows after every
change. Use isolated local demo data for verification.

## Simplification review between iterations

User direction added September 5, 2026: before each new iteration, review the
app overall for feature bloat, repeated controls, confusing navigation, and
unnecessary steps. Consider Play, Courts, Community, and Me together. Record
what should be removed, consolidated, or reworked before selecting the next
change. Prefer improving an existing path over adding a new feature or entry
point. Keep the experience easy, fun, interactive, and useful for players.
After each change, check the complete flow and visual consistency, then repeat
this overall review before the next iteration.

## Cycle 1: reconnect with people you played with

Community → Players now has Nearby, Friends, and Played together views.
The new view uses completed shared games and sessions, shows when players last
played together and shared usual play times, and offers one relationship-aware
action per person. Friends open an invite-only planner with the correct player
selected. Incoming requests can be accepted in place; sent requests remain
labeled rather than disappearing. Profile actions also update the list.

The recent-player endpoint returns current friendship direction/status. It
excludes scored games where the viewer never participated, honors blocks in
both directions, and removes deleted accounts before applying the result limit.
No schema change is needed.

Verification so far:

- 263 API tests passed, including new relationship, block, deletion, and
  participation cases.
- Browser: accepted a request, opened Play again, verified only that person was
  selected in the private planner, and scheduled the session in local test data.
- Phone layouts checked at 390px and 320px. The narrow view exposed a tab-label
  overflow, corrected with more width for Played together. Controls use the
  existing design tokens and minimum touch target.

## Cycle 2: let players pause setup

Each profile step now offers Finish later. Closing a profile step pauses the
whole flow instead of opening another sheet. Pausing is remembered per account
on this device. The server completion flag remains tied to actual saved data.
Completing a walkthrough with skipped fields also prevents it from reopening
automatically. The profile card stays in Play → My plans, where explicit resume
skips saved rating, play-time, photo, and primary-court details.

Verification so far:

- Browser: created a local account, paused setup, reloaded without another
  blocking sheet, and reached Complete profile through My plans.
- 85 related UI/release/setup tests passed; new executable transition tests
  cover account isolation, explicit resume, dismissal races, and skipped fields.

## Packaging and ongoing verification

Changes are in release r67, retaining r66 and earlier supported immutable assets.
The service-worker cache advances to r69. Generated files, static routes, and
source hashes must stay synchronized after each final source change.

Continue checking empty and failed recent-player loads, keyboard focus, the
latest narrow and desktop layouts, and resuming partially completed profiles.
Then examine community discovery and the game-to-group path for the next
concrete pain point. No production deployment has been performed.

## Cycle 3: a useful New message picker

New message now starts with up to eight existing friends, sorted by name.
Clearing a search restores that list. Search results honor the API's
`can_message` capability: eligible players open their conversation; others
open a profile with a clear connection action instead of a forbidden thread.
Phones do not receive an extra forced search focus that hides contacts behind
the keyboard. Contacts and search responses are sequenced so a slow friends
request cannot overwrite newer search results.

Browser verification showed the initial friend list, a search for an
unconnected player, and the successful handoff to that player's profile.
An executable JavaScript test also covers delayed contacts, search clearing,
permission-dependent rows, escaped names, and mobile focus behavior.

The first complete regression run had 1,241 passes and three test-contract
failures: an obsolete source delimiter already missing in HEAD, a cache-version
expectation needing the release bump, and a court-only rule applied globally
to unrelated keyboard focus restoration. Those checks were corrected; all 71
focused checks then passed. A fresh complete run is in progress.

The rerun completed with **1,245 tests passing** for cycles 1–3.

## Cycle 4: forming and managing a private group

Long friend lists now have name search in both group creation and Add players.
Filtering preserves selections. Selected counts appear above the list, and
unselected rows stop accepting selections when all available spaces are taken.
Existing members and pending invitations both count toward the 12-player cap.
A no-friends creation form offers Find players instead of a dead-end message.

The browser test created a local group with 11 invitations, confirmed that two
remaining friends could not be selected, and verified that deselecting a
player re-enabled the others. The resulting group showed one member and 11
pending invitations.

That test exposed a missing owner action: pending invitations could occupy all
spaces indefinitely. Added an owner-only Pending invitations disclosure with
Withdraw actions. The new DELETE endpoint serializes against joins using the
existing User-before-Crew locking order. It refuses accepted or changed
invitations, compares the displayed invitation timestamp to protect against
reused invitation IDs, removes the old notification, and allows safe retries
of an already withdrawn invitation. No schema migration is required.

A full Add players flow now leads to Review invitations. Withdrawal returns to
the open disclosure rather than losing the owner's context. API tests cover
capacity reuse, old-link rejection, notification cleanup, owner authorization,
accepted-member protection, stale resend protection, and cross-group IDs.
Thirty-one focused group checks passed before the final layout refinements;
full regression and browser verification of the finished withdrawal flow are
in progress.

### Local browser harness

The temporary server is `tmp/ui-loop/serve.py` on port 8012. It uses an isolated
in-memory test database and runs with `threaded=False`: the testing config's
single SQLite connection cannot be shared by simultaneous requests. Earlier
intermittent browser errors were traced to that harness, not the production
connection pool. The executable at `tmp/ui-loop/browser` points to the installed
agent-browser CLI, avoiding repeated `npx` package-resolution delays.

The finished group-management browser flow is verified: full group → Review
invitations → withdraw one → list remains open and focus moves to the next row
→ Add players reports one available space → selecting one disables the other
candidates → replacement invitation succeeds. Screenshots are saved under
`output/ui-loop/`. The local test data is synthetic and no production messages,
invitations, data changes, or deployment were performed.

The full regression completed with 1,249 passing tests and two callback-signature
assertions needing the new invitation-view options. Both were updated to check
the retained parent refresh and forwarding those options. All 57 affected tests
then passed. A subsequent 83-test UI, offline, and release run also passed after
the final group-state refinements.

At 320px, pending invitation rows wrap without horizontal overflow and Review
invitations moves keyboard focus into the open list. Desktop light-theme layout
also passed visual review. The no-friends creation path reaches player discovery;
the empty Played together path reaches Find games; keyboard arrow navigation
preserves focus on the selected Players tab.

Group planning now explains the waiting state before any player has accepted,
with a disabled planner and a useful owner action. Full groups go straight to
Review invitations. Redundant empty-session and pending notes are hidden in this
state. Accepting the invitation as a second synthetic player immediately enabled
planning; the planner showed the two accepted players and the correct court.

## Cycle 5: useful, accurate player context in the planner

Played together now shows the actual shared usual play times. Play again and
the profile's shared-time shortcut use the existing scheduling module to pick
a future time shared by both players, retaining the exact invitee and private
audience. A passed morning no longer wins over a shared evening later today.
Missing or disjoint preferences retain normal planner choices. Four executable
tests cover future scheduling, no-overlap fallback, and player identity across
saved/restored snapshots.

Browser verification uncovered that planner snapshots dropped numeric
self-ratings and DUPR details, falling back to a different legacy level. Those
fields now survive sanitization; unknown levels stay unknown. Usual play times
are labeled as preferences instead of claiming the person is free.

The 320px picker also squeezed names and ratings into unreadable two-column
pills. It now fits columns to available width, wraps player details, displays
the selected checkmark, and keeps selected metadata legible. Selected court and
time summaries wrap instead of clipping essential details.

Full regression passed **1,254 tests** through the shared-time change. The later
identity/picker changes passed 27 focused planner/profile checks, and release /
offline checks passed before the final CSS layout correction. Browser confirms
the selected player's 3.5 rating, future shared time, private audience, and no
horizontal overflow at 320px. Final desktop and release verification continues.

The final desktop picker has two readable columns, with selected checkmarks and
metadata in the selected text color. Release/offline checks passed again after
the CSS correction. Updated screenshots are in `output/ui-loop/`.

## Cycle 6: get from a known court to local play

The home-area picker offers Use my primary court’s area when a primary court
is set. It reads that court only after the explicit click, validates coordinates,
and uses the existing home-area save flow. Failed or invalid reads retain the
picker and restore controls; duplicate clicks cannot start another save. A
missing current area now uses Maybe later instead of Keep current area.

An executable test covers delayed reads, double clicks, valid saves, invalid
coordinates, and accounts without a primary court. Seventy-one related checks
passed. Browser verification at 320px confirmed the shortcut's layout, saving
Costa Mesa from the synthetic court, and the empty-game → nearby-courts path.
Network response mocking was bypassed by the active service worker; invalid
coordinates were verified by the executable test instead of claimed as a
browser result.

The court path exposed an ambiguous Find people to play here button that led
straight to on-site check-in. It now says Check in to find players before
arrival and Look for a game for someone already checked in quietly. The empty
court explains that players can check in on arrival or create a game for later.
Fifty-eight focused court, instant-play, and area checks passed.

### Next observed issues to examine

- At 320px, the court's visit-fact cards truncate Hours not listed and No
  schedule into incomplete fragments. Preserve readable access information.
- The general planner's My friends audience still says Everyone you follow.
  Align that copy with accepted friendships and confirm audience behavior.

A fresh full regression run is checking the accumulated implementation after
cycle 6. The browser confirmed the new court check-in label at 320px.
The profile shared-time shortcut also opened the correct player and time;
the new picker passed a 320px dark-theme review with readable selected metadata
and a visible checkmark.

The final full regression for cycles 1–6 passed **1,256 tests in 241.10 seconds**.
Frontend compilation, syntax validation, source-hash/release checks, and
`git diff --check` also passed. The goal remains active. Continue with the
observed access-detail and audience-copy issues above.

## Cycle 7: readable court access details and audience language

Court visit facts wrap instead of clipping hours, fees, and venue open-play
information. At 380px and below, the open-play fact has its own full-width row.
The 320px browser check shows complete missing-data labels without overflow.
The planner says Friends can join instead of Everyone you follow; the backend
audience uses accepted friendships and excludes blocked pairs. Forty-one
focused court, planner, and release checks passed.

## Cycle 8: help a new group become a regular game

Groups with accepted players and no upcoming session now show Make it a regular
game. If at least two accepted members share usual play times, the card names
the suggested time and how many members usually play then. Review a weekly plan
opens the existing planner with weekly repetition, the suggested local weekday,
the accepted roster, the group court, and the private audience. It creates
nothing until Schedule is pressed. Groups with upcoming play show that play
instead of another setup card.

Two new executable tests cover local-day scheduling after a passed shared
morning, preserving group consent/version and accepted invitees, and leaving
the time flexible when no usual preferences exist. Seventy-two related UI
checks and 29 group/planner/API checks passed; 11 release/offline/shortcut checks
passed after the build.

The browser created a synthetic private Saturday session in America/Los_Angeles
with a September 19 end date. It appeared in the group's upcoming play, replacing
the weekly setup card. Jordan's account saw an invitation with Accept invitation
and Can’t make it; the API confirmed that Jordan was invited but not joined.
Joining a group therefore did not imply an RSVP. No production invitations or
messages were sent. A full regression is in progress.

Next observations to inspect:

- The 320px game-detail header is cramped and repeats attendance counts. Weekly
  recurrence copy also exposes raw timezone identifiers in a small pill.
- Removing a friend or blocking a player reloads the profile, but the underlying
  recent-player view may retain its old relationship until it refreshes. Confirm
  this in the browser and keep the return path coherent.

Full regression for cycles 1–8 passed **1,258 tests in 233.48 seconds**.
The frontend build, syntax check, release checks, and whitespace check passed.
The goal remains active; continue with the observed session-detail and
relationship-return issues. The local browser is currently Jordan's synthetic
account viewing its unaccepted invitation to weekly game 5. Alex owns group 1;
both are accepted group members, but only Alex has RSVP'd to that game.

## Cycle 9: readable game details on a small phone

The game header now keeps its title and Close control separate from the summary.
Chat and Share have labeled buttons. Roster availability appears once, and the
audience appears beside the game format. Pending invitations retain the game
time as their headline, with the inviter named below. Court names and player
roles wrap instead of clipping.

Weekly repetition has a readable block with a local clock and timezone label
(for example, Repeats Sat at 10:00 AM PT), followed by the end date. The clock
formatter preserves the series' standing local hour regardless of the viewer's
timezone; executable cases cover Pacific and Tokyo environments, midnight,
missing/invalid clocks, and unfamiliar legacy timezone names.

Browser checks covered a host at 320px in light mode, an invited player at 320px
and 1280px in dark mode, and a completed scored game at 1280px. No horizontal
overflow appeared. Chat opened and returned to game details. The invitation
remained pending; no RSVP was inferred. Screenshots are in `output/ui-loop/`.

## Cycle 10: coherent friendship and safety return paths

Accepting a request from a profile refreshes that profile, preserving a single
Message button and exposing the correct friendship actions. Removal refreshes
the visible Community or Play view, so a recent-player row immediately changes
from Play again to Add friend. Blocking removes the player from discovery and
returns to the refreshed list. It no longer attempts to fetch a blocked profile,
which the API intentionally returns as unavailable. Settings unblocking also
invalidates the relationship-dependent views.

A browser trace exposed a shared confirmation timing bug: the old helper
resolved before its Back traversal completed, allowing a fast API response to
replace the parent before a delayed popstate then dismissed it. Confirmation
results now resolve from the history-aware dismissal callback. An executable
test verifies delayed resolution, double-tap protection, and cancellation via
cleanup.

Browser verification covered acceptance, removal, block, Settings unblock, and
one-step return from the profile. A locally injected failed removal preserved
the friendship and profile, restored the action, and focused its readable error.
Cancelling the confirmation returned focus to Remove friend. The CLI's route
abort did not intercept the request even without a service worker, so the actual
failure check used a narrowly scoped, temporary window.fetch rejection in the
synthetic browser. That override was restored afterward.

The first full run found an undersized unread badge and an outdated source
assertion expecting timezone access inside gameScreenHtml. The badge now uses
the app's minimum text token; the assertion follows the extracted formatter.
Eighty-eight focused design, recurrence, attendance, confirmation, Community,
and release checks passed. A fresh full regression is running against the final
source. The goal remains active.

### Local browser harness

Use session `pickleball-ui-review`. It was opened with
`--init-script tmp/ui-loop/no-service-worker.js`, which disables registration
only on localhost:8012. The old `pickleball-loop-final` session served stale
immutable assets even after cache clearing. Registration count, cache count,
and service-worker controller are now verified empty in the new session.
`tmp/ui-loop/refresh-browser.sh` targets that session and release r67. Production
service-worker behavior is unchanged; these browser checks are not offline tests.

Next opportunity to inspect: the existing Play again planner can optionally
save players as a group, but the group API uses the original game's eligible
players while the planner permits changing the next game's invitees. Verify
that the review copy and selected people match the invitations actually sent.

The fresh full regression passed **1,260 tests in 248.80 seconds**. The build,
JavaScript syntax, focused release checks, and whitespace check also passed.
All current changes remain local and uncommitted. No production data changed.
The goal remains active.

Fixture checkpoint: Alex and Sam are now accepted friends; Alex and Jordan are
not friends, but both remain accepted members of group 1. Taylor is unblocked.
Jordan's game 5 invitation is still pending. The temporary fetch failure override
and network route were removed. The local single-threaded server remains on
port 8012.

## Cycle 11: start a group without choosing the next game first

Completed games now offer Start a play group beside Play again. The group review
uses the completed game's eligible co-players, lets the player choose exactly
who to invite, and shows its default court and editable name. It starts with no
players selected. It creates a private group only on Start play group; nobody
is joined automatically, and no future game is created. Existing members see
Open play group, while invitees see Review group invitation and the normal
Join/Decline choice.

The source-game group API accepts an explicit co-player selection. It rejects
self, outsiders, non-playing RSVPs, empty selections, and more than 11 invitees.
If a selected player becomes unavailable while the request waits for locks,
the API returns a review error before writing a group or invitations. Omitted
selections retain the legacy eligible-source-roster behavior, now with the same
12-person cap as other groups. Retries retain the existing source group's
identity and do not send another batch of invitations.

New Play again forms no longer include the optional group-save checkbox. Older
saved drafts retain their option, with explicit copy that it refers to the
original game's eligible players. A replay attaches to an existing group only
when every original player has accepted membership. Otherwise, it retains the
full original roster as an independent invite-only game. This prevents a smaller
saved group or unanswered invitations from changing who Play again selects.

Group forms freeze their reviewed inputs during a pending creation request and
use a fixed invitation snapshot for success reporting. Returning to the finished
game updates its action to Open play group. Async child replacements now restore
their Back label after the loading header is replaced; a targeted executable
test ensures a late result cannot decorate an unrelated newer screen.

Browser checks used a 320px light review and a 320px dark search/selection review,
plus a 1280px dark completed-game and pending-invitation view. Selecting Jordan
and Sam from a four-player game created group 2 with exactly those two pending
invitations; Morgan was omitted. A 13-player source allowed 11 selections and
disabled the remaining player; filtering preserved the selections. A held local
request confirmed the form became inert and retained the chosen invitation
payload. The resulting group used Back, and one Back returned to Open play group
on the original game.

The replay of that four-player source created private game 7 with only Alex
joined. Separate reads as Jordan, Sam, and Morgan confirmed that all three had
pending invitations and none was joined. Game 7 has no group attachment, because
the smaller group's invitations remain pending. No production data changed.

Four new API tests cover exact invitations/no game creation, invalid selection,
eligibility changes during locking, and the 12-person limit. Two executable
frontend tests cover async child Back labels and replay membership attachment.
The focused API/UI run passed 56 tests; later navigation/release checks passed
91, and replay/weekly-planning checks passed 29. An intermediate full run found
one old source assertion expecting a single hardcoded friends endpoint; the
assertion now covers both supported entry paths and the submitted selection
snapshot. The final full regression is in progress.

The synthetic server was restarted to load the API change, resetting prior
fixture mutations. It is now session 20632, still single-threaded on port 8012,
with logs in `tmp/ui-loop/server.log`. Games 1–4 are the original scored pairs;
game 5 is a completed four-player session and game 6 a completed 13-player
session. Group 1 is the startup Weekend regulars fixture. Group 2 comes from game
5 (Alex owns it, Jordan and Sam are pending); group 3 comes from game 4 (Alex owns
it, Taylor is pending). Game 7 is the independent replay scheduled for September
11 at 6 PM Pacific. Initial friendships were restored by the restart. The old
`pickleball-loop-final` browser session is closed; use `pickleball-ui-review`.

A temporary browser response mock also verified the new eligibility-change
recovery: the list removed the unavailable selected player, retained the other
selection, restored form interaction, and focused the review error. The fetch
override was restored; no group was created by that check.

Next observations:

- The group-player search currently contributes to the generic form draft even
  though it is only a filter. Exclude it so reopening does not restore an
  irrelevant query or claim that an otherwise unchanged group form is a draft.
- A new private group's empty chat says only No messages yet — pick the next
  time, even when the owner is alone and invitations are pending. Explore a
  useful introduction/coordination starting point that prepares an editable
  message without sending it or replacing an existing draft. The 320px baseline
  is `output/ui-loop/third-shot-empty-group-before-320.png`.

Final cycle 11 regression passed **1,266 tests in 506.19 seconds**. After the
last filter-draft and replay-copy cleanup, **113 focused checks passed**,
including group flows, draft/form behavior, planner membership, and release
packaging. Build, JavaScript syntax, and whitespace checks passed. The browser
confirmed that searching for a player no longer creates a group draft and that
reopening has an empty filter, no false draft banner, and an explicit fresh
player selection. The original-player replay now says players rather than
teammates, since its roster includes opponents too. The goal remains active.

## Cycle 12: help a new group start its first conversation

An empty private-group chat now offers Say hello, Find a time, and Ask for a
court tip. Each choice fills an empty composer with editable text and uses the
normal draft persistence and resizing path. It never sends a message, replaces
existing text (including whitespace), or clears an attachment. Starters hide
while a draft is present. Empty group chats no longer focus the composer on
opening, leaving the welcome and choices visible before a player chooses to type.

The welcome distinguishes a group with players from an owner waiting for
invitations to be accepted. Chat reads return the existing member-visible group
summary, so polling can keep the title, player count, and welcome current when
someone joins. The shared outbox accepts an optional empty-state renderer:
removing an unsent message restores the group welcome; real messages replace it.

The desktop visual pass found an existing white-on-white photo button in light
mode. Photo buttons now share a themed secondary icon style across direct and
group/room composers. Message sending behavior is unchanged.

Four executable frontend tests cover draft preservation, input length/events,
welcome copy, empty-outbox recovery, and optional composer focus (some checks
are grouped within a test). An API test covers pending versus accepted chat
access and current membership metadata. Sixty-one focused API, privacy, chat,
release, and design checks passed. Twenty-nine focused checks passed after the
photo-icon theme fix. Full regression passed **1,271 tests in 510.54 seconds**.
The final JavaScript syntax and whitespace checks also passed.

Browser evidence: the 320px dark welcome fits without horizontal overflow and
does not focus the empty composer. Say hello created a draft and focused the
composer. Jordan accepted group 2 while its chat stayed open; polling changed
the welcome and count to two players without altering the draft. An edited time
starter survived Back and reopen; attempting another starter preserved it.

A temporary fetch rejection blocked all synthetic chat POST attempts before
they reached the server. The message stayed in the outbox and hid the welcome;
removing it restored all three starters. The outbox was empty before restoring
fetch, and a server read confirmed zero messages. A separate read-only incoming
message fixture replaced the welcome while preserving the composer draft; its
override was removed and the page reloaded afterward. No messages were sent.

The synthetic server was restarted to load chat metadata. Session 57608 runs
the same single-threaded localhost:8012 harness. The script now recreates source
groups 2 and 3 and replay game 7 on startup. Group names are Sunset Park regulars.
Jordan has since accepted group 2, leaving Sam pending; group 3 still has only
Alex with Taylor pending. Alex has an unsent draft in group 2:
What days and times usually work for everyone? We can pick our next game from
there. Saturday mornings work for me.

Final visual evidence is saved in `output/ui-loop/` as
`third-shot-group-welcome-dark-320.png` and
`third-shot-group-welcome-light-1280.png`. Both fit without horizontal overflow;
the empty composer stays unfocused and the photo icon is visible in each theme.
The browser remains Alex in group 3's empty chat, dark theme at 320 × 800.
The improvement goal remains active.

## Cycle 13: keep the next session within reach of the conversation

Group chat now keeps a compact next-session card above the conversation: local
date and time, full court name, and the player's own invitation, attendance, or
waitlist status. Opening it retains the conversation; Back returns to the same
draft. With no visible upcoming session, a joined group of at least two players
gets Plan with this group. The shortcut fetches the current accepted roster
before opening the existing private-session planner. Pending invitations are
not silently included. Creating a session updates the retained chat card.

The chat endpoint returns one visible next-game summary. Group detail and chat
share the same chronological selection and visibility rules, including private
invite snapshots, blocked participants, cancellation, and the existing 15-minute
start grace window. Membership alone does not reveal an older private session.
The card updates through the existing chat poll without replacing its button
or touching composer content. The actual RSVP happens in session details.

The new flow exposed a pre-existing header issue: replacing the planner with
session details, or repainting those details, could turn a child sheet's Back
control into Close. Game rendering now restores its child navigation control
on every paint. Browser verification confirmed Back before and after an RSVP.

Final focused regression: **97 tests passed in 20.97 seconds**, covering group
privacy and membership, next-session selection, RSVP updates, blocked former
hosts, planner behavior, chat history/outbox/images, game navigation, and release
assets. Three additional API scenarios and two executable frontend tests cover
the new card. Frontend build, JavaScript syntax, and whitespace checks passed.
The previous cycle's full regression remains 1,271 tests; this cycle used the
focused checks above rather than repeating that entire suite.

Browser evidence: Alex opened the planner from chat, selected a time and the
accepted Jordan, and created private group session 8. The card immediately
showed You're in, and the saved time-starter draft remained unchanged. Jordan
opened the invitation from group chat, accepted it, and returned to a card that
now said You're in with keyboard focus on the same button. There is no mobile
horizontal overflow. Both dark 320px and light 1280px views were inspected.
No chat messages were sent; the server still reports zero messages in group 2.
An initial automated tap on the offscreen planner Next control did not advance;
a repeat with the button scrolled into view and its hit target verified did
advance normally via a native click. No application error was observed.

Screenshots in `output/ui-loop/`: `third-shot-chat-plan-empty-320.png`,
`third-shot-chat-next-session-320.png`, `third-shot-chat-invited-320.png`, and
`third-shot-chat-next-session-light-1280.png`.

The synthetic server now runs in session 92367. Its startup script also accepts
Jordan into group 2; group session 8 was created through the browser and is not
recreated on restart. Both Alex and Jordan have joined game 8, scheduled for
September 5 at 10 AM Pacific. Independent original-player replay 7 remains
unchanged. Alex's group-2 chat draft remains saved. The improvement loop stays
active.
The follow-up planner used to verify the native Next tap was discarded without
creating a second session. The browser is restored to Alex, dark theme at
320 × 800, in group 2's chat.

## Cycle 14: read the practical details before visiting a court

The three compact Today, Fees, and Open play facts now open a Before you go
sheet. Each shortcut focuses its matching section beneath the retained Back
header. The sheet preserves full fee/access notes, weekly hours, recurring
open-play times, level/cost notes, rotation guidance, listed facilities, and
safe directions/contact/reservation links. It labels the content as the
community-maintained listing and keeps missing fields explicitly unlisted.
It does not infer that an absent facility is unavailable or turn a published
open-play schedule into a reservation or RSVP.

Weekly hours support split windows, overnight closing times, and full-day
intervals; legacy free text and dawn-to-dusk remain available. Long schedule
notes are readable at 320px. Day/time, level/cost, and instructions use distinct
visual weight. Link actions match the button styling used elsewhere in the app.
The court's compact facts keep their existing grid with a full-tile hit area
and a visible chevron, while their accessible labels stay concise.

Three executable frontend tests cover complete hours/notes, missing fields,
overnight and full-day windows, HTML escaping, unsafe reservation links, and
focus ownership after dismissal. Final focused regression: **78 tests passed
in 3.00 seconds**, including court discovery, open-play API/consensus behavior,
existing court actions, and release packaging. Build, JavaScript syntax, and
whitespace checks passed. One intermediate release test raced the asset builder
and observed its temporarily absent manifest; rerunning after the completed
build passed. Keep future release checks sequential after build completion.

Browser evidence covers empty data and a read-only fetch fixture with long
hours, fees, newcomer instructions, split/overnight windows, and facilities.
The Fees and Open play headings remained below the sticky header, no horizontal
overflow occurred, and Back restored focus to the original court fact. The
320px dark and 1280px light views were inspected. No listing edits, reservations,
phone calls, external visits, or messages were made. Fixture script:
`tmp/ui-loop/visit-details-fixture.js`; it only overrides GET /api/courts/1 in
the current page and is removed by navigation/reload.

Final screenshots in `output/ui-loop/`: `third-shot-visit-shortcuts-320.png`,
`third-shot-visit-openplay-320.png`, `third-shot-visit-hours-320.png`, and
`third-shot-visit-hours-light-1280.png`.

Next audit priorities grounded in this pass:
- Court Quiet now / No one is here yet copy treats absence of app check-ins as
  physical court occupancy. Make the empty/live activity language accurately
  describe app-reported activity, consistently across cards and court detail.
- The photo-less court hero puts a decorative pickleball behind the court name
  at 320px. Rework its placement/contrast so it never competes with the title.
- The existing open-play planner shortcut advances a session by a full week
  when it starts within 50 minutes, and uses the browser timezone even when
  court structured hours name another timezone. Audit the intended near-term
  scheduling behavior and timezone correctness before changing that flow.

The improvement goal remains active. Server session 92367 and synthetic game
and group state are unchanged from cycle 13.

## Cycle 15: accurate court activity and a readable court header

Court cards and map previews now say No players checked in rather than Quiet
now. Positive counts say checked in, including map marker accessible names.
Court detail labels the source as Shared on Third Shot and describes current
check-ins without suggesting that the physical courts are empty. The player
section is labeled Checked-in players. Redundant activity pills were removed;
posted session counts and the closed-court status remain available.

The accessibility pass also found that a court with zero current check-ins
omitted its later sessions from the accessible card description. It now
announces both, matching the visible card summary.

The court hero uses normal document flow for its title and grows with long
names. Save and Close/Back remain above the title. The photo-less fallback is a
simple green background, so a decorative ball no longer sits behind the text.
Loaded photos fill the resulting header height, with enough shading behind the
name to keep bright images readable. A failed image leaves the same stable
fallback and text layout.

Final focused regression: **77 tests passed in 1.85 seconds**. Existing court,
map, pickup, privacy-safe aggregate, and release contracts were updated for the
new wording and layout. Build, JavaScript syntax, and whitespace checks passed.
No backend behavior or data mutation was required.

Browser evidence: a 320px long-name fixture kept the title 8px below the controls
and left 534px for the scrollable court content. Two anonymous check-ins were
shown as an aggregate with identities still hidden. The loaded-image 1280px
light view filled the 176px header exactly. A missing-image fixture removed the
broken image and retained the layout without horizontal overflow. The final
list announced No players checked in, 1 later session, matching its visible
text. No application errors were reported after the fixtures were removed.

The read-only fixture `tmp/ui-loop/court-signals-fixture.js` overrides only the
court-detail GET. The successful-image check used the existing local
`screenshots/courts-wide.png` as a bright layout fixture, not a real court photo;
the image-failure check requested a deliberately missing local file. Both were
removed by restoring fetch and navigating. No production or synthetic records
were changed. The browser is Alex, dark theme at 320 × 800, on court 1.

Screenshots in `output/ui-loop/`: `third-shot-court-signals-final-320.png`,
`third-shot-court-list-signals-final-320.png`,
`third-shot-court-long-title-320.png`,
`third-shot-court-photo-light-final-1280.png`, and
`third-shot-court-failed-photo-320.png`.

The next audit remains the open-play planning shortcut: near-term starts can
silently jump a week, and court timezone handling needs end-to-end verification.
The improvement goal remains active.

## Cycle 16 — Trustworthy open-play planning times

The court's Plan this time shortcut could silently skip a start less than
50 minutes away, or replace an exact supplied time with a generic suggestion.
It also interpreted posted court hours in the browser's timezone. The shortcut
now computes the next future occurrence in the court's stated timezone, with
America/Los_Angeles as the California fallback. Courts without a known timezone
still use the browser fallback; this pass does not invent timezone data.

The conversion validates the wall clock, skips nonexistent daylight-saving
starts, and uses the first occurrence of an ambiguous repeated hour, matching
the backend's recurrence rules. Exact future times stay selected even when
outside the half-hour suggestions or eight-day quick-picker window. An expired
supplied start requires a new selection rather than silently choosing a default.

The planner expands Selected date and time before the other suggestions, labels
the player's timezone in familiar language, and explains the timezone used for
repeat days, times, and end dates. Creation and editing validate the end date
against that schedule calendar. The source banner retains the open-play schedule
label instead of claiming previous teammates are invited. Empty avatar space is
removed from banners without selected players.

Five executable frontend tests cover near-term starts, multiple browser zones,
quarter-hour offsets, daylight-saving gaps and folds (including Lord Howe's
half-hour change), exact planner inputs, cross-calendar end dates, and truthful
source-banner copy. The full suite completed with **1,281 passed and two stale
copy-contract failures** in 236.51 seconds. Both failures expected the replaced
daylight-saving help sentence. After updating them and correcting the banner,
**61 focused planner, recurrence, edit, and release tests passed** in 3.51 seconds.
The final CSS adjustment was followed by **15 tests passed** in 1.51 seconds.
Build, JavaScript syntax, and whitespace checks passed. Full-run log:
`tmp/ui-loop/cycle16-tests.log`; focused log: `tmp/ui-loop/cycle16-focused.log`.

Browser verification used the read-only court GET override in
`tmp/ui-loop/open-play-time-fixture.js` with a Tokyo schedule and a Pacific-time
browser. A near-term 7:24 AM Pacific input matched the fixture's exact
2026-09-05T14:24Z instant. A separate cross-day session survived Keep draft and
Resume earlier plan with Saturday 4:10 PM Pacific and Sunday repeat day intact.
A Saturday end date was rejected and focus returned to that field; Sunday was
accepted. The authoritative saved game confirmed scheduled_at
2026-09-05T23:10Z, recurrence_timezone Asia/Tokyo, local time 08:10, Sunday, and
end date 2026-09-06. No production records or messages were created.

The earlier synthetic server had stopped, so it was restarted from serve.py.
Earlier temporary games 8 and 9 no longer exist. The current synthetic game 8
is the cross-day open-play session above, with Alex as its only joined player.
The earlier near-term save had already been verified before that restart.
The fixture was removed and the browser returned to real synthetic court 1,
Alex, dark theme at 320 × 800. Current local server session: 27509.

Final screenshots in `output/ui-loop/`:
`third-shot-open-play-selected-final-320.png`,
`third-shot-open-play-repeat-final-320.png`, and
`third-shot-open-play-light-1280.png`. The 320px view had no horizontal overflow;
both themes kept the exact-time input and help readable.

The next audit should return to community value: getting from an upcoming game
to useful group coordination, and checking that invitations, chat, and recurring
sessions feel like one coherent flow. The improvement goal remains active.

## Cycle 17 — Simpler group coordination

The overall review covered Play, Courts, Community, and Me before choosing the
change. Their main destinations still have distinct jobs. The sharpest local
clutter was the group overview: a second weekly-planning prompt, repeated
pending-invitation copy, and invitation management ahead of upcoming play.

Removed the separate weekly-planning card, shortcut helper, and unused styles.
The single group planner still suggests shared availability, carries accepted
members, and exposes Repeat this schedule. Pending invitations and Add players
now live inside Players. A group waiting for members shows chat and the open
player section instead of a disabled planning button. The group count retains
pending-invitation information without another warning banner.

Upcoming play now appears before administration. The next session shows its
local date, court, roster count, and the player's own invited/joined/waitlisted
state. Later sessions use a collapsed More upcoming sessions disclosure.
An authoritative game update refreshes the retained group row, preserving its
button for return focus; cancelled/finished dates are removed. Session creation
and group chat keep their existing paths. No new feature, endpoint, or data
model was added.

Validation: the broader frontend suite plus release and crew-chat-context checks
passed **570 tests in 8.88 seconds**. After preserving the keyboard return target,
**27 focused tests passed in 1.23 seconds**. Four focused executable/structural
checks cover personal RSVP state, an explicit zero-spots count, disclosure of
later dates, the single planning path, cancellation, detached views, and stable
return focus. The former weekly-shortcut tests now verify the shared group
planner's accepted roster and availability behavior. Build, JS syntax, and
whitespace checks passed. Logs: `tmp/ui-loop/cycle17-frontend-tests.log` and
`tmp/ui-loop/cycle17-tests.log` (the initial targeted run had one obsolete copy
assertion, corrected before the broader pass).

Browser: at 320px, Jordan saw You’re invited, opened game 9, accepted, and returned
to You’re in with 2/4 in and 2 spots left. Back restored focus to that exact
session card. Group chat showed the same date and personal status. Alex's waiting
group showed chat and Add players without disabled planning; the capacity-filled
group's Review invitations opened both player and invitation disclosures and
focused the first withdrawal action. No invitations were withdrawn. The single
planner still showed the repeat checkbox and only Alex/Jordan as selected group
players. Dark phone and light 1280px layouts were reviewed without overflow or
application errors.

Synthetic data change: game 9 is now a private crew-2 session titled Sunday group
game, scheduled 2026-09-06T14:48:27.261Z, capacity 4, duration 90 minutes, with Alex
and Jordan joined. Creation used the local fixture API; Jordan's acceptance used
the app UI. No production data or chat messages were sent. Browser fixtures are
cleared; Alex is at the Community root, dark theme, 320 × 800.

Screenshots in `output/ui-loop/`: `third-shot-group-before-320.png`,
`third-shot-group-next-final-320.png`, `third-shot-group-waiting-320.png`, and
`third-shot-group-next-light-1280.png`.

Overall review after this iteration: the four main destinations remain intact;
this change reduces competing controls inside groups. Next, inspect repeated
chat and share actions in game details and consolidate them if the existing
header actions can provide a clear, accessible route. Preserve important RSVP,
host, and safety controls. The user's between-iteration simplification rule is
recorded above and applies before every subsequent change.

## Cycle 18 — Remove empty and repeated game-detail controls

The simplification review after cycle 17 found two general Share buttons in game
details, plus an empty chat-preview card repeating the header's Chat action.
The preview is useful when it carries a message or unread activity, so this pass
keeps that information while removing the empty duplicate entry point.

Share remains in the labeled header toolbar. Its duplicate in host tools and
in the joined-player secondary actions was removed, along with the redundant
binding. The contextual Share link within the invitation choices remains next
to Friends and Court chat because those choices describe how to fill open spots.
Host editing, rescheduling, cancellation, RSVP, and calendar controls remain.

The chat-preview card appears only for joined players when there is a preview
or unread activity. If only the unread count is available, it says how many
messages are unread rather than saying there are no messages. Its short Chat
heading leaves room for the unread badge at 320px. Header Chat remains available
for starting a conversation when there are no messages.

Verification: **25 focused frontend/API/release tests passed in 4.08 seconds**;
a final shorter-label pass had **18 tests pass in 0.80 seconds**. Existing host
management contracts now assert the single general Share action. Build,
JavaScript syntax, and whitespace checks passed. Log: `tmp/ui-loop/cycle18-tests.log`.
An initial command referenced a nonexistent continuity test file and ran no
tests; the corrected command above completed successfully.

Browser: the empty synthetic game has one general Share action, no empty chat
preview, and a working header Chat action opening the private session composer.
A temporary navigator.share recorder confirmed the handoff URL was /g/9 without
sharing anything externally. Read-only GET fixtures in
`tmp/ui-loop/game-preview-fixture.js` verified a message preview plus two unread
messages, and the unread-only fallback. At 320px the final Chat 2 new heading
fit its 186px content area without clipping. Light desktop and dark phone layouts
were inspected. The fixtures and share recorder were removed by navigation;
no messages or data mutations were made in this cycle. The browser is Alex,
dark theme at 320 × 800, on crew 2. No application errors were reported.

Screenshots in `output/ui-loop/`: `third-shot-game-before-simplify-320.png`,
`third-shot-game-simplified-320.png`,
`third-shot-game-chat-preview-final-320.png`, and
`third-shot-game-chat-preview-light-1280.png`.

Overall review after this iteration: no additional feature or navigation level
was added. Game details and group information now expose fewer empty or repeated
actions while retaining useful conversation context. The next candidate is
Community navigation: review message filters and the distinction between private
play groups and public communities before adding any further coordination UI.
Continue applying the simplification review before every iteration.

## Cycle 19 — Groups opens the group; Messages opens the conversation

Previous goal turn classification: progress — cycles 17 and 18 changed the local
app and were verified. Before this iteration, the overall review kept the four
main destinations' jobs in view: Play organizes games, Courts supports local
discovery, Community connects people, and Me holds personal details. Within
Community, the strongest friction was that both Groups and Messages opened the
same chat screens. Reaching plans or membership from Groups required a detour
through chat and its header. No extra message filter was added.

Groups now opens private-group information or public-community information
directly. Messages preserves its existing direct-to-chat routes. Group cards
show membership/court context instead of the latest chat message and their
accessible names describe opening group info. Existing groups appear before
the find/create prompt. Redundant per-card type badges are omitted in Groups,
where section headings already distinguish private groups and communities;
full group names can wrap on narrow screens. Messages retains type badges,
message previews, timestamps, and its existing filters.

Opening group information leaves conversation read state alone. Closing either
kind of view still refreshes the list once, without background inbox requests
while that view is open. The now-unused preview wrapper was removed; the same
escaping and preview-text helper still serves message cards.

Validation: **160 focused Community, interaction, privacy-route, and release
checks passed in 4.33 seconds** after the final layout cleanup. Two executable
checks render the real inbox function and run the actual row binding, covering
private/public group-info versus chat destinations, membership previews,
escaping, unread context, discovery ordering, empty groups, and one refresh on
return. Build, JavaScript syntax, and whitespace checks passed. Log:
`tmp/ui-loop/cycle19-tests.log`.

Browser: Groups → private group loaded only /api/crews/2 and showed upcoming
session 9 directly, without a chat request. Groups → public community loaded
only /api/clubs/1 and showed community information. The corresponding Messages
rows loaded /chat and opened the appropriate composers. Alex's saved private
group draft survived the round trip. No chat messages were sent. At 320px, all
four normal group names fit their 186px title area without ellipsis or horizontal
overflow. Final dark-phone and light-desktop layouts were inspected.

Synthetic setup: public community 1, Sunset Park community, was created through
the local fixture API with court 1, open joining, and Alex as owner/only member.
The request recorder changed no responses and was removed by navigation. No
production records were touched. Browser has no fixture overrides; Alex is on
Community → Groups. Final cleanup returns it to dark theme and 320 × 800.

Screenshots in `output/ui-loop/`: `third-shot-groups-info-final-320.png` and
`third-shot-groups-info-final-light-1280.png`.

Overall review after this iteration: the main navigation has not gained controls
or categories. Groups and Messages now have clearer, distinct destinations.
Public-community information is the next simplification candidate: its current
owner view exposes separate one-time/weekly planning, chat, invite/share,
announcement, blocked-player, notification, settings, and closure controls.
Compare their purpose and priority with the simplified private-group screen,
keeping moderation and public-membership needs accessible while reducing the
number of actions shown at once. Continue the whole-app simplification review
before the next change.

## Push checkpoint — September 5, 2026

The user requested pushing all accumulated changes, then continuing the loop
with a whole-app simplification and cohesion check between iterations. The
pre-push full suite passed **1,290 tests in 253.24 seconds**. Backend compilation,
frontend/SW syntax, and a deterministic rebuild against staged r67 assets passed.
The push includes the existing marketing assets as requested; their Markdown
hard line breaks were preserved. Runtime fixtures, test logs, screenshots, and
local databases remain ignored. Full-run log: `tmp/ui-loop/pre-push-tests.log`.

Once pushed, r67 is an immutable release. Subsequent frontend work must use a
new release directory and service-worker cache version, retaining prior routes
and assets for already-open clients. Continue with public-community information
simplification and the established between-iteration overall review.

GitHub push confirmed: `871f30b87c7e91690992e8c56612c22ebc79ed87` on `main`.
GitHub Actions run 33976951117 completed successfully, including the full test
suite, immutable-asset check, and container build.

## Cycle 20: simplify public-community information

The between-iteration review selected the dense public-community action stack.
Upcoming play and the home court now precede one planning path and community
chat. Recurrence remains in the existing planner's Repeat this schedule option;
the separate weekly shortcut and its hidden tomorrow/eight-week defaults were
removed. Members contains invitations, sharing, and the privacy-aware roster.
Community settings contains notification preferences and role-appropriate
organizer/membership actions. Pending join requests remain visible outside the
disclosures. Visitors keep joining/request cancellation and sharing.

Phone verification also exposed clipped court names and run-together private
roster copy. Community court names now wrap, and the private-member explanation
has a separate heading. No new navigation destination, feature category, or
backend behavior was added. This local iteration begins r68 and SW cache r70;
the already-pushed r67 assets remain unchanged.

Validation: **84 focused frontend, release, membership, governance, deletion,
chat, and crew tests passed in 7.84 seconds** after the final build. An executable
render check covers member, owner, organizer, visitor, pending-request, roster
privacy, escaping, and action-priority behavior. JavaScript/SW syntax and diff
whitespace checks passed. Log: `tmp/ui-loop/cycle20-final-tests.log`.

Browser verification used synthetic localhost data only: owner settings opened
notification preferences; the single planner exposed repeat weekdays/end date;
Jordan joined the community and received member-only planning/chat controls;
chat opened its composer without sending a message. Owner, visitor, and member
phone views and the light desktop member view were inspected. Final court names
wrap at 320px, with no horizontal overflow. Screenshots:
`tmp/ui-loop/cycle20-member-dark-320-final.png`,
`tmp/ui-loop/cycle20-member-light-1280.png`, and
`tmp/ui-loop/cycle20-settings-dark-320.png`.

Synthetic server restarted for r68. It now seeds community 1 and community
session 10, plus the previous private-group fixtures. Game 8 was regenerated as
a normal future open session; the earlier cross-day recurrence check remains
covered by its prior evidence and regression tests. Jordan joined community 1
through the real local UI. No production data or messages were changed.

Overall review before cycle 21: revisited Play (find games/My plans), Courts
(search/map/list), and Me (upcoming plan, usual times, court, settings), with
Community inspected throughout cycle 20. Their existing jobs remain distinct;
no new top-level controls are justified. Public-community upcoming games are
the concrete cohesion gap: five dates can push all actions down the sheet,
custom session titles disappear, and the parent card remains stale after RSVP.
Reproduced with Jordan: joining session 10 showed two players in the game,
then Back still showed 1/8 and seven spots on the community card. Next change:
share the private-group card presentation, reveal later dates on demand, and
refresh the existing card after a game update while preserving return focus.

## Cycle 21: keep community plans current and compact

Public-community and private-group information now share the same session-card
renderer. Public cards retain custom session names, show personal RSVP/waitlist
status, and distinguish ranked matches. Only the next date is exposed by
default; later sessions use the existing More upcoming sessions disclosure.
Opening a community game now passes the same authoritative update callback used
by private groups. Joining updates the original card in place, preserving its
focus target and any expanded dates. Cancelled/completed sessions leave the
upcoming list. This reuses existing UI and game behavior rather than adding a
new planning path.

Validation: **606 frontend checks passed in 6.42 seconds**. Executable coverage
includes community titles/escaping, ranked wording, compact later dates, fresh
RSVP counts, removed cancelled sessions, and preserving the original return
focus target. The browser confirmed session 10 now returns with 2/8, six spots,
and “You’re in.” Joining session 11 from expanded later dates preserved both
the expanded disclosure and focus on session 11, with the same fresh count.
Phone and light desktop layouts were inspected with four future community
sessions. No messages were sent. Log: `tmp/ui-loop/cycle21-frontend-tests.log`.
Screenshots: `tmp/ui-loop/cycle21-multi-dark-320.png` and
`tmp/ui-loop/cycle21-member-light-1280.png`.

## Requested pause after deployment — September 5, 2026

The user requested completing this iteration, deploying all changes, and then
stopping/pausing the improvement loop. Do not start another improvement cycle.
Finish release verification and deployment only. The goal is already paused;
resume improvement work only when the user asks again. Cycles 20–21 comprise the
r68 release, retaining the pushed r67 assets and routes.

Final pre-deployment validation: **1,293 tests passed in 291.53 seconds**.
Backend compilation, JavaScript/service-worker syntax, unchanged prior r67
assets, and whitespace checks passed. A final deterministic frontend rebuild
is checked against the staged r68 artifacts before committing. Full suite log:
`tmp/ui-loop/final-deploy-tests.log`. The production deployment uses the existing
GitHub → Vercel integration for `main`; post-deployment health, exact asset
hashes, service-worker version, public browser boot, and runtime errors are the
remaining release checks. The improvement loop stays paused after those checks.
