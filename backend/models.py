import json
from backend.app import db
from backend.time_utils import utcnow_naive


def _safe_json(raw_value, fallback=None):
    if fallback is None:
        fallback = {}
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError):
        return fallback


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    google_sub = db.Column(db.String(255), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    name = db.Column(db.String(120), default='')
    photo_url = db.Column(db.String(500), default='')
    skill_level = db.Column(db.Float, nullable=True)
    bio = db.Column(db.Text, default='')
    play_style = db.Column(db.String(50), default='')  # singles, doubles, mixed
    preferred_times = db.Column(db.String(200), default='')  # morning, afternoon, evening
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    games_played = db.Column(db.Integer, default=0)
    elo_rating = db.Column(db.Float, default=1200.0)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    def to_dict(self):
        return {
            'id': self.id, 'username': self.username, 'email': self.email,
            'name': self.name, 'photo_url': self.photo_url,
            'skill_level': self.skill_level, 'bio': self.bio,
            'play_style': self.play_style, 'preferred_times': self.preferred_times,
            'wins': self.wins, 'losses': self.losses,
            'games_played': self.games_played, 'elo_rating': self.elo_rating,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Court(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    address = db.Column(db.String(500), default='')
    city = db.Column(db.String(100), default='')
    state = db.Column(db.String(2), default='CA')
    zip_code = db.Column(db.String(10), default='')
    county_slug = db.Column(db.String(80), default='humboldt', nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    indoor = db.Column(db.Boolean, default=False)
    lighted = db.Column(db.Boolean, default=False)
    num_courts = db.Column(db.Integer, default=1)
    surface_type = db.Column(db.String(50), default='')
    hours = db.Column(db.Text, default='')
    open_play_schedule = db.Column(db.Text, default='')
    fees = db.Column(db.String(200), default='')
    phone = db.Column(db.String(30), default='')
    website = db.Column(db.String(500), default='')
    email = db.Column(db.String(200), default='')
    photo_url = db.Column(db.String(500), default='')
    # Amenities
    has_restrooms = db.Column(db.Boolean, default=False)
    has_parking = db.Column(db.Boolean, default=False)
    has_water = db.Column(db.Boolean, default=False)
    has_pro_shop = db.Column(db.Boolean, default=False)
    has_ball_machine = db.Column(db.Boolean, default=False)
    wheelchair_accessible = db.Column(db.Boolean, default=False)
    nets_provided = db.Column(db.Boolean, default=True)
    paddle_rental = db.Column(db.Boolean, default=False)
    # Meta
    skill_levels = db.Column(db.String(100), default='all')
    court_type = db.Column(db.String(50), default='dedicated')
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'description': self.description,
            'address': self.address, 'city': self.city, 'state': self.state,
            'zip_code': self.zip_code, 'county_slug': self.county_slug,
            'latitude': self.latitude, 'longitude': self.longitude,
            'indoor': self.indoor, 'lighted': self.lighted,
            'num_courts': self.num_courts, 'surface_type': self.surface_type,
            'hours': self.hours, 'open_play_schedule': self.open_play_schedule,
            'fees': self.fees, 'phone': self.phone, 'website': self.website,
            'email': self.email, 'photo_url': self.photo_url,
            'has_restrooms': self.has_restrooms, 'has_parking': self.has_parking,
            'has_water': self.has_water, 'has_pro_shop': self.has_pro_shop,
            'has_ball_machine': self.has_ball_machine,
            'wheelchair_accessible': self.wheelchair_accessible,
            'nets_provided': self.nets_provided, 'paddle_rental': self.paddle_rental,
            'skill_levels': self.skill_levels, 'court_type': self.court_type,
            'verified': self.verified,
        }


class Friendship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    friend_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    user = db.relationship('User', foreign_keys=[user_id], backref='sent_requests')
    friend = db.relationship('User', foreign_keys=[friend_id], backref='received_requests')


# ── Legacy Games (backward compatibility) ──────────────────────────────

class Game(db.Model):
    """Legacy scheduled game model kept for API/test backward compatibility."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    date_time = db.Column(db.DateTime, nullable=False)
    max_players = db.Column(db.Integer, default=4)
    skill_level = db.Column(db.String(20), default='all')
    game_type = db.Column(db.String(20), default='open')
    is_open = db.Column(db.Boolean, default=True)
    recurring = db.Column(db.String(50), default='')
    status = db.Column(db.String(20), default='upcoming')  # upcoming, in_progress, completed
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    court = db.relationship('Court', backref='games')
    creator = db.relationship('User', backref='games_created')
    players = db.relationship('GamePlayer', backref='game', lazy='joined',
                              cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'court_id': self.court_id,
            'creator_id': self.creator_id,
            'title': self.title,
            'description': self.description,
            'date_time': self.date_time.isoformat() if self.date_time else None,
            'max_players': self.max_players,
            'skill_level': self.skill_level,
            'game_type': self.game_type,
            'is_open': self.is_open,
            'recurring': self.recurring,
            'status': self.status,
            'court': self.court.to_dict() if self.court else None,
            'creator': self.creator.to_dict() if self.creator else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class GamePlayer(db.Model):
    """Legacy game RSVP participant."""
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rsvp_status = db.Column(db.String(20), default='yes')  # yes, no, maybe, invited
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    user = db.relationship('User', backref='legacy_game_participations')

    def to_dict(self):
        return {
            'id': self.id,
            'game_id': self.game_id,
            'user_id': self.user_id,
            'rsvp_status': self.rsvp_status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'user': self.user.to_dict() if self.user else None,
        }


# ── Open to Play Sessions (replaces Game / GamePlayer) ────────────────

class PlaySession(db.Model):
    """Open-to-play session — immediate ('now') or scheduled."""
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    session_type = db.Column(db.String(20), default='now')  # 'now' or 'scheduled'
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    game_type = db.Column(db.String(20), default='open')  # open, doubles, singles
    skill_level = db.Column(db.String(20), default='all')
    max_players = db.Column(db.Integer, default=4)
    visibility = db.Column(db.String(20), default='all')  # 'all' or 'friends'
    notes = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='active')  # active, completed, cancelled
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    creator = db.relationship('User', backref='play_sessions')
    court = db.relationship('Court', backref='play_sessions')
    players = db.relationship('PlaySessionPlayer', backref='session', lazy='joined',
                              cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id, 'creator_id': self.creator_id,
            'court_id': self.court_id, 'session_type': self.session_type,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'game_type': self.game_type, 'skill_level': self.skill_level,
            'max_players': self.max_players, 'visibility': self.visibility,
            'notes': self.notes, 'status': self.status,
            'creator': self.creator.to_dict() if self.creator else None,
            'court': self.court.to_dict() if self.court else None,
            'players': [p.to_dict() for p in self.players],
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PlaySessionPlayer(db.Model):
    """A player who has joined or been invited to a play session."""
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('play_session.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='joined')  # joined, invited, waitlisted
    joined_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    user = db.relationship('User', backref='session_participations')

    def to_dict(self):
        return {
            'id': self.id, 'session_id': self.session_id,
            'user_id': self.user_id, 'status': self.status,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
            'user': self.user.to_dict() if self.user else None,
        }


class RecurringSessionSeries(db.Model):
    """Metadata for a recurring scheduled session series."""
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recurrence = db.Column(db.String(20), default='weekly')  # weekly, biweekly
    interval_weeks = db.Column(db.Integer, default=1)
    occurrences = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    creator = db.relationship('User', backref='session_series')
    items = db.relationship('RecurringSessionSeriesItem', backref='series', lazy='joined',
                            cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'creator_id': self.creator_id,
            'recurrence': self.recurrence,
            'interval_weeks': self.interval_weeks,
            'occurrences': self.occurrences,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class RecurringSessionSeriesItem(db.Model):
    """Maps individual play sessions to a recurring series."""
    id = db.Column(db.Integer, primary_key=True)
    series_id = db.Column(db.Integer, db.ForeignKey('recurring_session_series.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('play_session.id'), nullable=False)
    sequence = db.Column(db.Integer, default=1)  # 1-based order in series
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    session = db.relationship('PlaySession', backref='series_items')

    def to_dict(self):
        return {
            'id': self.id,
            'series_id': self.series_id,
            'session_id': self.session_id,
            'sequence': self.sequence,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ── Messaging ─────────────────────────────────────────────────────────

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey('play_session.id'), nullable=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    msg_type = db.Column(db.String(20), default='court')  # court, session, direct, game
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')

    def to_dict(self):
        return {
            'id': self.id, 'sender_id': self.sender_id,
            'court_id': self.court_id,
            'session_id': self.session_id,
            'recipient_id': self.recipient_id, 'content': self.content,
            'msg_type': self.msg_type,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'sender': self.sender.to_dict() if self.sender else None,
        }


# ── Presence ──────────────────────────────────────────────────────────

class CheckIn(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    checked_in_at = db.Column(db.DateTime, default=lambda: utcnow_naive())
    last_presence_ping_at = db.Column(db.DateTime, default=lambda: utcnow_naive())
    checked_out_at = db.Column(db.DateTime, nullable=True)
    looking_for_game = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref='check_ins')
    court = db.relationship('Court', backref='check_ins')


class ActivityLog(db.Model):
    """Tracks court activity over time for busyness patterns."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    hour = db.Column(db.Integer, nullable=False)
    player_count = db.Column(db.Integer, default=0)
    day_of_week = db.Column(db.Integer, nullable=False)

    court = db.relationship('Court', backref='activity_logs')


# ── Notifications ─────────────────────────────────────────────────────

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notif_type = db.Column(db.String(50), nullable=False)
    content = db.Column(db.Text, nullable=False)
    reference_id = db.Column(db.Integer, nullable=True)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    user = db.relationship('User', backref='notifications')

    def to_dict(self):
        return {
            'id': self.id, 'notif_type': self.notif_type,
            'content': self.content, 'reference_id': self.reference_id,
            'read': self.read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CourtReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())


# ── Community Court Data (user contributions + review queue) ─────────

class CourtCommunityInfo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, unique=True)
    location_notes = db.Column(db.Text, default='')
    parking_notes = db.Column(db.Text, default='')
    access_notes = db.Column(db.Text, default='')
    court_rules = db.Column(db.Text, default='')
    best_times = db.Column(db.Text, default='')
    closure_notes = db.Column(db.Text, default='')
    hours_notes = db.Column(db.Text, default='')
    additional_info = db.Column(db.Text, default='')
    last_updated_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    court = db.relationship('Court', backref=db.backref('community_info', uselist=False))

    def to_dict(self):
        return {
            'location_notes': self.location_notes,
            'parking_notes': self.parking_notes,
            'access_notes': self.access_notes,
            'court_rules': self.court_rules,
            'best_times': self.best_times,
            'closure_notes': self.closure_notes,
            'hours_notes': self.hours_notes,
            'additional_info': self.additional_info,
            'last_updated_at': self.last_updated_at.isoformat() if self.last_updated_at else None,
        }


class CourtImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    source_submission_id = db.Column(db.Integer, db.ForeignKey('court_update_submission.id'), nullable=True)
    image_url = db.Column(db.Text, nullable=False)
    caption = db.Column(db.String(200), default='')
    approved = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    court = db.relationship('Court', backref='images')
    submitted_by = db.relationship('User', foreign_keys=[submitted_by_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'court_id': self.court_id,
            'image_url': self.image_url,
            'caption': self.caption,
            'approved': self.approved,
            'submitted_by_user_id': self.submitted_by_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CourtEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    source_submission_id = db.Column(db.Integer, db.ForeignKey('court_update_submission.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    organizer = db.Column(db.String(200), default='')
    contact = db.Column(db.String(200), default='')
    link = db.Column(db.String(500), default='')
    recurring = db.Column(db.String(80), default='')
    approved = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    court = db.relationship('Court', backref='events')
    submitted_by = db.relationship('User', foreign_keys=[submitted_by_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'court_id': self.court_id,
            'title': self.title,
            'description': self.description,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'organizer': self.organizer,
            'contact': self.contact,
            'link': self.link,
            'recurring': self.recurring,
            'approved': self.approved,
            'submitted_by_user_id': self.submitted_by_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CourtUpdateSubmission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    summary = db.Column(db.String(500), default='')
    payload_json = db.Column(db.Text, nullable=False)
    analysis_json = db.Column(db.Text, default='{}')
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewer_notes = db.Column(db.Text, default='')
    reviewed_at = db.Column(db.DateTime, nullable=True)
    auto_applied = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    court = db.relationship('Court', backref='update_submissions')
    user = db.relationship('User', foreign_keys=[user_id], backref='court_update_submissions')
    reviewer = db.relationship('User', foreign_keys=[reviewer_id], backref='court_reviews_completed')

    def to_dict(self, include_payload=False):
        data = {
            'id': self.id,
            'court_id': self.court_id,
            'court_name': self.court.name if self.court else '',
            'court_city': self.court.city if self.court else '',
            'user_id': self.user_id,
            'status': self.status,
            'summary': self.summary,
            'analysis': _safe_json(self.analysis_json, {}),
            'reviewer_id': self.reviewer_id,
            'reviewer_notes': self.reviewer_notes,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'auto_applied': self.auto_applied,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'submitted_by': self.user.to_dict() if self.user else None,
            'reviewed_by': self.reviewer.to_dict() if self.reviewer else None,
        }
        if include_payload:
            data['payload'] = _safe_json(self.payload_json, {})
        return data


# ── Ranked Competitive Play ──────────────────────────────────────────

class Tournament(db.Model):
    """Court-hosted ranked tournament (v1 single-elimination)."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    host_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    tournament_format = db.Column(db.String(40), default='single_elimination')
    access_mode = db.Column(db.String(20), default='open')  # open, invite_only
    match_type = db.Column(db.String(20), default='singles')  # singles for v1
    affects_elo = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), default='upcoming')
    # upcoming, live, completed, cancelled
    start_time = db.Column(db.DateTime, nullable=False)
    registration_close_time = db.Column(db.DateTime, nullable=True)
    max_players = db.Column(db.Integer, default=16)
    min_participants = db.Column(db.Integer, default=4)
    check_in_required = db.Column(db.Boolean, default=True)
    no_show_policy = db.Column(db.String(30), default='auto_forfeit')
    # auto_forfeit, host_mark
    no_show_grace_minutes = db.Column(db.Integer, default=10)
    bracket_size = db.Column(db.Integer, nullable=True)
    total_rounds = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    __table_args__ = (
        db.Index('ix_tournament_court_status_start', 'court_id', 'status', 'start_time'),
    )

    court = db.relationship('Court', backref='tournaments')
    host_user = db.relationship('User', foreign_keys=[host_user_id], backref='hosted_tournaments')
    participants = db.relationship(
        'TournamentParticipant',
        backref='tournament',
        lazy='joined',
        cascade='all, delete-orphan',
    )
    results = db.relationship(
        'TournamentResult',
        backref='tournament',
        lazy='joined',
        cascade='all, delete-orphan',
    )

    def to_dict(self, include_participants=False, include_results=False):
        participant_list = [p.to_dict() for p in self.participants] if include_participants else []
        checked_in_count = sum(
            1 for p in self.participants
            if p.participant_status == 'checked_in'
        )
        registered_count = sum(
            1 for p in self.participants
            if p.participant_status in {'registered', 'checked_in'}
        )
        data = {
            'id': self.id,
            'court_id': self.court_id,
            'host_user_id': self.host_user_id,
            'name': self.name,
            'description': self.description,
            'tournament_format': self.tournament_format,
            'access_mode': self.access_mode,
            'match_type': self.match_type,
            'affects_elo': self.affects_elo,
            'status': self.status,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'registration_close_time': (
                self.registration_close_time.isoformat()
                if self.registration_close_time else None
            ),
            'max_players': self.max_players,
            'min_participants': self.min_participants,
            'check_in_required': self.check_in_required,
            'no_show_policy': self.no_show_policy,
            'no_show_grace_minutes': self.no_show_grace_minutes,
            'bracket_size': self.bracket_size,
            'total_rounds': self.total_rounds,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'host_user': self.host_user.to_dict() if self.host_user else None,
            'court': self.court.to_dict() if self.court else None,
            'registered_count': registered_count,
            'checked_in_count': checked_in_count,
        }
        if include_participants:
            data['participants'] = participant_list
        if include_results:
            data['results'] = [result.to_dict() for result in self.results]
        return data


class TournamentParticipant(db.Model):
    """Tournament participant registration and invite/check-in state."""
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    invite_status = db.Column(db.String(20), default='none')
    # none, invited, accepted, declined
    participant_status = db.Column(db.String(20), default='registered')
    # invited, registered, checked_in, no_show, eliminated, withdrawn, winner, declined
    seed = db.Column(db.Integer, nullable=True)
    final_placement = db.Column(db.Integer, nullable=True)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    points = db.Column(db.Integer, default=0)
    checked_in_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    __table_args__ = (
        db.UniqueConstraint('tournament_id', 'user_id', name='uq_tournament_participant_unique'),
        db.Index('ix_tournament_participant_tournament_status', 'tournament_id', 'participant_status'),
    )

    user = db.relationship('User', foreign_keys=[user_id], backref='tournament_entries')
    invited_by = db.relationship('User', foreign_keys=[invited_by_user_id], backref='tournament_invites_sent')

    def to_dict(self):
        return {
            'id': self.id,
            'tournament_id': self.tournament_id,
            'user_id': self.user_id,
            'invited_by_user_id': self.invited_by_user_id,
            'invite_status': self.invite_status,
            'participant_status': self.participant_status,
            'seed': self.seed,
            'final_placement': self.final_placement,
            'wins': self.wins,
            'losses': self.losses,
            'points': self.points,
            'checked_in_at': self.checked_in_at.isoformat() if self.checked_in_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'user': self.user.to_dict() if self.user else None,
        }


class TournamentResult(db.Model):
    """Immutable-ish result snapshot used for leaderboard/profile history."""
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    placement = db.Column(db.Integer, nullable=False)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    __table_args__ = (
        db.UniqueConstraint('tournament_id', 'user_id', name='uq_tournament_result_unique'),
        db.Index('ix_tournament_result_court_points', 'court_id', 'points'),
        db.Index('ix_tournament_result_user_created', 'user_id', 'created_at'),
    )

    user = db.relationship('User', backref='tournament_results')
    court = db.relationship('Court', backref='tournament_results')

    def to_dict(self):
        return {
            'id': self.id,
            'tournament_id': self.tournament_id,
            'user_id': self.user_id,
            'court_id': self.court_id,
            'placement': self.placement,
            'wins': self.wins,
            'losses': self.losses,
            'points': self.points,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'user': self.user.to_dict() if self.user else None,
            'court': self.court.to_dict() if self.court else None,
            'tournament': {
                'id': self.tournament.id,
                'name': self.tournament.name,
                'status': self.tournament.status,
                'start_time': self.tournament.start_time.isoformat() if self.tournament.start_time else None,
            } if self.tournament else None,
        }


class Match(db.Model):
    """A competitive ranked match between players/teams."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=True)
    bracket_round = db.Column(db.Integer, nullable=True)
    bracket_slot = db.Column(db.Integer, nullable=True)
    match_type = db.Column(db.String(20), nullable=False)  # singles, doubles
    status = db.Column(db.String(20), default='in_progress')
    # pending_confirmation = score submitted, waiting for all players to accept
    # completed = all players accepted, ELO applied
    # cancelled = match cancelled by a player or expired
    team1_score = db.Column(db.Integer, nullable=True)
    team2_score = db.Column(db.Integer, nullable=True)
    winner_team = db.Column(db.Integer, nullable=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('ix_match_court_status', 'court_id', 'status'),
        db.Index('ix_match_tournament_round_slot', 'tournament_id', 'bracket_round', 'bracket_slot'),
    )

    court = db.relationship('Court', backref='matches')
    tournament = db.relationship('Tournament', backref='matches')
    players = db.relationship('MatchPlayer', backref='match', lazy='joined')

    def to_dict(self):
        players_list = [mp.to_dict() for mp in self.players]
        confirmed_count = sum(1 for player in players_list if player.get('confirmed'))
        submitter_player = next(
            (player for player in players_list if player.get('user_id') == self.submitted_by),
            None,
        )
        return {
            'id': self.id, 'court_id': self.court_id,
            'tournament_id': self.tournament_id,
            'bracket_round': self.bracket_round,
            'bracket_slot': self.bracket_slot,
            'match_type': self.match_type, 'status': self.status,
            'team1_score': self.team1_score, 'team2_score': self.team2_score,
            'winner_team': self.winner_team, 'submitted_by': self.submitted_by,
            'submitted_by_user': submitter_player.get('user') if submitter_player else None,
            'confirmed_count': confirmed_count,
            'total_players': len(players_list),
            'players': players_list,
            'team1': [p for p in players_list if p['team'] == 1],
            'team2': [p for p in players_list if p['team'] == 2],
            'court': self.court.to_dict() if self.court else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class MatchPlayer(db.Model):
    """Links a player to a match with team assignment and ELO tracking."""
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    team = db.Column(db.Integer, nullable=False)
    elo_before = db.Column(db.Float, nullable=True)
    elo_after = db.Column(db.Float, nullable=True)
    elo_change = db.Column(db.Float, nullable=True)
    confirmed = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.Index('ix_match_player_user_confirmed', 'user_id', 'confirmed'),
    )

    user = db.relationship('User', backref='match_participations')

    def to_dict(self):
        return {
            'id': self.id, 'match_id': self.match_id,
            'user_id': self.user_id, 'team': self.team,
            'elo_before': self.elo_before, 'elo_after': self.elo_after,
            'elo_change': self.elo_change, 'confirmed': self.confirmed,
            'user': self.user.to_dict() if self.user else None,
        }


class RankedQueue(db.Model):
    """Players waiting for a ranked match at a court."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    match_type = db.Column(db.String(20), default='doubles')
    joined_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'court_id', name='uq_ranked_queue_user_court'),
    )

    user = db.relationship('User', backref='queue_entries')
    court = db.relationship('Court', backref='queue_entries')

    def to_dict(self):
        return {
            'id': self.id, 'user_id': self.user_id, 'court_id': self.court_id,
            'match_type': self.match_type,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
            'user': self.user.to_dict() if self.user else None,
        }


class RankedLobby(db.Model):
    """Pre-match ranked lobby for queue/challenge/scheduled flows."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    match_type = db.Column(db.String(20), nullable=False)  # singles, doubles
    source = db.Column(db.String(30), default='manual')  # queue, court_challenge, scheduled_challenge
    scheduled_for = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default='pending_acceptance')
    # pending_acceptance, ready, started, cancelled, declined, expired
    started_match_id = db.Column(db.Integer, db.ForeignKey('match.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: utcnow_naive())

    __table_args__ = (
        db.Index('ix_ranked_lobby_court_status', 'court_id', 'status'),
    )

    court = db.relationship('Court', backref='ranked_lobbies')
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_ranked_lobbies')
    started_match = db.relationship('Match', foreign_keys=[started_match_id])
    players = db.relationship(
        'RankedLobbyPlayer',
        backref='lobby',
        lazy='joined',
        cascade='all, delete-orphan',
    )

    def to_dict(self):
        players_list = [p.to_dict() for p in self.players]
        return {
            'id': self.id,
            'court_id': self.court_id,
            'created_by_id': self.created_by_id,
            'match_type': self.match_type,
            'source': self.source,
            'scheduled_for': self.scheduled_for.isoformat() if self.scheduled_for else None,
            'status': self.status,
            'started_match_id': self.started_match_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'players': players_list,
            'team1': [p for p in players_list if p['team'] == 1],
            'team2': [p for p in players_list if p['team'] == 2],
            'court': self.court.to_dict() if self.court else None,
            'created_by': self.created_by.to_dict() if self.created_by else None,
        }


class RankedLobbyPlayer(db.Model):
    """Participant + acceptance state for a ranked lobby."""
    id = db.Column(db.Integer, primary_key=True)
    lobby_id = db.Column(db.Integer, db.ForeignKey('ranked_lobby.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    team = db.Column(db.Integer, nullable=False)
    acceptance_status = db.Column(db.String(20), default='pending')
    # pending, accepted, declined
    responded_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('lobby_id', 'user_id', name='uq_ranked_lobby_player_unique'),
    )

    user = db.relationship('User', backref='ranked_lobby_participations')

    def to_dict(self):
        return {
            'id': self.id,
            'lobby_id': self.lobby_id,
            'user_id': self.user_id,
            'team': self.team,
            'acceptance_status': self.acceptance_status,
            'responded_at': self.responded_at.isoformat() if self.responded_at else None,
            'user': self.user.to_dict() if self.user else None,
        }
