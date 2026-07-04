"""Database models for the pickleball player network."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from werkzeug.security import check_password_hash, generate_password_hash

from backend.app import db


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + 'Z' if dt else None


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


SKILL_LEVELS = ['beginner', 'intermediate', 'advanced', 'pro']
DEFAULT_RATING = 1200
# "Usually plays" slots: <day>-<part>, e.g. mon-eve.
AVAILABILITY_SLOTS = [
    f'{day}-{part}'
    for day in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
    for part in ('am', 'pm', 'eve')
]


class User(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    bio = db.Column(db.String(500), nullable=False, default='')
    skill_level = db.Column(db.String(20), nullable=False, default='beginner')
    avatar_color = db.Column(db.String(7), nullable=False, default='#2f9e44')
    avatar_url = db.Column(db.String(500), nullable=False, default='')
    rating = db.Column(db.Integer, nullable=False, default=DEFAULT_RATING)
    best_rating = db.Column(db.Integer, nullable=False, default=DEFAULT_RATING)
    ranked_wins = db.Column(db.Integer, nullable=False, default=0)
    ranked_losses = db.Column(db.Integer, nullable=False, default=0)
    current_streak = db.Column(db.Integer, nullable=False, default=0)
    best_streak = db.Column(db.Integer, nullable=False, default=0)
    home_court_id = db.Column(db.Integer, db.ForeignKey('court.id'))
    # Last-known location, used for "players near you" discovery (set on check-in).
    last_lat = db.Column(db.Float, index=True)
    last_lng = db.Column(db.Float, index=True)
    last_location_at = db.Column(db.DateTime)
    # Persisted home area — the app centers the map/feeds here on launch.
    home_lat = db.Column(db.Float)
    home_lng = db.Column(db.Float)
    home_area = db.Column(db.String(120))
    # "Usually plays" slots as a JSON array of AVAILABILITY_SLOTS tokens.
    availability = db.Column(db.Text, nullable=False, default='[]')
    # Last ISO week ('YYYY-Www') a weekly recap was generated for.
    last_recap_week = db.Column(db.String(10), nullable=False, default='')
    # JSON array of notification kinds this user has muted (only MUTEABLE ones).
    muted_notifications = db.Column(db.Text, nullable=False, default='[]')
    # JSON array of badge ids the user has already been congratulated for.
    notified_badges = db.Column(db.Text, nullable=False, default='[]')
    # Unguessable token for the personal calendar (.ics) feed; set on demand.
    calendar_token = db.Column(db.String(64), index=True)

    def muted_kinds(self):
        try:
            parsed = json.loads(self.muted_notifications or '[]')
            return {k for k in parsed if k in MUTEABLE_NOTIFICATIONS}
        except (ValueError, TypeError):
            return set()
    # Set when the account is deleted; the anonymized row stays for opponents'
    # match history, but auth and all discovery surfaces reject it.
    deleted_at = db.Column(db.DateTime)

    home_court = db.relationship('Court', foreign_keys=[home_court_id])
    checkins = db.relationship(
        'CheckIn', back_populates='user', lazy='dynamic',
        foreign_keys='CheckIn.user_id',
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def availability_list(self):
        try:
            parsed = json.loads(self.availability or '[]')
        except ValueError:
            return []
        return [s for s in parsed if s in AVAILABILITY_SLOTS] if isinstance(parsed, list) else []

    def to_public_dict(self):
        return {
            'id': self.id,
            'display_name': self.display_name,
            'bio': self.bio,
            'skill_level': self.skill_level,
            'avatar_color': self.avatar_color,
            'avatar_url': self.avatar_url or '',
            'rating': self.rating,
            'best_rating': self.best_rating,
            'ranked_wins': self.ranked_wins,
            'ranked_losses': self.ranked_losses,
            'current_streak': self.current_streak,
            'best_streak': self.best_streak,
            'home_court_id': self.home_court_id,
            'home_court_name': self.home_court.name if self.home_court else None,
            'availability': self.availability_list(),
        }

    def to_dict(self):
        data = self.to_public_dict()
        data['email'] = self.email
        data['home_lat'] = self.home_lat
        data['home_lng'] = self.home_lng
        data['home_area'] = self.home_area
        data['muted_notifications'] = sorted(self.muted_kinds())
        return data


class Court(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(255), nullable=False, default='')
    city = db.Column(db.String(120), nullable=False, default='')
    state = db.Column(db.String(2), nullable=False, default='CA', index=True)
    county_slug = db.Column(db.String(120), nullable=False, default='', index=True)
    zip_code = db.Column(db.String(12), nullable=False, default='')
    latitude = db.Column(db.Float, index=True)
    longitude = db.Column(db.Float, index=True)
    indoor = db.Column(db.Boolean, nullable=False, default=False)
    lighted = db.Column(db.Boolean, nullable=False, default=False)
    num_courts = db.Column(db.Integer, nullable=False, default=1)
    surface_type = db.Column(db.String(120), nullable=False, default='')
    court_type = db.Column(db.String(40), nullable=False, default='')
    open_play_schedule = db.Column(db.Text, nullable=False, default='')
    fees = db.Column(db.String(255), nullable=False, default='')
    # Free-text opening hours, community-maintained via edit suggestions.
    hours = db.Column(db.String(255), nullable=False, default='')
    phone = db.Column(db.String(40), nullable=False, default='')
    website = db.Column(db.String(500), nullable=False, default='')
    photo_url = db.Column(db.String(500), nullable=False, default='')
    # User-uploaded photo as a data URL, served via /courts/<id>/photo
    # (Render's free-tier disk is ephemeral, so files can't live on disk).
    photo_data = db.Column(db.Text)
    has_restrooms = db.Column(db.Boolean, nullable=False, default=False)
    has_water = db.Column(db.Boolean, nullable=False, default=False)
    nets_provided = db.Column(db.Boolean, nullable=False, default=False)
    verified = db.Column(db.Boolean, nullable=False, default=False)
    # Community-flagged permanently closed (via 2-user suggest consensus).
    # Hidden from map/list/search but kept for historical game/check-in refs.
    closed = db.Column(db.Boolean, nullable=False, default=False)

    checkins = db.relationship('CheckIn', back_populates='court', lazy='dynamic')
    games = db.relationship('Game', back_populates='court', lazy='dynamic')

    def to_summary_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'city': self.city,
            'state': self.state,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'indoor': bool(self.indoor),
            'lighted': bool(self.lighted),
            'num_courts': self.num_courts,
            'photo_url': self.photo_url,
        }

    def to_dict(self):
        data = self.to_summary_dict()
        data.update({
            'address': self.address,
            'zip_code': self.zip_code,
            'county_slug': self.county_slug,
            'surface_type': self.surface_type,
            'court_type': self.court_type,
            'open_play_schedule': self.open_play_schedule,
            'fees': self.fees,
            'hours': self.hours,
            'phone': self.phone,
            'website': self.website,
            'has_restrooms': bool(self.has_restrooms),
            'has_water': bool(self.has_water),
            'nets_provided': bool(self.nets_provided),
            'closed': bool(self.closed),
        })
        return data


class CheckIn(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    looking_for_game = db.Column(db.Boolean, nullable=False, default=False)
    checked_in_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    checked_out_at = db.Column(db.DateTime)
    last_presence_ping_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship('User', back_populates='checkins', foreign_keys=[user_id])
    court = db.relationship('Court', back_populates='checkins')


FRIENDSHIP_STATUSES = ['pending', 'accepted']


class Friendship(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('requester_id', 'addressee_id', name='uq_friendship_pair'),
    )

    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    addressee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='pending')

    requester = db.relationship('User', foreign_keys=[requester_id])
    addressee = db.relationship('User', foreign_keys=[addressee_id])

    def other_user(self, user_id):
        return self.addressee if self.requester_id == user_id else self.requester


class BlockedUser(TimestampMixin, db.Model):
    """blocker no longer sees blocked (and vice versa) in social surfaces,
    and DMs between the pair are refused in both directions."""
    __table_args__ = (
        db.UniqueConstraint('blocker_id', 'blocked_id', name='uq_blocked_pair'),
    )

    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    blocked_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    blocker = db.relationship('User', foreign_keys=[blocker_id])
    blocked = db.relationship('User', foreign_keys=[blocked_id])


def rating_history_for(user, limit=20):
    """Ranked rating trajectory (chronological), rebuilt by walking completed
    ranked games newest→oldest and subtracting each game's rating delta from
    the player's current rating. Shared by /me/stats and public profiles."""
    rows = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.game_type == 'ranked',
            Game.completed_at.isnot(None),
        )
        .order_by(Game.completed_at.desc())
        .limit(limit)
        .all()
    )
    history = []
    rating = user.rating
    for game in rows:
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if not mine or mine.rating_delta is None:
            continue
        history.append({'at': iso(game.completed_at), 'rating': rating})
        rating -= mine.rating_delta
    if history:
        # Baseline before the earliest shown game, so the line has a start.
        history.append({'at': None, 'rating': rating})
        history.reverse()
    return history


