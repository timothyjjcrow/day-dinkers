# UI/UX pass 2 — browsing before signup

September 14, 2026. First iteration after78703a4. Source-only changes; not built into a new release or deployed.

## What became easier

- Court search is immediately available without a redundant Browse courts disclosure or repeated search instruction. The mobile page starts near the top rather than centring a small card in a bright gradient. The entry and account surfaces now use the app's theme background.
- Selecting a court or session makes that place/plan the main heading. The welcome headline and introductory copy disappear while viewing details, and the duplicate account button is hidden when the preview already offers its relevant account action.
- Public navigation now explicitly follows search → court → session. Back to court opens the session's court; Back to courts returns to search. Within this page session, it preserves the query, results, originating result and scroll position. Reloading still starts a fresh in-memory search context.
- Opening signup and returning restores the exact preview action and its scroll position. If an asynchronous preview replacement removed the original control, focus falls back to the current preview action. The visible preview names the screen for assistive technology. Missing-link feedback receives focus and offers Back to courts.
- Replaced the ambiguous Save this court or plan a session button with Open court plus the account requirement. Removed the session's duplicate View this court link after adding Back to court. Counts now say signed up instead of going, avoiding a claim of fresh attendance confirmation.
- Search controls wrap at narrow widths. Public time/court/level/cost facts retain two columns at normal sizes and stack when enlarged text needs the space; the original narrow columns broke words. Noninteractive preview headings remain programmatic reading targets without looking like outlined input fields; interactive controls keep their focus treatment.

## Browser verification

Used the existing owned disposable fixture onport8065, confirmed live before inspection. All courts and sessions were synthetic. No account was created and no real location or production data was used.

1. At390px dark: captured baseline entry and court preview, then final entry, court and session layouts. Court search for Portland returned two synthetic courts; selecting Cedar Park opened its sessions.
2. Court → After-work doubles → signup → Back retained `#game/8`, focused Join this session and restored the preview's accessible name. Back to court → Back to courts preserved Portland, both results and focus on court1. No authentication or automatic joining occurred.
3. At320px light: inspected empty results for a deliberately missing name, normal search wrapping and enlarged text. At twice computed font size, screen scroll width remained320px; the query retained a visible focus ring.
4. At320px dark with doubled text, public session facts stacked into250px-wide rows. Keyboard Enter on Join this session opened signup. Screenshot confirms whole words and readable time/court facts rather than narrow-column fragments.
5. At1280×900: inspected the two-column session facts and overall preview. An unavailable court link showed an explicit message, focused `auth-preview-title`, and named the screen with that message. Back returned to search. Browser errors were empty.

Evidence directory: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-02/`. `entry-before.png` / `entry-after-final.png` and `public-court-before.png` / `public-court-final-dark.png` show the main changes. Other screenshots cover narrow, enlarged-text, session, desktop and error states. The desktop and narrow court evidence precedes only the final removal of the noninteractive heading outline. This is browser verification, not physical-device or screen-reader certification.

## Checks and remaining scope

The existing executed public-preview test now requires signed-up counts and still checks escaping, fees, availability, full/ended actions and private-data omission. Focused24 auth/public/private-link checks passed. Final frontend/design sweep: **756 passed in13.38s**, saved in `ui-entry-checks-final.log` in the evidence directory. Counts overlap. Syntax and diff checks passed.

Pass2 remains in progress. Next inspect actual signup/login continuation, area selection, minimal onboarding and first useful plan. Pagination, network retry, private invitation return paths, transient preview replacement while entering credentials and other account recovery states still need their own browser checks. First-load private/shared-link handling remains a functional regression reference rather than newly certified UI coverage.

Other full-app passes remain open. Source changes are still ahead of the inherited immutable r80 bundle; release/concurrency gates and the production backup approval boundary are unchanged.
