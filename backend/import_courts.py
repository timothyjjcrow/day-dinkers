"""CLI utility to import/upsert court datasets by county or state."""

import argparse
import json

from backend.app import create_app, db
from backend.services.court_importer import (
    import_county_from_file,
    import_county_slug,
    import_state,
    list_county_files,
    list_state_dirs,
)


def _build_parser():
    parser = argparse.ArgumentParser(
        description='Import court data from county/state JSON files and upsert into the database.',
    )
    parser.add_argument(
        '--county',
        help='County slug to import from backend/data/courts/ca/<county>.json or to force for --file.',
    )
    parser.add_argument(
        '--state',
        help='State slug to import from output/<state>/. Imports the combined state file.',
    )
    parser.add_argument(
        '--all-states',
        action='store_true',
        help='Import courts from all available state directories under output/.',
    )
    parser.add_argument(
        '--file',
        help='Path to a JSON payload file. If omitted, --county or --state dataset file is used.',
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
    parser.add_argument(
        '--list-states',
        action='store_true',
        help='List available state directories under output/.',
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

        if args.list_states:
            states = list_state_dirs()
            print(json.dumps({'states': states, 'count': len(states)}, indent=2))
            return 0

        if args.all_states:
            states = list_state_dirs()
            summary = {'states_processed': 0, 'total_created': 0, 'total_updated': 0, 'errors': []}
            for state_slug in states:
                try:
                    result = import_state(state_slug, commit=not args.dry_run)
                    summary['states_processed'] += 1
                    summary['total_created'] += int(result.get('created', 0))
                    summary['total_updated'] += int(result.get('updated', 0))
                    print(f'{state_slug}: created={result.get("created", 0)} updated={result.get("updated", 0)}')
                except (FileNotFoundError, ValueError) as exc:
                    summary['errors'].append(f'{state_slug}: {exc}')
                    print(f'{state_slug}: SKIPPED - {exc}')
            if args.dry_run:
                db.session.rollback()
                summary['dry_run'] = True
            print(json.dumps(summary, indent=2))
            return 0

        if args.state:
            result = import_state(args.state, commit=not args.dry_run)
            if args.dry_run:
                db.session.rollback()
                result['dry_run'] = True
            print(json.dumps(result, indent=2))
            return 0

        if not args.file and not args.county:
            raise SystemExit('Provide --file, --county, --state, or --all-states (or use --list-counties / --list-states).')

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
