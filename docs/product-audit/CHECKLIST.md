# Product audit implementation checklist

Canonical status and evidence: [requirements.json](requirements.json). Implemented means local work; verified requires completion evidence and the separate release gate.

| ID | Area | Requirement | Status |
|---|---|---|---|
| PL-01 | 1. Play home, discovery and live refresh | P1 — a refresh interrupts browsing. Confirmed and browser-observed by main auditor. | implemented |
| PL-02 | 1. Play home, discovery and live refresh | P2 — choose play intent before asking implementation choices. Judgment supported by source. | implemented |
| PL-03 | 1. Play home, discovery and live refresh | P2 — make discovery searchable by actual constraint. Judgment. | in_progress |
| PL-04 | 1. Play home, discovery and live refresh | P2 — feed completeness. Confirmed structural limitation. | implemented |
| PL-05 | 2. My plans and calendar | P1 — future commitments can disappear from My plans. Confirmed rendering path. | implemented |
| PL-06 | 2. My plans and calendar | P1 — personal schedule is not a unified schedule. Confirmed. | implemented |
| PL-07 | 3. Create game / planner | P2 — group capacity and match format are mixed together. Judgment. | pending |
| PL-08 | 3. Create game / planner | P2 — practical essentials are buried with optional prose. Judgment. | pending |
| PL-09 | 3. Create game / planner | P2 — numeric level selection is clearer for experienced players than new ones. Judgment. | pending |
| PL-10 | 4. Invitations and joining | P1 — Invite by link does not invite into the draft being created. Confirmed. | in_progress |
| PL-11 | 4. Invitations and joining | P1 — a new user cannot straightforwardly create a private game for existing offline friends. Confirmed limitation. | verified |
| PL-12 | 4. Invitations and joining | P2 — joined state should summarize the commitment. Judgment. | verified |
| PL-13 | 5. Session detail, roster and payment/access expectations | P1 — cost and reservation status can be below the join decision. Confirmed layout; priority is a design judgment. | verified |
| PL-14 | 5. Session detail, roster and payment/access expectations | P2 — clearer roster state distinctions. Judgment. | pending |
| PL-15 | 6. Waitlist, arrival and host changes | P1 — waitlist promotion can falsely imply a confirmed commitment. Confirmed. | implemented |
| PL-16 | 6. Waitlist, arrival and host changes | P2 — host handoff lacks the new host’s consent. Confirmed behavior, design improvement. | implemented |
| PL-17 | 6. Waitlist, arrival and host changes | P2 — immediate-play concepts need visual continuity. Judgment. | pending |
| PL-18 | 7. Weekly sessions and completed play | P1 — recurring sessions need separate dates as durable records. Confirmed architectural gap. | implemented |
| PL-19 | 7. Weekly sessions and completed play | P1 — current recurring completion controls can promise an unsupported action. Confirmed code-path mismatch, not exercised in browser. | implemented |
| PL-20 | 7. Weekly sessions and completed play | P1 — recurrence rollover ignores session duration. Confirmed. | implemented |
| PL-21 | 7. Weekly sessions and completed play | P2 — actual attendance should not overwrite all participation history. Confirmed behavior; feature recommendation. | in_progress |
| PL-22 | 8. Ranked matches, results and disputes | P1 — unresolved results need an honest next step. Confirmed mismatch. | implemented |
| PL-23 | 8. Ranked matches, results and disputes | P2 — result history should explain provenance compactly. Judgment. | implemented |
| PL-24 | 9. Rankings | P1 — a player outside the first page can be told to win their first match. Confirmed. | implemented |
| PL-25 | 9. Rankings | P2 — use rankings as a clear optional view. Judgment. | implemented |
| PL-26 | 10. Competition hub and creation | P2 — browsing needs a player-first entry. Judgment. | implemented |
| PL-27 | 11. Tournament registration, teams and arrival | P1 — “Here” does not mean both doubles partners have arrived. Confirmed. | implemented |
| PL-28 | 11. Tournament registration, teams and arrival | P2 — fuller registration expectations. Judgment. | implemented |
| PL-29 | 12. Tournament bracket, match times and organizer operations | P1 — estimated match times look firmer than the scheduling system supports. Confirmed behavior; improvement judgment. | implemented |
| PL-30 | 12. Tournament bracket, match times and organizer operations | P2 — participant default should center the actual next match. Judgment. | implemented |
| PL-31 | 12. Tournament bracket, match times and organizer operations | P2 — organizer preview before start. Judgment. | implemented |
| PL-32 | 13. League opponents, scheduling, standings and season management | P1 — arranging a league match stops at chat. Confirmed missing workflow. | implemented |
| PL-33 | 13. League opponents, scheduling, standings and season management | P1 — movement uses season totals rather than the round just completed. Confirmed behavior; product-rule decision needed. | implemented |
| PL-34 | 13. League opponents, scheduling, standings and season management | P2 — league lifecycle needs clear absence and season boundaries. Judgment. | implemented |
| PL-35 | Browser corroboration | League task order | implemented |
| PL-36 | Browser corroboration | Competition first viewport | implemented |
| PL-37 | Browser corroboration | Agenda scope | implemented |
| ID-01 | 1. Welcome, login, signup and recovery | the account form still dominates the entry page. | implemented |
| ID-02 | 1. Welcome, login, signup and recovery | registration and change-password accept six characters, while reset-password requires eight. | implemented |
| ID-03 | 2. New-player setup | a skippable flow can still feel like too much setup. | implemented |
| ID-04 | 2. New-player setup | self-rating choices use button-based `role="radio"`, but the visible binding handles click only, with all choices in the tab sequence; there is no radio-group arrow/roving-focus behavior in those handlers. | implemented |
| ID-05 | 3. Me / personal dashboard | only the first upcoming session is outside the disclosure. | implemented |
| ID-06 | 3. Me / personal dashboard | “Usually plays” and avatar shortcuts both open the entire long profile editor. | implemented |
| ID-07 | 3. Me / personal dashboard | empty Up next offers creating a six-person session as its one main action. | implemented |
| ID-08 | 4. Profile editing and ratings identity | split About, Playing preferences, Photo and Home court into focused edits with a compact player-card preview. | implemented |
| ID-09 | 4. Profile editing and ratings identity | the DUPR field is user-entered, while help calls it a “published” rating. | implemented |
| ID-10 | 4. Profile editing and ratings identity | usual time blocks are helpful hints, not specific availability. | implemented |
| ID-11 | 5. Other-player profiles | primary social actions appear after stats, badges, competition titles, comparison cards and availability. | implemented |
| ID-12 | 5. Other-player profiles | “Schedule at a shared time” only appears when both profiles have overlapping stored availability; ordinary invite is missing from that primary action row. | implemented |
| ID-13 | 6. Progress, stats and play history | filters are applied only to already-loaded history items. | implemented |
| ID-14 | 6. Progress, stats and play history | the first three completed sessions render in Recent play, then appear again in the full Play history inside the same disclosure. | implemented |
| ID-15 | 6. Progress, stats and play history | new players see “Your ranked story starts with one match,” a ranked CTA and badge milestones. | implemented |
| ID-16 | 7. Community navigation and inbox | the broad label Community is still both an app destination and a subtype of public group. | pending |
| ID-17 | 7. Community navigation and inbox | the inbox lacks a conversation-search control. | implemented |
| ID-18 | 8. Direct messages and chat continuity | any permitted non-friend conversation is called **“Message request”**, but it is already a normal message thread, with read receipts and a reply composer, and no accept/decline request state. | implemented |
| ID-19 | 8. Direct messages and chat continuity | add an inline **Plan a game** affordance to DMs, using the existing planner with the other player selected. | implemented |
| ID-20 | 8. Direct messages and chat continuity | reply-to and conversation text search would help recover a plan; editing a typo is less urgent. | implemented |
| ID-21 | 9. Players, friends and “Played together” | Nearby hardcodes a 50-mile query and exposes All/3.0/3.5/4.0+ filters, omitting a dedicated beginner choice. | implemented |
| ID-22 | 9. Players, friends and “Played together” | Played together only shows Play again once friendship is accepted, although the model already permits contextual communication after shared play. | implemented |
| ID-23 | 10. Private play groups and public communities | standardize both group headers around **Next session / Who is coming / Chat / Members**. | implemented |
| ID-24 | 10. Private play groups and public communities | private group planning is unavailable until at least one other invitee accepts. | implemented |
| ID-25 | 10. Private play groups and public communities | group notification settings require explicit Save, while global settings save automatically. | implemented |
| ID-26 | 11. Activity / notifications | all kinds outside people/groups fall into Games, including business and moderation/account-related activity. | implemented |
| ID-27 | 11. Activity / notifications | global settings say invitations/challenges/score confirmations **“always come through”**, even when device delivery is off or server push unavailable. | implemented |
| ID-28 | 11. Activity / notifications | Activity filters also filter only the loaded 20-item page; “No groups activity yet” can appear despite older matching items. | implemented |
| ID-29 | 11. Activity / notifications | add quiet hours and digest cadence only after channel delivery is reliable. | pending |
| ID-30 | 12. Calendar and installation | “updates and cancellations stay in sync” can suggest immediate changes. | verified |
| ID-31 | 12. Calendar and installation | offer installation and calendar subscription after a person joins a plan, with a concrete benefit. | verified |
| ID-32 | 13. Privacy, safety and help | when checked in, the heading always says **“Court presence · Visible now”**, even if nearby visibility is Hidden. | implemented |
| ID-33 | 13. Privacy, safety and help | Settings has no Help entry; contact details are buried in auth policy dialogs and feedback is on Me. | in_progress |
| ID-34 | 13. Privacy, safety and help | reporting copy promises a trained operator/safety team. | implemented |
| ID-35 | 14. Account, settings and cross-app quality | simplify the frequent path, not just each isolated card. | pending |
| ID-36 | 14. Account, settings and cross-app quality | real iPhone/Android keyboard, 200% text scaling, keyboard-only self-rating, screen-reader announcements, 320px layouts, long names, denied permissions, stale/offline cached cards and logout/account-switch restoration. | pending |
| CV-01 | Courts map, list, search and saved courts | P1 — Make the map answer a player question. | implemented |
| CV-02 | Courts map, list, search and saved courts | P1 — Unify hours. | implemented |
| CV-03 | Courts map, list, search and saved courts | P2 — Make search scope obvious. | implemented |
| CV-04 | Courts map, list, search and saved courts | P2 — Add decision filters, not more cosmetic filters. | pending |
| CV-05 | Courts map, list, search and saved courts | P2 — Saved courts should be actionable. | implemented |
| CV-06 | Court detail and planning a visit | P1 — Reorder by intent. | implemented |
| CV-07 | Court detail and planning a visit | P1 — One timeline, explicit organizers. | implemented |
| CV-08 | Court detail and planning a visit | P2 — Reduce the visit uncertainty. | implemented |
| CV-09 | Court detail and planning a visit | P2 — Better hierarchy for secondary activity. | pending |
| CV-10 | Presence, check-in and current conditions | P1 — Reconcile presence enforcement. Source-observed contract gap. | implemented |
| CV-11 | Presence, check-in and current conditions | P1 — Presence is a signal, not guaranteed play. | pending |
| CV-12 | Presence, check-in and current conditions | P2 — Help when GPS fails. | implemented |
| CV-13 | Presence, check-in and current conditions | P1 — Court data moderation needs impact tiers. | implemented |
| CV-14 | Presence, check-in and current conditions | P2 — Conditions and crowd estimates need confidence labels. | implemented |
| CV-15 | Reviews, photos and court contributions | P2 — Make contributions useful to a visit. | in_progress |
| CV-16 | Reviews, photos and court contributions | P2 — Keep contributor progress visible. | implemented |
| CV-17 | Venue discovery, claim and verification | P1 — Finish the missing-venue journey. | implemented |
| CV-18 | Venue discovery, claim and verification | P2 — Verification should have a clear next action. | implemented |
| CV-19 | Venue editor, publishing and player preview | P1 — Ordinary editing must not unexpectedly remove a live venue. | implemented |
| CV-20 | Venue editor, publishing and player preview | P1 — One save model. | implemented |
| CV-21 | Venue editor, publishing and player preview | P2 — Preview the full player journey. | implemented |
| CV-22 | Venue schedule and services | P1 — Manage dated occurrences, not abstract rows. | implemented |
| CV-23 | Venue schedule and services | P1 — Fix timezone setup. | implemented |
| CV-24 | Venue schedule and services | P1 — Be honest about availability. | implemented |
| CV-25 | Venue schedule and services | P2 — Separate services from scheduled sessions. | implemented |
| CV-26 | Booking links, connections and analytics | P1 — Make the handoff self-explanatory. | implemented |
| CV-27 | Booking links, connections and analytics | P2 — Translate connections into manager tasks. | implemented |
| CV-28 | Booking links, connections and analytics | P2 — Simplify analytics. | implemented |
| CV-29 | Team, revisions, ownership, security and operator tools | P1 — Human-readable change history. | implemented |
| CV-30 | Team, revisions, ownership, security and operator tools | P1 — Prevent simultaneous edit loss at the server. | implemented |
| CV-31 | Team, revisions, ownership, security and operator tools | P2 — Make permissions and pending invitations legible. | implemented |
