# Schedule picker rework — release r69

The play planner now starts with three complete time suggestions. **More common
times** expands to nine choices in place. A visible date button and editable
12-hour clock with AM/PM buttons replace the nested disclosures, half-hour grid,
and native combined date/time popup.

The shared inline calendar supports month navigation, arrow-key movement,
Escape to close, today highlighting, and disabled past days. Exact minutes,
noon/midnight, and local timezone conversion are retained; nonexistent spring
DST times are rejected. The same editor is used when editing and rescheduling
existing games. Date-only and AM/PM-only edits participate in discard checks.

Duration offers 1 hour, 1½ hours, 2 hours, and no end time. Custom minutes appear
on demand, with the selected duration retained in the button label. Weekly
repeat settings stay behind a single switch. The Next button remains available
while scrolling expanded options.

One canonical local date/time value now drives the visible editor, suggestions,
summary, availability matching, recurrence defaults, saved drafts, and submitted
payload. Legacy preset/custom drafts still restore from their saved timestamp.

Validation:

- All 1,295 tests passed before production promotion (262 seconds).
- 610 frontend/design-system checks passed on the final sources and generated assets.
- 133 targeted planner, recurrence, edit, calendar, and release checks passed.
- Browser checks on an isolated in-memory demo server verified suggestion
  expansion/selection, invalid-time recovery, exact minutes, month rollover by
  keyboard, date selection without dismissing the planner, custom duration,
  weekly recurrence, draft recovery, and create/edit persistence.
- Mobile checks at 320 and 390 pixels found no horizontal overflow and kept Next
  reachable. Desktop/light and mobile/dark layouts were visually inspected.
- Production assets remain below the existing 250 KiB Brotli app budget. r69
  has separate immutable routes; earlier releases remain available. Service
  worker revision is r71.
- Staged and live checks verified health/database readiness, exact asset hashes,
  compressed delivery, the new shell, and availability of the previous release.

Local demo preview: http://127.0.0.1:8013/ (ui-player-0@example.com / pickleball).
Screenshots are in `output/schedule-picker/`.

Published September 5, 2026 to https://third-shot.vercel.app/.
Deployment: https://third-shot-ba3xlv0fh-timothyjjcrows-projects.vercel.app
Source commit: `7244223`. The deployment was built without assigning the public
domain, verified, and then promoted after the full suite passed.
