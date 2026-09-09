# Identity and Activity implementation evidence — 2026-09-09 UTC

Implemented in the shared working tree; no production deployment or browser completion claim.

- ID-02: 8-character minimum for new registration, password change and reset; legacy login remains accepted. API test covers 7-character rejection and legacy 6-character login.
- ID-05/06/07: Upcoming plans expand directly below Up next, outside history. Find a game is the empty-state primary action. Photo and usual-times shortcuts open focused edits.
- ID-08 partial: Edit profile in Settings opens About / Photo / Usually plays / Primary court choices. PATCH is scoped to the selected section, with a behavioral test proving photo/availability changes cannot overwrite name, ratings or court. Compact preview and competition-rating disclosure remain refinements.
- ID-04 partial: Profile self-rating uses roving focus and arrow/Home/End keys. Onboarding and group radio groups still need the same treatment.
- ID-09 partial: Editor accurately labels DUPR as added by the player, not verified. Other public identity displays remain to review.
- ID-13/14: /games/history filters wins/losses/ranked/casual before pagination. UI fetches each filter, ignores superseded requests and provides retry. Duplicate Recent play rows removed.
- ID-26/28: Activity filters on the server before pagination and separates venues, safety/account and other updates. Needs your action is derived from current friendship, crew, club, invitation, score and partner-request state; reading does not falsely resolve a decision.
- ID-27: Notification wording distinguishes Activity storage from device delivery.
- ID-32: Nearby visibility updates the open privacy sheet’s court-presence heading and audience together.
- ID-33 partial: Help & safety is in Settings, with joining, session/match, cancellation, privacy, alerts, disputes and reporting topics plus support and settings links. Contextual links from each product control remain.

Verification: node --check public/app-v15.js passed. python3 -m pytest on identity_audit_flows, identity_audit_frontend, profile_history_focus_frontend, profile_activity_settings_frontend, ui_interaction_foundation_frontend, account_deletion_and_photo_frontend, primary_court_area_frontend and auth_onboarding_boot_audit_frontend: 89 passed. Pagination-notification and profile-stats regression subset also passed earlier (18 total).

Browser verification to complete with the integrated site: Me plans expansion; focused photo and availability saving; privacy visibility change without closing; Activity filter reaches older items and request resolution removes inline decisions; narrow-width/keyboard navigation.
