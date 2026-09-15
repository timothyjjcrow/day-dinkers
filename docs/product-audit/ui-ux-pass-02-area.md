# UI/UX pass 2 — first account and area selection

September 14, 2026. Second iteration afterc6e0570. Source changes only; no production deployment.

## Changes

The area picker now leads with Choose your area and a city search. Device location remains an explicit alternative below it, and a saved primary court remains available when present. Removed the large decorative icon, uppercase kicker, divider, and duplicate explanations that the choice is optional. A short note preserves the option to skip and tells players where to change it. Existing save locking, cancellation, failure recovery and account-owned persistence remain intact.

City search previously treated the geocoder's HTTP200 `{error:'geocode_unavailable',items:[]}` response as No cities found. It now shows a service error with Try again. Retry keeps the query and focuses the search input before replacing its button. Typing a city clears an obsolete location/save error, so switching methods does not retain unrelated failure copy.

## Verified flow and evidence

The owned port8065 fixture was confirmed live at the start. Created one disposable account, `area-review@example.test`, through the actual signup form. The first-run picker appeared without a profile questionnaire. Selecting Portland, Oregon returned to nearby games with Portland in the header. A refresh retained the area. No device location was read.

- 390px dark: inspected the initial picker and resulting game discovery screen. The revised picker was inspected through Me → Settings → Privacy & safety → Change home area, also proving the named editing destination exists.
- 320px light/dark: search, all four same-name city choices, skip/cancel and location alternative were visible and scrollable. At twice computed text size, modal scroll width remained320px; Tab moved from search to the first city and Enter saved it.
- Simulated one city-service failure using a one-shot response override for the local `/api/geocode` request. The original UI mislabelled this as No cities found. With the fix it showed an error and Try again; retry used the unchanged query and returned the actual geocoder choices. No automatic area selection occurred.
- Simulated a browser location-permission denial without reading coordinates. The error received focus and re-enabled both search and location controls. On the final source, typing a city hid the obsolete location error. Cancel returned to Privacy & safety, retained Portland, Oregon and focused the home-area control.
- 1280×900: inspected the picker and resulting city choices. Application browser error output was empty. An automated navigation attempt did not reach Me; it was corrected using a fresh snapshot and verified controls before continuing.
- A fresh API read of the synthetic account showed `home_area='Portland, Oregon'`, `email_verified=false`, and `onboarding_complete=false`. Selecting an area did not falsely certify profile completion or email verification.

Evidence: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-02/area/`. `area-before.png` is the old first-run picker; `area-after-dark.png` and `area-light-320.png` show the new picker in Settings, so the dismissal label differs. Included result, enlarged-text, retry-error, permission-denial and desktop screenshots show the relevant real UI states. Screenshots with the original permission error precede the final cleanup when switching to city search. Physical devices and assistive technology were not tested.

Frontend/design sweep: **757 passed in14.41s**. After the final small obsolete-error cleanup, **87 focused checks passed in2.09s**. Counts overlap. The new executed geocoder test covers the HTTP200 service error, retrying the same query, focus restoration and no implicit selection. Existing primary-court, optional-onboarding, mutation locking and form recovery checks passed. Syntax and diff checks passed. The broad log is saved as `ui-area-checks.log` with the evidence.

## Remaining work and next pass

Pass2 remains in progress. First-time dismissal with no area, email verification/reauthentication before joining, password recovery, and linked-destination account creation still need current browser coverage. Existing functional tests remain regression evidence rather than UI certification.

The first discovery screen exposes several rows of tabs and filters before a game, and the synthetic example leads with full sessions despite available play later in the list. `first-discovery-before.png` records this. Next inspect and consolidate the Find games controls and help users identify available play without hiding full/waitlisted sessions. Area changes, account completion and underlying data integrity must remain accurate.

All remaining full-app passes stay open. Immutable release assets are still behind readable sources, and the recorded concurrency/release gates and production backup approval boundary are unchanged.
