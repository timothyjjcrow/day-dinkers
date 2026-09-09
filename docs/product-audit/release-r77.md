# Third Shot r77 release

Status: production schema is upgraded; application candidate awaits deployment.

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
- Whole-suite CI results, release commit and deployment URLs will be appended after publication.

## Known boundaries

Browser push delivery remains disabled in production; real background receipt is not part of the release claim. Physical-device accessibility and large live competition fields remain unverified audit gates. The original 104-group implementation goal remains open.

The new functionality increases the main Brotli application transfer to about 286 KiB. The release has explicit 300 KiB application and 370 KiB aggregate runtime-asset caps. Deferred loading remains a performance improvement to pursue.
