# County Import Workflow

Use this workflow to add or update courts county-by-county as you expand across California.

## 1) Prepare county data file

- Save each county as one JSON file in:
  - `backend/data/courts/ca/<county-slug>.json`
- Example:
  - `backend/data/courts/ca/humboldt.json`
  - `backend/data/courts/ca/alameda.json`
  - `backend/data/courts/ca/los-angeles.json`

Each file should be a JSON array of court objects.
The repo includes all California county files as templates (empty arrays) so you can fill them incrementally.

Include `county_slug` in each item (recommended), or pass `--county` in the import command to force it.

See exact field constraints in:
- `docs/court_payload_spec.md`

## 2) Preview import (no DB writes)

Run a dry run first:

```bash
./venv/bin/python -m backend.import_courts --county alameda --dry-run
```

Or import a custom file path:

```bash
./venv/bin/python -m backend.import_courts --file /absolute/path/to/alameda.json --county alameda --dry-run
```

## 3) Apply import/upsert

From county file in repo:

```bash
./venv/bin/python -m backend.import_courts --county alameda
```

From custom file path:

```bash
./venv/bin/python -m backend.import_courts --file /absolute/path/to/alameda.json --county alameda
```

## 4) What the importer does

- Validates each court payload using the same backend validation as API writes.
- Upserts courts by:
  - `county_slug`
  - normalized `name`
  - normalized `city`
- Updates existing matching courts.
- Creates new courts when no match exists.
- Ignores duplicate rows within the same input file (last duplicate wins).

## 5) Verify

Check counties available to the app:

```bash
curl "http://localhost:5001/api/courts/counties"
```

Check county-specific courts:

```bash
curl "http://localhost:5001/api/courts?county_slug=alameda"
```

## 6) Helpful commands

List county files available in repo:

```bash
./venv/bin/python -m backend.import_courts --list-counties
```

Use production config:

```bash
./venv/bin/python -m backend.import_courts --county alameda --env production
```
