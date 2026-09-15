# Saved checkpoint — UI focus and session entry

Superseded September 14: the user replaced and resumed the app goal with a complete UI/UX focus. Continue using `UI_UX_GOAL.md`. The code/release and backup-approval boundaries below still apply.

Work is saved on `codex/mobile-auth-clarity` in response to the user's request to push the changes and pause. Future passes must follow `ui-pass-standard.md`; do not resume the audit loop until the user asks.

This branch contains mobile account-access improvements and source changes requiring review of the current session plan before a new join or waitlist acceptance. A changed price, time, court, host, or other playing terms produces an explicit review sheet showing the current plan and players before a place is taken. Missing snapshots also require review. Existing membership retries retain their separate reconfirmation policy.

Validation recorded at this checkpoint: 752 frontend/design-token checks passed (one pre-existing warning), 15 new backend plan-review checks passed, and the focused review/request tests passed. A broader API batch returned 309 passes and two legacy fixture failures; the fixtures were updated to send the reviewed plan and both focused reruns passed. This is not a full-release test result. Browser inspection at 390px confirmed that changing a free session to $12 opened review and did not change the roster on cancellation.

Remaining: verify the latest cancellation change refreshes the underlying detail screen; complete small-screen, theme, enlarged-text and keyboard checks for the review sheet; adapt PostgreSQL concurrency fixtures and test edits racing against first joins and waitlist accepts; run full release gates; build a new immutable asset release. The inherited r80 bundles do not include these source changes. Do not deploy this worktree as it stands.

The separate r80 candidate passed CI run 34922007591 with 1,762 tests passed and one PostgreSQL-only skip, including its frontend build and container job. Production remains r79. Production deployment is held pending the user's explicit approval for the private production database backup previously rejected by automatic approval review. Do not bypass or retry that export without approval.

No Third Shot recurring automation was found in the configured local automations directory. The available goal tool can only mark complete or blocked, and cannot pause an active goal. Neither status describes this unfinished work; the goal must be paused through the application's user control. Stop further implementation at this checkpoint.
