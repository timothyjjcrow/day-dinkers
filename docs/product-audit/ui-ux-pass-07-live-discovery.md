# Pass 7 — Finding immediate play

September 15, 2026. Source follows 703dcd2. Pass 7 and the full-app goal remain in progress.

## What improved

The on-the-way entry now opens Nearby play now. The former horizontal card squeezed the court name into an ellipsis beside a counts sentence and button. Each game now has a full court heading, its city and distance, separate Here / On the way / Open spots counts, and clear Court details and On my way actions. Loose players looking for casual play have their own section, with full names and their court. The live discovery endpoint intentionally provides aggregate rally information; the new action opens court details through existing permissions, not private game rosters. Remote game details were confirmed inaccessible before arrival.

The long introduction and duplicate Not now footer were removed. Area and search radius sit beside Refresh. Empty results offer Check again and the existing availability flow; a failed lookup offers Try again in the same task. No area means a short Set area state and no nearby request. Late responses after closure or account changes cannot render into the old view. Refresh and retry focus the results, while opening court details preserves the list and returns focus to its card.

The Play now menu also now uses horizontal icon-and-label rows. Its previous styles inherited column direction from a different layout, stacking every icon above its label and wasting vertical space. The new live cards use shared surfaces and corners, wrapped text, aligned counts and normal-flow actions. At enlarged text sizes, buttons stack and the distance moves below the heading instead of squeezing the court name.

## Evidence and behavior

Primary workspace evidence: `output/product-audit/evidence/ui-ux-pass-07/live-discovery/`. All accounts and mutations used the existing isolated, in-memory fixture on port 8080.

- `immediate-entry-before390.png` and `immediate-menu-after390.png` show the actual menu change. `immediate-list-before390.png` shows the clipped court; `immediate-final390-light.png` and `immediate-final390-dark.png` show the new cards. Early `after` screenshots predate final enlarged-text refinements.
- Morgan started a real casual rally at Cedar Park Courts, game 13, using the synthetic court coordinates and the existing check-in proof flow. The discovery API returned one here, zero on the way and three open spots. Casey checked in looking for play at Riverside Indoor Courts, exercising the separate player section with a long name (`immediate-mixed390.png`, `immediate-player320-light.png`). No friend notification was sent by the browser test.
- Court details opened Cedar Park's actual detail page and its access/schedule information (`immediate-court-child390.png`). Back restored the same live list and focused View Cedar Park Courts court details. A remote GET of the game returned 404 under existing access rules; this change does not broaden roster visibility.
- Simulated 503 discovery responses produced Couldn’t load nearby play and Try again (`immediate-list-failure390.png`). Restoring fetch and retrying loaded the real list. A simulated successful empty response displayed No nearby play right now, Check again and Share that I’m free this hour (`immediate-empty390.png`). Check again recovered to the real populated list.
- A 1.5-second delayed refresh released after Escape closed the sheet. Read-back confirmed the response completed and the live modal did not reopen. Executed frontend tests also cover account changes before response delivery.
- In a separate browser with Sam's unset area, opening the new view made zero `/players/looking` requests, removed the busy state, hid Refresh and showed Set area. That action opened the existing city picker (`immediate-noarea-final390.png`). Completing geocoder/area selection was not repeated in this pass.
- Keyboard Enter on On my way opened the existing ETA form. Alex selected five minutes and submitted. The sheet closed to Play and the saved ETA banner appeared (`immediate-eta-success390.png`). API read-back showed game 13 with one here, one on the way, three open spots and Alex not joined. Reopening discovery showed Your ETA; it opened the existing arrival status rather than another ETA request (`immediate-existing-eta390.png`).
- Inspected 320/390 mobile widths and 1280 desktop in both themes. Independent doubling of computed fonts initially exposed uneven count alignment and split button words; both were fixed. `immediate-large320-final.png` and `immediate-large320-actions-final.png` show the final enlarged state. Page and modal scroll widths remained 320px. Normal-size desktop and narrow-card captures predate this last layout refinement. No physical-device or screen-reader verification was performed.

## Validation and next scope

809 targeted frontend/design and live-rally/arrival backend checks passed after the final changes, along with JavaScript syntax and diff whitespace checks. New executed tests verify escaped full names, distinct counts, unavailable actions, retry, empty results and ignored late reads. An existing source contract was updated for the shared court-details handler. The first broader run caught the no-area wording and retained Sent icon contracts; both are satisfied in the final run.

This change covers live discovery, the menu, court integration and entry to ETA sharing. It does not complete immediate play. Next review the ETA form/status, cancellation, on-arrival check-in and joining, then at-court creation and completion. Joined-host discovery labels, roster visibility expectations, larger result sets, active-area changes, nearby home banners, stale capability/full-game responses, physical location denial and background/mobile presence remain open for that review. Source only; no release build, database export or deployment. The existing production backup-approval boundary is unchanged.
