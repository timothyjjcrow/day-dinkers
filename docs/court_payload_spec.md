# Court Payload Spec

This file defines what the app expects for court-related payloads.

Use this as the safe contract for:
- creating courts (`POST /api/courts`)
- admin updating courts (`PUT /api/courts/<court_id>`)
- user-submitted court updates (`POST /api/courts/<court_id>/updates`)

If a field is not listed here, it is ignored by the backend.

## 1) Admin Court Payload (`POST /api/courts`, `PUT /api/courts/<court_id>`)

### Example create payload

```json
{
  "name": "Arcata Sports Complex",
  "description": "Outdoor dedicated pickleball courts with lights.",
  "address": "123 Main St",
  "city": "Arcata",
  "state": "CA",
  "zip_code": "95521",
  "county_slug": "humboldt",
  "latitude": 40.8665,
  "longitude": -124.0813,
  "indoor": false,
  "lighted": true,
  "num_courts": 6,
  "surface_type": "Asphalt",
  "hours": "Daily 7am-10pm",
  "open_play_schedule": "Mon/Wed/Fri 8am-11am",
  "fees": "Free",
  "phone": "(707) 555-1212",
  "website": "https://example.org/courts",
  "email": "parks@example.org",
  "photo_url": "https://example.org/courts/arcata.jpg",
  "has_restrooms": true,
  "has_parking": true,
  "has_water": true,
  "has_pro_shop": false,
  "has_ball_machine": false,
  "wheelchair_accessible": true,
  "nets_provided": true,
  "paddle_rental": false,
  "skill_levels": "all,beginner,intermediate,advanced",
  "court_type": "dedicated",
  "verified": true
}
```

### Required fields
- Create (`POST /api/courts`): `name`, `latitude`, `longitude`
- Update (`PUT /api/courts/<court_id>`): no required fields, but at least one valid field must be provided

### Field constraints

#### Strings
- `name`: max 200, cannot be empty if provided
- `description`: max 3000
- `address`: max 500
- `city`: max 100
- `state`: uppercased and trimmed to 2 chars
- `zip_code`: max 10
- `county_slug`: max 80, normalized to lowercase slug (letters/numbers/hyphens). Example: `los-angeles`
- `surface_type`: max 50
- `hours`: max 2000
- `open_play_schedule`: max 2000
- `fees`: max 200
- `phone`: max 30
- `website`: max 500
- `email`: max 200
- `photo_url`: max 500
- `skill_levels`: max 100 (free-form; recommended comma-separated values)
- `court_type`: max 50, lowercased, must be one of:
  - `dedicated`
  - `converted`
  - `shared`

#### Numbers
- `latitude`: number between `-90` and `90`
- `longitude`: number between `-180` and `180`
- `num_courts`: integer between `1` and `100`

#### Booleans
Fields:
- `indoor`
- `lighted`
- `has_restrooms`
- `has_parking`
- `has_water`
- `has_pro_shop`
- `has_ball_machine`
- `wheelchair_accessible`
- `nets_provided`
- `paddle_rental`
- `verified`

Accepted bool inputs:
- true values: `true`, `1`, `yes`, `on`
- false values: `false`, `0`, `no`, `off`
- native JSON booleans also accepted

### Frontend behavior expectations
- UI labels treat `court_type` as:
  - `dedicated` => dedicated pickleball
  - `converted` => converted/tennis-lined
  - anything else falls back to shared label
- Dedicated filter checks exactly `court_type === "dedicated"`.

## 2) Community Update Payload (`POST /api/courts/<court_id>/updates`)

### Example payload

