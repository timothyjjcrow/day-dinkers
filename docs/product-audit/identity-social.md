# Third Shot product audit — identity, social, account and shared flows

Reviewed 9 September 2026 UTC (8 September Pacific), current readable frontend and backend, plus relevant regression contracts. Read-only source review; the coordinating audit supplies browser observations. Priorities: **P1** = core usability/correctness before broader use; **P2** = useful next refinement. Design judgments are identified separately from confirmed behavior. No current-customer outcomes are inferred.

## Important correction to earlier onboarding concerns

**Invited users already bypass generic onboarding to reach their shared destination.** Successful auth calls `resumePlayerInviteIntentAfterAuth`, match-route restoration, then `openDeepLink`; setup is deferred when a linked destination opens. Normal court/game/player/group/tournament/league links and specific competition match links are supported. Setup resumes when the overlay stack becomes empty. This is implemented and should be preserved, not proposed as new work. Evidence: [public/app-v15.js:3062](</Users/timothycrowley/pickleball local /public/app-v15.js:3062>), `:8913`, `:38004`, `:38136`.

For the separate inviter-referral route, the app validates the destination, opens it, and immediately asks **“Send a friend request?”** An unavailable shared game falls back to the inviter's profile with an explanation. Friendship requires consent; opening the link does not create a friendship. This is a useful boundary. The product improvement is to move the optional friend action inline after joining, so a new arrival can understand the game before another modal intervenes (`:38112–38169`).

## 1. Welcome, login, signup and recovery

**Current:** email/password login; display name at signup; password reveal; autocomplete; inline accessible validation; actionable duplicate-email error; rate-limit countdown; MFA challenge where enabled; verification, password-reset and email-change links. Signed-out shared entities get a small contextual preview. Registration can finish despite verification-email delivery failure; the account screen exposes retry. Recovery response does not reveal whether an email exists. Existing return-to-intent behavior is a strength.

**P1 design:** the account form still dominates the entry page. A prospective player cannot inspect enough of a public court/session to decide whether the app helps them. Expand the existing safe preview into a useful public detail view; make signup the joining step, preserve private audiences and location privacy. This is presentation/access scope work, not a missing deep-link system. Evidence: [public/index.html:113](</Users/timothycrowley/pickleball local /public/index.html:113>); [public/app-v15.js:8090](</Users/timothycrowley/pickleball local /public/app-v15.js:8090>).

**P2 consistency:** registration and change-password accept six characters, while reset-password requires eight. Use one clear policy and matching UI text across all three flows. This is a confirmed UX inconsistency, not a claim of an exploited vulnerability. Evidence: [backend/routes/auth.py:929](</Users/timothycrowley/pickleball local /backend/routes/auth.py:929>), `:1008`; [public/app-v15.js:32965](</Users/timothycrowley/pickleball local /public/app-v15.js:32965>), `:33110`.

**Check before release:** expired link, link opened while another account is active, verification-email failure, wrong MFA code, long offline attempt, browser password manager and retained game intent after reauthentication. Source includes targeted safeguards and tests; this audit did not send real account emails.

## 2. New-player setup

**Current:** generic new registrations can pass through self-rating, usual play times and photo; then home area, starter courts, invite-a-friend and a three-screen tour. Each profile screen saves independently, has skip and Finish later; pause is device-local and missing server profile data remains honest. Starter court search/pagination and photo mobile layout have already been fixed. Evidence: [public/app-v15.js:37059](</Users/timothycrowley/pickleball local /public/app-v15.js:37059>); `docs/PROFILE_SETUP_FIXES.md`.

**P1 design:** a skippable flow can still feel like too much setup. “Profile step 3 of 3” is followed by multiple other onboarding screens. Let generic entrants choose a court/area and view play first, then request a level when it affects suitability and a photo when joining. Move inviting and the feature tour out of the initial chain. Keep the existing resume card and independent saves.

