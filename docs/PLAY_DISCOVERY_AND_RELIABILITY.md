# Play discovery and reliability — local release r63

This pass builds on the UI simplification in r62. It focuses on finding a
suitable game, confirming attendance, and playing with the same people again.

## Discovery

- Find games offers Any time, Now, Today, and Next 7 days.
- Now includes games in progress or starting within an hour. Today ends at the
  player's local midnight, including daylight-saving transitions.
- Travel radius and skill preferences are remembered separately for each account.
- Time filters apply before pagination and preserve private-game visibility.
  My plans remains available regardless of discovery filters.
- Empty results offer a filter reset and court discovery. When friends' games
  are already shown, the nearby empty state explicitly refers to other games.

## Attendance and repeat play

- Scheduled games distinguish open spots, a full roster, and confirmed attendance.
  Attendance confirmation does not imply a court reservation or physical presence.
- Players who need to reconfirm see the game in their next actions with an
  I’m coming button using the existing attendance endpoint.
- My plans offers repeat play from a recent completed session with other players.
  It opens the existing planner directly with the court, duration, and eligible
  teammates carried forward. The plan defaults to private and remains editable.
- Existing crew authorization and invitation rules apply. Opening the planner
  does not create a game or send invitations; scheduling remains the final action.

## Verification and delivery

602 selected frontend, release, privacy, pagination, roster-management, completed
game, and discovery checks passed. New executable tests cover local-day boundaries,
timezone offsets, malformed filters, filtering before pagination, private-game
visibility, account-scoped preferences, and attendance summaries. JavaScript
syntax checks and `git diff --check` passed.

Browser checks used a separate temporary SQLite database. They verified Today
excludes tomorrow's session, attendance confirmation clears the pending action,
repeat play opens a private plan with the same teammate and duration, and travel
radius survives reload. Mobile and desktop layouts were checked for overflow and
browser errors. The repeat planner was closed without creating a game.

Release r63 includes generated minified/compressed assets and service-worker
shell revision r65. Previous release assets remain available. Preview:
http://127.0.0.1:8012/.

Deployed September 4, 2026 to https://third-shot.vercel.app/ as
`dpl_HMamq8BR8VKry11KANZEhEM3wpLH` (READY, production, Flask).
Production health returned `status: ok` and `db: true`. The live document
references r63, and the served JavaScript SHA-256 matches the local r63 artifact.
The public account screen loaded without reported browser errors. A deployment
error-log query returned no entries; authenticated flows were verified locally.
