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
| 2 | Public entry, signup, login, recovery, onboarding | Browse first and enter the intended task with minimal setup | Pending |
| 3 | Play home, discovery, filters, personal agenda, calendar | Find suitable play and see every commitment | Pending |
| 4 | Map, list, search scope, selected/saved courts | Explore freely while retaining the selected place | Pending |
| 5 | Court detail, timelines, hours/access, photos, reviews, contributions | Understand a visit and find the right next action | Pending |
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