**P2 accessibility:** self-rating choices use button-based `role="radio"`, but the visible binding handles click only, with all choices in the tab sequence; there is no radio-group arrow/roving-focus behavior in those handlers. Use native radio inputs or the shared keyboard pattern. Same issue applies to self-rating in Edit profile and private/public group notification choice sheets. Evidence: `:37403–37422`, `:33986–33987`, `:34075–34088`, `:27745–27765`. Confirm with keyboard/screen reader; source inspection is not a completed accessibility certification.

## 3. Me / personal dashboard

**Current:** recognizable player identity and usual availability; one next game; saved courts; invite and feedback actions; collapsed progress/history; venue entry. Current source has explicit ranked labels and current/best streak distinction, and handles unavailable sections without pretending there is no activity. Cached data is retained with an honest refresh error. Do not revive the already corrected demo-stat inconsistency.

**P1 confirmed information-architecture problem:** only the first upcoming session is outside the disclosure. `pf-upcoming-more`, containing all remaining upcoming games, lives inside **“Your progress & history.”** Future commitments are hidden under a label for past activity. Keep all upcoming sessions under Up next with a “See all plans” expansion; isolate history/stats below. Evidence: [public/app-v15.js:33500](</Users/timothycrowley/pickleball local /public/app-v15.js:33500>), `:33647–33655`.

**P2 design:** “Usually plays” and avatar shortcuts both open the entire long profile editor. Open the relevant small section directly, with its own save/cancel. A returning user changing Tuesday evening should not traverse ratings, DUPR, photo and court fields. Evidence: `:33534–33535`, `:33951–34069`.

**P2 design:** empty Up next offers creating a six-person session as its one main action. A new player may want to join first. Make “Find a game” primary; retain “Plan a session” secondary. Evidence: `:33665–33675`.

## 4. Profile editing and ratings identity

**Current:** name, bio, self-rating with descriptions, optional DUPR and DUPR ID, photo optimization/removal/link fallback, initials color, weekly availability and primary court. Self-rating, manually entered DUPR and Third Shot calculated match rating are explicitly distinguished. Draft and discard protection exists.

**P1 design:** split About, Playing preferences, Photo and Home court into focused edits with a compact player-card preview. Three rating concepts plus seven/eight numeric self-rating choices overwhelm the basic task of being recognizable and finding suitable play. Keep one self-rating prominent, put competition identities behind “Competition ratings.”

**P2 trust:** the DUPR field is user-entered, while help calls it a “published” rating. Display it clearly as **“DUPR · added by player”** until verified/imported. Do not suggest a verified integration already exists. Evidence: [public/app-v15.js:33992](</Users/timothycrowley/pickleball local /public/app-v15.js:33992>).

**P2 feature:** usual time blocks are helpful hints, not specific availability. Keep the distinction visible and allow a temporary “Away until…” or current-week exception if the app uses usual times for suggestions. Exact session time selection already exists elsewhere; no need to force a new scheduling product into profile editing.

## 5. Other-player profiles

**Current:** identity, rating, mutual friends, ranked statistics or non-ranked play counts, badges/titles, head-to-head and teammate history, shared usual time suggestions, friend/message actions, upcoming visible games and courts. Privacy and relationship permissions are checked server-side. Block/report controls are discoverable through More.

**P1 design:** primary social actions appear after stats, badges, competition titles, comparison cards and availability. For a player deciding “Can I play with this person?”, lead with name/photo, level, common court/time and one **Invite to play** or Message action. Keep stats under “Play history.” Evidence: [public/app-v15.js:29713](</Users/timothycrowley/pickleball local /public/app-v15.js:29713>).

**P2 consistency:** “Schedule at a shared time” only appears when both profiles have overlapping stored availability; ordinary invite is missing from that primary action row. A valid invitation should be available even without profile availability. Use the existing planner, showing best-fit suggestions only when data supports them. Ranked challenge remains a deliberate secondary action, not the default connection task.

## 6. Progress, stats and play history

**Current:** ranked win/loss, rate and streak are labeled; casual/session play builds full history; filters, pagination, top court, partner/rival, patterns and earned milestones exist. Head-to-head explicitly combines ranked+casual. This is feature-rich; the remaining need is clear scope and compact organization.

