# Release r82: map-first overhaul

Requested by Tim on September 25, 2026 (resuming UI work after the r81 stop
checkpoint): make choosing a court on the map, seeing its details, and playing
there with friends as simple as possible. Changes were made decisively rather
than as incremental passes.

## Pain points found (walkthrough at 390px, 320px and 1280px)

1. The app opened on a dense Play feed (three segment rows, time chips,
   filters, an area prompt) instead of the map, which is the product's core.
2. Signed-out visitors saw a bare search box with no map at all.
3. On a court page, the main actions (plan a game here, check in) sat below
   the fold or inside a collapsed "At the court now?" disclosure.
4. "Create game" first asked Casual / Ranked / Tournament, adding a step to the
   most common path.
5. In the planner, inviting friends was hidden in a collapsed "Invite specific
   players · Optional" section, while optional court access and cost fields
   were always expanded.
6. Community opened Players on "Nearby", which demands an area before showing
   anything; friends were a second click away and each friend row led with
   Message, with Invite to play buried in a menu.
7. "Updated just now" chips on every tab, "Player times · GMT" and
   "Court hours · Hours not listed" lines added noise without informing.
8. The Play tab always opened on Find games, even when the player had games
   coming up or decisions waiting.

## Changes

- Map is the first tab and the default for every session; tabs are now
  Map, Play, Friends, Me.
- Signed-out landing includes a live court map. Pins open the existing public
  court preview; search recenters the map; location is used only if already
  granted or requested with the locate button.
- Court page has a sticky action bar: "I'm here" (check in / find players),
  "Play here" (plan a game at this court, or open your session there), and
  court chat. Each reuses the existing in-page control.
- "Create game" opens the casual planner directly. Ranked matches remain
  under Play now; tournaments and leagues under Events.
- Planner defaults "Who can join" to My friends when the player has friends,
  shows the friend invite list open, and folds court booking and cost into an
  optional disclosure.
- Friends tab: segments are Chats, Friends, Groups; people view opens on
  My friends; each friend row leads with "Play" (invite to a game) plus a
  message icon; an invite-link card follows the list.
- Play opens on My plans when the player has plans or pending decisions.
- Freshness chips appear only when a view is more than five minutes old; the
  court timeline shows a time zone only when it differs from the player's.
- Map peek sheet: "See all N courts as a list" is a quiet link instead of a
  full-width primary button; the check-in strip reads "You're here".

## Verification

Local browser walkthrough against the seeded demo data (tile and photo CDNs are
blocked in the sandbox, so map tiles were stubbed). Full pytest suite and the
reproducible asset build are run in CI.
