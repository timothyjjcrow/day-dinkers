# Complete-app UI and UX review

Active user direction, September 14, 2026. This supersedes the previous pause checkpoint: the app goal was replaced and resumed with this focus.

Review every feature and the journeys between features. Make Third Shot professional, inviting, beautiful, simple and easy to use. Use the fewest words and steps that communicate the task accurately. Remove redundant content, visual clutter and unnecessary choices. Move controls and information to where people need them. Improve the actual application, with visual evidence of the result.

## Working standard

- Inspect each real screen before editing. Identify the user's task, essential facts and next action. Prioritize clarity of the entire journey over isolated component polish.
- Establish a consistent visual language: type hierarchy, spacing, surfaces, colors, corners, icons, button priority and interaction feedback. Keep the app's character without decorative noise.
- Show people, places, dates, scores and states directly. Replace explanatory paragraphs with clear structure when possible. Keep costs, permissions, consent and consequences explicit.
- Make the main task easy to find. Group related information; move secondary tools into their appropriate context. Do not shorten the apparent screen by hiding information essential to a decision.
- Simplify mobile first and adapt deliberately to desktop. Preserve readable names, large text, usable tap targets, focus, scroll and Back behavior. Respect reduced motion.
- Check loading, empty, failure, success, stale-data and permission states where applicable. A polished populated screenshot is insufficient.
- Record before/after screenshots, the removed friction and any unverified boundaries. Follow `ui-pass-standard.md` on every pass. Existing functional implementation statuses do not certify this new visual review.

## Coverage and order

| Pass | Feature coverage | Main user outcome | UI review status |
| --- | --- | --- | --- |
| 1 | Session detail, roster, joining, invitations, waitlist offers, changed plans | Know what the plan is, who is coming and what accepting means | In progress |
| 2 | Public entry, signup, login, recovery, onboarding | Browse first and enter the intended task with minimal setup | In progress |
| 3 | Play home, discovery, filters, personal agenda, calendar | Find suitable play and see every commitment | In progress |
| 4 | Map, list, search scope, selected/saved courts | Explore freely while retaining the selected place | In progress |
| 5 | Court detail, timelines, hours/access, photos, reviews, contributions | Understand a visit and find the right next action | In progress |
| 6 | Session creation/editing, recurring dates, host tools | Make or change a plan with concise, understandable controls | Pending |
| 7 | Immediate play, on-the-way, arrival, presence, completion | Understand and control participation at each stage | Pending |
| 8 | Ranked matches, score entry/confirmation/correction, rankings | Identify teams, result and required action without jargon | Pending |
| 9 | Competition discovery, registration, partners, brackets, organizer controls | Find the next match and understand event progress | Pending |
| 10 | League opponents, proposals, scheduling, rounds, standings, seasons | Know who to play, arrange it and understand progress | Pending |
| 11 | Me, profile editing, other players, friends, availability, history | Find plans and people; update identity without a long form | Pending |
| 12 | Messages, search, replies, public/private groups, group planning | Continue a conversation and turn it into play | Pending |
| 13 | Activity, notifications, calendar/install, contextual help | Recognize actionable updates and return to the right task | Pending |
| 14 | Venue claim, verification, listing edits, preview, publication | Prepare and publish a recognizable venue listing confidently | Pending |
| 15 | Venue dates, hours, services, booking handoffs, connections, analytics | Manage what players can find and book | Pending |
| 16 | Venue teams, invitations, permissions, revisions, ownership, operator tools | Understand access and resolve management work | Pending |
| 17 | Account, security, privacy, safety/reporting, settings, support | Control the account with clear choices and outcomes | Pending |
| 18 | Shared navigation, sheets/dialogs, forms, accessibility, performance, cross-feature journeys | A coherent and dependable application across devices | Pending |

Inspect reachable routes and actions within each pass and add any discovered feature to coverage. The original 104 requirements in `requirements.json` remain a regression and completeness reference; do not delete unresolved functionality or correctness work to simplify the visual review.

## Completion evidence

Each pass records the actual screens and roles examined, changes made, concise-copy and hierarchy decisions, browser evidence and relevant behavioral checks. Verify 320px and typical mobile widths, desktop, both themes, enlarged text and keyboard use. Record physical-device and assistive-technology limitations honestly. Finish with complete player, organizer and venue-owner journeys against the final built assets. Keep source work, verified builds and deployed versions distinct.