**P1 confirmed false-empty risk:** filters are applied only to already-loaded history items. If the initial page has no ranked results but older pages do, “No play matches this filter” appears until the person manually loads older pages. Make the API filter before pagination, or explicitly say “No matches in the loaded results” and offer “Search older play.” Evidence: [public/app-v15.js:33860](</Users/timothycrowley/pickleball local /public/app-v15.js:33860>).

**P2 design:** the first three completed sessions render in Recent play, then appear again in the full Play history inside the same disclosure. Choose one history list, expandable and filtered. Separate **Matches/results** from **Sessions attended** while retaining a combined activity timeline. Scope legends should sit beside stats rather than rely on remembering different cards.

**P2 design:** new players see “Your ranked story starts with one match,” a ranked CTA and badge milestones. Favor “Your play history” and only foreground ranked progress for people using ranked play. Evidence: `:33473–33478`, `:33686–33729`.

## 7. Community navigation and inbox

**Current:** top-level Messages, Groups and Players; Players has Nearby, Friends and Played together; Messages filters All/Direct/Games/Courts. Unified inbox brings direct, court, game, private group, public community, tournament and league conversations together. Group list rows open group information; conversation rows open chat. Empty upcoming game chats do not crowd the inbox. Partial endpoint failures are labeled and retryable. These recent improvements should stay.

**P2 design:** the broad label Community is still both an app destination and a subtype of public group. Keep stable user vocabulary: **Messages / Groups / Players**, with group badges **Public** and **Private**. Internal `crew`, `club` and league distinctions should not leak into new labels. A short consistency pass is sufficient; do not add more navigation levels. Evidence: [public/index.html:269](</Users/timothycrowley/pickleball local /public/index.html:269>); [public/app-v15.js:24798](</Users/timothycrowley/pickleball local /public/app-v15.js:24798>), `:25250–25390`, `:25663–25750`.

**P2 feature:** the inbox lacks a conversation-search control. With seven conversation types, people will lose a court name or game thread as activity grows. Add one search over permitted conversations, and later optional archive/pin. Do not add a social feed just to fill an empty inbox.

## 8. Direct messages and chat continuity

**Current:** multiline composer, photos, link rendering, dates, read receipts, hearts, deletion and reporting; earlier messages can load without jumping the scroll position; durable per-account outbox preserves retryable sending and drafts; reconnection state/backoff; block and per-thread mute. Existing chats remain replyable when the original coordination context ends; arbitrary unrelated people cannot DM. Evidence: [backend/models.py:1949](</Users/timothycrowley/pickleball local /backend/models.py:1949>); [public/app-v15.js:8938](</Users/timothycrowley/pickleball local /public/app-v15.js:8938>), `:26419–26643`; `tests/test_chat_history_frontend.py`.

**P1 label/state issue:** any permitted non-friend conversation is called **“Message request”**, but it is already a normal message thread, with read receipts and a reply composer, and no accept/decline request state. Either call it “Not in your friends” with why you are connected, or implement an actual Requests inbox with Accept/Ignore and explicit privacy behavior. Do not imply a consent gate that does not exist. Evidence: [backend/routes/chat.py:1668](</Users/timothycrowley/pickleball local /backend/routes/chat.py:1668>), `:1693–1717`; [public/app-v15.js:26443](</Users/timothycrowley/pickleball local /public/app-v15.js:26443>).

**P2 feature:** add an inline **Plan a game** affordance to DMs, using the existing planner with the other player selected. Group chat already does this well; DMs currently send the user via profile/shared availability or other screens. Keep the important plan above chat so decisions do not disappear in messages.

**P2 later:** reply-to and conversation text search would help recover a plan; editing a typo is less urgent. Existing durable sending and clear reconnect states matter more than typing animation, more reactions or media types.

## 9. Players, friends and “Played together”

