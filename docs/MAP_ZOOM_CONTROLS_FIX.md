# Map zoom controls and refreshed launch assets

Published September 5, 2026 in frontend r70 (service worker r72).

The search bar previously occupied Leaflet's top-left zoom-control position.
The map now reserves a separate left strip for both zoom buttons. Search,
quick filters, and search suggestions share the same inset, including at the
900-pixel desktop breakpoint. Buttons have 44 x 44 pixel targets and visible
keyboard focus. Search suggestion titles and descriptions occupy separate
lines so narrow menus do not run them together.

The layout respects the top and left safe-area insets. The desktop results
panel remains separate from the search menu. The small map used for adding a
court is unchanged; the new styles are scoped to the main court map.

Validation:

- 161 focused court, frontend, accessibility, release, and service-worker checks
  passed on the final build.
- Browser geometry and hit tests passed at 320x568, 360x740, 390x844, 430x880,
  600x900, 768x1024, 899x700, 900x700, 1024x768, 1280x800, 1440x900,
  1920x1080, 568x320, 667x375, 844x390, and 1024x500.
- Both buttons were at least 44 x 44, inside the viewport, reachable by pointer,
  and separate from the search bar, filters, results panel, and navigation.
- Open search suggestions were checked at narrow-phone and desktop widths.
  Pointer zoom-in and keyboard Enter zoom-out changed the map's tile scale.
- Syntax and whitespace checks passed. Production health/database checks,
  service worker, all three release asset hashes, and previous-release
  availability passed after promotion.

Deployment: https://third-shot-m5c3jqwnw-timothyjjcrows-projects.vercel.app
Live site: https://third-shot.vercel.app/

The LinkedIn kit in `output/linkedin/` was rebuilt with fresh map and planner
screenshots. It includes revised 2,823-character post copy, eight 1080x1350
images, an eight-page PDF, a 42-second silent feature-tour MP4, alt text,
a preview page, and a ZIP. Demo-account labels and map attribution remain.
These are local draft artifacts; nothing was published to LinkedIn.
