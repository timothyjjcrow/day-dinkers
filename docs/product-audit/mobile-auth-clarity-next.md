# Mobile account access — next release work

September 14, isolated on `codex/mobile-auth-clarity` at `/tmp/thirdshot-mobile-auth-clarity`. Not included in the r80 release candidate. Source edits require a new immutable asset build before shipping; do not deploy this worktree with its inherited r80 bundle.

The old mobile form appeared below the full court-discovery panel. The larger logo and introductory copy pushed the login switch below the first screen. Opening account access now shows the form alone on mobile, uses a smaller brand image and shorter registration copy, and labels the screen with its active purpose. Back to browsing restores the search state and saved scroll position. Desktop keeps its two-column layout.

Enlarged-text inspection found that the absolutely positioned Show/Hide control could overlap password text. It now participates in the row layout beside the input and keeps its own tap target.

Evidence: 13 relevant auth/private-session checks passed. Synthetic browser at390px opened registration and switched directly to login; Back restored the court query Cedar and the Cedar Park result, with focus on the account-access button. At320px, normal/enlarged text and light/dark theme, content width remained320px. Enlarged input right178.6px versus Show button left182.6px proved no overlap; keyboard Tab then Enter toggled visibility and preserved the synthetic value. A final source-browser journey opened the shared session at #game/5, signed in as the synthetic Alex account through the visible form, and returned directly to that session. No browser script errors were reported. Screenshots are retained in the primary workspace’s ignored `output/product-audit/evidence/mobile-auth-next/`.

Remaining before release: integrate current r80 CI corrections, build a new immutable release, run its relevant gates, and exercise successful login and shared-session return after the final build. Physical phone keyboard/screen-reader verification remains an audit boundary. Do not mark ID-35 complete from this one entry-flow improvement.