def _badge_defs(user):
    """(id, emoji, label, current, target) for every badge — the single source
    of truth for both earned lists and progress views."""
    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
        .limit(500)
        .all()
    )
    wins = 0
    courts = set()
    for game in games:
        courts.add(game.court_id)
        mine = next((p for p in game.players if p.user_id == user.id), None)
        if mine and mine.team and game.score_team1 is not None \
                and (game.score_team1 > game.score_team2) == (mine.team == 1):
            wins += 1
    friend_count = Friendship.query.filter(
        Friendship.status == 'accepted',
        db.or_(Friendship.requester_id == user.id, Friendship.addressee_id == user.id),
    ).count()
    mvp_count = GameMvpVote.query.filter_by(votee_id=user.id).count()
    titles = (
        Tournament.query
        .join(TournamentEntry, Tournament.champion_entry_id == TournamentEntry.id)
        .filter(
            Tournament.status == 'completed',
            db.or_(
                TournamentEntry.player1_id == user.id,
                TournamentEntry.player2_id == user.id,
            ),
        )
        .count()
    )
    return [
        ('first_win', '🏅', 'First win', wins, 1),
        ('ten_games', '🔟', '10 games played', len(games), 10),
        ('explorer', '🧭', 'Played 5 courts', len(courts), 5),
        ('hot_streak', '🔥', '3-win streak', user.best_streak or 0, 3),
        ('mvp', '🌟', 'Voted MVP', mvp_count, 1),
        ('social', '🤝', '5 friends', friend_count, 5),
        ('champion', '🏆', 'Tournament champion', titles, 1),
    ]


