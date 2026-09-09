# Play and agenda implementation evidence

Local working-tree implementation; no deployment. Production verification remains a separate release gate.

Implemented: Play refresh preserves keyed disclosures, focused controls/selection and scroll; creation explicitly chooses casual, ranked or competition; planning creates the actual game before opening its exact invitation URL; My plans includes later dates, pending invitations/waitlists and specific tournament/league matches; discovery offers real pagination and avoids duplicate occurrences from one series. Accepted league dates appear in the calendar; proposed times do not. Tournament match appointments are labeled estimated and exported as tentative.

The isolated mobile fixture on port 8040 verified an open Play now menu and keyboard focus remained through many 15-second refreshes. At 390×844, My plans exposed +8/+20-day sessions and pending competition decisions. Casual creation through Where/When/Who created game 14, opened its detail, then opened the invitation showing /g/14. Native sharing was inconclusive, so a visible URL and independent Copy link fallback were added. The latest fallback still needs its final click check; this evidence does not claim native sharing succeeded.

Focused executable checks cover future plans, invitations/waitlists without invented membership, revoked private visibility, stable pagination, league/tournament agenda context and actual calendar content. The frontend tests execute schedule rendering, intent choices and loaded-window restoration. The additive league migration test preserves old result scores/IDs and is idempotent. A recent combined root run had 33 tests pass; subsequent consent/schema changes require integrated rerun.

Files: tests/test_product_audit_agenda.py, tests/test_product_audit_play_frontend.py, tests/test_product_audit_migrations.py. League scheduling and recurrence have separate evidence notes. Full multi-role release verification remains outstanding.