```json
{
  "summary": "Updated location pin, open play schedule, and added one image.",
  "source_notes": "Verified in person on 2026-02-11.",
  "confidence_level": "high",
  "location": {
    "address": "123 Main St",
    "city": "Arcata",
    "state": "CA",
    "zip_code": "95521",
    "latitude": 40.8665,
    "longitude": -124.0813
  },
  "court_info": {
    "name": "Arcata Sports Complex",
    "description": "Recently resurfaced.",
    "num_courts": 6,
    "surface_type": "Asphalt",
    "fees": "Free",
    "phone": "(707) 555-1212",
    "website": "https://example.org/courts",
    "email": "parks@example.org",
    "skill_levels": "all,beginner,intermediate",
    "court_type": "dedicated",
    "indoor": false,
    "lighted": true,
    "has_restrooms": true,
    "has_parking": true,
    "has_water": true,
    "has_pro_shop": false,
    "has_ball_machine": false,
    "wheelchair_accessible": true,
    "nets_provided": true,
    "paddle_rental": false
  },
  "hours": {
    "hours": "Daily 7am-10pm",
    "open_play_schedule": "Mon/Wed/Fri 8am-11am",
    "hours_notes": "Holiday schedule varies."
  },
  "community_notes": {
    "location_notes": "Best entrance is from 4th street lot.",
    "parking_notes": "Overflow lot behind gym.",
    "access_notes": "Gate opens at 6:45am.",
    "court_rules": "Paddle queue in effect at peak hours.",
    "best_times": "Weekday mornings",
    "closure_notes": "Closed during heavy rain.",
    "additional_info": "Wind screens added in 2026."
  },
  "images": [
    {
      "image_url": "https://example.org/courts/photo-1.jpg",
      "caption": "South-facing courts"
    }
  ],
  "events": [
    {
      "title": "Spring Round Robin",
      "start_time": "2026-03-12T18:00:00",
      "end_time": "2026-03-12T20:30:00",
      "description": "All skill levels welcome.",
      "organizer": "Local Club",
      "contact": "coach@example.org",
      "link": "https://example.org/events/round-robin",
      "recurring": "monthly"
    }
  ]
}
```

### Required/validation rules
- `summary`: required, minimum 10 chars, max 500
- Must include at least one structured change (`location`, `court_info`, `hours`, `community_notes`, `images`, or `events`)
- `confidence_level`: `low`, `medium`, or `high` (defaults to `medium`)

### Location rules
- `address`, `city`, `zip_code`: max 200
- `state`: uppercased, max 2 chars
- `latitude`: number in `[-90, 90]`
- `longitude`: number in `[-180, 180]`

### Court info rules
- Text limits:
  - `name` 200
  - `description` 3000
  - `surface_type` 80
  - `fees` 300
  - `phone` 40
  - `website` 500
  - `email` 200
  - `skill_levels` 120
  - `court_type` 80
- `court_type` must be one of:
  - `dedicated`
  - `converted`
  - `shared`
- `num_courts`: integer in `[1, 100]`
- same boolean parsing as admin payload

### Hours rules
- `hours`: max 1000
- `open_play_schedule`: max 1000
- `hours_notes`: max 1200

### Community notes limits
- `location_notes`: 1200
- `parking_notes`: 1200
- `access_notes`: 1200
- `court_rules`: 1200
- `best_times`: 800
- `closure_notes`: 1200
- `additional_info`: 2000

### Images
- max images per submission controlled by env (`COURT_UPDATE_MAX_IMAGES`, default `8`)
- each image must be:
  - valid `http://` or `https://` URL, or
  - `data:image/...;base64,...`
- base64 image size limit controlled by env (`COURT_UPDATE_MAX_IMAGE_BYTES`, default `2MB`)

### Events
- max events per submission controlled by env (`COURT_UPDATE_MAX_EVENTS`, default `6`)
- each event requires:
  - `title`
  - valid ISO `start_time`
- `end_time` (if provided) must be >= `start_time`
- `link` (if provided) must be valid `http(s)` URL

## 3) Safe defaults to avoid breakage

- Always set `county_slug` explicitly for imports (example: `humboldt`, `alameda`, `los-angeles`)
- Always use lowercase `court_type`: `dedicated`, `converted`, or `shared`
- Keep `skill_levels` as a comma-separated string (example: `all,beginner,intermediate`)
- Send real JSON booleans (`true`/`false`) when possible
- Send numeric `latitude`/`longitude` (not text)
- For updates, only send fields you actually want to change

## 4) County-aware endpoints

- `GET /api/courts?county_slug=<slug>` returns only courts in one county
- `GET /api/courts/counties` returns available counties and court counts
- `GET /api/courts/resolve-county?lat=<lat>&lng=<lng>` returns the nearest county with known courts

For county import/upsert workflow, see:
- `docs/county_import_workflow.md`
