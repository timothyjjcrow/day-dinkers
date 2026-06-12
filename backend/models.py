"""Database models for the pickleball player network."""
from __future__ import annotations

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


class User(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    bio = db.Column(db.String(500), nullable=False, default='')
    skill_level = db.Column(db.String(20), nullable=False, default='beginner')
    avatar_color = db.Column(db.String(7), nullable=False, default='#2f9e44')
    rating = db.Column(db.Integer, nullable=False, default=DEFAULT_RATING)
    ranked_wins = db.Column(db.Integer, nullable=False, default=0)
    ranked_losses = db.Column(db.Integer, nullable=False, default=0)
    current_streak = db.Column(db.Integer, nullable=False, default=0)
    best_streak = db.Column(db.Integer, nullable=False, default=0)
    home_court_id = db.Column(db.Integer, db.ForeignKey('court.id'))

    home_court = db.relationship('Court', foreign_keys=[home_court_id])
    checkins = db.relationship(
        'CheckIn', back_populates='user', lazy='dynamic',
        foreign_keys='CheckIn.user_id',
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def to_public_dict(self):
        return {
            'id': self.id,
            'display_name': self.display_name,
            'bio': self.bio,
            'skill_level': self.skill_level,
            'avatar_color': self.avatar_color,
            'rating': self.rating,
            'ranked_wins': self.ranked_wins,
            'ranked_losses': self.ranked_losses,
            'current_streak': self.current_streak,
            'best_streak': self.best_streak,
            'home_court_id': self.home_court_id,
            'home_court_name': self.home_court.name if self.home_court else None,
        }

    def to_dict(self):
        data = self.to_public_dict()
        data['email'] = self.email
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
    phone = db.Column(db.String(40), nullable=False, default='')
    website = db.Column(db.String(500), nullable=False, default='')
    photo_url = db.Column(db.String(500), nullable=False, default='')
    has_restrooms = db.Column(db.Boolean, nullable=False, default=False)
    has_water = db.Column(db.Boolean, nullable=False, default=False)
    nets_provided = db.Column(db.Boolean, nullable=False, default=False)
    verified = db.Column(db.Boolean, nullable=False, default=False)

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
            'phone': self.phone,
            'website': self.website,
            'has_restrooms': bool(self.has_restrooms),
            'has_water': bool(self.has_water),
            'nets_provided': bool(self.nets_provided),
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


class Message(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False, default='')
    read_at = db.Column(db.DateTime)

    sender = db.relationship('User', foreign_keys=[sender_id])
    recipient = db.relationship('User', foreign_keys=[recipient_id])

    def to_dict(self):
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'recipient_id': self.recipient_id,
            'body': self.body,
            'created_at': iso(self.created_at),
            'read_at': iso(self.read_at),
        }


GAME_TYPES = ['casual', 'ranked']
GAME_STATUSES = ['upcoming', 'awaiting_confirmation', 'completed', 'cancelled']


class Game(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    game_type = db.Column(db.String(20), nullable=False, default='casual')
    max_players = db.Column(db.Integer, nullable=False, default=4)
    notes = db.Column(db.String(500), nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='upcoming', index=True)
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

    def to_dict(self, current_user_id=None):
        players = sorted(self.players, key=lambda p: p.id)
        me = next((p for p in players if p.user_id == current_user_id), None)
        submitter = next(
            (p for p in players if p.user_id == self.score_submitted_by_id), None,
        )
        # Only a player on the opposing team of whoever reported the score may confirm it.
        awaiting_mine = bool(
            self.status == 'awaiting_confirmation'
            and me and submitter and me.team and submitter.team
            and me.team != submitter.team
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

    game = db.relationship('Game', back_populates='players')
    user = db.relationship('User')

    def to_dict(self):
        data = self.user.to_public_dict() if self.user else {'id': self.user_id}
        data['user_id'] = self.user_id
        data['team'] = self.team
        data['rating_delta'] = self.rating_delta
        return data


class FavoriteCourt(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('user_id', 'court_id', name='uq_favorite_court'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)

    user = db.relationship('User')
    court = db.relationship('Court')


class Notification(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False, default='general')
    title = db.Column(db.String(255), nullable=False, default='')
    body = db.Column(db.Text, nullable=False, default='')
    read = db.Column(db.Boolean, nullable=False, default=False)
    related_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    related_game_id = db.Column(db.Integer, db.ForeignKey('game.id'))

    def to_dict(self):
        return {
            'id': self.id,
            'kind': self.kind,
            'title': self.title,
            'body': self.body,
            'read': bool(self.read),
            'related_user_id': self.related_user_id,
            'related_game_id': self.related_game_id,
            'created_at': iso(self.created_at),
        }


def notify(user_id, kind, title, body='', related_user_id=None, related_game_id=None):
    db.session.add(Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        related_user_id=related_user_id,
        related_game_id=related_game_id,
    ))
