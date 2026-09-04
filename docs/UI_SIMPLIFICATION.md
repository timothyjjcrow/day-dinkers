# UI simplification — September 2026

The second pass replaces competing entry points with focused tasks. It retains
existing APIs, permissions, game formats, recurring sessions, and integrations.

## Player paths

- Play has a compact Create game / Play now toolbar. Find games and My plans
  separate discovery from commitments. Rankings and Events remain reachable.
  Empty weekly-session sections and the Play progress card no longer compete
  with finding a game. Score follow-ups still appear in My plans with a count.
- Scheduling asks for the court, then time, then players. Game format and capacity
  belong to the player step. Direct invitations expand when needed; skill range,
  notes, and extended settings live in More options. Existing review, draft
  recovery, validation, recurrence, and private-invitation rules remain intact.
- Community has Messages, Groups, and Players tabs. Groups opens discovery and
  the user's groups directly, without message filters or unrelated court chats.
  Friend requests and group invitations have separate tab badges.
- Courts has Happening now, Saved, and Filters. Open now and Verified venues live
  in the filter sheet; auto check-in remains in Privacy settings. Court details
  prioritize finding players, creating a game, directions, and booking, with
  additional actions in More court options.
- Game details disclose host editing, rescheduling, sharing, and cancellation
  under Manage game. Required score and RSVP actions remain primary.
- Profile prioritizes availability, upcoming play, and saved courts. Progress and
  history share a disclosure. Manage a venue is a simple entry row.

## Business paths

Business Hub is a single venue workspace with an accurate status and next action,
then Venue details, Bookings, Sessions & events, and optional Lessons & services.
Team access, activity, verification, history, security, and ownership are grouped
below. Optional content no longer lowers an artificial setup percentage.

Claim setup separates selecting an existing court from confirming management.
Selection is validated before continuing, and claim evidence stays private.
Claims still require the existing review and authorization process.

Bookings has its own form for an existing booking page and optional membership
link. Schedule feeds and integration requests are secondary options. Booking
changes PATCH only booking fields; venue details no longer duplicates them.
Links require HTTPS and saved links are never described as live while the venue
is unverified, unpublished, under review, or suspended.

## Delivery and verification

Release r62 adds immutable minified and compressed assets; previous release URLs
remain available. The service worker uses shell revision r64. The previous r61
pass was deployed separately; this document describes the current local pass.

Browser walkthroughs use a temporary SQLite database and demo accounts, keeping
existing project and production data separate. Automated coverage includes
runtime business-state and permission checks, staged Play tab switching, claim
step validation/back navigation, and existing frontend/backend regression tests.

Final verification: 581 frontend, design-system, court-filter, pagination, and
release checks passed against the final sources and generated assets. The broad
regression run passed 1,202 tests; its remaining failures were updated UI
expectations and styling checks, all covered by the final passing run. JavaScript
syntax and diff checks passed. Browser checks confirmed private booking review,
HTTPS validation, claim selection gating, the staged planner, separate Groups,
responsive map accessibility, no horizontal overflow, and no reported errors.

The local review server is http://127.0.0.1:8012/ with a separate demo database.
Demo sign-in: dana@example.com / pickleball. No production deployment was made
for this second pass.