**Current:** nearby states distinguish current court presence, recent sighting and profile area; blocks/visibility are enforced; common availability and real shared play are surfaced; recent-player rows can add friends, accept a request, or plan again. Search understands messaging permissions and avoids stale-result overwrites.

**P1 design/capability gap:** Nearby hardcodes a 50-mile query and exposes All/3.0/3.5/4.0+ filters, omitting a dedicated beginner choice. Let players adjust distance and show beginner-friendly labels alongside numbers. A 2.5 player should not have to infer that All is their only option. Evidence: [public/app-v15.js:25796](</Users/timothycrowley/pickleball local /public/app-v15.js:25796>).

**P2 friction:** Played together only shows Play again once friendship is accepted, although the model already permits contextual communication after shared play. Offer **Invite to play** to eligible past players with an explicit one-off invitation; keep Add friend secondary. Preserve existing block/private-game rules. Evidence: `:25558–25575`; [backend/models.py:1949](</Users/timothycrowley/pickleball local /backend/models.py:1949>).

## 10. Private play groups and public communities

**Current:** explicit private/public creation choice; invitation preview protects private roster identities before acceptance; group details feature upcoming sessions, group chat, player list, invite management, preferences and membership actions. Private group chat has a persistent next-plan card and empty-state starters. Public groups support announcements, join approval, organizers, bans, upcoming games, tournaments and leagues. This is already substantial group functionality.

**P1 design:** standardize both group headers around **Next session / Who is coming / Chat / Members**. Public community chat has an announcement but no equivalent next-plan card to the private group chat; the player must return to information to rediscover upcoming play. Reuse the existing private-group pattern. Evidence: [public/app-v15.js:27932](</Users/timothycrowley/pickleball local /public/app-v15.js:27932>), `:28137–28170`, `:28295–28374`.

**P2 design:** private group planning is unavailable until at least one other invitee accepts. The empty state explains why, but a creator may naturally want to schedule first and invite people to the actual event. Let the owner create a draft group plan with pending invitees, clearly separating Invited and Going; do not silently RSVP anyone. Evidence: `:27482–27525`.

**P2 consistency:** group notification settings require explicit Save, while global settings save automatically. Both can work; show “Changes save automatically” or a persistent Save bar consistently, and guard unsaved choice changes. Private group names, messages and members must retain their existing invitation/privacy boundaries.

## 11. Activity / notifications

**Current:** chronological Activity with unread, games, people and groups filters; inline invite/request/score actions; clear and mark-read tools; pagination, undo around deletion, deep-linked targets. Device push capability distinguishes unsupported, unconfigured, blocked, granted and subscribed states. Good foundations exist.

**P1 confirmed classification issue:** all kinds outside people/groups fall into Games, including business and moderation/account-related activity. Put **Needs your action** first, then chronology, with accurate categories or a separate operator inbox. Resolve stale action buttons from the entity's current state, not merely notification text. Evidence: [public/app-v15.js:36191](</Users/timothycrowley/pickleball local /public/app-v15.js:36191>), `:36206–36229`.

**P1 copy/reliability:** global settings say invitations/challenges/score confirmations **“always come through”**, even when device delivery is off or server push unavailable. Say these remain in Activity, then state which channels can actually deliver. Source README explicitly documents daily push as unsuitable for time-sensitive alerts and keeping delivery disabled until appropriate scheduling exists. Do not claim live configuration or real delivery failures from source alone. Evidence: [public/app-v15.js:32375](</Users/timothycrowley/pickleball local /public/app-v15.js:32375>), `:32440–32455`; [README.md:318](</Users/timothycrowley/pickleball local /README.md:318>), `:338–345`.

**P2 false-empty risk:** Activity filters also filter only the loaded 20-item page; “No groups activity yet” can appear despite older matching items. Apply category filters before server pagination, or label loaded scope. Evidence: [public/app-v15.js:36145](</Users/timothycrowley/pickleball local /public/app-v15.js:36145>), `:36253–36273`.

**P2 preference:** add quiet hours and digest cadence only after channel delivery is reliable. Existing per-conversation/group muting should remain easy to access.

