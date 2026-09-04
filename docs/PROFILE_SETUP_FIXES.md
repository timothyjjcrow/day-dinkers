# Profile setup fixes — r66

The photo step inherited a two-column layout whose first column was only 50px.
The extra label put the photo actions in that narrow column. Onboarding now uses
a centered 96px preview and properly sized photo controls on mobile and desktop.

Court setup no longer truncates suggestions to three. It offers 12 nearby
results, cursor-based Show more, and name/city search across the full directory.
Search is available even without a home area or when nearby results fail.
Empty results, retry, and stale-request protection keep the picker usable.
Primary-court and saved-court actions work on every result. Done keeps the
primary-button styling instead of inheriting the icon-only close-button color.

Browser verification used the separate demo database: searched Larson Park,
saved it, set it primary, checked the database record, and expanded nearby
results from 12 to 24. Desktop and mobile photo layouts were visually checked.
603 regression tests passed before the final small button-style adjustment;
focused onboarding and release checks cover the final sources and assets.

This release also includes the previously reviewed local r65 planner changes.
