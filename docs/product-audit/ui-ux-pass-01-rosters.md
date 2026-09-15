# UI/UX pass 1 — invitations and large rosters

September 14, 2026. Third iteration on `codex/mobile-auth-clarity`, after 55b0af3. Source only; not deployed.

## What became easier

An 18-player mixed roster previously showed 17 names across three sections before Join. At390px the action began at1466px. Large rosters now show four people, with the host and current player first, explicit per-person confirmation labels, and accurate totals for Confirmed, To confirm and Reserved. One native disclosure reveals every remaining player. Join now begins at762px in the same390×844 viewport. Counts refer to commitments, not physical attendance. Rosters of eight or fewer show all people in their status groups.

The small-roster heading “Confirm again” became “Needs confirmation,” so it reads as a player state rather than an instruction to the viewer. Direct invitations keep the inviter beside the title; the repeated card below the roster was removed. Accept invitation and Can't make it remain explicit.

## Browser evidence and behavior

Used an opt-in extension of the disposable discovery fixture: `DISCOVERY_ROSTER_SCENARIOS=1`, port8065, SQLite in memory. All players, invitations and activity were synthetic. Existing fixture scenarios remain unchanged when the option is absent.

- Inspected before/after at390px dark; narrow320px light; desktop1280×900. No horizontal overflow at320px.
- Confirmed four visible people while collapsed and all18 after expansion, with18 unique profile-control IDs. Profile navigation and Back restored the expanded list and the originating player's focus.
- At320px with computed text doubled, the modal remained320px wide. Keyboard Enter expanded the roster; Tab reached the next player's profile with a visible focus outline and fully wrapped name. This is a browser text-scaling check, not physical-device or screen-reader certification.
- Accepted the unchanged direct invitation through the actual button. The roster changed from two to three confirmed players, showed Alex as You, changed two available spots to one, and displayed You're in plus session chat. No repeated inviter card remained.
- Browser error output was empty. Syntax and diff checks passed.

Screenshots and log: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-01/rosters/`. `roster-before.png` and `roster-after.png` show the action-placement improvement. `invite-before.png`, `invite-after.png` and `invite-after-light.png` show the removed duplication. Narrow, enlarged-text and desktop screenshots are included.

## Automated checks and remaining boundaries

Focused final checks:15 passed. They cover stable host/viewer prioritization without mutating the underlying roster, all players present exactly once, explicit status labels, small-roster visibility, confirmation placement, and disclosure restoration. The broader frontend/design filename sweep passed800 checks and failed the existing release-source hash gate because readable sources have advanced beyond the immutable r80 bundle. Do not change that assertion or overwrite r80 to hide this: the next release still requires a new bundle and its previously recorded backend/concurrency gates. The focused checks overlap the broader count. The final small-roster change was followed by the15 focused checks.

Pass1 remains in progress. Full invitations, declined invitations, changed-plan invitation acceptance, expired/passed offers, sending-host controls, and cancelled/ended states still need their own visual checks. The previous plan-review and waitlist cancellation evidence remains applicable but does not certify these unexamined paths. Remaining17 full-app passes are still pending. The production database backup approval boundary remains unchanged.
