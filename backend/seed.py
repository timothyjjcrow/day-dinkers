"""Import court data from the scraper output into the app database.

Usage:
    python -m backend.seed --courts-dir "../pickleball court web scraper/output"
    python -m backend.seed --demo          # also create demo users/games near Orange County, CA
"""
import argparse
import json
import os
import sys
from datetime import timedelta

from backend.app import create_app, db
from backend.models import CheckIn, Court, Friendship, Game, GamePlayer, Message, User, utcnow
from backend.services.court_payloads import normalize_county_slug

DEFAULT_COURTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'pickleball court web scraper',
    'output',
)


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes'}


def _coerce_int(value, default=1):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _court_from_record(record):
    lat = record.get('latitude')
    lng = record.get('longitude')
    if lat is None or lng is None:
        return None
    name = str(record.get('name') or '').strip()
    if not name:
        return None
    state = str(record.get('state') or '').strip().upper()[:2] or 'CA'
    return Court(
        name=name[:255],
        address=str(record.get('address') or '').strip()[:255],
        city=str(record.get('city') or '').strip()[:120],
        state=state,
        county_slug=normalize_county_slug(record.get('county_slug'), fallback=''),
        zip_code=str(record.get('zip_code') or '').strip()[:12],
        latitude=float(lat),
        longitude=float(lng),
        indoor=_coerce_bool(record.get('indoor')),
        lighted=_coerce_bool(record.get('lighted')),
        num_courts=_coerce_int(record.get('num_courts'), default=1),
        surface_type=str(record.get('surface_type') or '').strip()[:120],
        court_type=str(record.get('court_type') or '').strip()[:40],
        open_play_schedule=str(record.get('open_play_schedule') or '').strip(),
        fees=str(record.get('fees') or '').strip()[:255],
        phone=str(record.get('phone') or '').strip()[:40],
        website=str(record.get('website') or '').strip()[:500],
        photo_url=str(record.get('photo_url') or '').strip()[:500],
        has_restrooms=_coerce_bool(record.get('has_restrooms')),
        has_water=_coerce_bool(record.get('has_water')),
        nets_provided=_coerce_bool(record.get('nets_provided')),
        verified=_coerce_bool(record.get('verified')),
    )


def import_court_records(records, seen=None):
    """Insert court records, deduplicating on (name, lat, lng). Returns count added."""
    if seen is None:
        seen = {
            (c.name.lower(), round(c.latitude, 5), round(c.longitude, 5))
            for c in Court.query.all()
        }
    imported = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        court = _court_from_record(record)
        if court is None:
            continue
        key = (court.name.lower(), round(court.latitude, 5), round(court.longitude, 5))
        if key in seen:
            continue
        seen.add(key)
        db.session.add(court)
        imported += 1
    db.session.flush()
    return imported


def import_courts_file(path):
    """Import courts from a single JSON file (optionally gzipped)."""
    import gzip
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt') as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise ValueError(f'{path} does not contain a JSON list')
    imported = import_court_records(records)
    db.session.commit()
    return imported


def import_courts(courts_dir):
    json_files = []
    for root, _dirs, files in os.walk(courts_dir):
        for filename in files:
            if filename.endswith('.json'):
                json_files.append(os.path.join(root, filename))
    if not json_files:
        print(f'No JSON files found under {courts_dir}', file=sys.stderr)
        return 0

    seen = {
        (c.name.lower(), round(c.latitude, 5), round(c.longitude, 5))
        for c in Court.query.all()
    }
    imported = 0
    for path in sorted(json_files):
        try:
            with open(path) as handle:
                records = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f'Skipping {path}: {exc}', file=sys.stderr)
            continue
        if not isinstance(records, list):
            continue
        imported += import_court_records(records, seen=seen)
    db.session.commit()
    return imported


DEMO_USERS = [
    ('dana@example.com', 'Dana Vasquez', 'advanced', '#e8590c'),
    ('marcus@example.com', 'Marcus Lee', 'intermediate', '#1971c2'),
    ('priya@example.com', 'Priya Shah', 'intermediate', '#9c36b5'),
    ('tom@example.com', 'Tom Becker', 'beginner', '#2f9e44'),
]


def seed_demo():
    """Create demo players, friendships, check-ins, and games near Orange County, CA."""
    courts = (
        Court.query.filter(Court.state == 'CA', Court.county_slug == 'orange-county')
        .order_by(Court.num_courts.desc())
        .limit(6)
        .all()
    )
    if not courts:
        courts = Court.query.order_by(Court.num_courts.desc()).limit(6).all()
    if not courts:
        print('No courts available; skipping demo seed.', file=sys.stderr)
        return

    users = []
    for email, name, skill, color in DEMO_USERS:
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                email=email,
                display_name=name,
                skill_level=skill,
                avatar_color=color,
                bio='Demo player. Say hi on the courts!',
                home_court_id=courts[0].id,
            )
            user.set_password('pickleball')
            db.session.add(user)
        users.append(user)
    db.session.flush()

    if not Friendship.query.filter_by(requester_id=users[0].id, addressee_id=users[1].id).first():
        db.session.add(Friendship(requester_id=users[0].id, addressee_id=users[1].id, status='accepted'))

    if not CheckIn.query.filter_by(user_id=users[0].id, checked_out_at=None).first():
        db.session.add(CheckIn(user_id=users[0].id, court_id=courts[0].id, looking_for_game=True))
    if not CheckIn.query.filter_by(user_id=users[1].id, checked_out_at=None).first():
        db.session.add(CheckIn(user_id=users[1].id, court_id=courts[0].id, looking_for_game=False))

    if not Game.query.filter_by(creator_id=users[0].id).first():
        for offset_hours, game_type, court in [(26, 'casual', courts[0]), (50, 'ranked', courts[1 % len(courts)])]:
            game = Game(
                court_id=court.id,
                creator_id=users[0].id,
                scheduled_at=utcnow() + timedelta(hours=offset_hours),
                game_type=game_type,
                max_players=4,
                notes='Demo game — all levels welcome!' if game_type == 'casual' else 'Demo ranked doubles.',
            )
            db.session.add(game)
            db.session.flush()
            db.session.add(GamePlayer(game_id=game.id, user_id=users[0].id))
            db.session.add(GamePlayer(game_id=game.id, user_id=users[1].id))

    if not Message.query.filter_by(sender_id=users[1].id, recipient_id=users[0].id).first():
        db.session.add(Message(
            sender_id=users[1].id,
            recipient_id=users[0].id,
            body='Hey! Up for a game this weekend?',
        ))

    db.session.commit()
    print(f'Demo data ready: {len(users)} users (password: "pickleball").')


def main():
    parser = argparse.ArgumentParser(description='Seed the pickleball database.')
    parser.add_argument('--courts-dir', default=DEFAULT_COURTS_DIR)
    parser.add_argument('--courts-file', help='Single JSON(.gz) court export, e.g. data/courts.json.gz')
    parser.add_argument('--skip-courts', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db.create_all()
        if not args.skip_courts:
            if args.courts_file:
                count = import_courts_file(args.courts_file)
            else:
                count = import_courts(args.courts_dir)
            print(f'Imported {count} new courts (total: {Court.query.count()}).')
        if args.demo:
            seed_demo()


if __name__ == '__main__':
    main()
