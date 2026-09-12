# Third Shot r77 release

Status: **live in production on September 9, 2026** at https://third-shot.vercel.app.

Application release: `164030db8316b7b46a1c2edf30a5866e769c06f7`. Main release commit: `23ce8e0f103bfc21bac597330076797a1a919555` (the latter changes only a test fixture and evidence note). Vercel verified the main deployment READY: `dpl_GAuUUYkBYix97t8qa2V5FN2rYBpx`, https://third-shot-ks01rl0tp-timothyjjcrows-projects.vercel.app. The CLI candidate was independently verified before promotion; Git main then deployed identical production files.

## Changes included

- Mobile court discovery, selected-place actions, useful mixed play timelines and preserved venue access details.
- Dated recurring sessions, explicit waitlist offers and consent, host handoff, attendance and conflict review across sessions, leagues and tournaments.
- Clearer match/result context, tournament partner recovery, all active matches, league scheduling and round operations.
- Venue editing, reviewed public content, dated offerings, visitor information and location corrections.
- Account/profile flows, readable data export, chat search/replies, preferences, activity and privacy boundary fixes.
- Shared text/corner styling and a new immutable frontend release; earlier assets remain available for open clients.

## Verification

- Production schema migration completed after a private backup. A second read confirmed unchanged core counts: 19 accounts, 18,510 courts, 85 games, 117 roster records and 11 messages.
- Same migration ran twice on a disposable PostgreSQL database initialized from the preceding source/schema. Existing synthetic records survived; defaults, foreign keys and reply deletion were checked.
- 29 scheduling/recurrence/agenda tests passed on PostgreSQL.
- Four true concurrent PostgreSQL request tests passed. A recurring-edit/join deadlock found during testing was fixed with a targeted User row lock change, preserving fresh overlap review and rollback on changed rosters.
- 50 release-asset, schedule request, schema and migration checks passed.
- Browser verification at 390px covered map→mixed court timeline→dated session→refreshed return, partner expiry→extension→fresh consent, all-match return navigation, account export/MFA, public-group preferences, waitlist acceptance→conflict confirmation→saved overlaps→correct session. No errors in the final agenda browser check.
- Complete local suite: **1,719 passed, 1 skipped** in 462.97 seconds. The dedicated PostgreSQL-only suite passed separately (four concurrent-request cases).
- Final frontend contracts: **762 passed** across 112 files; release assets were covered separately.
- First Linux CI run rebuilt the assets exactly and passed 1,718 tests. One test fixture imported optional Pillow; replaced its generated images with identical static PNG bytes, and all ten venue reviewed-version tests passed. No production dependency or application code changed. The complete CI rerun passed (verified September 12, 2026) at https://github.com/timothyjjcrow/day-dinkers/actions/runs/34322492440.
- Live public smoke checks passed for production database health, index/r77 links, court details, court play timeline, exact Brotli application/CSS bytes and unchanged r76 compatibility. Runtime error query returned no errors.
- Fresh live mobile browser at 390×844 loaded r77, searched Larson Park and opened its public court page; no horizontal overflow and no browser errors.

## Known boundaries

Browser push delivery remains disabled in production; real background receipt is not part of the release claim. Physical-device accessibility and large live competition fields remain unverified audit gates. The original 104-group implementation goal remains open.

The new functionality increases the main Brotli application transfer to about 286 KiB. The release has explicit 300 KiB application and 370 KiB aggregate runtime-asset caps. Deferred loading remains a performance improvement to pursue.