def player_badges(user):
    """Earned achievement badges — shared by /me/stats and public profiles."""
    return [
        {'id': bid, 'emoji': emoji, 'label': label}
        for bid, emoji, label, current, target in _badge_defs(user)
        if current >= target
    ]


def badge_progress(user):
    """The closest few unearned badges, with how far along the player is.
    Own-profile only — a nudge toward the next milestone."""
    locked = [
        {'id': bid, 'emoji': emoji, 'label': label, 'current': min(current, target), 'target': target}
        for bid, emoji, label, current, target in _badge_defs(user)
        if current < target
    ]
    # Nearest to completion first.
    locked.sort(key=lambda b: (b['target'] - b['current'], b['target']))
    return locked[:3]


def tournament_titles(user, limit=3):
    """Tournaments this player has won (solo or as a doubles pair) — count
    plus the most recent few. Shared by /me/stats and public profiles."""
    won = (
        Tournament.query
        .join(TournamentEntry, Tournament.champion_entry_id == TournamentEntry.id)
        .filter(
            Tournament.status == 'completed',
            db.or_(
                TournamentEntry.player1_id == user.id,
                TournamentEntry.player2_id == user.id,
            ),
        )
        .order_by(Tournament.completed_at.desc())
    )
    recent = won.limit(limit).all()
    return {
        'count': won.count(),
        'recent': [
            {'id': t.id, 'name': t.name, 'completed_at': iso(t.completed_at)}
            for t in recent
        ],
    }