The current production database backup approval boundary remains unchanged. This UI goal update is not approval for the previously rejected data export.

## Current evidence

Pass1: `ui-ux-pass-01-session.md` covers session essentials and first-join review; `ui-ux-pass-01-decisions.md` covers held offers, hosting decisions, cancellation and outcome feedback; `ui-ux-pass-01-rosters.md` covers direct-invitation acceptance and compact large rosters; `ui-ux-pass-01-ended.md` covers full invitation decline, queue focus and ended/cancelled plans. The pass remains in progress with its unverified states explicitly listed. Next, broaden into first-time entry and discovery, retaining the remaining session states for cross-feature verification.

Pass2: `ui-ux-pass-02-public-entry.md` covers public search, court/session previews, Back paths, signup return and responsive entry layout. `ui-ux-pass-02-area.md` covers first-account area selection, saved-area editing, geocoder retry and location-denial recovery. Email verification and account recovery remain open. Next inspect the Find games controls and available-play hierarchy recorded in the area receipt.

Pass3: `ui-ux-pass-03-discovery.md` covers consolidated game filters, a direct open-spots switch, filter cancellation and validation, responsive layouts, empty searches and service retry. Personal agenda, calendar and remaining discovery states still need review.

Pass3 continued: `ui-ux-pass-03-plans.md` covers agenda hierarchy, complete date groups, invitation/hosting decision transitions, overlap disclosures, profile prompt reduction and return focus after a plan changes. Calendar entry was checked; provider syncing and mixed competition schedules remain open.

Pass3 continued: `ui-ux-pass-03-calendar.md` covers the calendar subscription sheet, separated provider choices, private-link disclosure, copy recovery, reset/reload states and account guards. Third-party calendar syncing remains unverified. Next broaden to court map/list navigation while retaining the remaining discovery/agenda integration checks.

Pass4: `ui-ux-pass-04-court-browsing.md` covers direct list-to-details navigation, explicit map selection, compact court previews, Saved access, next-session drill-in, schedule retry and responsive preview actions. Next inspect court-detail hierarchy and schedule dates; map filter/search/pagination and richer multi-court coverage remain open.

Pass5: `ui-ux-pass-05-court-schedule.md` covers consistent player date/time ranges, full-width schedule cards, concise week navigation, retry and return focus. Court access, photos, reviews and contributions remain open.

Pass5 continued: `ui-ux-pass-05-court-visit.md` covers browsing shortcuts, topic-based visit details, grouped sources, facilities, keyboard/large-text behavior, photo retry and contextual access-edit entry. Next review reading/writing reviews, followed by the remaining photo and contribution flows.

Pass5 continued: `ui-ux-pass-05-court-reviews.md` covers the dedicated reader/editor, own-review paging, authoritative save/edit/delete totals, draft and failure recovery, stale-page protection, report entry and responsive/focus checks. Next inspect photo upload/gallery and contribution workflows; pass 5 remains in progress.

Pass5 continued: `ui-ux-pass-05-court-photos.md` covers the scannable gallery, category-specific viewer, concise metadata, photo actions, delete/cancel/failure consistency, keyboard/return focus, empty and missing-image states. Next simplify and verify the upload editor and cover refresh/caching, then contribution workflows.

Pass5 continued: `ui-ux-pass-05-court-upload.md` covers a separate short upload form, preserved gallery context, draft/date/pending/error states, authoritative saved cards, capacity feedback and stable cover selection across player/moderator removals. Next review listing contributions, confirmations and history/conflicts; also simplify the overly detailed current-day Open play chip.

Pass5 continued: `ui-ux-pass-05-court-corrections.md` covers topic-based corrections, edited-field-only submission, preserved pending topics, per-field receipts, draft/failure/pending feedback, compact Open play summaries and responsive/keyboard checks. Next inspect review/history/conflicts and source/permission-specific contribution states; court detail remains in progress.

Pass5 continued: `ui-ux-pass-05-correction-review.md` covers current/proposed comparisons, own-update withdrawal, competing proposals, compact history, authoritative results, stale/read/write recovery and preservation of unsent edits. Remaining source/role-specific contribution coverage is recorded; then broaden to session creation and host tools.
