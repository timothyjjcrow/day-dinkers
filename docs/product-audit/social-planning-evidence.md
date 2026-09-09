# Social planning implementation evidence

Verified locally on 2026-09-08. These are unshipped worktree changes, not production behavior or a claim that the whole audit is complete.

## Requirements covered

- **ID-11/12:** another player's profile leads with Invite to play, Message and friendship controls. Availability, upcoming plans and courts precede collapsed play history. An eligible invitation does not require matching availability; the existing planner carries the player and court, with a shared-time suggestion only when both players' availability supports it.
- **ID-21:** Nearby offers 5/10/25/50-mile distance and a Beginner (under 3.0) filter, preserving location privacy and block checks. Filters run before pagination, and Show more retains the selected filters.
- **ID-17/18:** conversation search finds permitted direct, court, game, tournament, league and group conversations by name or court context, including older conversations outside the compact inbox limits. Partial source failures are visible and retryable. Ordinary permitted nonfriend chats are labelled Direct, with Not in your friends in the thread; no unsupported acceptance/request flow is implied.
- **ID-19/22:** Plan a game in a direct conversation opens the planner with the other person selected. A shared plan card identifies the court/date and each person's Going or Invited state. Played together offers a direct invitation independently of friendship and carries the last shared court into the planner.

## Verification

44 tests passed across `test_social_audit_paths.py`, `test_social_audit_frontend.py`, `test_community_chat_completion.py`, `test_recent_player_reconnection.py`, `test_community_profile_ui_consistency_frontend.py`, `test_profile_rating_frontend.py`, `test_chat_history_frontend.py`, `test_profile_stats_scope_frontend.py`, `test_crew_chat_context.py`, and `test_chat_history_pagination.py`. `node --check public/app-v15.js` passed after the final UI changes.

The API tests cover search beyond the ordinary 50-DM/20-court limits, result pagination, old joined game conversations, all seven supported conversation types, hidden/private and blocked exclusions, source failure reporting, radius/beginner filtering, invitation eligibility without friendship, no automatic RSVP, atomic rejection of blocked invitations, shared-plan privacy and cancellation. Node tests execute the actual planner-option and recent-player action helpers, including no fabricated mutual availability.

CUA used synthetic Alex/Jordan accounts in an isolated in-memory testing server on port 8051. At a verified 390×844 viewport:

1. Jordan's nonfriend profile, with no shared availability, showed Invite before stats and opened the planner with Jordan and Bonita Canyon Sports Park selected.
2. The direct thread showed Not in your friends and Plan a game. Creating a private session returned to the thread with You going / Jordan invited; reopening the conversation retained that server-backed plan. The guest was not silently added to the roster.
3. Search opened the correct direct conversation, then Back retained the query/results. A court-name search returned joined group, court, game and tournament rooms. Mobile wrapping was corrected during verification; dated game context distinguishes repeated match names.
4. Changing Nearby to five miles and Beginner left the synthetic 2.5-rated player visible and removed the higher-rated players.
5. Played together showed Invite and Add friend as separate actions. Invite opened the planner with the last shared court and selected player.
6. The browser error log was empty. The temporary tab was closed and viewport override reset.

This browser check used desktop Chrome with a mobile viewport, not a physical phone or OS keyboard. Automated tests used isolated SQLite; this slice adds no database columns. Search pagination is not a transactionally frozen snapshot if conversations change while paging. Existing messaging permission and blocking rules remain authoritative; this is not a new message-request system.
