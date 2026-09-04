# Discovery clarity — local release r64

This development pass follows the production deployment of r63.

- Time, travel radius, and skill controls appear together before game results.
- Friends' games respect the same travel radius as nearby games when a player
  has selected an area. Filtering happens before pagination. The fallback
  request uses the same area, and My plans remains independent of search filters.
- Game cards display one roster summary, retain confirmation and waitlist
  information, and give the title its own line. Compact discovery cards keep
  the stated skill range without redundant personalized match badges.
- Court pages show official venue information, regular play, and scheduled
  player sessions before reviews and player lists. Existing joining, directions,
  hosting, check-in, and business actions remain available.

603 selected regression checks passed. Added API coverage checks nearby and
distant friends' games, filtering before pagination, widening the radius, and
preserving personal plans and the legacy friends view without a location.
JavaScript syntax and diff checks passed.

Browser walkthroughs on the separate demo database confirmed filters precede
results, cards open the full game plan, and court sessions retain join, share,
and calendar actions. Court sections appear in the intended order. Mobile and
desktop overflow checks passed, with no reported browser errors.

Release r64 includes separate immutable assets and service-worker revision r66.
Deployed September 4, 2026 to https://third-shot.vercel.app/ as
`dpl_Fp1AJkRZNHAjn5CSh1vFyUstcpZF` (READY, production, Flask).
The production health check returned `status: ok` and `db: true`. The live
document references r64 and the served JavaScript SHA-256 matches the local
r64 artifact. The public account screen loaded without reported browser errors.
A deployment error-log query returned no entries; authenticated flows were
verified locally.
