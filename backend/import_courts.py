"""CLI utility to import/upsert county court datasets."""

import argparse
import json

from backend.app import create_app, db
from backend.services.court_importer import (
    import_county_from_file,
    import_county_slug,
    list_county_files,
)


def _build_parser():
    parser = argparse.ArgumentParser(
        description='Import court data from a county JSON file and upsert by county/name/city.',
    )
    parser.add_argument(
        '--county',
        help='County slug to import from backend/data/courts/ca/<county>.json or to force for --file.',
    )
    parser.add_argument(
        '--file',
        help='Path to a JSON payload file. If omitted, --county dataset file is used.',
    )
    parser.add_argument(
        '--env',
        default='development',
        choices=['development', 'testing', 'production'],
        help='App config environment to use (default: development).',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate and preview results without committing database changes.',
    )
    parser.add_argument(
        '--list-counties',
        action='store_true',
        help='List available county files under backend/data/courts/ca.',
    )
    return parser


def main():
    args = _build_parser().parse_args()
    app = create_app(args.env)

    with app.app_context():
        if args.list_counties:
            counties = list_county_files()
            print(json.dumps({'counties': counties}, indent=2))
            return 0

        if not args.file and not args.county:
            raise SystemExit('Provide --file or --county (or use --list-counties).')

        if args.file:
            result = import_county_from_file(
                args.file,
                county_slug=args.county,
                commit=not args.dry_run,
            )
        else:
            result = import_county_slug(
                args.county,
                commit=not args.dry_run,
            )

        if args.dry_run:
            db.session.rollback()
            result['dry_run'] = True
        print(json.dumps(result, indent=2))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