def is_blocked_between(user_a_id, user_b_id):
    """True when either user has blocked the other."""
    return db.session.query(BlockedUser.id).filter(
        db.or_(
            db.and_(BlockedUser.blocker_id == user_a_id, BlockedUser.blocked_id == user_b_id),
            db.and_(BlockedUser.blocker_id == user_b_id, BlockedUser.blocked_id == user_a_id),
        )
    ).first() is not None


def blocked_pair_ids(user_id):
    """All user ids hidden from user_id: people they blocked or who blocked them."""
    rows = BlockedUser.query.filter(
        db.or_(BlockedUser.blocker_id == user_id, BlockedUser.blocked_id == user_id)
    ).all()
    return {r.blocked_id if r.blocker_id == user_id else r.blocker_id for r in rows}


class Message(TimestampMixin, db.Model):
    """A direct message (recipient_id), court-room message (court_id),
    game-thread message (game_id), or tournament-thread message
    (tournament_id)."""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), index=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), index=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), index=True)
    body = db.Column(db.Text, nullable=False, default='')
    read_at = db.Column(db.DateTime)

    sender = db.relationship('User', foreign_keys=[sender_id])
    recipient = db.relationship('User', foreign_keys=[recipient_id])

    def to_dict(self):
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'sender_name': self.sender.display_name if self.sender else None,
            'sender_color': self.sender.avatar_color if self.sender else '#2f9e44',
            'recipient_id': self.recipient_id,
            'court_id': self.court_id,
            'game_id': self.game_id,
            'tournament_id': self.tournament_id,
            'body': self.body,
            'created_at': iso(self.created_at),
            'read_at': iso(self.read_at),
        }


class UserReport(TimestampMixin, db.Model):
    """A player flagging another for review. Write-only audit trail for now —
    surfaced to operators out-of-band until an admin view exists."""
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    reported_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    reason = db.Column(db.String(500), nullable=False, default='')

    reporter = db.relationship('User', foreign_keys=[reporter_id])
    reported = db.relationship('User', foreign_keys=[reported_id])


class CourtChatRead(TimestampMixin, db.Model):
    """How far a player has read a court's chat room — powers the unread
    badge on court detail. No row until they open that chat once."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'court_id', name='uq_court_chat_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)


class GameChatRead(TimestampMixin, db.Model):
    """How far a player has read a game's chat thread. Members with no row
    haven't read anything — every message counts as unread."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'game_id', name='uq_game_chat_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)


GAME_TYPES = ['casual', 'ranked']
GAME_STATUSES = ['upcoming', 'awaiting_confirmation', 'completed', 'cancelled']
# Who can see / join a game:
#   open    -> anyone nearby
#   friends -> the creator's friends
#   private -> only specifically invited players
GAME_VISIBILITIES = ['open', 'friends', 'private']
# How a session repeats. 'weekly' open-play sessions roll forward to the next
# occurrence (re-RSVP each week); 'none' is a one-off game.
GAME_RECURRENCES = ['none', 'weekly']


