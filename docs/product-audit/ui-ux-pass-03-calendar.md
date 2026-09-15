# Calendar subscription UI: September 14, 2026

Source review following78a4ad8 on `codex/mobile-auth-clarity`. Part of pass3; not deployed.

## Findings and changes

At390px, the old Google and device calendar anchors rendered inline and overlapped. Manual-link sharing, copying and reset controls also competed with connecting a calendar. The revised Add to calendar sheet has two distinct, full-width provider rows. It says where each opens, with a short update-delay note. Other calendars and private-link tools are grouped under one disclosure. The privacy explanation remains next to the link.

Reset still requires a concrete confirmation that existing subscriptions will stop updating. While copying, sharing or resetting, competing actions and provider URLs are disabled. A successful reset updates all URLs, clears the old Copied label and points back to reconnecting. An uncertain reset response disables the potentially stale links and offers Reload calendar link. Reload reads the current token rather than repeating the mutation.

Manual-copy failure now restores input focus and full-link selection after the error announcement. Previously the shared error helper could take focus back from the selected input. Closing the sheet or switching accounts prevents late action results from moving focus or installing a new token. Share cancellation remains quiet; a real sharing failure offers copying instead.

## Evidence

Browser verification used the existing in-memory local fixture at8065 with synthetic Sam. No production users or external calendar accounts were used.

- Normal clipboard copy succeeded.
- Cancel reset kept the URL and returned focus to Reset private link.
- Confirm reset changed the local token: fetching the old feed returned404, the new feed returned200. Copy label returned to Copy calendar link; focus moved to the provider choice.
- A browser-only503 override simulated an uncertain reset response. Copy was disabled and provider hrefs removed. Restoring fetch and Reload recovered a usable current link without another reset request.
- A browser-only clipboard rejection left the complete input value selected and focused. All overrides were restored.
- 390px light/dark,320px light/dark,1280px light desktop, doubled element text at320px in both themes. Sheet scroll width matched client width320. No uncaught browser errors were reported. Dark/enlarged screenshots precede the final shortening of “Add your play calendar” to “Add to calendar”; final light screenshots show the shipped source heading.

765 frontend/design checks passed in14.19s. New executable checks cover concurrent reset/copy exclusion, uncertain reset recovery, cancellation, account changes and manual-copy focus. The broad run also caught an outdated source assertion from the preceding agenda pass; it was updated alongside an executable check ensuring eligible expired sessions remain in wrap-up tasks while future and cancelled sessions do not. JavaScript syntax and whitespace checks passed.

Evidence directory: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-03/calendar/`. Compare `before-390-light.png` with `after-390-light.png`. Other images show responsive layouts and copy/reset outcomes; tokens visible in expanded screenshots belong only to the disposable local fixture.

## Limits and next work

Provider links were inspected but not opened into real Google/Apple/Outlook accounts. Provider-specific refresh, manual import compatibility and physical-device behavior remain unverified. The initial token-load failure still uses the existing toast/retry-entry behavior. The full calendar feed coverage, nested settings entry, mixed competition agendas and real assistive technology need further integration checks. Pass3 remains in progress.

Next broaden the actual UI review into court map/list navigation. The production release still needs rebuilt immutable assets and its existing prerequisites; this pass does not resolve the production backup approval boundary.
