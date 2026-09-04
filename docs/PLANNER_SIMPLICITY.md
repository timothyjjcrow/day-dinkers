# Planner simplicity — local release r65

This cycle follows the production deployment of r64. It simplifies game setup
without removing scheduling or audience options.

- Suggested times remain the first choice. Choose another time opens an exact
  date/time field; the day strip and half-hour grid remain in Browse half-hour
  times instead of competing with the date field.
- Standard durations and No end time stay visible. Custom duration opens on
  demand and opens automatically when a restored or preset duration is custom.
- An invalid duration is caught before advancing to player selection. The
  relevant control opens and receives focus with an inline explanation.
- Type and group size appear as an accurate summary with Change/Done controls.
  Ranked, singles, doubles, group capacity, and existing permission restrictions
  are retained. Group sizes above four are no longer summarized as Doubles.
- Optional community posting is disclosed separately and starts open when a
  community is already selected. Invitations and advanced settings are retained.
- Moving between planner steps returns the form to the top and focuses the new
  heading. Validation reveals hidden setup controls when they need correction.

603 selected regression checks passed. Browser checks on the separate demo
database verified exact time and custom duration persistence across steps,
invalid-duration handling, changing group size, and saving an open doubles plan
with the selected title, 10:15 AM start, and 45-minute duration. The saved record
was checked directly in the demo database. No production game was created.
Final browser checks confirmed step transitions return to the top and focus the
new heading. Mobile and desktop layouts had no horizontal overflow or reported
browser errors. JavaScript syntax and diff checks passed.

Release r65 uses separate immutable assets and service-worker revision r67.
The production site remains on r64. Preview: http://127.0.0.1:8012/.
This development pass has not been deployed.
