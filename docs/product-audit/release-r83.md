# Release r83: full visual redesign

Requested by Tim on September 25, 2026, after r82: "I'm not seeing enough UI
changes that transform the site for the better... go into every aspect of the
site page by page, feature by feature, and redesign the UI." r82 reordered the
app around the map; r83 changes how every screen looks and reads while keeping
that map → court → play-with-friends flow.

## Design system

- Self-hosted brand typeface, Plus Jakarta Sans (SIL OFL,
  `public/vendor/plus-jakarta-sans/`), with bold, tight headlines.
- Palette: deep court green (`--forest`) for hero surfaces, a pickleball-volt
  accent (`--volt`) reserved for the primary action and active states, and a
  warm off-white background. Dark mode has matching tokens.
- Components: volt pill primary buttons, neutral pill secondary buttons, pill
  segmented controls with a raised thumb, borderless cards with soft shadows,
  pill chips and native selects, chevron disclosures, a floating glass bottom
  nav with a volt active indicator, and a branded desktop rail.
- Radii stay on the shared token scale and type never drops below 12px
  (enforced by `tests/test_design_system_tokens.py`).

## Pages

- **Landing and sign-up:** a forest hero with a large headline, pill search,
  the live court map in a floating card, and a volt "Log in or sign up free".
  The sign-up card sits on the hero; onboarding sheets use one icon tile and a
  big headline, and "Use current location" is the primary area choice.
- **Map:** pill search with icon, round controls, chip filters, volt clusters
  and forest court pins. Court list cards drop noise ("Hours not listed", extra
  chips) and lead with a court tile, name, distance pill and live status.
- **Court page:** a scroll-away hero with the court photo or a drawn pickleball
  court, glass fact pills, a condensing top bar, one card of fact rows
  (hours, access, open play), a date-rail schedule, and a floating
  "I'm here / Play here / chat" dock.
- **Play:** a forest "Up next" hero with the next plan, one volt Create game
  button and always-visible Play-now tiles; underline tabs; agenda rows with
  date tiles; game cards with a date tile, avatars and a spots-left pill.
  Rankings and Events are restyled; the Events empty state now offers
  "Create tournament or league" instead of an error.
- **Planner:** step cards, solid-selection chips for times and duration, row
  court suggestions, avatar invite rows, and a sticky volt submit bar.
- **Game page:** a forest hero with the time, court and your status, a roster
  card with avatars, and clear primary and secondary actions.
- **Friends and chat:** inbox rows in one card with unread pills, chip
  filters, friend rows with a volt Play pill, a forest invite card, a forest
  "Find or create a group" card, forest chat bubbles for your messages, and a
  pill composer with a round send button.
- **Me and Settings:** a forest profile hero with stats (plays, upcoming,
  courts, week streak), grouped iOS-style settings lists.

## Size

Measured brotli: app 299.6 KiB, stylesheet 67.0 KiB, aggregate 386.1 KiB. The
app cap moves to 305 KiB and the aggregate cap to 395 KiB. The latin font file
(27 KiB) is a separate cached vendor asset.