## 12. Calendar and installation

**Current:** Google subscription, Apple/Outlook `webcal`, copy/manual fallback, private-link explanation and reset/rotation. Individual game calendar actions exist. iPhone installation guidance is already shown when necessary. Do not propose calendar integration as absent. Evidence: [public/app-v15.js:10803](</Users/timothycrowley/pickleball local /public/app-v15.js:10803>), `:30028–30140`, `:32368–32379`.

**P1 copy:** “updates and cancellations stay in sync” can suggest immediate changes. State that subscribed calendars refresh on their own schedule; use the app's current game status for an urgent change. Keep one-time “Add this game” visibly different from ongoing “Subscribe to my plans.”

**P2 design:** offer installation and calendar subscription after a person joins a plan, with a concrete benefit. Account security settings are an odd primary place for installation. Keep Settings as a reliable fallback rather than making setup a prerequisite.

## 13. Privacy, safety and help

**Current:** home area, optional open-app auto-check-in, everyone/friends/hidden nearby visibility, precise audience explanation, check-out action and blocked-player recovery. Reporting works for players and content with private context and review outcome updates. Backend respects blocked identities and audience boundaries. Good product groundwork.

**P1 confirmed contradictory display:** when checked in, the heading always says **“Court presence · Visible now”**, even if nearby visibility is Hidden. The paragraph below says the identity is hidden. Changing the visibility radio updates the server but does not update this summary in the open privacy sheet; it only rerenders the separate profile tab. Derive heading and audience text from the same current state and update together. Evidence: [public/app-v15.js:32582](</Users/timothycrowley/pickleball local /public/app-v15.js:32582>), `:32632–32639`, `:32687–32703`.

**P2 help:** Settings has no Help entry; contact details are buried in auth policy dialogs and feedback is on Me. Add a compact Help & safety page answering real action questions: session vs match, how to join, what check-in reveals, cancellation, notifications, score dispute and support. Link each answer from the relevant control rather than making a long tour. Evidence: [public/app-v15.js:33363](</Users/timothycrowley/pickleball local /public/app-v15.js:33363>), `:2840–2865`, `:33561–33614`.

**P2 trust:** reporting copy promises a trained operator/safety team. Ensure the real support workflow matches those words; in a small prelaunch operation, accurately state who reviews and where an outcome appears. This audit verifies the queue/UI exists, not staffing or response times.

## 14. Account, settings and cross-app quality

**Current:** grouped settings, email verification/change, password change, revoke other sessions, logout, deletion impact review and explicit irreversible-action confirmation. Appearance is separate from calendar. Shared modal handling includes labels, focus trapping/restoration, Escape, back navigation, mobile visual viewport adjustments and draft guards. CSS includes touch-target and reduced-motion support. These should be preserved.

**P1 shared pattern:** simplify the frequent path, not just each isolated card. Use the same small vocabulary and ordering for every person/group/game detail: identity → next relevant information → primary action → secondary detail. Existing deep nesting is manageable technically but still makes basic tasks feel longer.

**P2 verification needed:** real iPhone/Android keyboard, 200% text scaling, keyboard-only self-rating, screen-reader announcements, 320px layouts, long names, denied permissions, stale/offline cached cards and logout/account-switch restoration. Static tests cover many contracts, but cannot prove readability, touch comfort or screen-reader coherence. No sweeping “accessible” or “secure” certification is justified by this source audit.

## Highest-value sequence within this audit area

1. Fix upcoming-session placement, privacy summary and loaded-page filter false empties.
2. Put public/shared destination usefulness before signup; preserve the existing intent bypass.
3. Shorten generic setup and use focused profile edits; move social actions before profile statistics.
4. Unify person/group-to-plan transitions and clarify Message request semantics.
5. Make notification claims match actual channels; provide accurate Activity categories and calendar expectations.
6. Finish keyboard/assistive-technology verification and contextual Help.

This is primarily a need for clearer tasks and more consistent transitions. The app does not need another large layer of social features to become understandable.