class Game(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    game_type = db.Column(db.String(20), nullable=False, default='casual')
    visibility = db.Column(db.String(16), nullable=False, default='open', index=True)
    recurrence = db.Column(db.String(16), nullable=False, default='none')
    max_players = db.Column(db.Integer, nullable=False, default=4)
    notes = db.Column(db.String(500), nullable=False, default='')
    # 32 chars: must fit 'awaiting_confirmation' (Postgres enforces this, SQLite doesn't)
    status = db.Column(db.String(32), nullable=False, default='upcoming', index=True)
    score_team1 = db.Column(db.Integer)
    score_team2 = db.Column(db.Integer)
    score_submitted_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    score_submitted_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    court = db.relationship('Court', back_populates='games')
    creator = db.relationship('User', foreign_keys=[creator_id])
    score_submitted_by = db.relationship('User', foreign_keys=[score_submitted_by_id])
    players = db.relationship(
        'GamePlayer', back_populates='game', lazy='selectin',
        cascade='all, delete-orphan',
    )
    invites = db.relationship(
        'GameInvite', back_populates='game', lazy='selectin',
        cascade='all, delete-orphan',
    )
    waitlist = db.relationship(
        'GameWaitlist', back_populates='game', lazy='selectin',
        order_by='GameWaitlist.id', cascade='all, delete-orphan',
    )
    mvp_votes = db.relationship(
        'GameMvpVote', back_populates='game', lazy='selectin',
        cascade='all, delete-orphan',
    )

    def invited_user_ids(self):
        return {inv.user_id for inv in self.invites}

    def visible_to(self, user_id, friend_ids=None):
        """Whether this game should appear to a given viewer in public/nearby feeds."""
        if user_id and (self.creator_id == user_id
                        or any(p.user_id == user_id for p in self.players)):
            return True
        if self.visibility == 'open':
            return True
        if self.visibility == 'friends':
            return bool(user_id and friend_ids and self.creator_id in friend_ids)
        if self.visibility == 'private':
            return bool(user_id and user_id in self.invited_user_ids())
        return False

    def to_dict(self, current_user_id=None):
        players = sorted(self.players, key=lambda p: p.id)
        me = next((p for p in players if p.user_id == current_user_id), None)
        submitter = next(
            (p for p in players if p.user_id == self.score_submitted_by_id), None,
        )
        # A player on the opposing team of whoever reported the score confirms it.
        # If the reporter wasn't on a team (scorekeeper), any other assigned player can.
        awaiting_mine = bool(
            self.status == 'awaiting_confirmation'
            and me and submitter and me.team
            and me.user_id != submitter.user_id
            and (not submitter.team or me.team != submitter.team)
        )
        you_won = None
        if (
            self.status == 'completed' and me and me.team
            and self.score_team1 is not None and self.score_team2 is not None
        ):
            you_won = (self.score_team1 > self.score_team2) == (me.team == 1)
        return {
            'id': self.id,
            'court': self.court.to_summary_dict() if self.court else None,
            'creator_id': self.creator_id,
            'scheduled_at': iso(self.scheduled_at),
            'game_type': self.game_type,
            'visibility': self.visibility,
            'recurrence': self.recurrence,
            'max_players': self.max_players,
            'notes': self.notes,
            'status': self.status,
            'score_team1': self.score_team1,
            'score_team2': self.score_team2,
            'score_submitted_by': self.score_submitted_by_id,
            'score_submitted_by_name': (
                submitter.user.display_name if submitter and submitter.user else None
            ),
            'awaiting_your_confirmation': awaiting_mine,
            'your_rating_delta': me.rating_delta if me else None,
            'you_won': you_won,
            'completed_at': iso(self.completed_at),
            'players': [p.to_dict() for p in players],
            'spots_left': max(0, self.max_players - len(players)),
            'is_joined': me is not None,
            'is_creator': self.creator_id == current_user_id,
            'waitlist_count': len(self.waitlist),
            'waitlist_position': next(
                (i + 1 for i, w in enumerate(self.waitlist) if w.user_id == current_user_id),
                None,
            ),
            'my_mvp_vote': next(
                (v.votee_id for v in self.mvp_votes if v.voter_id == current_user_id),
                None,
            ),
            'mvp': self._mvp_summary(),
        }

    def _mvp_summary(self):
        if not self.mvp_votes:
            return None
        counts = {}
        for vote in self.mvp_votes:
            counts[vote.votee_id] = counts.get(vote.votee_id, 0) + 1
        top_id = max(sorted(counts), key=lambda uid: counts[uid])
        top = next((p.user for p in self.players if p.user_id == top_id), None)
        return {
            'user_id': top_id,
            'display_name': top.display_name if top else 'Player',
            'votes': counts[top_id],
        }


class GamePlayer(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('game_id', 'user_id', name='uq_game_player'),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    team = db.Column(db.Integer)
    rating_delta = db.Column(db.Integer)
    reminded_at = db.Column(db.DateTime)          # hour-before reminder sent
    day_reminded_at = db.Column(db.DateTime)      # day-before reminder sent
    # "I'm coming 👋" — set when the player confirms attendance for this
    # occurrence; cleared when a weekly session rolls forward.
    attending_at = db.Column(db.DateTime)

    game = db.relationship('Game', back_populates='players')
    user = db.relationship('User')

    def to_dict(self):
        data = self.user.to_public_dict() if self.user else {'id': self.user_id}
        data['user_id'] = self.user_id
        data['team'] = self.team
        data['rating_delta'] = self.rating_delta
        data['attending'] = self.attending_at is not None
        return data


class GameMvpVote(TimestampMixin, db.Model):
    """One MVP vote per player per completed game."""
    __table_args__ = (
        db.UniqueConstraint('game_id', 'voter_id', name='uq_game_mvp_voter'),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    voter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    votee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    game = db.relationship('Game', back_populates='mvp_votes')
    voter = db.relationship('User', foreign_keys=[voter_id])
    votee = db.relationship('User', foreign_keys=[votee_id])


class GameWaitlist(TimestampMixin, db.Model):
    """FIFO queue for a full game — earliest entry is promoted when a spot opens."""
    __table_args__ = (
        db.UniqueConstraint('game_id', 'user_id', name='uq_game_waitlist'),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    game = db.relationship('Game', back_populates='waitlist')
    user = db.relationship('User')


class GameInvite(TimestampMixin, db.Model):
    """A personal invite to a private game — only invited users can see it."""
    __table_args__ = (
        db.UniqueConstraint('game_id', 'user_id', name='uq_game_invite'),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    game = db.relationship('Game', back_populates='invites')
    user = db.relationship('User')


class FavoriteCourt(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('user_id', 'court_id', name='uq_favorite_court'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)

    user = db.relationship('User')
    court = db.relationship('Court')


COURT_CONDITIONS = ['good', 'busy', 'wet', 'nets_down', 'closed']


class CourtCondition(TimestampMixin, db.Model):
    """A player's on-the-ground report of court state; fresh ones surface on
    the court detail for a few hours."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    condition = db.Column(db.String(20), nullable=False)

    court = db.relationship('Court')
    user = db.relationship('User')


class CourtPhoto(TimestampMixin, db.Model):
    """A community photo of a court, stored as a data URL (Render free-tier
    disk is ephemeral). Newest one doubles as the hero when no curated photo."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    photo_data = db.Column(db.Text, nullable=False)
    caption = db.Column(db.String(140), nullable=False, default='')

    court = db.relationship('Court')
    user = db.relationship('User')


class CourtReview(TimestampMixin, db.Model):
    """One 1–5 star rating (+ optional comment) per user per court; editable."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'court_id', name='uq_court_review'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False, default=5)
    comment = db.Column(db.String(500), nullable=False, default='')

    user = db.relationship('User')
    court = db.relationship('Court')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': self.user.display_name if self.user else 'Player',
            'avatar_color': self.user.avatar_color if self.user else '#2f9e44',
            'avatar_url': (self.user.avatar_url or '') if self.user else '',
            'rating': self.rating,
            'comment': self.comment,
            'created_at': iso(self.created_at),
        }


class CourtEditSuggestion(TimestampMixin, db.Model):
    """A player's proposed corrections to scraped court data, as a JSON object
    of {field: value}. Applied automatically once two distinct users agree."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False, default='{}')
    status = db.Column(db.String(16), nullable=False, default='pending', index=True)

    court = db.relationship('Court')
    user = db.relationship('User')


class Notification(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False, default='general')
    title = db.Column(db.String(255), nullable=False, default='')
    body = db.Column(db.Text, nullable=False, default='')
    read = db.Column(db.Boolean, nullable=False, default=False)
    related_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    related_game_id = db.Column(db.Integer, db.ForeignKey('game.id'))
    related_tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'))

    def to_dict(self):
        return {
            'id': self.id,
            'kind': self.kind,
            'title': self.title,
            'body': self.body,
            'read': bool(self.read),
            'related_user_id': self.related_user_id,
            'related_game_id': self.related_game_id,
            'related_tournament_id': self.related_tournament_id,
            'created_at': iso(self.created_at),
        }


# Notification kinds a user may silence. Everything else (score confirmations,
# disputes, direct invites, challenges) is essential and always delivered.
MUTEABLE_NOTIFICATIONS = {
    'court_game': 'New games at courts you saved',
    'friend_checkin': 'Friends checking in to play',
    'game_message': 'Game chat messages',
    'tournament_message': 'Tournament chat messages',
    'session_rsvp': 'Weekly session re-RSVP reminders',
    'weekly_recap': 'Your weekly recap',
}


def notify(user_id, kind, title, body='', related_user_id=None, related_game_id=None,
           related_tournament_id=None):
    # Respect the recipient's mute preferences for optional kinds.
    if kind in MUTEABLE_NOTIFICATIONS:
        recipient = db.session.get(User, user_id)
        if recipient and kind in recipient.muted_kinds():
            return
    db.session.add(Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        related_user_id=related_user_id,
        related_game_id=related_game_id,
        related_tournament_id=related_tournament_id,
    ))


TOURNAMENT_FORMATS = ['single_elim', 'round_robin']
TOURNAMENT_EVENT_TYPES = ['singles', 'doubles']
TOURNAMENT_STATUSES = ['registration', 'active', 'completed', 'cancelled']


class Tournament(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    organizer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    format = db.Column(db.String(20), nullable=False, default='single_elim')
    event_type = db.Column(db.String(20), nullable=False, default='singles')
    max_entries = db.Column(db.Integer, nullable=False, default=8)
    # Ranked tournaments feed every decided match into ELO at completion.
    ranked = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default='registration', index=True)
    reminded_at = db.Column(db.DateTime)          # hour-before reminder sent
    day_reminded_at = db.Column(db.DateTime)      # day-before reminder sent
    # use_alter breaks the tournament <-> tournament_entry FK cycle at create_all.
    champion_entry_id = db.Column(
        db.Integer,
        db.ForeignKey('tournament_entry.id', use_alter=True, name='fk_tournament_champion'),
    )
    completed_at = db.Column(db.DateTime)

    court = db.relationship('Court')
    organizer = db.relationship('User', foreign_keys=[organizer_id])
    champion_entry = db.relationship(
        'TournamentEntry', foreign_keys=[champion_entry_id], post_update=True,
    )
    entries = db.relationship(
        'TournamentEntry', back_populates='tournament', lazy='selectin',
        order_by='TournamentEntry.id', cascade='all, delete-orphan',
        foreign_keys='TournamentEntry.tournament_id',
    )
    matches = db.relationship(
        'TournamentMatch', back_populates='tournament', lazy='selectin',
        order_by='(TournamentMatch.round, TournamentMatch.position)',
        cascade='all, delete-orphan',
    )

    def total_rounds(self):
        return max((m.round for m in self.matches), default=0)

    def entry_for(self, user_id):
        return next(
            (e for e in self.entries
             if user_id in (e.player1_id, e.player2_id)),
            None,
        )

    def participant_ids(self):
        ids = set()
        for entry in self.entries:
            ids.add(entry.player1_id)
            if entry.player2_id:
                ids.add(entry.player2_id)
        return ids

    def to_dict(self, current_user_id=None, detail=False):
        my_entry = self.entry_for(current_user_id) if current_user_id else None
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'format': self.format,
            'event_type': self.event_type,
            'status': self.status,
            'ranked': bool(self.ranked),
            'starts_at': iso(self.starts_at),
            'max_entries': self.max_entries,
            'court': self.court.to_summary_dict() if self.court else None,
            'organizer_id': self.organizer_id,
            'organizer_name': self.organizer.display_name if self.organizer else None,
            'entry_count': len(self.entries),
            'is_organizer': self.organizer_id == current_user_id,
            'my_entry_id': my_entry.id if my_entry else None,
            'champion': self.champion_entry.to_dict() if self.champion_entry else None,
            'completed_at': iso(self.completed_at),
        }
        if detail:
            data['entries'] = [e.to_dict() for e in self.entries]
            data['matches'] = [m.to_dict() for m in self.matches]
            data['total_rounds'] = self.total_rounds()
        return data


class TournamentEntry(TimestampMixin, db.Model):
    """One competing unit — a single player, or a doubles pair."""
    __table_args__ = (
        db.UniqueConstraint('tournament_id', 'player1_id', name='uq_tournament_player1'),
    )

    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(
        db.Integer, db.ForeignKey('tournament.id'), nullable=False, index=True,
    )
    player1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    player2_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    seed = db.Column(db.Integer)
    # Day-of arrival confirmation ("we're here"), settable from 24h out.
    checked_in_at = db.Column(db.DateTime)

    tournament = db.relationship(
        'Tournament', back_populates='entries', foreign_keys=[tournament_id],
    )
    player1 = db.relationship('User', foreign_keys=[player1_id])
    player2 = db.relationship('User', foreign_keys=[player2_id])

    def players(self):
        return [p for p in (self.player1, self.player2) if p]

    def display_name(self):
        names = [p.display_name for p in self.players()]
        return ' & '.join(names) if names else 'Entry'

    def avg_rating(self):
        ratings = [p.rating for p in self.players()]
        return sum(ratings) // len(ratings) if ratings else DEFAULT_RATING

    def to_dict(self):
        return {
            'id': self.id,
            'seed': self.seed,
            'name': self.display_name(),
            'rating': self.avg_rating(),
            'checked_in': self.checked_in_at is not None,
            'players': [p.to_public_dict() for p in self.players()],
        }


class TournamentMatch(TimestampMixin, db.Model):
    """A bracket slot. Single-elim: winner of (round r, position p) feeds
    (round r+1, position p//2), slot 1 when p is even. Round robin: every
    pairing exists up front, grouped into rounds by the circle method."""
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(
        db.Integer, db.ForeignKey('tournament.id'), nullable=False, index=True,
    )
    round = db.Column(db.Integer, nullable=False, default=1)
    position = db.Column(db.Integer, nullable=False, default=0)
    entry1_id = db.Column(db.Integer, db.ForeignKey('tournament_entry.id'))
    entry2_id = db.Column(db.Integer, db.ForeignKey('tournament_entry.id'))
    score1 = db.Column(db.Integer)
    score2 = db.Column(db.Integer)
    winner_entry_id = db.Column(db.Integer, db.ForeignKey('tournament_entry.id'))

    tournament = db.relationship('Tournament', back_populates='matches')
    entry1 = db.relationship('TournamentEntry', foreign_keys=[entry1_id])
    entry2 = db.relationship('TournamentEntry', foreign_keys=[entry2_id])
    winner_entry = db.relationship('TournamentEntry', foreign_keys=[winner_entry_id])

    def status(self):
        if self.winner_entry_id is not None:
            # A bye never had two entries or a score.
            return 'bye' if self.score1 is None and (
                self.entry1_id is None or self.entry2_id is None) else 'done'
        if self.entry1_id is not None and self.entry2_id is not None:
            return 'ready'
        return 'pending'

    def to_dict(self):
        return {
            'id': self.id,
            'round': self.round,
            'position': self.position,
            'entry1_id': self.entry1_id,
            'entry2_id': self.entry2_id,
            'score1': self.score1,
            'score2': self.score2,
            'winner_entry_id': self.winner_entry_id,
            'status': self.status(),
        }
