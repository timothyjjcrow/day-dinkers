"""Database models for the pickleball player network."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from backend.app import db


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + 'Z' if dt else None


def local_date_for_timezone(timezone_name='UTC', as_of=None):
    """Return the calendar date at an IANA zone for a UTC instant."""
    instant = as_of or utcnow()
    if not isinstance(instant, datetime):
        return instant
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    try:
        zone = ZoneInfo(str(timezone_name or 'UTC'))
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    return instant.astimezone(zone).date()


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


class RateLimitBucket(db.Model):
    """Shared fixed-window counter for stateless/multi-instance deployments."""
    bucket_key = db.Column(db.String(64), primary_key=True)
    window_id = db.Column(db.BigInteger, primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


SKILL_LEVELS = ['beginner', 'intermediate', 'advanced', 'pro']
SELF_RATING_LEVELS = (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5)
DEFAULT_RATING = 1200
# Ranked results wait long enough to cover a weekend or a missed notification.
# These live here (rather than in the route module) so serialization and the
# maintenance worker cannot drift to different deadlines.
GAME_SCORE_AUTO_CONFIRM_HOURS = 72
GAME_SCORE_LATE_DISPUTE_DAYS = 7
GAME_CASUAL_SCORE_CORRECTION_MINUTES = 15
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
    # Player-facing pickleball self-rating. ``4.5`` represents 4.5+ in the
    # onboarding UI; the existing ELO-like ``rating`` remains match-derived.
    skill_rating = db.Column(db.Float)
    # Optional external DUPR value. It is never inferred from Third Shot ELO.
    dupr_rating = db.Column(db.Float)
    dupr_id = db.Column(db.String(80), nullable=False, default='')
    avatar_color = db.Column(db.String(7), nullable=False, default='#2f9e44')
    avatar_url = db.Column(db.String(500), nullable=False, default='')
    # Uploaded profile photos can be hundreds of kilobytes. Keep the payload
    # deferred so ordinary player-card/feed queries only load the small URL;
    # the dedicated image endpoint opts into the blob when it needs it.
    avatar_data = db.deferred(db.Column(db.Text))
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
    # Coarse presence: touched (throttled) on any authed request.
    last_active_at = db.Column(db.DateTime)
    # Who may discover this account in nearby-player and passive presence
    # surfaces. Explicit participation in a public game remains governed by
    # that game's audience rather than this profile preference.
    nearby_visibility = db.Column(
        db.String(16), nullable=False, default='everyone', index=True,
    )
    # ISO-week marker for the open-games-near-you digest.
    last_games_digest_week = db.Column(db.String(10), nullable=False, default='')
    # ISO-week marker for the weekend keep-your-streak nudge.
    last_streak_nag_week = db.Column(db.String(10), nullable=False, default='')
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
    # Incrementing this invalidates every previously issued JWT. Legacy tokens
    # are interpreted as version 1 so an additive rollout does not sign out all
    # existing players, while password and MFA changes revoke older sessions.
    auth_version = db.Column(db.Integer, nullable=False, default=1)
    email_verified_at = db.Column(db.DateTime)
    # Trusted provisioning may grant ``reviewer`` or ``admin``. No ordinary
    # account endpoint is allowed to write this field.
    operator_role = db.Column(db.String(20), nullable=False, default='', index=True)
    # TOTP seeds are encrypted with MFA_ENCRYPTION_KEY. Recovery codes are
    # individually salted hashes in a JSON list and are consumed one-way.
    mfa_secret_encrypted = db.Column(db.Text, nullable=False, default='')
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False, index=True)
    mfa_enabled_at = db.Column(db.DateTime)
    mfa_recovery_codes = db.Column(db.Text, nullable=False, default='[]')
    # First-run setup is a cross-device account state, not a browser hint.
    # Existing accounts are backfilled by the additive migration while new
    # accounts remain incomplete until the final onboarding step succeeds.
    onboarding_completed_at = db.Column(db.DateTime)
    # Set only during account creation from a valid public invite. Kept off
    # the general public payload; profile responses expose only the boolean
    # "invited by you" relationship to the inviter.
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)

    # Safety enforcement is separate from account deletion. Suspending an
    # account revokes its sessions but preserves the evidence, conversations,
    # and match history a moderator may need to review or restore.
    suspended_at = db.Column(db.DateTime, index=True)
    suspension_reason = db.Column(db.String(500), nullable=False, default='')
    suspended_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))

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
            'skill_rating': self.skill_rating,
            'dupr_rating': self.dupr_rating,
            'dupr_id': self.dupr_id or '',
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
            # Coarse on purpose — "in the app within ~10 minutes", nothing finer.
            'active_now': bool(
                self.nearby_visibility == 'everyone'
                and self.last_active_at
                and (utcnow() - self.last_active_at).total_seconds() < 600
            ),
        }

    def to_summary_dict(self):
        """Identity-sized shape for lists; omit account stats and presence."""
        return {
            'id': self.id,
            'user_id': self.id,
            'display_name': self.display_name,
            'avatar_color': self.avatar_color,
            'avatar_url': self.avatar_url or '',
            'skill_level': self.skill_level,
            'skill_rating': self.skill_rating,
            'dupr_rating': self.dupr_rating,
            'rating': self.rating,
        }

    def to_dict(self):
        data = self.to_public_dict()
        data['email'] = self.email
        data['email_verified'] = self.email_verified_at is not None
        data['home_lat'] = self.home_lat
        data['home_lng'] = self.home_lng
        data['home_area'] = self.home_area
        data['nearby_visibility'] = (
            self.nearby_visibility
            if self.nearby_visibility in {'everyone', 'friends', 'hidden'}
            else 'everyone'
        )
        data['muted_notifications'] = sorted(self.muted_kinds())
        data['onboarding_complete'] = self.onboarding_completed_at is not None
        return data


ACCOUNT_ACTION_PURPOSES = (
    'password_reset', 'email_verification', 'email_change',
)


class AccountActionToken(TimestampMixin, db.Model):
    """One-time, short-lived account-security link.

    Only a SHA-256 digest is stored, so a database read cannot be turned into
    a password reset or email-change link. Tokens are consumed transactionally
    and all older links for the same purpose are invalidated on success.
    """

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    purpose = db.Column(db.String(32), nullable=False, index=True)
    token_hash = db.Column(
        db.String(64), nullable=False, unique=True, index=True,
    )
    pending_email = db.Column(db.String(255), nullable=False, default='')
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    consumed_at = db.Column(db.DateTime)

    user = db.relationship('User')

    def is_active(self, now=None):
        return bool(
            self.purpose in ACCOUNT_ACTION_PURPOSES
            and self.consumed_at is None
            and self.expires_at > (now or utcnow())
        )


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
    # Canonical recurring open-play windows.  Each JSON row contains a
    # weekday, start/end clock time, and optional level/cost/notes.  The
    # legacy free-text field above remains available for imported and
    # community-maintained listings that have not been normalized yet.
    open_play_schedule_rows = db.Column(db.Text, nullable=False, default='[]')
    fees = db.Column(db.String(255), nullable=False, default='')
    # Free-text opening hours, community-maintained via edit suggestions.
    hours = db.Column(db.String(255), nullable=False, default='')
    # Canonical weekly hours JSON: {"timezone":"America/Los_Angeles",
    # "mon":{"open":"06:00","close":"22:00"}, ...}. Free text above
    # remains as an honest fallback for unstructured community data.
    structured_hours = db.Column(db.Text, nullable=False, default='{}')
    hours_dawn_to_dusk = db.Column(db.Boolean, nullable=False, default=False)
    reservation_url = db.Column(db.String(500), nullable=False, default='')
    fee_type = db.Column(db.String(24), nullable=False, default='')
    phone = db.Column(db.String(40), nullable=False, default='')
    website = db.Column(db.String(500), nullable=False, default='')
    photo_url = db.Column(db.String(500), nullable=False, default='')
    # User-uploaded photo as a data URL, served via /courts/<id>/photo
    # (free container disks are commonly ephemeral, so files can't live there).
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

    def structured_hours_dict(self):
        try:
            parsed = json.loads(self.structured_hours or '{}')
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def open_play_schedule_rows_list(self):
        try:
            parsed = json.loads(self.open_play_schedule_rows or '[]')
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        fields = ('weekday', 'start', 'end', 'level', 'cost', 'notes')
        return [
            {field: str(row.get(field) or '') for field in fields}
            for row in parsed
            if isinstance(row, dict)
        ]

    @staticmethod
    def _hours_clock_label(value):
        try:
            parsed = datetime.strptime(value, '%H:%M')
        except (TypeError, ValueError):
            return ''
        return parsed.strftime('%-I:%M %p').replace(':00 ', ' ')

    def hours_status(self, as_of=None):
        if self.closed:
            return {'state': 'closed', 'is_open': False, 'label': 'Closed'}
        schedule = self.structured_hours_dict()
        if self.hours_dawn_to_dusk:
            return {
                'state': 'dawn_to_dusk', 'is_open': None,
                'label': 'Dawn to dusk',
            }
        if not schedule:
            return {
                'state': 'unavailable', 'is_open': None,
                'label': self.hours or '',
            }
        zone_name = str(schedule.get('timezone') or 'America/Los_Angeles')
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = UTC
            zone_name = 'UTC'
        instant = as_of or utcnow()
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        local_now = instant.astimezone(zone)
        day_keys = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
        intervals = []
        for offset in range(-1, 8):
            day = local_now.date() + timedelta(days=offset)
            raw = schedule.get(day_keys[day.weekday()])
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                if not isinstance(value, dict):
                    continue
                opens = value.get('open')
                closes = value.get('close')
                try:
                    open_time = datetime.strptime(opens, '%H:%M').time()
                    close_time = datetime.strptime(closes, '%H:%M').time()
                except (TypeError, ValueError):
                    continue
                start = datetime.combine(day, open_time, tzinfo=zone)
                end = datetime.combine(day, close_time, tzinfo=zone)
                if end <= start:
                    end += timedelta(days=1)
                intervals.append((start, end))
        current = next(
            ((start, end) for start, end in intervals if start <= local_now < end),
            None,
        )
        if current:
            return {
                'state': 'open', 'is_open': True,
                'label': f'Open until {self._hours_clock_label(current[1].strftime("%H:%M"))}',
                'timezone': zone_name,
            }
        upcoming = next(
            ((start, end) for start, end in sorted(intervals) if start > local_now),
            None,
        )
        if upcoming:
            day_prefix = '' if upcoming[0].date() == local_now.date() \
                else upcoming[0].strftime('%a ')
            return {
                'state': 'closed', 'is_open': False,
                'label': f'Opens {day_prefix}{self._hours_clock_label(upcoming[0].strftime("%H:%M"))}',
                'timezone': zone_name,
            }
        return {'state': 'closed', 'is_open': False, 'label': 'Closed today'}

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
            'has_restrooms': bool(self.has_restrooms),
            'has_water': bool(self.has_water),
            'nets_provided': bool(self.nets_provided),
            'num_courts': self.num_courts,
            'photo_url': self.photo_url,
            'open_status': self.hours_status(),
            'reservation_url': self.reservation_url or '',
            'fee_type': self.fee_type or '',
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
            'open_play_schedule_rows': self.open_play_schedule_rows_list(),
            'fees': self.fees,
            'hours': self.hours,
            'structured_hours': self.structured_hours_dict(),
            'hours_dawn_to_dusk': bool(self.hours_dawn_to_dusk),
            'reservation_url': self.reservation_url or '',
            'fee_type': self.fee_type or '',
            'phone': self.phone,
            'website': self.website,
            'has_restrooms': bool(self.has_restrooms),
            'has_water': bool(self.has_water),
            'nets_provided': bool(self.nets_provided),
            'closed': bool(self.closed),
        })
        return data


BUSINESS_CLAIM_STATUSES = ('pending', 'verified', 'rejected')
BUSINESS_OFFERING_CATEGORIES = (
    'lesson', 'open_play', 'clinic', 'league', 'tournament', 'membership',
    'court_rental', 'other',
)
BUSINESS_SCHEDULE_KINDS = (
    'hours', 'lesson', 'open_play', 'clinic', 'league', 'tournament',
    'event', 'other',
)
BUSINESS_INTEGRATION_CAPABILITIES = (
    'bookings', 'lessons', 'schedule', 'memberships', 'events', 'other',
)
BUSINESS_INTEGRATION_REQUEST_STATUSES = (
    'submitted', 'contacted', 'completed', 'declined',
)
BUSINESS_CLAIM_REVIEW_DECISIONS = ('approve', 'reject')
BUSINESS_CLAIM_VERIFICATION_METHODS = (
    'business_email', 'business_phone', 'website_domain', 'documents',
    'in_person', 'other',
)
BUSINESS_GOVERNANCE_STATUSES = ('active', 'suspended', 'relinquished')
BUSINESS_CONTENT_REVIEW_STATUSES = ('approved', 'pending', 'rejected')
BUSINESS_TEAM_ROLES = ('owner', 'admin', 'editor', 'viewer')
BUSINESS_EVIDENCE_STATUSES = (
    'submitted', 'challenge_sent', 'verified', 'accepted', 'rejected',
)
BUSINESS_REVISION_REVIEW_STATUSES = ('approved', 'pending', 'rejected')
BUSINESS_REPORT_STATUSES = ('submitted', 'reviewing', 'resolved', 'dismissed')
BUSINESS_REPORT_CATEGORIES = (
    'broken_link', 'incorrect_info', 'ownership', 'safety', 'other',
)
BUSINESS_OPERATOR_ACTION_TYPES = ('claim_transfer', 'suspend', 'revoke')
BUSINESS_OPERATOR_ACTION_STATUSES = ('proposed', 'confirmed', 'cancelled', 'expired')


class BusinessProfile(TimestampMixin, db.Model):
    """A court or club's player-facing integration profile.

    Creating a profile submits a claim; it never verifies that claim.  The
    separate status and timestamp make it impossible for ordinary profile
    edits to accidentally render a business as verified.
    """
    __table_args__ = (
        db.UniqueConstraint('court_id', name='uq_business_profile_court'),
        db.CheckConstraint(
            "claim_status IN ('pending', 'verified', 'rejected')",
            name='ck_business_profile_claim_status',
        ),
        db.CheckConstraint(
            "governance_status IN ('active', 'suspended', 'relinquished')",
            name='ck_business_profile_governance_status',
        ),
        db.CheckConstraint(
            "content_review_status IN ('approved', 'pending', 'rejected')",
            name='ck_business_profile_content_review_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_profile_owner_id_fkey'),
        nullable=False,
        index=True,
    )
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_organization.id',
            name='business_profile_organization_id_fkey',
        ),
        index=True,
    )
    court_id = db.Column(
        db.Integer,
        db.ForeignKey('court.id', name='business_profile_court_id_fkey'),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    claimant_role = db.Column(db.String(80), nullable=False, default='')
    claim_status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    verified_at = db.Column(db.DateTime)
    published = db.Column(db.Boolean, nullable=False, default=False, index=True)
    governance_status = db.Column(
        db.String(20), nullable=False, default='active', index=True,
    )
    suspension_reason = db.Column(db.String(500), nullable=False, default='')
    suspended_at = db.Column(db.DateTime)
    suspended_by = db.Column(db.String(120), nullable=False, default='')
    content_review_status = db.Column(
        db.String(20), nullable=False, default='approved', index=True,
    )
    content_reviewed_at = db.Column(db.DateTime)
    description = db.Column(db.String(2000), nullable=False, default='')
    announcement = db.Column(db.String(500), nullable=False, default='')
    contact_email = db.Column(db.String(255), nullable=False, default='')
    contact_phone = db.Column(db.String(40), nullable=False, default='')
    hours = db.Column(db.String(1000), nullable=False, default='')
    amenities = db.Column(db.Text, nullable=False, default='[]')
    website_url = db.Column(db.String(500), nullable=False, default='')
    booking_url = db.Column(db.String(500), nullable=False, default='')
    membership_url = db.Column(db.String(500), nullable=False, default='')
    logo_url = db.Column(db.String(500), nullable=False, default='')
    # Same-origin image data; external logos remain readable during rollout but
    # new uploads use this validated payload to avoid third-party tracking.
    logo_data = db.Column(db.Text, nullable=False, default='')

    owner = db.relationship('User', foreign_keys=[owner_id])
    court = db.relationship('Court', foreign_keys=[court_id])
    offerings = db.relationship(
        'BusinessOffering', back_populates='business',
        cascade='all, delete-orphan', order_by='BusinessOffering.sort_order',
    )
    schedule_items = db.relationship(
        'BusinessScheduleItem', back_populates='business',
        cascade='all, delete-orphan', order_by='BusinessScheduleItem.sort_order',
    )
    claims = db.relationship(
        'BusinessClaim', back_populates='business',
        cascade='all, delete-orphan',
    )
    integration_requests = db.relationship(
        'BusinessIntegrationRequest', back_populates='business',
        cascade='all, delete-orphan',
    )
    organization = db.relationship(
        'BusinessOrganization', back_populates='businesses',
        foreign_keys=[organization_id],
    )
    revisions = db.relationship(
        'BusinessProfileRevision', back_populates='business',
        cascade='all, delete-orphan',
        foreign_keys='BusinessProfileRevision.business_id',
    )
    governance_events = db.relationship(
        'BusinessGovernanceEvent', back_populates='business',
        cascade='all, delete-orphan',
    )
    reports = db.relationship(
        'BusinessProfileReport', back_populates='business',
        cascade='all, delete-orphan',
    )

    def amenities_list(self):
        try:
            parsed = json.loads(self.amenities or '[]')
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    @property
    def verified(self):
        return self.claim_status == 'verified' and self.verified_at is not None

    def to_dict(self, *, include_inactive=False):
        offerings = self.offerings if include_inactive else (
            item for item in self.offerings if item.active
        )
        schedule = self.schedule_items if include_inactive else (
            item for item in self.schedule_items if item.active
        )
        data = {
            'id': self.id,
            'name': self.name,
            'court_id': self.court_id,
            'court_name': self.court.name if self.court else None,
            'court_city': self.court.city if self.court else None,
            'court_state': self.court.state if self.court else None,
            'role': self.claimant_role,
            'claim_status': self.claim_status,
            'verified': self.verified,
            'published': bool(self.published),
            'governance_status': self.governance_status,
            'suspension_reason': self.suspension_reason,
            'suspended_at': iso(self.suspended_at),
            'content_review_status': self.content_review_status,
            'content_reviewed_at': iso(self.content_reviewed_at),
            'description': self.description,
            'announcement': self.announcement,
            'email': self.contact_email,
            'phone': self.contact_phone,
            'hours': self.hours,
            'amenities': self.amenities_list(),
            'website_url': self.website_url,
            'booking_url': self.booking_url,
            'membership_url': self.membership_url,
            'logo_url': self.logo_url,
            'has_logo_upload': bool(self.logo_data),
            'offerings': [item.to_dict() for item in offerings],
            'schedule': [item.to_dict() for item in schedule],
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
        }
        if include_inactive:
            data['integration_requests'] = [
                item.to_dict()
                for item in sorted(
                    self.integration_requests,
                    key=lambda item: (item.created_at, item.id),
                    reverse=True,
                )
            ]
        return data

    def to_public_dict(self):
        """Player-facing profile without ownership or claim workflow state."""
        data = self.to_dict()
        data['schedule'] = [
            item.to_dict()
            for item in self.schedule_items
            if item.active and item.is_current()
        ]
        for field in (
            'role', 'claim_status', 'published', 'governance_status',
            'suspension_reason', 'suspended_at', 'content_review_status',
            'content_reviewed_at', 'created_at', 'updated_at',
        ):
            data.pop(field, None)
        return data


class BusinessClaim(TimestampMixin, db.Model):
    """A verification request, including competing claims for one court."""
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'court_id', name='uq_business_claim_user_court',
        ),
        db.CheckConstraint(
            "status IN ('pending', 'verified', 'rejected')",
            name='ck_business_claim_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_claim_user_id_fkey'),
        nullable=False,
        index=True,
    )
    court_id = db.Column(
        db.Integer,
        db.ForeignKey('court.id', name='business_claim_court_id_fkey'),
        nullable=False,
        index=True,
    )
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_claim_business_id_fkey'),
        index=True,
    )
    role = db.Column(db.String(80), nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    reviewed_at = db.Column(db.DateTime)
    assigned_operator_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_claim_assigned_operator_id_fkey'),
        index=True,
    )
    assigned_operator_identifier = db.Column(
        db.String(120), nullable=False, default='',
    )
    due_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: utcnow() + timedelta(hours=48), index=True,
    )
    claimant_feedback = db.Column(db.String(1000), nullable=False, default='')

    user = db.relationship('User', foreign_keys=[user_id])
    court = db.relationship('Court', foreign_keys=[court_id])
    business = db.relationship('BusinessProfile', back_populates='claims')
    assigned_operator = db.relationship('User', foreign_keys=[assigned_operator_id])
    review_events = db.relationship(
        'BusinessClaimReviewEvent', back_populates='claim',
        cascade='all, delete-orphan',
        order_by='BusinessClaimReviewEvent.created_at',
    )
    evidence = db.relationship(
        'BusinessVerificationEvidence', back_populates='claim',
        cascade='all, delete-orphan',
        order_by='BusinessVerificationEvidence.created_at',
    )

    def to_dict(self):
        return {
            'id': self.id,
            'court_id': self.court_id,
            'court_name': self.court.name if self.court else None,
            'business_id': self.business_id,
            'role': self.role,
            'status': self.status,
            'reviewed_at': iso(self.reviewed_at),
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
            'evidence_count': len(self.evidence),
            'response_due_at': iso(self.due_at),
            'feedback': self.claimant_feedback,
        }


class BusinessClaimReviewEvent(db.Model):
    """Immutable operator record for every business-control decision.

    A claim's current status is intentionally convenient for queries, while
    these append-only rows preserve who made each decision, how control was
    checked, and why.  They are never included in claimant/public serializers.
    """
    __table_args__ = (
        db.CheckConstraint(
            "decision IN ('approve', 'reject')",
            name='ck_business_claim_review_event_decision',
        ),
        db.CheckConstraint(
            "verification_method IN ('business_email', 'business_phone', "
            "'website_domain', 'documents', 'in_person', 'other')",
            name='ck_business_claim_review_event_method',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_claim.id',
            ondelete='CASCADE',
            name='business_claim_review_event_claim_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    reviewer_identifier = db.Column(db.String(120), nullable=False)
    verification_method = db.Column(db.String(32), nullable=False)
    decision = db.Column(db.String(16), nullable=False)
    review_note = db.Column(db.String(1000), nullable=False)
    ownership_transferred = db.Column(db.Boolean, nullable=False, default=False)
    previous_owner_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'user.id',
            ondelete='SET NULL',
            name='business_claim_review_event_previous_owner_id_fkey',
        ),
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    claim = db.relationship('BusinessClaim', back_populates='review_events')
    previous_owner = db.relationship('User', foreign_keys=[previous_owner_id])

    def to_operator_dict(self):
        return {
            'id': self.id,
            'claim_id': self.claim_id,
            'reviewer_identifier': self.reviewer_identifier,
            'verification_method': self.verification_method,
            'decision': self.decision,
            'review_note': self.review_note,
            'ownership_transferred': bool(self.ownership_transferred),
            'previous_owner_id': self.previous_owner_id,
            'created_at': iso(self.created_at),
        }


class BusinessVerificationEvidence(TimestampMixin, db.Model):
    """Private, reviewable proof submitted for one venue-control claim."""
    __table_args__ = (
        db.CheckConstraint(
            "evidence_type IN ('business_email', 'business_phone', "
            "'website_domain', 'documents', 'in_person', 'other')",
            name='ck_business_verification_evidence_type',
        ),
        db.CheckConstraint(
            "status IN ('submitted', 'challenge_sent', 'verified', "
            "'accepted', 'rejected')",
            name='ck_business_verification_evidence_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_claim.id', ondelete='CASCADE',
            name='business_verification_evidence_claim_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    submitted_by_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'user.id', name='business_verification_evidence_submitter_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    evidence_type = db.Column(db.String(32), nullable=False)
    evidence_value = db.Column(db.String(500), nullable=False, default='')
    note = db.Column(db.String(1000), nullable=False, default='')
    domain_match = db.Column(db.Boolean)
    status = db.Column(db.String(24), nullable=False, default='submitted', index=True)
    challenge_token_hash = db.Column(db.String(64), nullable=False, default='')
    challenge_expires_at = db.Column(db.DateTime)
    challenge_verified_at = db.Column(db.DateTime)
    challenge_failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    challenge_locked_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.String(120), nullable=False, default='')
    review_note = db.Column(db.String(1000), nullable=False, default='')
    reviewed_at = db.Column(db.DateTime)

    claim = db.relationship('BusinessClaim', back_populates='evidence')
    submitted_by = db.relationship('User', foreign_keys=[submitted_by_id])

    def to_owner_dict(self):
        return {
            'id': self.id,
            'claim_id': self.claim_id,
            'type': self.evidence_type,
            'value': self.evidence_value,
            'note': self.note,
            'domain_match': self.domain_match,
            'status': self.status,
            'challenge_expires_at': iso(self.challenge_expires_at),
            'challenge_verified_at': iso(self.challenge_verified_at),
            'challenge_attempts_remaining': max(
                0, 5 - int(self.challenge_failed_attempts or 0),
            ),
            'challenge_locked': bool(self.challenge_locked_at),
            'review_note': self.review_note,
            'reviewed_at': iso(self.reviewed_at),
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
        }

    def to_operator_dict(self):
        data = self.to_owner_dict()
        data.update({
            'submitted_by_id': self.submitted_by_id,
            'submitted_by_email': (
                self.submitted_by.email if self.submitted_by else None
            ),
            'reviewed_by': self.reviewed_by,
        })
        return data


class BusinessOrganization(TimestampMixin, db.Model):
    """A reusable business team that may manage several venue profiles."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_organization_creator_id_fkey'),
        nullable=False,
        index=True,
    )

    businesses = db.relationship(
        'BusinessProfile', back_populates='organization',
        foreign_keys='BusinessProfile.organization_id',
    )
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    members = db.relationship(
        'BusinessOrganizationMember', back_populates='organization',
        cascade='all, delete-orphan',
    )
    invitations = db.relationship(
        'BusinessStaffInvitation', back_populates='organization',
        cascade='all, delete-orphan',
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'business_ids': sorted(business.id for business in self.businesses),
        }


class BusinessOrganizationMember(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            'organization_id', 'user_id', name='uq_business_organization_member',
        ),
        db.CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'viewer')",
            name='ck_business_organization_member_role',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_organization.id', ondelete='CASCADE',
            name='business_organization_member_organization_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_organization_member_user_id_fkey'),
        nullable=False,
        index=True,
    )
    role = db.Column(db.String(20), nullable=False, default='viewer', index=True)
    accepted_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    organization = db.relationship('BusinessOrganization', back_populates='members')
    user = db.relationship('User', foreign_keys=[user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'display_name': self.user.display_name if self.user else None,
            'email': self.user.email if self.user else None,
            'role': self.role,
            'accepted_at': iso(self.accepted_at),
        }


class BusinessStaffInvitation(TimestampMixin, db.Model):
    __table_args__ = (
        db.CheckConstraint(
            "role IN ('admin', 'editor', 'viewer')",
            name='ck_business_staff_invitation_role',
        ),
        db.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name='ck_business_staff_invitation_status',
        ),
        db.UniqueConstraint('token_hash', name='uq_business_staff_invitation_token'),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_organization.id', ondelete='CASCADE',
            name='business_staff_invitation_organization_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    invited_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_staff_invitation_inviter_id_fkey'),
        nullable=False,
        index=True,
    )
    email = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    accepted_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_staff_invitation_acceptor_id_fkey'),
        index=True,
    )
    accepted_at = db.Column(db.DateTime)

    organization = db.relationship('BusinessOrganization', back_populates='invitations')
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])
    accepted_by = db.relationship('User', foreign_keys=[accepted_by_id])

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'role': self.role,
            'status': self.status,
            'expires_at': iso(self.expires_at),
            'accepted_by_id': self.accepted_by_id,
            'accepted_at': iso(self.accepted_at),
            'created_at': iso(self.created_at),
        }


class BusinessProfileRevision(db.Model):
    __table_args__ = (
        db.CheckConstraint(
            "review_status IN ('approved', 'pending', 'rejected')",
            name='ck_business_profile_revision_review_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_profile.id', ondelete='CASCADE',
            name='business_profile_revision_business_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_profile_revision_actor_id_fkey'),
        index=True,
    )
    action = db.Column(db.String(40), nullable=False)
    change_summary = db.Column(db.String(500), nullable=False, default='')
    # Rejections can atomically put the last accepted content back without
    # relying on an earlier revision row having survived an ownership reset.
    previous_snapshot = db.Column(db.Text, nullable=False, default='{}')
    snapshot = db.Column(db.Text, nullable=False)
    sensitive = db.Column(db.Boolean, nullable=False, default=False, index=True)
    review_status = db.Column(
        db.String(20), nullable=False, default='approved', index=True,
    )
    reviewer_identifier = db.Column(db.String(120), nullable=False, default='')
    review_note = db.Column(db.String(1000), nullable=False, default='')
    reviewed_at = db.Column(db.DateTime)
    restored_from_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_profile_revision.id', ondelete='SET NULL',
            name='business_profile_revision_restored_from_id_fkey',
        ),
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    business = db.relationship(
        'BusinessProfile', back_populates='revisions', foreign_keys=[business_id],
    )
    actor = db.relationship('User', foreign_keys=[actor_user_id])
    restored_from = db.relationship(
        'BusinessProfileRevision', remote_side=[id], foreign_keys=[restored_from_id],
    )

    def snapshot_dict(self):
        try:
            value = json.loads(self.snapshot or '{}')
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def previous_snapshot_dict(self):
        try:
            value = json.loads(self.previous_snapshot or '{}')
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def to_dict(self, *, include_snapshot=False):
        data = {
            'id': self.id,
            'business_id': self.business_id,
            'actor_user_id': self.actor_user_id,
            'actor_name': self.actor.display_name if self.actor else None,
            'action': self.action,
            'change_summary': self.change_summary,
            'sensitive': bool(self.sensitive),
            'review_status': self.review_status,
            'review_note': self.review_note,
            'reviewed_at': iso(self.reviewed_at),
            'restored_from_id': self.restored_from_id,
            'created_at': iso(self.created_at),
        }
        if include_snapshot:
            snapshot = self.snapshot_dict()
            profile = dict(snapshot.get('profile') or {})
            profile['has_logo_upload'] = bool(profile.pop('logo_data', ''))
            data['snapshot'] = {**snapshot, 'profile': profile}
        return data


class BusinessGovernanceEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_profile.id', ondelete='CASCADE',
            name='business_governance_event_business_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_governance_event_actor_id_fkey'),
        index=True,
    )
    operator_identifier = db.Column(db.String(120), nullable=False, default='')
    event_type = db.Column(db.String(48), nullable=False, index=True)
    details = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    business = db.relationship('BusinessProfile', back_populates='governance_events')
    actor = db.relationship('User', foreign_keys=[actor_user_id])

    def details_dict(self):
        try:
            value = json.loads(self.details or '{}')
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def to_dict(self):
        return {
            'id': self.id,
            'business_id': self.business_id,
            'actor_user_id': self.actor_user_id,
            'actor_name': self.actor.display_name if self.actor else None,
            'operator_identifier': self.operator_identifier,
            'event_type': self.event_type,
            'details': self.details_dict(),
            'created_at': iso(self.created_at),
        }


class BusinessOperatorAction(db.Model):
    """A destructive operator action awaiting a different administrator."""
    __table_args__ = (
        db.CheckConstraint(
            "action_type IN ('claim_transfer', 'suspend', 'revoke')",
            name='ck_business_operator_action_type',
        ),
        db.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'cancelled', 'expired')",
            name='ck_business_operator_action_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_profile.id', ondelete='CASCADE',
            name='business_operator_action_business_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    claim_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_claim.id', ondelete='CASCADE',
            name='business_operator_action_claim_id_fkey',
        ),
        index=True,
    )
    action_type = db.Column(db.String(24), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False, default='{}')
    status = db.Column(db.String(20), nullable=False, default='proposed', index=True)
    proposed_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_operator_action_proposer_id_fkey'),
        nullable=False,
        index=True,
    )
    confirmed_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_operator_action_confirmer_id_fkey'),
        index=True,
    )
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    confirmed_at = db.Column(db.DateTime)

    business = db.relationship('BusinessProfile', foreign_keys=[business_id])
    claim = db.relationship('BusinessClaim', foreign_keys=[claim_id])
    proposed_by = db.relationship('User', foreign_keys=[proposed_by_id])
    confirmed_by = db.relationship('User', foreign_keys=[confirmed_by_id])

    def payload_dict(self):
        try:
            value = json.loads(self.payload or '{}')
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def to_dict(self):
        return {
            'id': self.id,
            'business_id': self.business_id,
            'claim_id': self.claim_id,
            'action_type': self.action_type,
            'status': self.status,
            'payload': self.payload_dict(),
            'proposed_by_id': self.proposed_by_id,
            'proposed_by': (
                self.proposed_by.display_name if self.proposed_by else None
            ),
            'confirmed_by_id': self.confirmed_by_id,
            'expires_at': iso(self.expires_at),
            'created_at': iso(self.created_at),
            'confirmed_at': iso(self.confirmed_at),
        }


class OperatorSecurityEvent(db.Model):
    """Append-only audit record for trusted operator role administration."""

    id = db.Column(db.Integer, primary_key=True)
    actor_identifier = db.Column(db.String(120), nullable=False)
    target_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='operator_security_event_target_user_id_fkey'),
        nullable=False,
        index=True,
    )
    action = db.Column(db.String(40), nullable=False, index=True)
    previous_role = db.Column(db.String(20), nullable=False, default='')
    new_role = db.Column(db.String(20), nullable=False, default='')
    reason = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    target_user = db.relationship('User', foreign_keys=[target_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'actor_identifier': self.actor_identifier,
            'target_user_id': self.target_user_id,
            'target_email': self.target_user.email if self.target_user else None,
            'action': self.action,
            'previous_role': self.previous_role,
            'new_role': self.new_role,
            'reason': self.reason,
            'created_at': iso(self.created_at),
        }


class BusinessProfileReport(TimestampMixin, db.Model):
    __table_args__ = (
        db.CheckConstraint(
            "category IN ('broken_link', 'incorrect_info', 'ownership', "
            "'safety', 'other')",
            name='ck_business_profile_report_category',
        ),
        db.CheckConstraint(
            "status IN ('submitted', 'reviewing', 'resolved', 'dismissed')",
            name='ck_business_profile_report_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_profile.id', ondelete='CASCADE',
            name='business_profile_report_business_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    reporter_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='business_profile_report_reporter_id_fkey'),
        nullable=False,
        index=True,
    )
    category = db.Column(db.String(32), nullable=False)
    details = db.Column(db.String(2000), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='submitted', index=True)
    handled_by = db.Column(db.String(120), nullable=False, default='')
    status_message = db.Column(db.String(1000), nullable=False, default='')
    status_changed_at = db.Column(db.DateTime)
    assigned_operator_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'user.id', name='business_profile_report_assigned_operator_id_fkey',
        ),
        index=True,
    )
    assigned_operator_identifier = db.Column(
        db.String(120), nullable=False, default='',
    )
    due_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: utcnow() + timedelta(hours=48), index=True,
    )

    business = db.relationship('BusinessProfile', back_populates='reports')
    reporter = db.relationship('User', foreign_keys=[reporter_id])
    assigned_operator = db.relationship('User', foreign_keys=[assigned_operator_id])

    def to_dict(self, *, operator=False):
        data = {
            'id': self.id,
            'business_id': self.business_id,
            'business_name': self.business.name if self.business else None,
            'category': self.category,
            'details': self.details,
            'status': self.status,
            'status_message': self.status_message,
            'status_changed_at': iso(self.status_changed_at),
            'response_due_at': iso(self.due_at),
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
        }
        if operator:
            data.update({
                'reporter_id': self.reporter_id,
                'reporter_email': self.reporter.email if self.reporter else None,
                'handled_by': self.handled_by,
                'assigned_operator_id': self.assigned_operator_id,
                'assigned_operator_identifier': self.assigned_operator_identifier,
            })
        return data


class BusinessOffering(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_offering_business_id_fkey'),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(32), nullable=False, default='other')
    description = db.Column(db.String(1000), nullable=False, default='')
    price_text = db.Column(db.String(120), nullable=False, default='')
    duration_minutes = db.Column(db.Integer)
    booking_url = db.Column(db.String(500), nullable=False, default='')
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    business = db.relationship('BusinessProfile', back_populates='offerings')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category,
            'description': self.description,
            'price_text': self.price_text,
            'duration_minutes': self.duration_minutes,
            'booking_url': self.booking_url,
            'active': bool(self.active),
            'sort_order': self.sort_order,
        }


class BusinessScheduleItem(TimestampMixin, db.Model):
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('scheduled','cancelled','sold_out','completed')",
            name='ck_business_schedule_item_status',
        ),
        db.CheckConstraint(
            'capacity IS NULL OR capacity >= 0',
            name='ck_business_schedule_item_capacity',
        ),
        db.CheckConstraint(
            'spots_remaining IS NULL OR spots_remaining >= 0',
            name='ck_business_schedule_item_spots',
        ),
        db.CheckConstraint(
            'capacity IS NULL OR spots_remaining IS NULL '
            'OR spots_remaining <= capacity',
            name='ck_business_schedule_item_spots_capacity',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey('business_profile.id', name='business_schedule_item_business_id_fkey'),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(32), nullable=False, default='other')
    day_of_week = db.Column(db.String(12), nullable=False, default='')
    start_time = db.Column(db.String(5), nullable=False, default='')
    end_time = db.Column(db.String(5), nullable=False, default='')
    skill_level = db.Column(db.String(40), nullable=False, default='all')
    booking_url = db.Column(db.String(500), nullable=False, default='')
    timezone = db.Column(db.String(64), nullable=False, default='UTC')
    recurrence = db.Column(db.String(24), nullable=False, default='weekly')
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    event_date = db.Column(db.Date, index=True)
    capacity = db.Column(db.Integer)
    spots_remaining = db.Column(db.Integer)
    status = db.Column(
        db.String(24), nullable=False, default='scheduled', index=True,
    )
    location_note = db.Column(db.String(240), nullable=False, default='')
    instructor = db.Column(db.String(120), nullable=False, default='')
    source_updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    business = db.relationship('BusinessProfile', back_populates='schedule_items')

    def is_current(self, as_of=None):
        """Return whether a manager-maintained schedule row can still help players."""
        today = local_date_for_timezone(self.timezone, as_of)
        if self.recurrence == 'dated':
            return bool(self.event_date and self.event_date >= today)
        if self.recurrence == 'date_range':
            return bool(self.end_date and self.end_date >= today)
        return True

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'kind': self.kind,
            'day_of_week': self.day_of_week,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'skill_level': self.skill_level,
            'booking_url': self.booking_url,
            'timezone': self.timezone,
            'recurrence': self.recurrence,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'event_date': self.event_date.isoformat() if self.event_date else None,
            'capacity': self.capacity,
            'spots_remaining': self.spots_remaining,
            'status': self.status,
            'cancelled': self.status == 'cancelled',
            'location_note': self.location_note,
            'instructor': self.instructor,
            'source_updated_at': iso(self.source_updated_at),
            # Manual availability is useful, but it is not a provider-backed
            # real-time inventory promise. Keep that distinction explicit.
            'freshness': {
                'source': 'manager',
                'updated_at': iso(self.source_updated_at),
                'live': False,
            },
            'active': bool(self.active),
            'sort_order': self.sort_order,
        }


class BusinessIntegrationRequest(TimestampMixin, db.Model):
    """A durable request to connect an existing business system to the app."""
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('submitted', 'contacted', 'completed', 'declined')",
            name='ck_business_integration_request_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'business_profile.id',
            name='business_integration_request_business_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    requested_by_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'user.id', name='business_integration_request_requested_by_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    provider = db.Column(db.String(120), nullable=False, default='')
    capabilities = db.Column(db.Text, nullable=False, default='[]')
    details = db.Column(db.String(2000), nullable=False, default='')
    contact_email = db.Column(db.String(255), nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='submitted', index=True)
    handled_by = db.Column(db.String(120), nullable=False, default='')
    status_message = db.Column(db.String(1000), nullable=False, default='')
    status_changed_at = db.Column(db.DateTime)
    assigned_operator_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'user.id', name='business_integration_request_assigned_operator_id_fkey',
        ),
        index=True,
    )
    assigned_operator_identifier = db.Column(
        db.String(120), nullable=False, default='',
    )
    due_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: utcnow() + timedelta(hours=72), index=True,
    )

    business = db.relationship('BusinessProfile', back_populates='integration_requests')
    requested_by = db.relationship('User', foreign_keys=[requested_by_id])
    assigned_operator = db.relationship('User', foreign_keys=[assigned_operator_id])

    def capabilities_list(self):
        try:
            parsed = json.loads(self.capabilities or '[]')
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [
            value for value in parsed
            if value in BUSINESS_INTEGRATION_CAPABILITIES
        ]

    def to_dict(self):
        return {
            'id': self.id,
            'business_id': self.business_id,
            'business_name': self.business.name if self.business else None,
            'court_id': self.business.court_id if self.business else None,
            'court_name': (
                self.business.court.name
                if self.business and self.business.court else None
            ),
            'provider': self.provider,
            'capabilities': self.capabilities_list(),
            'details': self.details,
            'contact_email': self.contact_email,
            'requested_by_id': self.requested_by_id,
            'requested_by_name': (
                self.requested_by.display_name if self.requested_by else None
            ),
            'requested_by_email': (
                self.requested_by.email if self.requested_by else None
            ),
            'status': self.status,
            'status_message': self.status_message,
            'status_changed_at': iso(self.status_changed_at),
            'response_due_at': iso(self.due_at),
            'request_only': True,
            'connection_active': False,
            'sync_active': False,
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
        }

    def to_operator_dict(self):
        data = self.to_dict()
        data['handled_by'] = self.handled_by
        data['assigned_operator_id'] = self.assigned_operator_id
        data['assigned_operator_identifier'] = self.assigned_operator_identifier
        return data


class CheckIn(TimestampMixin, db.Model):
    __table_args__ = (
        # A player can have historical visits at many courts, but only one
        # current physical presence.  The predicate keeps checked-out history
        # unconstrained while making concurrent/serverless check-ins converge
        # on a database-enforced invariant.
        db.Index(
            'uq_check_in_active_user',
            'user_id',
            unique=True,
            postgresql_where=db.text('checked_out_at IS NULL'),
            sqlite_where=db.text('checked_out_at IS NULL'),
        ),
    )

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
        db.CheckConstraint('requester_id <> addressee_id', name='ck_friendship_not_self'),
    )

    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    addressee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='pending')

    requester = db.relationship('User', foreign_keys=[requester_id])
    addressee = db.relationship('User', foreign_keys=[addressee_id])

    def other_user(self, user_id):
        return self.addressee if self.requester_id == user_id else self.requester


# Direction records who initiated a pending request, while this expression
# index defines the relationship's true identity: one unordered pair. CASE is
# portable across both SQLite and PostgreSQL (unlike LEAST/GREATEST on SQLite).
db.Index(
    'uq_friendship_unordered_pair',
    db.case(
        (Friendship.requester_id < Friendship.addressee_id, Friendship.requester_id),
        else_=Friendship.addressee_id,
    ),
    db.case(
        (Friendship.requester_id < Friendship.addressee_id, Friendship.addressee_id),
        else_=Friendship.requester_id,
    ),
    unique=True,
)


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
    completed = (
        db.session.query(Game.id)
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
    )
    games_count = int(completed.count() or 0)
    courts_count = int(
        db.session.query(db.func.count(db.func.distinct(Game.court_id)))
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
        )
        .scalar() or 0
    )
    wins = int(
        db.session.query(db.func.count(Game.id))
        .join(GamePlayer, GamePlayer.game_id == Game.id)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
            Game.completed_at.isnot(None),
            Game.score_team1.isnot(None),
            Game.score_team2.isnot(None),
            db.or_(
                db.and_(
                    GamePlayer.team == 1,
                    Game.score_team1 > Game.score_team2,
                ),
                db.and_(
                    GamePlayer.team == 2,
                    Game.score_team2 > Game.score_team1,
                ),
            ),
        )
        .scalar() or 0
    )
    friend_count = Friendship.query.filter(
        Friendship.status == 'accepted',
        db.or_(Friendship.requester_id == user.id, Friendship.addressee_id == user.id),
    ).count()
    invite_conversions = User.query.filter_by(
        invited_by_user_id=user.id, deleted_at=None,
    ).count()
    mvp_count = GameMvpVote.query.filter_by(votee_id=user.id).count()
    league_wins = League.query.filter(
        League.status == 'completed', League.champion_user_id == user.id,
    ).count()
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
        ('ten_games', '🔟', '10 games played', games_count, 10),
        ('explorer', '🧭', 'Played 5 courts', courts_count, 5),
        ('hot_streak', '🔥', '3-win streak', user.best_streak or 0, 3),
        ('mvp', '🌟', 'Voted MVP', mvp_count, 1),
        # Keep the legacy five-friend path while also recognizing the more
        # intentional act of bringing one new player into Third Shot.
        ('social', '🤝', 'Community builder', invite_conversions + (1 if friend_count >= 5 else 0), 1),
        ('champion', '🏆', 'Tournament champion', titles, 1),
        ('league_champion', '📦', 'League champion', league_wins, 1),
        ('sharpshooter', '🎯', '10 ranked wins', user.ranked_wins or 0, 10),
        ('globetrotter', '🗺', 'Played 15 courts', courts_count, 15),
        ('century', '💯', '100 games played', games_count, 100),
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


def award_new_badges(*users):
    """Queue each newly earned badge exactly once inside the caller's write.

    This intentionally never commits: game/tournament/social mutations own
    their transaction, while profile/dashboard GETs remain read-only.
    """
    awarded = {}
    for user in users:
        if not user or user.deleted_at is not None:
            continue
        try:
            already = set(json.loads(user.notified_badges or '[]'))
        except (ValueError, TypeError):
            already = set()
        badges = player_badges(user)
        fresh = [badge for badge in badges if badge['id'] not in already]
        if not fresh:
            continue
        for badge in fresh:
            notify(
                user.id,
                'badge_earned',
                f'Badge unlocked: {badge["emoji"]} {badge["label"]}',
            )
        user.notified_badges = json.dumps(sorted(
            already | {badge['id'] for badge in badges},
        ))
        awarded[user.id] = fresh
    return awarded


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


def mvp_award_count(user):
    """Games where this player actually won the MVP vote (not just received
    one) — powers the 🌟 ×N flair on profiles."""
    games = (
        Game.query.join(GamePlayer)
        .filter(
            GamePlayer.user_id == user.id,
            Game.status == 'completed',
        )
        .limit(500)
        .all()
    )
    count = 0
    for game in games:
        summary = game._mvp_summary()
        if summary and summary['user_id'] == user.id:
            count += 1
    return count


def league_titles(user, limit=3):
    """Box-league seasons this player has won — mirrors tournament_titles."""
    won = (
        League.query
        .filter(League.status == 'completed', League.champion_user_id == user.id)
        .order_by(League.completed_at.desc())
    )
    recent = won.limit(limit).all()
    return {
        'count': won.count(),
        'recent': [
            {'id': lg.id, 'name': lg.name, 'completed_at': iso(lg.completed_at)}
            for lg in recent
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


def can_direct_message(user_a_id, user_b_id):
    """Whether two players have a real-world or in-app coordination context.

    Friendship is not required after people have played, joined the same
    group, competed together, or actually overlapped at a court. An existing
    thread remains replyable if that context later ends. Blocks always win.
    """
    try:
        user_a_id = int(user_a_id)
        user_b_id = int(user_b_id)
    except (TypeError, ValueError):
        return False
    if user_a_id <= 0 or user_b_id <= 0 or user_a_id == user_b_id:
        return False
    if is_blocked_between(user_a_id, user_b_id):
        return False

    paired = lambda left, right: db.or_(
        db.and_(left == user_a_id, right == user_b_id),
        db.and_(left == user_b_id, right == user_a_id),
    )
    if Friendship.query.filter(
        Friendship.status == 'accepted',
        paired(Friendship.requester_id, Friendship.addressee_id),
    ).first() is not None:
        return True

    # Once a legitimate conversation exists, either participant can continue
    # it even after a time-limited check-in or group membership disappears.
    if Message.query.filter(
        Message.recipient_id.isnot(None),
        paired(Message.sender_id, Message.recipient_id),
    ).first() is not None:
        return True

    def shares_membership(model, scope_column, user_column):
        scopes = db.session.query(scope_column).filter(
            user_column == user_a_id,
        )
        return model.query.filter(
            user_column == user_b_id,
            scope_column.in_(scopes),
        ).first() is not None

    if shares_membership(GamePlayer, GamePlayer.game_id, GamePlayer.user_id):
        return True
    if shares_membership(ClubMember, ClubMember.club_id, ClubMember.user_id):
        return True
    if shares_membership(LeagueMember, LeagueMember.league_id, LeagueMember.user_id):
        return True

    tournament_ids = db.session.query(TournamentEntry.tournament_id).filter(
        db.or_(
            TournamentEntry.player1_id == user_a_id,
            TournamentEntry.player2_id == user_a_id,
        ),
    )
    if TournamentEntry.query.filter(
        TournamentEntry.tournament_id.in_(tournament_ids),
        db.or_(
            TournamentEntry.player1_id == user_b_id,
            TournamentEntry.player2_id == user_b_id,
        ),
    ).first() is not None:
        return True

    # Crew owners are normally members too, but include ownership explicitly
    # so older rows and partially migrated groups preserve coordination.
    crew_ids_a = {
        int(row[0]) for row in db.session.query(CrewMember.crew_id).filter(
            CrewMember.user_id == user_a_id,
        ).all()
    } | {
        int(row[0]) for row in db.session.query(Crew.id).filter(
            Crew.owner_id == user_a_id,
        ).all()
    }
    if crew_ids_a and (
        CrewMember.query.filter(
            CrewMember.user_id == user_b_id,
            CrewMember.crew_id.in_(crew_ids_a),
        ).first() is not None
        or Crew.query.filter(
            Crew.owner_id == user_b_id,
            Crew.id.in_(crew_ids_a),
        ).first() is not None
    ):
        return True

    # A court encounter must have overlapped in time; merely choosing the same
    # home court does not expose a player to unsolicited messages.
    checkin_a = aliased(CheckIn)
    checkin_b = aliased(CheckIn)
    now = utcnow()
    overlap = db.session.query(checkin_a.id).join(
        checkin_b, checkin_b.court_id == checkin_a.court_id,
    ).filter(
        checkin_a.user_id == user_a_id,
        checkin_b.user_id == user_b_id,
        checkin_a.checked_in_at <= db.func.coalesce(checkin_b.checked_out_at, now),
        checkin_b.checked_in_at <= db.func.coalesce(checkin_a.checked_out_at, now),
    ).first()
    return overlap is not None


GROUP_KINDS = ('club', 'crew')
GROUP_PRIVACY_LEVELS = ('open', 'approval', 'invite')
CONVERSATION_KINDS = (
    'court', 'game', 'tournament', 'club', 'crew', 'league',
)


class Group(TimestampMixin, db.Model):
    """Canonical identity shared by public Communities and private groups.

    ``legacy_scope_id`` deliberately remains a polymorphic compatibility key
    during the additive migration. Existing Club/Crew URLs and foreign keys
    stay valid while every group also gains one durable, globally unique id.
    """
    __tablename__ = 'community_group'
    __table_args__ = (
        db.UniqueConstraint(
            'kind', 'legacy_scope_id', name='uq_community_group_legacy_scope',
        ),
        db.CheckConstraint(
            "kind IN ('club', 'crew')", name='ck_community_group_kind',
        ),
        db.CheckConstraint(
            "privacy IN ('open', 'approval', 'invite')",
            name='ck_community_group_privacy',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), nullable=False, index=True)
    privacy = db.Column(db.String(16), nullable=False, index=True)
    legacy_scope_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    owner_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='community_group_owner_id_fkey'),
        nullable=False,
        index=True,
    )
    home_court_id = db.Column(
        db.Integer,
        db.ForeignKey('court.id', name='community_group_home_court_id_fkey'),
        nullable=True,
        index=True,
    )
    archived_at = db.Column(db.DateTime, index=True)

    owner = db.relationship('User', foreign_keys=[owner_id])
    home_court = db.relationship('Court', foreign_keys=[home_court_id])

    def to_dict(self):
        return {
            'id': self.id,
            'kind': self.kind,
            'privacy': self.privacy,
            'scope_id': self.legacy_scope_id,
            'name': self.name,
            'description': self.description,
            'owner_id': self.owner_id,
            'home_court_id': self.home_court_id,
            'archived_at': iso(self.archived_at),
            'action_url': f'/#{self.kind}/{self.legacy_scope_id}',
        }


class Conversation(TimestampMixin, db.Model):
    """One durable room identity for all six non-direct chat scopes."""
    __table_args__ = (
        db.UniqueConstraint(
            'kind', 'scope_id', name='uq_conversation_scope',
        ),
        db.CheckConstraint(
            "kind IN ('court', 'game', 'tournament', 'club', 'crew', 'league')",
            name='ck_conversation_kind',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(24), nullable=False, index=True)
    scope_id = db.Column(db.Integer, nullable=False, index=True)
    group_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'community_group.id', ondelete='SET NULL',
            name='conversation_group_id_fkey',
        ),
        nullable=True,
        index=True,
    )

    group = db.relationship('Group', foreign_keys=[group_id])


class ConversationRead(TimestampMixin, db.Model):
    """Canonical per-user read position for every persisted room."""
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'conversation_id', name='uq_conversation_read',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='conversation_read_user_id_fkey'),
        nullable=False,
        index=True,
    )
    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'conversation.id', ondelete='CASCADE',
            name='conversation_read_conversation_id_fkey',
        ),
        nullable=False,
        index=True,
    )
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)

    conversation = db.relationship(
        'Conversation',
        backref=db.backref(
            'read_markers', cascade='all, delete-orphan', passive_deletes=True,
        ),
    )
    user = db.relationship('User')


class Message(TimestampMixin, db.Model):
    """A direct message (recipient_id), court-room message (court_id),
    game-thread message (game_id), tournament-thread message
    (tournament_id), club-room message (club_id), or private crew-room
    message (crew_id)."""
    __table_args__ = (
        db.Index(
            'uq_message_sender_attempt',
            'sender_id',
            'client_attempt_id',
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), index=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), index=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), index=True)
    club_id = db.Column(db.Integer, db.ForeignKey('club.id'), index=True)
    crew_id = db.Column(db.Integer, db.ForeignKey('crew.id'), index=True)
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'), index=True)
    # Canonical room identity. Legacy scope columns remain populated during the
    # rolling migration so older application instances can serve the same row.
    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey(
            'conversation.id', ondelete='SET NULL',
            name='message_conversation_id_fkey',
        ),
        nullable=True,
        index=True,
    )
    body = db.Column(db.Text, nullable=False, default='')
    # Optional photo (JPEG data URL, DM-only for now). Served via its own
    # endpoint so thread payloads stay light.
    image_data = db.Column(db.Text)
    read_at = db.Column(db.DateTime)
    # DM-only ❤️ from the recipient (rooms would need a table; DMs don't).
    hearted = db.Column(db.Boolean, nullable=False, default=False)
    # Stable per-send key generated on the device. Together these columns make
    # a response-lost/offline retry safe without making legacy unkeyed sends
    # globally unique. The fingerprint rejects accidental key reuse for a
    # different recipient, room, body, or photo.
    client_attempt_id = db.Column(db.String(64), nullable=True)
    client_attempt_fingerprint = db.Column(db.String(64), nullable=True)

    sender = db.relationship('User', foreign_keys=[sender_id])
    recipient = db.relationship('User', foreign_keys=[recipient_id])
    conversation = db.relationship('Conversation', foreign_keys=[conversation_id])

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
            'club_id': self.club_id,
            'crew_id': self.crew_id,
            'league_id': self.league_id,
            'body': self.body,
            'has_image': bool(self.image_data),
            'hearted': self.hearted,
            'heart_count': len(self.hearts),
            'heart_user_ids': [h.user_id for h in self.hearts],
            'client_attempt_id': self.client_attempt_id,
            'created_at': iso(self.created_at),
            'read_at': iso(self.read_at),
        }


class MessageSendAttempt(TimestampMixin, db.Model):
    """Durable reservation and terminal state for one device send attempt.

    The ledger exists from the first keyed send—not only after deletion—so a
    retry racing a hard delete can never slip between two table reads and
    recreate content the sender removed.
    """
    __table_args__ = (
        db.UniqueConstraint(
            'sender_id',
            'client_attempt_id',
            name='uq_message_send_attempt',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False,
        index=True,
    )
    client_attempt_id = db.Column(db.String(64), nullable=False)
    client_attempt_fingerprint = db.Column(db.String(64), nullable=True)
    message_id = db.Column(
        db.Integer,
        db.ForeignKey('message.id', ondelete='SET NULL'),
        nullable=True,
    )
    deleted_at = db.Column(db.DateTime)


class MessageHeart(TimestampMixin, db.Model):
    """A per-user ❤️ on a room-chat message (DMs use Message.hearted — two
    people only ever need a boolean). ondelete CASCADE so the bulk message
    purges (club disband etc.) don't strand rows on Postgres."""
    __table_args__ = (
        db.UniqueConstraint('message_id', 'user_id', name='uq_message_heart'),
    )
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey('message.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    message = db.relationship(
        'Message',
        backref=db.backref('hearts', lazy='selectin', cascade='all, delete-orphan',
                           passive_deletes=True),
    )


class UserReport(TimestampMixin, db.Model):
    """A player or one piece of their content flagged for safety review."""
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    reported_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    reason = db.Column(db.String(500), nullable=False, default='')
    details = db.Column(db.String(2000), nullable=False, default='')
    content_type = db.Column(db.String(32), nullable=False, default='user', index=True)
    content_id = db.Column(db.Integer, index=True)
    # Preserve the evidence an operator saw even if the author removes the
    # live object before the queue is reviewed.
    content_snapshot = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='open', index=True)
    assigned_operator_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    outcome = db.Column(db.String(1000), nullable=False, default='')
    resolved_at = db.Column(db.DateTime)

    reporter = db.relationship('User', foreign_keys=[reporter_id])
    reported = db.relationship('User', foreign_keys=[reported_id])
    assigned_operator = db.relationship('User', foreign_keys=[assigned_operator_id])

    def to_moderation_dict(self):
        return {
            'id': self.id,
            'kind': 'user_report',
            'reporter': self.reporter.to_public_dict() if self.reporter else None,
            'reported': self.reported.to_public_dict() if self.reported else None,
            'reason': self.reason,
            'details': self.details,
            'content_type': self.content_type or 'user',
            'content_id': self.content_id,
            'content_snapshot': self.content_snapshot,
            'status': self.status,
            'assigned_operator_id': self.assigned_operator_id,
            'outcome': self.outcome,
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
            'resolved_at': iso(self.resolved_at),
        }


class PlayerFeedback(TimestampMixin, db.Model):
    """Durable product feedback that operators can triage and close."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    message = db.Column(db.String(2000), nullable=False)
    context = db.Column(db.String(300), nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='open', index=True)
    assigned_operator_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    outcome = db.Column(db.String(1000), nullable=False, default='')
    resolved_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id])
    assigned_operator = db.relationship('User', foreign_keys=[assigned_operator_id])

    def to_moderation_dict(self):
        return {
            'id': self.id,
            'kind': 'feedback',
            'user': self.user.to_public_dict() if self.user else None,
            'message': self.message,
            'context': self.context,
            'status': self.status,
            'assigned_operator_id': self.assigned_operator_id,
            'outcome': self.outcome,
            'created_at': iso(self.created_at),
            'updated_at': iso(self.updated_at),
            'resolved_at': iso(self.resolved_at),
        }


class ModerationAction(TimestampMixin, db.Model):
    """Append-only audit record for every safety-queue mutation."""
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    user_report_id = db.Column(db.Integer, db.ForeignKey('user_report.id'), index=True)
    feedback_id = db.Column(db.Integer, db.ForeignKey('player_feedback.id'), index=True)
    action = db.Column(db.String(40), nullable=False, index=True)
    reason = db.Column(db.String(1000), nullable=False, default='')

    actor = db.relationship('User', foreign_keys=[actor_id])
    target_user = db.relationship('User', foreign_keys=[target_user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'actor_id': self.actor_id,
            'target_user_id': self.target_user_id,
            'user_report_id': self.user_report_id,
            'feedback_id': self.feedback_id,
            'action': self.action,
            'reason': self.reason,
            'created_at': iso(self.created_at),
        }


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


class DirectChatPreference(TimestampMixin, db.Model):
    """Per-partner notification preference for a direct conversation."""
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'partner_id', name='uq_direct_chat_preference',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    partner_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    muted_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            'muted': self.muted_at is not None,
            'muted_at': iso(self.muted_at),
        }


class CourtChatSubscription(TimestampMixin, db.Model):
    """Explicit court-room membership, independent of favorites and reads."""
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'court_id', name='uq_court_chat_subscription',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    court_id = db.Column(
        db.Integer, db.ForeignKey('court.id'), nullable=False, index=True,
    )
    joined_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    muted_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            'joined': True,
            'muted': self.muted_at is not None,
            'joined_at': iso(self.joined_at),
            'muted_at': iso(self.muted_at),
        }


class TournamentChatRead(TimestampMixin, db.Model):
    """How far a participant has read a tournament's chat thread — powers the
    unread badge on the tournament screen. No row = nothing read yet."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'tournament_id', name='uq_tournament_chat_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    tournament_id = db.Column(
        db.Integer, db.ForeignKey('tournament.id'), nullable=False, index=True,
    )
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
GAME_STATUSES = [
    'upcoming', 'awaiting_confirmation', 'completed', 'cancelled', 'expired',
    'unresolved',
]
EXPIRED_SCORE_GRACE_DAYS = 30
# Who can see / join a game:
#   open    -> anyone nearby
#   friends -> the creator's friends
#   private -> only specifically invited players
GAME_VISIBILITIES = ['open', 'friends', 'private']
# How a session repeats. ``weekly`` is the wire-compatible name for a local
# weekly pattern that may include several weekdays; ``none`` is a one-off.
GAME_RECURRENCES = ['none', 'weekly']


class Game(TimestampMixin, db.Model):
    __table_args__ = (
        db.Index(
            'uq_game_creator_attempt',
            'creator_id',
            'client_attempt_id',
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    # Stable per-submit key supplied by clients. The creator-scoped unique index
    # makes POST /games safe to retry while allowing different hosts to reuse a
    # UUID generated independently on their own devices.
    client_attempt_id = db.Column(db.String(64), nullable=True)
    # SHA-256 of the normalized immutable create request. It distinguishes a
    # legitimate retry from accidental reuse of the key for a different game.
    client_attempt_fingerprint = db.Column(db.String(64), nullable=True)
    # Set when the game is hosted on behalf of a club — members get pinged
    # and the game carries the club's tag.
    club_id = db.Column(db.Integer, db.ForeignKey('club.id'), index=True)
    # Immutable provenance for a private game assembled from an accepted Crew
    # roster. Later roster edits never rewrite the game's players or invites.
    crew_id = db.Column(db.Integer, db.ForeignKey('crew.id'), index=True)
    crew_roster_version = db.Column(db.Integer)
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    game_type = db.Column(db.String(20), nullable=False, default='casual')
    visibility = db.Column(db.String(16), nullable=False, default='open', index=True)
    recurrence = db.Column(db.String(16), nullable=False, default='none')
    # Weekly patterns are anchored to a wall-clock time in an IANA timezone.
    # Storing the local rule separately from scheduled_at prevents a UTC hour
    # from drifting when daylight-saving time changes.
    recurrence_timezone = db.Column(
        db.String(64), nullable=False, default='UTC', server_default='UTC',
    )
    recurrence_local_time = db.Column(
        db.String(5), nullable=False, default='', server_default='',
    )
    recurrence_weekdays = db.Column(
        db.Text, nullable=False, default='[]', server_default='[]',
    )
    recurrence_ends_on = db.Column(db.Date)
    max_players = db.Column(db.Integer, nullable=False, default=4)
    # Optional planning details for scheduled sessions. Empty/null defaults keep
    # every pre-upgrade game valid while allowing hosts to describe larger open
    # play without overloading the legacy free-form note.
    title = db.Column(
        db.String(120), nullable=False, default='', server_default='',
    )
    description = db.Column(
        db.String(1000), nullable=False, default='', server_default='',
    )
    duration_minutes = db.Column(db.Integer)
    cost_cents = db.Column(db.Integer)
    court_number = db.Column(
        db.String(40), nullable=False, default='', server_default='',
    )
    court_count = db.Column(db.Integer)
    # Hosts can pause automatic FIFO promotion while they review the visible
    # waitlist.  Existing games retain the historical auto-fill behaviour.
    auto_fill_waitlist = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true(),
    )
    notes = db.Column(db.String(500), nullable=False, default='')
    # Server-owned direct-challenge semantics.  ``NULL`` is reserved for rows
    # written by a legacy application process during a rolling schema upgrade;
    # new code always writes an explicit boolean.  That tri-state lets the
    # compatibility fallback recognize old sword-prefixed challenges without
    # allowing user-authored notes to override an explicit ``False`` value.
    is_challenge = db.Column(db.Boolean, nullable=True, default=False)
    # Expectation-setting for open games: 'any' or one of SKILL_LEVELS.
    preferred_level = db.Column(db.String(16), nullable=False, default='any')
    # Inclusive player-facing pickleball range. The categorical field remains
    # for rolling-client compatibility, while discovery and new editors use
    # these standard 2.0–5.5 values.
    level_min = db.Column(db.Float)
    level_max = db.Column(db.Float)
    # Server-owned provenance for the one-tap, at-court assembly flow.  It is
    # deliberately separate from notes and scheduled_at: ordinary open games
    # must never be absorbed into an instant rally merely because they happen
    # to start around the same time.
    is_instant = db.Column(db.Boolean, nullable=False, default=False, index=True)
    # One-way close for the physical assembly phase. Multi-player rows remain
    # upcoming so their participants can enter a result, but can never be
    # resurrected into Play Now discovery after presence lapses/replacement.
    assembly_closed_at = db.Column(db.DateTime)
    # 32 chars: must fit 'awaiting_confirmation' (Postgres enforces this, SQLite doesn't)
    status = db.Column(db.String(32), nullable=False, default='upcoming', index=True)
    score_team1 = db.Column(db.Integer)
    score_team2 = db.Column(db.Integer)
    score_submitted_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    score_submitted_at = db.Column(db.DateTime)
    # Durable confirmation provenance. ``timeout`` is deliberately distinct
    # from a player tap so result history can explain what happened and offer
    # the bounded late-dispute path only for unattended confirmations.
    score_confirmation_kind = db.Column(
        db.String(16), nullable=False, default='', server_default='',
    )
    score_confirmed_by_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', name='game_score_confirmed_by_id_fkey'),
    )
    score_confirmation_reminded_at = db.Column(db.DateTime)
    # Count opposing-player disagreements for the current result.  The first
    # dispute opens a counter-score round; a second closes the result as
    # unresolved without ever applying rating changes.
    score_dispute_count = db.Column(
        db.Integer, nullable=False, default=0, server_default='0',
    )
    score_dispute_reason = db.Column(
        db.String(500), nullable=False, default='', server_default='',
    )
    completed_at = db.Column(db.DateTime)

    court = db.relationship('Court', back_populates='games')
    creator = db.relationship('User', foreign_keys=[creator_id])
    club = db.relationship('Club', foreign_keys=[club_id])
    crew = db.relationship('Crew', foreign_keys=[crew_id])
    score_submitted_by = db.relationship('User', foreign_keys=[score_submitted_by_id])
    score_confirmed_by = db.relationship('User', foreign_keys=[score_confirmed_by_id])
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
    open_calls = db.relationship(
        'GameOpenCall', back_populates='game', lazy='selectin',
        order_by='GameOpenCall.id', cascade='all, delete-orphan',
        passive_deletes=True,
    )
    arrival_intents = db.relationship(
        'GameArrivalIntent', back_populates='game', lazy='dynamic',
        order_by='GameArrivalIntent.id', cascade='all, delete-orphan',
    )
    mvp_votes = db.relationship(
        'GameMvpVote', back_populates='game', lazy='selectin',
        cascade='all, delete-orphan',
    )
    recurrence_rsvps = db.relationship(
        'GameRecurrenceRsvp', back_populates='game', lazy='selectin',
        cascade='all, delete-orphan',
    )
    score_lines = db.relationship(
        'GameScoreLine', back_populates='game', lazy='selectin',
        order_by='GameScoreLine.game_number', cascade='all, delete-orphan',
    )

    def invited_user_ids(self):
        return {inv.user_id for inv in self.invites}

    def active_open_call(self):
        """The one durable court call currently attached to this game.

        Ended rows remain as retry/audit history, so callers must not assume
        the newest historical row is still the live call.
        """
        return next(
            (call for call in reversed(self.open_calls) if call.active),
            None,
        )

    @property
    def is_direct_challenge(self):
        """Return semantic challenge state with a narrow legacy fallback.

        New and migrated rows carry an explicit boolean.  A ``NULL`` can only
        come from an older process that inserted during a rolling upgrade, so
        only then may the historical note marker participate in detection.
        The rest of the original challenge shape keeps an arbitrary sword in
        an ordinary game's notes from changing application behavior.
        """
        if self.is_challenge is not None:
            return bool(self.is_challenge)
        return bool(
            self.game_type == 'ranked'
            and self.visibility == 'private'
            and self.max_players == 2
            and str(self.notes or '').startswith('⚔')
        )

    def visible_to(self, user_id, friend_ids=None):
        """Whether this game should appear to a given viewer in public/nearby feeds."""
        if user_id and (self.creator_id == user_id
                        or any(p.user_id == user_id for p in self.players)):
            return True
        # A direct invitation remains an explicit visibility grant even when
        # the broader audience is ``friends`` or ``open``. Crew-hosted casual
        # sessions use this to include every accepted group member without
        # requiring each member to also be the host's direct friend.
        if user_id and user_id in self.invited_user_ids():
            return True
        if self.visibility == 'open':
            return True
        if self.visibility == 'friends':
            return bool(user_id and friend_ids and self.creator_id in friend_ids)
        if self.visibility == 'private':
            return bool(user_id and user_id in self.invited_user_ids())
        return False

    @property
    def completion_kind(self):
        """Distinguish a scored match from an unscored group-session record."""
        if self.status != 'completed':
            return None
        if self.score_team1 is not None and self.score_team2 is not None:
            return 'score'
        return 'session'

    def to_dict(self, viewer_id=None, perspective_user_id=None, *,
                slim_players=False):
        """Serialize a game for ``viewer_id``.

        Most callers want the viewer's own result perspective, so it remains
        the default. Public profiles are the exception: privacy-sensitive
        fields still belong to the requester while win/rating fields describe
        the profile owner.
        """
        if perspective_user_id is None:
            perspective_user_id = viewer_id
        players = sorted(self.players, key=lambda p: p.id)
        now = utcnow()
        visible_crew = bool(
            self.crew
            and not self.crew.archived_at
            and viewer_id
            and self.crew.is_member(viewer_id)
        )
        viewer = next((p for p in players if p.user_id == viewer_id), None)
        perspective = next(
            (p for p in players if p.user_id == perspective_user_id), None,
        )
        submitter = next(
            (p for p in players if p.user_id == self.score_submitted_by_id), None,
        )
        # A player on the opposing team of whoever reported the score confirms it.
        # If the reporter wasn't on a team (scorekeeper), any other assigned player can.
        awaiting_mine = bool(
            self.status == 'awaiting_confirmation'
            and viewer and submitter and viewer.team
            and viewer.user_id != submitter.user_id
            and (not submitter.team or viewer.team != submitter.team)
        )
        you_won = None
        if (
            self.status == 'completed' and perspective and perspective.team
            and self.score_team1 is not None and self.score_team2 is not None
        ):
            you_won = (
                (self.score_team1 > self.score_team2) == (perspective.team == 1)
            )
        spots_left = max(0, self.max_players - len(players))
        assembly_state = None
        if self.is_instant:
            if self.status != 'upcoming':
                assembly_state = 'closed'
            elif len(players) < 2:
                assembly_state = 'finding'
            elif spots_left:
                # A singles game can start with two people while the same
                # rally remains recruitable for doubles.
                assembly_state = 'ready'
            else:
                assembly_state = 'full'
        expired_score_deadline = (
            self.scheduled_at + timedelta(days=EXPIRED_SCORE_GRACE_DAYS)
            if self.status == 'expired' and self.scheduled_at else None
        )
        can_enter_score = bool(
            viewer
            and (
                self.status == 'upcoming'
                or (
                    self.status == 'expired'
                    and expired_score_deadline
                    and expired_score_deadline >= now
                )
            )
            and self.recurrence == 'none'
            and len(players) >= 2
            and self.max_players <= 4
            # Instant games are happening now by provenance, not by comparing
            # a client clock to their scheduled timestamp.
            and (self.is_instant or self.scheduled_at <= now)
        )
        can_complete_session = bool(
            viewer
            and self.status == 'upcoming'
            and self.game_type == 'casual'
            and self.recurrence == 'none'
            and len(players) >= 2
            and self.scheduled_at <= now
        )
        attendance_confirmed_count = sum(
            player.attendance_confirmed() for player in players
        )
        attendance_unconfirmed_count = (
            len(players) - attendance_confirmed_count
        )
        attendance_confirmation_due = bool(
            viewer
            and viewer.user_id != self.creator_id
            and viewer.attendance_confirmation_requested_at() is not None
            and not viewer.attendance_confirmed()
            and not self.is_instant
            and self.status == 'upcoming'
            and self.scheduled_at > now
        )
        open_call = self.active_open_call()
        try:
            recurrence_weekdays = json.loads(self.recurrence_weekdays or '[]')
        except (TypeError, ValueError):
            recurrence_weekdays = []
        recurrence_weekdays = [
            value for value in recurrence_weekdays
            if value in {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}
        ]
        recurrence_zone = self.recurrence_timezone or 'UTC'
        try:
            zone = ZoneInfo(recurrence_zone)
        except (ZoneInfoNotFoundError, ValueError):
            recurrence_zone = 'UTC'
            zone = UTC
        occurrence_on = (
            self.scheduled_at.replace(tzinfo=UTC).astimezone(zone).date()
            if self.scheduled_at else None
        )
        my_recurrence = next(
            (
                row for row in self.recurrence_rsvps
                if viewer_id and row.user_id == viewer_id
            ),
            None,
        )
        personal_invite = next(
            (
                invite for invite in self.invites
                if viewer_id and invite.user_id == viewer_id
            ),
            None,
        )
        score_auto_confirms_at = (
            self.score_submitted_at + timedelta(hours=GAME_SCORE_AUTO_CONFIRM_HOURS)
            if self.status == 'awaiting_confirmation' and self.score_submitted_at
            else None
        )
        score_confirmed_by = (
            'timeout'
            if self.score_confirmation_kind == 'timeout'
            else self.score_confirmed_by_id
        )
        late_dispute_deadline = (
            self.completed_at + timedelta(days=GAME_SCORE_LATE_DISPUTE_DAYS)
            if (
                self.status == 'completed'
                and self.game_type == 'ranked'
                and self.score_confirmation_kind == 'timeout'
                and self.completed_at
            )
            else None
        )
        can_late_dispute = bool(
            late_dispute_deadline
            and viewer
            and viewer_id != self.score_submitted_by_id
            and viewer.team in (1, 2)
            and submitter
            and submitter.team in (1, 2)
            and viewer.team != submitter.team
            and now <= late_dispute_deadline
        )
        casual_correction_deadline = (
            self.completed_at + timedelta(
                minutes=GAME_CASUAL_SCORE_CORRECTION_MINUTES,
            )
            if (
                self.status == 'completed'
                and self.game_type == 'casual'
                and self.completion_kind == 'score'
                and self.completed_at
            )
            else None
        )
        waitlist_position = next(
            (i + 1 for i, row in enumerate(self.waitlist) if row.user_id == viewer_id),
            None,
        )
        score_games = [row.to_dict() for row in self.score_lines]
        # Rows written before the score-ledger rollout remain fully readable.
        # A legacy result is one game; new multi-game matches always carry the
        # child rows needed to distinguish game points from match wins.
        if (
            not score_games
            and self.score_team1 is not None
            and self.score_team2 is not None
        ):
            score_games = [{
                'game_number': 1,
                'score_team1': self.score_team1,
                'score_team2': self.score_team2,
            }]
        match_wins_team1 = sum(
            row['score_team1'] > row['score_team2'] for row in score_games
        )
        match_wins_team2 = sum(
            row['score_team2'] > row['score_team1'] for row in score_games
        )
        # Queue identities are roster-management data: hosts may review and
        # promote people, while other players see only the count/their position.
        waitlist_people = []
        if self.creator_id == viewer_id:
            waitlist_people = [
                {
                    **row.user.to_public_dict(),
                    'user_id': row.user_id,
                    'position': index + 1,
                }
                for index, row in enumerate(self.waitlist)
                if row.user and not row.user.deleted_at
            ]
        return {
            'id': self.id,
            'court': self.court.to_summary_dict() if self.court else None,
            'creator_id': self.creator_id,
            'club_id': self.club_id,
            'club_name': self.club.name if self.club else None,
            'crew_id': self.crew_id if visible_crew else None,
            'crew_name': self.crew.name if visible_crew else None,
            'crew_roster_version': self.crew_roster_version if visible_crew else None,
            'scheduled_at': iso(self.scheduled_at),
            'game_type': self.game_type,
            'visibility': self.visibility,
            'recurrence': self.recurrence,
            'recurrence_timezone': recurrence_zone,
            'recurrence_local_time': self.recurrence_local_time or '',
            'recurrence_weekdays': recurrence_weekdays,
            'recurrence_ends_on': (
                self.recurrence_ends_on.isoformat()
                if self.recurrence_ends_on else None
            ),
            'recurrence_occurrence_on': (
                occurrence_on.isoformat() if occurrence_on else None
            ),
            'my_recurrence_rsvp': (
                my_recurrence.to_dict(occurrence_on)
                if my_recurrence else None
            ),
            'max_players': self.max_players,
            'title': self.title or '',
            'description': self.description or '',
            'duration_minutes': self.duration_minutes,
            'ends_at': iso(
                self.scheduled_at + timedelta(minutes=self.duration_minutes)
            ) if self.scheduled_at and self.duration_minutes else None,
            'cost_cents': self.cost_cents,
            'court_number': self.court_number or '',
            'court_count': self.court_count,
            'auto_fill_waitlist': bool(self.auto_fill_waitlist),
            'notes': self.notes,
            'is_challenge': self.is_direct_challenge,
            'preferred_level': self.preferred_level,
            'level_min': self.level_min,
            'level_max': self.level_max,
            'is_instant': bool(self.is_instant),
            'assembly_state': assembly_state,
            'can_enter_score': can_enter_score,
            'expired_score_deadline_at': iso(expired_score_deadline),
            'can_complete_session': can_complete_session,
            'completion_kind': self.completion_kind,
            'status': self.status,
            'score_team1': self.score_team1,
            'score_team2': self.score_team2,
            'score_games': score_games,
            'match_score_team1': match_wins_team1 if score_games else None,
            'match_score_team2': match_wins_team2 if score_games else None,
            'score_submitted_by': self.score_submitted_by_id,
            'score_submitted_by_name': (
                submitter.user.display_name if submitter and submitter.user else None
            ),
            'score_submitted_at': iso(self.score_submitted_at),
            'score_confirmation_kind': self.score_confirmation_kind or None,
            'score_confirmed_by': score_confirmed_by,
            'score_confirmed_by_name': (
                self.score_confirmed_by.display_name
                if self.score_confirmed_by else None
            ),
            'confirmed_automatically': self.score_confirmation_kind == 'timeout',
            'score_confirmation_reminded_at': iso(
                self.score_confirmation_reminded_at,
            ),
            'score_auto_confirms_at': iso(score_auto_confirms_at),
            'score_auto_confirm_seconds': max(
                0, int((score_auto_confirms_at - now).total_seconds()),
            ) if score_auto_confirms_at else None,
            'score_dispute_count': int(self.score_dispute_count or 0),
            'score_dispute_reason': self.score_dispute_reason or '',
            'late_dispute_deadline_at': iso(late_dispute_deadline),
            'can_late_dispute': can_late_dispute,
            'can_fix_score': bool(
                viewer_id == self.score_submitted_by_id
                and (
                    self.status == 'awaiting_confirmation'
                    or (
                        casual_correction_deadline
                        and now <= casual_correction_deadline
                    )
                )
            ),
            'score_correction_deadline_at': iso(casual_correction_deadline),
            'awaiting_your_confirmation': awaiting_mine,
            'your_rating_delta': perspective.rating_delta if perspective else None,
            'you_won': you_won,
            'completed_at': iso(self.completed_at),
            'players': [
                p.to_summary_dict() if slim_players else p.to_dict()
                for p in players
            ],
            'attendance_confirmed_count': attendance_confirmed_count,
            'attendance_unconfirmed_count': attendance_unconfirmed_count,
            'attendance_confirmation_due': attendance_confirmation_due,
            'spots_left': spots_left,
            'is_joined': viewer is not None,
            'is_creator': self.creator_id == viewer_id,
            'waitlist_count': len(self.waitlist),
            'waitlist_position': waitlist_position,
            'waitlist_people': waitlist_people,
            'is_invited': personal_invite is not None,
            'my_invite_status': 'pending' if personal_invite else None,
            'invited_by': (
                (
                    self.creator.to_summary_dict()
                    if slim_players else self.creator.to_public_dict()
                )
                if personal_invite and self.creator and not self.creator.deleted_at
                else None
            ),
            'open_call': (
                open_call.to_dict(viewer_id) if open_call else None
            ),
            'my_mvp_vote': next(
                (v.votee_id for v in self.mvp_votes if v.voter_id == viewer_id),
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


class GameScoreLine(TimestampMixin, db.Model):
    """One played game inside a singles/doubles match result.

    ``Game.score_team*`` remains the compact, indexed-compatible match result
    used by legacy clients. This ledger preserves every actual game score and
    lets the server derive the match winner instead of trusting a client-side
    summary.
    """
    __table_args__ = (
        db.UniqueConstraint(
            'game_id', 'game_number', name='uq_game_score_line_number',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(
        db.Integer,
        db.ForeignKey('game.id', name='game_score_line_game_id_fkey'),
        nullable=False,
        index=True,
    )
    game_number = db.Column(db.Integer, nullable=False)
    score_team1 = db.Column(db.Integer, nullable=False)
    score_team2 = db.Column(db.Integer, nullable=False)

    game = db.relationship('Game', back_populates='score_lines')

    def to_dict(self):
        return {
            'game_number': self.game_number,
            'score_team1': self.score_team1,
            'score_team2': self.score_team2,
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

    def attendance_confirmation_requested_at(self):
        """Return the one reminder that asks this player to recommit.

        Prefer the day-before reminder. The hour reminder becomes the request
        only when the day sweep never reached this occurrence, so a player is
        never made to confirm twice for the same game.
        """
        return self.day_reminded_at or self.reminded_at

    def attendance_confirmed(self):
        if self.attending_at is None:
            return False
        # Scheduling the game is always the creator's active commitment.
        if self.game and self.user_id == self.game.creator_id:
            return True
        requested_at = self.attendance_confirmation_requested_at()
        return requested_at is None or self.attending_at >= requested_at

    def to_dict(self):
        data = self.user.to_public_dict() if self.user else {'id': self.user_id}
        data['user_id'] = self.user_id
        data['team'] = self.team
        data['rating_delta'] = self.rating_delta
        data['attending'] = self.attendance_confirmed()
        data['attendance_confirmation_requested_at'] = iso(
            self.attendance_confirmation_requested_at()
        )
        return data

    def to_summary_dict(self):
        data = (
            self.user.to_summary_dict()
            if self.user else {'id': self.user_id, 'user_id': self.user_id}
        )
        data['team'] = self.team
        data['rating_delta'] = self.rating_delta
        data['attending'] = self.attendance_confirmed()
        data['attendance_confirmation_requested_at'] = iso(
            self.attendance_confirmation_requested_at()
        )
        return data


class GameRecurrenceRsvp(TimestampMixin, db.Model):
    """A player's durable preference across occurrences of one game series."""
    __table_args__ = (
        db.UniqueConstraint(
            'game_id', 'user_id', name='uq_game_recurrence_rsvp',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(
        db.Integer, db.ForeignKey('game.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    standing_rsvp = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false(),
    )
    skipped_occurrence_on = db.Column(db.Date)
    last_rsvp_occurrence_on = db.Column(db.Date)

    game = db.relationship('Game', back_populates='recurrence_rsvps')
    user = db.relationship('User')

    def to_dict(self, occurrence_on=None):
        return {
            'standing_rsvp': bool(self.standing_rsvp),
            'skipped_occurrence_on': (
                self.skipped_occurrence_on.isoformat()
                if self.skipped_occurrence_on else None
            ),
            'last_rsvp_occurrence_on': (
                self.last_rsvp_occurrence_on.isoformat()
                if self.last_rsvp_occurrence_on else None
            ),
            'is_skipped': bool(
                occurrence_on
                and self.skipped_occurrence_on == occurrence_on
            ),
        }


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
    """A personal game invite and explicit visibility grant for its recipient."""
    __table_args__ = (
        db.UniqueConstraint('game_id', 'user_id', name='uq_game_invite'),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    game = db.relationship('Game', back_populates='invites')
    user = db.relationship('User')


class GameOpenCall(TimestampMixin, db.Model):
    """A typed, game-linked recruiting card posted into one court room.

    The call never reserves capacity and never grants visibility by itself.
    Its linked ``Game`` remains authoritative for time, roster, joinability,
    and waitlist state. Ended rows are retained so a lost-response retry can
    never create a second court message.
    """
    __table_args__ = (
        db.Index(
            'uq_game_open_call_active_game',
            'game_id',
            unique=True,
            postgresql_where=db.text('active IS TRUE'),
            sqlite_where=db.text('active = 1'),
        ),
        db.UniqueConstraint(
            'created_by_id', 'client_attempt_id',
            name='uq_game_open_call_creator_attempt',
        ),
        db.UniqueConstraint(
            'game_id', 'created_by_id',
            name='uq_game_open_call_game_creator',
        ),
        db.UniqueConstraint(
            'court_message_id', name='uq_game_open_call_message',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(
        db.Integer, db.ForeignKey('game.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    court_message_id = db.Column(
        db.Integer,
        db.ForeignKey('message.id', ondelete='SET NULL'),
        nullable=True,
    )
    client_attempt_id = db.Column(db.String(64), nullable=False)
    client_attempt_fingerprint = db.Column(db.String(64), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    ended_at = db.Column(db.DateTime)
    end_reason = db.Column(db.String(32), nullable=False, default='')

    game = db.relationship('Game', back_populates='open_calls')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    court_message = db.relationship('Message', foreign_keys=[court_message_id])

    def live_state(self, now=None):
        """Return the query-time card state without mutating history."""
        now = now or utcnow()
        game = self.game
        if not self.active or self.ended_at is not None:
            return 'withdrawn' if self.end_reason in {
                'host_withdrew', 'message_deleted',
            } else 'closed'
        if (
            not game
            or game.creator_id != self.created_by_id
            or game.status != 'upcoming'
            or game.visibility != 'open'
            or game.is_instant
            or game.recurrence != 'none'
            or not game.court
            or game.court.closed
            or game.scheduled_at < now - timedelta(hours=2)
        ):
            return 'closed'
        if len(game.players) >= game.max_players:
            return 'full'
        return 'open'

    def to_dict(self, viewer_id=None, now=None):
        now = now or utcnow()
        game = self.game
        state = self.live_state(now)
        player_count = len(game.players) if game else 0
        spots_left = max(0, game.max_players - player_count) if game else 0
        viewer_joined = bool(
            viewer_id and game
            and any(player.user_id == viewer_id for player in game.players)
        )
        viewer_waitlisted = bool(
            viewer_id and game
            and any(entry.user_id == viewer_id for entry in game.waitlist)
        )
        return {
            'id': self.id,
            'game_id': self.game_id,
            'court_message_id': self.court_message_id,
            'created_by_id': self.created_by_id,
            'created_at': iso(self.created_at),
            'state': state,
            'active': state in {'open', 'full'},
            'end_reason': self.end_reason or '',
            'ended_at': iso(self.ended_at),
            'scheduled_at': iso(game.scheduled_at) if game else None,
            'game_type': game.game_type if game else None,
            'preferred_level': game.preferred_level if game else None,
            'max_players': game.max_players if game else 0,
            'player_count': player_count,
            'spots_left': spots_left,
            'waitlist_count': len(game.waitlist) if game else 0,
            'is_joined': viewer_joined,
            'can_join': bool(
                viewer_id and state == 'open'
                and not viewer_joined and not viewer_waitlisted
            ),
            'can_waitlist': bool(
                viewer_id and state == 'full'
                and not viewer_joined and not viewer_waitlisted
            ),
            'can_withdraw': bool(
                state in {'open', 'full'} and game
                and viewer_id == game.creator_id == self.created_by_id
            ),
        }


class GameArrivalIntent(TimestampMixin, db.Model):
    """A short-lived ETA status while somebody travels to a live game.

    Arrival is intentionally separate from both ``GamePlayer`` and ``CheckIn``:
    it does not reserve roster capacity, claim physical presence, or make an
    assembly live/ready. Ended rows remain as a compact idempotency ledger.
    The partial index enforces at most one active ETA per person; the actual
    game roster remains first-come and is serialized under the locked Game row.
    """
    __table_args__ = (
        db.Index(
            'uq_game_arrival_active_user',
            'user_id',
            unique=True,
            postgresql_where=db.text('active IS TRUE'),
            sqlite_where=db.text('active = 1'),
        ),
        db.UniqueConstraint(
            'user_id', 'client_attempt_id',
            name='uq_game_arrival_user_attempt',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(
        db.Integer, db.ForeignKey('game.id'), nullable=False, index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    eta_minutes = db.Column(db.Integer, nullable=False)
    declared_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    arrives_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    ended_at = db.Column(db.DateTime)
    end_reason = db.Column(db.String(32), nullable=False, default='')
    client_attempt_id = db.Column(db.String(64), nullable=False)
    client_attempt_fingerprint = db.Column(db.String(64), nullable=False)
    last_announced_at = db.Column(db.DateTime)

    game = db.relationship('Game', back_populates='arrival_intents')
    user = db.relationship('User')

    def to_dict(self, now=None):
        now = now or utcnow()
        public_end_reason = self.end_reason or ''
        if public_end_reason in {'blocked', 'creator_deleted'}:
            # The durable ledger keeps the operational reason, but an owned
            # idempotency replay must not become an oracle for another user's
            # block or account action.
            public_end_reason = 'rally_closed'
        return {
            'id': self.id,
            'game_id': self.game_id,
            'eta_minutes': self.eta_minutes,
            'declared_at': iso(self.declared_at),
            'arrives_at': iso(self.arrives_at),
            'expires_at': iso(self.expires_at),
            'active': bool(
                self.active and not self.ended_at and self.expires_at > now
            ),
            'end_reason': public_end_reason,
        }


class PlayAvailabilityPulse(TimestampMixin, db.Model):
    """A short, remote promise that a player can start a local game soon.

    A pulse is deliberately not a ``CheckIn`` (physical presence), a
    ``GamePlayer`` (a committed roster), or an “On my way” ETA status. The active
    row is discovery state; ended rows are the retry ledger that lets a lost
    publish or acceptance response converge without extending the original
    hour or creating a second game.
    """
    __table_args__ = (
        db.Index(
            'uq_play_availability_pulse_active_user',
            'user_id',
            unique=True,
            postgresql_where=db.text('active IS TRUE'),
            sqlite_where=db.text('active = 1'),
        ),
        db.UniqueConstraint(
            'user_id', 'client_attempt_id',
            name='uq_play_availability_pulse_user_attempt',
        ),
        db.UniqueConstraint(
            'accepted_by_id', 'accept_client_attempt_id',
            name='uq_play_availability_pulse_accept_attempt',
        ),
        db.CheckConstraint(
            'expires_at > declared_at',
            name='ck_play_availability_pulse_positive_window',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    court_id = db.Column(
        db.Integer, db.ForeignKey('court.id'), nullable=False, index=True,
    )
    declared_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    ended_at = db.Column(db.DateTime)
    end_reason = db.Column(db.String(32), nullable=False, default='')
    client_attempt_id = db.Column(db.String(64), nullable=False)
    client_attempt_fingerprint = db.Column(db.String(64), nullable=False)

    # Acceptance is recorded on the pulse as well as on Game's creator-scoped
    # idempotency key.  This makes exact retries recoverable after the pulse is
    # no longer discoverable and makes a reused key on another pulse conflict.
    accepted_by_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), index=True,
    )
    accept_client_attempt_id = db.Column(db.String(64))
    accept_client_attempt_fingerprint = db.Column(db.String(64))
    accepted_game_id = db.Column(
        db.Integer, db.ForeignKey('game.id'), index=True,
    )

    user = db.relationship('User', foreign_keys=[user_id])
    court = db.relationship('Court', foreign_keys=[court_id])
    accepted_by = db.relationship('User', foreign_keys=[accepted_by_id])
    accepted_game = db.relationship('Game', foreign_keys=[accepted_game_id])

    def to_dict(self, now=None):
        now = now or utcnow()
        return {
            'id': self.id,
            'court': self.court.to_summary_dict() if self.court else None,
            'declared_at': iso(self.declared_at),
            'expires_at': iso(self.expires_at),
            'active': bool(
                self.active and not self.ended_at and self.expires_at > now
            ),
            'ended_at': iso(self.ended_at),
            'end_reason': self.end_reason or '',
            'accepted_game_id': self.accepted_game_id,
        }


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
    """A community photo stored as a data URL because hosted container disks
    are commonly ephemeral. The newest one can double as the court hero."""
    id = db.Column(db.Integer, primary_key=True)
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    photo_data = db.Column(db.Text, nullable=False)
    caption = db.Column(db.String(140), nullable=False, default='')

    court = db.relationship('Court')
    user = db.relationship('User')


class CourtPhotoLike(TimestampMixin, db.Model):
    """One heart per player per court photo."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'photo_id', name='uq_court_photo_like'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    photo_id = db.Column(
        db.Integer, db.ForeignKey('court_photo.id'), nullable=False, index=True,
    )


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
            'updated_at': iso(self.updated_at),
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
    __table_args__ = (
        db.Index(
            'uq_notification_user_unread_topic',
            'user_id',
            'unread_dedupe_key',
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False, default='general')
    title = db.Column(db.String(255), nullable=False, default='')
    body = db.Column(db.Text, nullable=False, default='')
    read = db.Column(db.Boolean, nullable=False, default=False)
    related_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    related_game_id = db.Column(db.Integer, db.ForeignKey('game.id'))
    related_tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'))
    related_club_id = db.Column(db.Integer, db.ForeignKey('club.id'))
    related_crew_id = db.Column(db.Integer, db.ForeignKey('crew.id'))
    related_league_id = db.Column(db.Integer, db.ForeignKey('league.id'))
    # Same-origin app destination. Result notifications use a match-level hash
    # so both the activity feed and web push can open the exact action needed.
    action_url = db.Column(db.String(500), nullable=False, default='')
    # Present only while a collapsible notification is unread. NULL restores
    # unlimited history after the prior ping is read or cleared.
    unread_dedupe_key = db.Column(db.String(160), nullable=True)

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
            'related_club_id': self.related_club_id,
            'related_crew_id': self.related_crew_id,
            'related_league_id': self.related_league_id,
            'action_url': self.action_url or '',
            'created_at': iso(self.created_at),
        }


# Notification kinds a user may silence. Everything else (score confirmations,
# disputes, direct invites, challenges) is essential and always delivered.
MUTEABLE_NOTIFICATIONS = {
    'direct_message': 'Direct messages from other players',
    'court_game': 'New play sessions and matches at courts you saved',
    'friend_checkin': 'Friends checking in to play',
    'rally_arrival': 'Players on their way to your pickup games',
    'game_message': 'Play session and match chat messages',
    'tournament_message': 'Tournament chat messages',
    'club_message': 'Community chat messages',
    'crew_message': 'Play group chat messages',
    'club_game': 'New play sessions and matches from your Communities',
    'league_message': 'League chat messages',
    'session_rsvp': 'Weekly session re-RSVP reminders',
    'nearby_games': 'Weekly digest of pickup games and ranked matches near you',
    'streak_nag': 'Keep-your-streak weekend reminders',
    'weekly_recap': 'Your weekly recap',
}


def notify(user_id, kind, title, body='', related_user_id=None, related_game_id=None,
           related_tournament_id=None, related_club_id=None, related_crew_id=None,
           related_league_id=None, action_url='', unread_dedupe_key=''):
    # A block is a two-way privacy boundary, including background/push paths.
    if related_user_id and related_user_id != user_id \
            and is_blocked_between(user_id, related_user_id):
        return None
    # Respect the recipient's mute preferences for optional kinds.
    if kind in MUTEABLE_NOTIFICATIONS:
        recipient = db.session.get(User, user_id)
        if recipient and kind in recipient.muted_kinds():
            return
    destination = str(action_url or '').strip()
    # WHATWG URL parsing treats backslashes as slashes for HTTP(S), so a value
    # such as ``/\attacker.example`` is cross-origin even though it begins with
    # one forward slash. Persist only unambiguous same-origin paths.
    if (
        not destination.startswith('/')
        or destination.startswith('//')
        or '\\' in destination
        or any(ord(char) < 32 or ord(char) == 127 for char in destination)
    ):
        destination = ''
    dedupe_key = str(unread_dedupe_key or '').strip()[:160] or None
    notification = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        related_user_id=related_user_id,
        related_game_id=related_game_id,
        related_tournament_id=related_tournament_id,
        related_club_id=related_club_id,
        related_crew_id=related_crew_id,
        related_league_id=related_league_id,
        action_url=destination[:500],
        unread_dedupe_key=dedupe_key,
    )
    if dedupe_key:
        # The savepoint keeps a simultaneous second message from rolling its
        # own Message transaction back when another request wins this unread
        # topic. Only the winner schedules a push.
        try:
            with db.session.begin_nested():
                db.session.add(notification)
                db.session.flush()
        except IntegrityError:
            return None
    else:
        db.session.add(notification)
    # Mirror to the user's devices only after this transaction commits. This
    # keeps a rolled-back notification from escaping through the push worker.
    try:
        from backend.services.push import defer_to_user_after_commit
        defer_to_user_after_commit(user_id, title, body, action_url=destination)
    except Exception:
        pass  # push is best-effort; never break the transaction
    return notification


COMPETITION_RESULT_STATES = (
    'unreported', 'awaiting_confirmation', 'disputed', 'confirmed', 'bye', 'void',
)
COMPETITION_TYPES = ('tournament', 'league')
COMPETITION_RESULT_ACTIONS = (
    'reported', 'confirmed', 'disputed', 'resolved', 'corrected', 'voided',
    'auto_confirmed', 'legacy_imported',
)


class CompetitionResultEvent(db.Model):
    """Immutable-by-convention audit entry for a competition result.

    Tournament and league match ids live in separate tables, so the
    competition type is part of the durable identity. Result writers append a
    new, monotonically-versioned row for every report, confirmation, dispute,
    resolution, or correction; existing rows are never rewritten.
    """
    __table_args__ = (
        db.UniqueConstraint(
            'competition_type', 'match_id', 'version',
            name='uq_competition_result_event_version',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    competition_type = db.Column(db.String(20), nullable=False, index=True)
    match_id = db.Column(db.Integer, nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    action = db.Column(db.String(32), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    score1 = db.Column(db.Integer)
    score2 = db.Column(db.Integer)
    reason = db.Column(db.String(500), nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    actor = db.relationship('User', foreign_keys=[actor_id], lazy='joined')

    def to_dict(self, include_reason=True):
        return {
            'id': self.id,
            'competition_type': self.competition_type,
            'match_id': self.match_id,
            'actor_id': self.actor_id,
            'actor_name': self.actor.display_name if self.actor else None,
            'action': self.action,
            'version': self.version,
            'score1': self.score1,
            'score2': self.score2,
            'reason': self.reason if include_reason else None,
            'created_at': iso(self.created_at),
        }

    @classmethod
    def record(cls, competition_type, match_id, action, version, actor_id=None,
               score1=None, score2=None, reason='', created_at=None):
        """Append an event to the current transaction; the caller commits.

        The unique match/version constraint detects duplicate lifecycle writes;
        route writers should pair it with a conditional version update.
        """
        if competition_type not in COMPETITION_TYPES:
            raise ValueError('invalid_competition_type')
        if action not in COMPETITION_RESULT_ACTIONS:
            raise ValueError('invalid_result_action')
        if not match_id or int(version) < 1:
            raise ValueError('invalid_result_event_identity')
        event = cls(
            competition_type=competition_type,
            match_id=match_id,
            actor_id=actor_id,
            action=action,
            version=int(version),
            score1=score1,
            score2=score2,
            reason=str(reason or '')[:500],
            created_at=created_at or utcnow(),
        )
        db.session.add(event)
        return event

    @classmethod
    def grouped_for_matches(cls, competition_type, match_ids):
        ids = [match_id for match_id in match_ids if match_id is not None]
        if not ids:
            return {}
        grouped = {match_id: [] for match_id in ids}
        events = (
            cls.query
            .filter(
                cls.competition_type == competition_type,
                cls.match_id.in_(ids),
            )
            .order_by(cls.match_id.asc(), cls.version.asc(), cls.id.asc())
            .all()
        )
        for event in events:
            grouped.setdefault(event.match_id, []).append(event)
        return grouped


TOURNAMENT_FORMATS = ['single_elim', 'round_robin']
TOURNAMENT_EVENT_TYPES = ['singles', 'doubles']
TOURNAMENT_GAME_FORMATS = ['single_11', 'single_15', 'best_of_3_11']
TOURNAMENT_STATUSES = ['registration', 'active', 'completed', 'cancelled']
TOURNAMENT_PARTNER_STATUSES = ['accepted', 'pending', 'needed']
TOURNAMENT_PARTNER_PENDING_ON = ['invitee', 'owner']


class Tournament(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    organizer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    # Set when the tournament runs under a club's banner.
    club_id = db.Column(db.Integer, db.ForeignKey('club.id'), index=True)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    format = db.Column(db.String(20), nullable=False, default='single_elim')
    event_type = db.Column(db.String(20), nullable=False, default='singles')
    # A tournament is one enforceable skill division. Organizers can create
    # parallel tournaments when an event needs more than one division.
    division_name = db.Column(db.String(80), nullable=False, default='Open')
    division_min_rating = db.Column(db.Float)
    division_max_rating = db.Column(db.Float)
    game_format = db.Column(db.String(32), nullable=False, default='single_11')
    court_count = db.Column(db.Integer, nullable=False, default=1)
    match_minutes = db.Column(db.Integer, nullable=False, default=30)
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
    club = db.relationship('Club', foreign_keys=[club_id])
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

    def partner_action_for(self, user_id):
        """Return the pending doubles entry whose next decision belongs to user."""
        return next((
            entry for entry in self.entries
            if entry.partner_status == 'pending'
            and (
                entry.partner_pending_on == 'invitee'
                and entry.partner_invitee_id == user_id
                or entry.partner_pending_on == 'owner'
                and entry.player1_id == user_id
            )
        ), None)

    def partner_offer_for(self, user_id):
        """Return an offer this unregistered player is waiting on."""
        return next((
            entry for entry in self.entries
            if entry.partner_status == 'pending'
            and entry.partner_pending_on == 'owner'
            and entry.partner_invitee_id == user_id
        ), None)

    def participant_ids(self):
        ids = set()
        for entry in self.entries:
            ids.add(entry.player1_id)
            if entry.player2_id:
                ids.add(entry.player2_id)
        return ids

    def to_dict(self, current_user_id=None, detail=False):
        my_entry = self.entry_for(current_user_id) if current_user_id else None
        partner_action = self.partner_action_for(current_user_id) \
            if current_user_id else None
        partner_offer = self.partner_offer_for(current_user_id) \
            if current_user_id else None
        ready_entries = sum(entry.partner_ready(self.event_type) for entry in self.entries)
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'format': self.format,
            'event_type': self.event_type,
            'division_name': self.division_name or 'Open',
            'division_min_rating': self.division_min_rating,
            'division_max_rating': self.division_max_rating,
            'game_format': self.game_format or 'single_11',
            'court_count': self.court_count or 1,
            'match_minutes': self.match_minutes or 30,
            'status': self.status,
            'ranked': bool(self.ranked),
            'starts_at': iso(self.starts_at),
            'max_entries': self.max_entries,
            'court': self.court.to_summary_dict() if self.court else None,
            'organizer_id': self.organizer_id,
            'organizer_name': self.organizer.display_name if self.organizer else None,
            'club_id': self.club_id,
            'club_name': self.club.name if self.club else None,
            'entry_count': len(self.entries),
            'ready_entry_count': ready_entries,
            'pending_partner_count': sum(
                entry.partner_status == 'pending' for entry in self.entries
            ) if self.event_type == 'doubles' else 0,
            'partner_pool_count': sum(
                entry.partner_status == 'needed' for entry in self.entries
            ) if self.event_type == 'doubles' else 0,
            'is_organizer': self.organizer_id == current_user_id,
            'my_entry_id': my_entry.id if my_entry else None,
            'my_partner_action': partner_action.partner_action_dict(current_user_id)
            if partner_action else None,
            'my_pending_partner_offer': partner_offer.partner_action_dict(current_user_id)
            if partner_offer else None,
            'champion': self.champion_entry.to_dict() if self.champion_entry else None,
            'completed_at': iso(self.completed_at),
        }
        if detail:
            data['entries'] = [
                e.to_dict(current_user_id, self.organizer_id)
                for e in self.entries
            ]
            event_groups = CompetitionResultEvent.grouped_for_matches(
                'tournament', [m.id for m in self.matches],
            )
            data['matches'] = [
                m.to_dict(
                    current_user_id,
                    result_events=event_groups.get(m.id, []),
                )
                for m in self.matches
            ]
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
    # A doubles slot is not silently granted to another person. During
    # registration, the proposed teammate lives here until the person named by
    # ``partner_pending_on`` accepts. ``player2_id`` is populated only after
    # that consent. Existing rows default to accepted for backward compatibility.
    partner_invitee_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    partner_status = db.Column(
        db.String(20), nullable=False, default='accepted',
    )
    partner_pending_on = db.Column(db.String(20), nullable=False, default='')
    seed = db.Column(db.Integer)
    # Day-of arrival confirmation ("we're here"), settable from 24h out.
    checked_in_at = db.Column(db.DateTime)

    tournament = db.relationship(
        'Tournament', back_populates='entries', foreign_keys=[tournament_id],
    )
    player1 = db.relationship('User', foreign_keys=[player1_id])
    player2 = db.relationship('User', foreign_keys=[player2_id])
    partner_invitee = db.relationship('User', foreign_keys=[partner_invitee_id])

    def players(self):
        return [p for p in (self.player1, self.player2) if p]

    def display_name(self):
        names = [p.display_name for p in self.players()]
        return ' & '.join(names) if names else 'Entry'

    def avg_rating(self):
        ratings = [p.rating for p in self.players()]
        return sum(ratings) // len(ratings) if ratings else DEFAULT_RATING

    def partner_ready(self, event_type='doubles'):
        if event_type != 'doubles':
            return True
        # Old rows predate partner_status but already have an accepted player2.
        return bool(self.player2_id and self.partner_status == 'accepted')

    def partner_action_dict(self, current_user_id=None):
        candidate = self.partner_invitee
        decision_for_me = bool(
            current_user_id and self.partner_status == 'pending'
            and (
                self.partner_pending_on == 'invitee'
                and self.partner_invitee_id == current_user_id
                or self.partner_pending_on == 'owner'
                and self.player1_id == current_user_id
            )
        )
        return {
            'entry_id': self.id,
            'status': self.partner_status,
            'pending_on': self.partner_pending_on or None,
            'decision_for_me': decision_for_me,
            'owner': self.player1.to_public_dict() if self.player1 else None,
            'candidate': candidate.to_public_dict() if candidate else None,
        }

    def to_dict(self, current_user_id=None, organizer_id=None):
        is_doubles = bool(self.tournament and self.tournament.event_type == 'doubles')
        status = 'accepted' if self.player2_id else self.partner_status
        name = self.display_name()
        if is_doubles and not self.player2_id:
            name = f'{name} · ' + (
                'partner invited' if status == 'pending'
                else 'needs a partner'
            )
        data = {
            'id': self.id,
            'seed': self.seed,
            'name': name,
            'rating': self.avg_rating(),
            'checked_in': self.checked_in_at is not None,
            'players': [p.to_public_dict() for p in self.players()],
            'partner_status': status,
            'partner_ready': not is_doubles or bool(
                self.player2_id and status == 'accepted'
            ),
            'needs_partner': is_doubles and status == 'needed',
            'partner_invite_pending': is_doubles and status == 'pending',
        }
        if self.partner_invitee and current_user_id in {
            self.player1_id, self.partner_invitee_id, organizer_id,
        }:
            data['pending_partner'] = self.partner_invitee.to_public_dict()
            data['partner_pending_on'] = self.partner_pending_on or None
        return data


class TournamentMatch(TimestampMixin, db.Model):
    """A bracket slot. Single-elim: winner of (round r, position p) feeds
    (round r+1, position p//2), slot 1 when p is even. Round robin: every
    pairing exists up front, grouped into rounds by the circle method."""
    __table_args__ = (
        db.Index(
            'ix_tournament_match_result_state_reported_at',
            'result_state', 'reported_at',
        ),
    )

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
    result_state = db.Column(
        db.String(32), nullable=False, default='unreported', index=True,
    )
    result_version = db.Column(db.Integer, nullable=False, default=0)
    reported_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reported_at = db.Column(db.DateTime)
    confirmed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    confirmed_at = db.Column(db.DateTime)
    disputed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    disputed_at = db.Column(db.DateTime)
    dispute_reason = db.Column(db.String(500), nullable=False, default='')
    resolution_kind = db.Column(db.String(32), nullable=False, default='')
    review_reminded_at = db.Column(db.DateTime)
    stall_alerted_at = db.Column(db.DateTime)
    last_nudged_at = db.Column(db.DateTime)
    # Scheduling is generated when the bracket is created and can then be
    # adjusted by the organizer without rewriting the bracket itself.
    scheduled_at = db.Column(db.DateTime)
    court_number = db.Column(db.Integer)
    # JSON array of {score1, score2}; aggregate game wins stay in score1/score2
    # so existing standings, rating, and result-history code remains valid.
    game_scores_json = db.Column(db.Text, nullable=False, default='[]')

    tournament = db.relationship('Tournament', back_populates='matches')
    entry1 = db.relationship('TournamentEntry', foreign_keys=[entry1_id])
    entry2 = db.relationship('TournamentEntry', foreign_keys=[entry2_id])
    winner_entry = db.relationship('TournamentEntry', foreign_keys=[winner_entry_id])
    reported_by = db.relationship('User', foreign_keys=[reported_by_id])
    confirmed_by = db.relationship('User', foreign_keys=[confirmed_by_id])
    disputed_by = db.relationship('User', foreign_keys=[disputed_by_id])

    def effective_result_state(self):
        """Compatibility view while legacy score routes still write winners.

        The durable field is authoritative for provisional states. A persisted
        winner remains terminal even when an older route did not yet maintain
        the new lifecycle columns.
        """
        if self.winner_entry_id is not None:
            if self.score1 is None and self.score2 is None and (
                self.entry1_id is None or self.entry2_id is None
            ):
                return 'bye'
            return 'confirmed'
        return self.result_state or 'unreported'

    def status(self):
        result_state = self.effective_result_state()
        if result_state in ('awaiting_confirmation', 'disputed', 'void'):
            return result_state
        if self.winner_entry_id is not None:
            # A bye never had two entries or a score.
            return 'bye' if self.score1 is None and (
                self.entry1_id is None or self.entry2_id is None) else 'done'
        if self.entry1_id is not None and self.entry2_id is not None:
            return 'ready'
        return 'pending'

    def game_scores(self):
        try:
            rows = json.loads(self.game_scores_json or '[]')
        except (TypeError, ValueError):
            return []
        if not isinstance(rows, list):
            return []
        return [
            {'score1': int(row['score1']), 'score2': int(row['score2'])}
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get('score1'), int)
            and not isinstance(row.get('score1'), bool)
            and isinstance(row.get('score2'), int)
            and not isinstance(row.get('score2'), bool)
        ]

    def to_dict(self, current_user_id=None, result_events=None):
        tournament = self.tournament
        active = bool(tournament and tournament.status == 'active')
        organizer = bool(
            current_user_id and tournament
            and tournament.organizer_id == current_user_id
        )
        viewer_entry_id = next(
            (
                entry.id for entry in (self.entry1, self.entry2)
                if entry and current_user_id is not None
                and current_user_id in (entry.player1_id, entry.player2_id)
            ),
            None,
        )
        reporter_entry_id = next(
            (
                entry.id for entry in (self.entry1, self.entry2)
                if entry and self.reported_by_id is not None
                and self.reported_by_id in (entry.player1_id, entry.player2_id)
            ),
            None,
        )
        is_participant = viewer_entry_id is not None
        state = self.effective_result_state()
        awaiting_mine = bool(
            active and state == 'awaiting_confirmation' and is_participant
            and current_user_id != self.reported_by_id
            and (
                reporter_entry_id is None
                or viewer_entry_id != reporter_entry_id
            )
        )
        can_audit = bool(organizer or is_participant)
        if result_events is None and can_audit and self.id is not None:
            result_events = CompetitionResultEvent.grouped_for_matches(
                'tournament', [self.id],
            ).get(self.id, [])
        history = [
            event.to_dict(include_reason=True)
            for event in (result_events or [])
        ] if can_audit else []

        return {
            'id': self.id,
            'round': self.round,
            'position': self.position,
            'entry1_id': self.entry1_id,
            'entry2_id': self.entry2_id,
            'score1': self.score1,
            'score2': self.score2,
            'game_scores': self.game_scores(),
            'scheduled_at': iso(self.scheduled_at),
            'court_number': self.court_number,
            'winner_entry_id': self.winner_entry_id,
            'status': self.status(),
            'result_state': state,
            'result_version': self.result_version or 0,
            'reported_by_id': self.reported_by_id,
            'reported_by_name': self.reported_by.display_name if self.reported_by else None,
            'reported_at': iso(self.reported_at),
            'confirmed_by_id': self.confirmed_by_id,
            'confirmed_by_name': self.confirmed_by.display_name if self.confirmed_by else None,
            'confirmed_at': iso(self.confirmed_at),
            'disputed_by_id': self.disputed_by_id,
            'disputed_by_name': self.disputed_by.display_name if self.disputed_by else None,
            'disputed_at': iso(self.disputed_at),
            'dispute_reason': self.dispute_reason if can_audit else None,
            'resolution_kind': self.resolution_kind,
            'confirmed_automatically': bool(
                state == 'confirmed'
                and self.resolution_kind == 'automatic_timeout'
            ),
            'awaiting_your_confirmation': awaiting_mine,
            'can_report_result': bool(
                active and state in ('unreported', 'disputed')
                and (organizer or is_participant)
                and self.entry1_id is not None and self.entry2_id is not None
            ),
            'can_confirm_result': awaiting_mine,
            'can_dispute_result': awaiting_mine,
            'can_resolve_result': bool(
                active and organizer
                and state in ('awaiting_confirmation', 'disputed')
            ),
            'can_correct_result': bool(
                active and organizer and state == 'confirmed'
            ),
            'result_history': history,
        }


class Club(TimestampMixin, db.Model):
    """A player-created group — a named crew that plays together. Members get
    a private chat room; a club can claim a home court for discovery."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    # Owner-pinned notice shown at the top of the club screen.
    announcement = db.Column(db.String(500), nullable=False, default='')
    announcement_author_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=True,
    )
    announcement_posted_at = db.Column(db.DateTime)
    # Public Communities may either admit players immediately or let the
    # organizer approve requests.  Keep ``open`` as the migration-safe default.
    join_policy = db.Column(db.String(16), nullable=False, default='open')
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    home_court_id = db.Column(db.Integer, db.ForeignKey('court.id'), index=True)
    # Watermark for the weekly activity digest (see clubs.send_club_digests).
    last_digest_at = db.Column(db.DateTime)
    # Closing a Community is recoverable. Archived rows are excluded from
    # discovery and normal membership APIs but retain their room/history.
    archived_at = db.Column(db.DateTime, index=True)

    creator = db.relationship('User', foreign_keys=[creator_id])
    announcement_author = db.relationship(
        'User', foreign_keys=[announcement_author_id],
    )
    home_court = db.relationship('Court', foreign_keys=[home_court_id])
    members = db.relationship(
        'ClubMember', back_populates='club', cascade='all, delete-orphan',
    )

    def member_ids(self):
        return {m.user_id for m in self.members}

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'announcement': self.announcement,
            'announcement_author_id': self.announcement_author_id,
            'announcement_author_name': (
                self.announcement_author.display_name
                if self.announcement_author else None
            ),
            'announcement_posted_at': iso(self.announcement_posted_at),
            'join_policy': self.join_policy or 'open',
            'member_count': len(self.members),
            'home_court_id': self.home_court_id,
            'home_court_name': self.home_court.name if self.home_court else None,
            'home_court_city': self.home_court.city if self.home_court else None,
            'created_at': iso(self.created_at),
            'archived_at': iso(self.archived_at),
        }


class ClubMember(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('club_id', 'user_id', name='uq_club_member'),
    )

    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(db.Integer, db.ForeignKey('club.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    # 'owner', 'admin', or 'member'; exactly one owner per club.
    role = db.Column(db.String(16), nullable=False, default='member')
    # all = every room message, mentions = direct @mentions only, off = quiet.
    notification_level = db.Column(db.String(16), nullable=False, default='all')

    club = db.relationship('Club', back_populates='members')
    user = db.relationship('User')


class ClubJoinRequest(TimestampMixin, db.Model):
    """Durable approval request for a request-to-join Community."""
    __table_args__ = (
        db.UniqueConstraint('club_id', 'user_id', name='uq_club_join_request'),
    )

    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(
        db.Integer, db.ForeignKey('club.id'), nullable=False, index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    status = db.Column(db.String(16), nullable=False, default='pending', index=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    resolved_at = db.Column(db.DateTime)

    club = db.relationship('Club')
    user = db.relationship('User', foreign_keys=[user_id])
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])


class ClubBan(TimestampMixin, db.Model):
    """A durable organizer block preventing a removed member from rejoining."""
    __table_args__ = (
        db.UniqueConstraint('club_id', 'user_id', name='uq_club_ban'),
    )

    id = db.Column(db.Integer, primary_key=True)
    club_id = db.Column(
        db.Integer, db.ForeignKey('club.id'), nullable=False, index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    banned_by_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False,
    )
    reason = db.Column(db.String(300), nullable=False, default='')

    club = db.relationship('Club')
    user = db.relationship('User', foreign_keys=[user_id])
    banned_by = db.relationship('User', foreign_keys=[banned_by_id])


class ClubChatRead(TimestampMixin, db.Model):
    """How far a member has read their club's chat — powers unread badges in
    the Chat tab. No row until they open that chat once."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'club_id', name='uq_club_chat_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    club_id = db.Column(db.Integer, db.ForeignKey('club.id'), nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)


class Crew(TimestampMixin, db.Model):
    """A small, private, consent-based group of recurring local players.

    Unlike a Club, a Crew is never discoverable or open-join. ``owner_id`` is
    the single management authority; accepted non-owner players live in
    CrewMember and pending consent lives separately in CrewInvite. A Crew can
    begin directly among friends or retain provenance from a completed game.
    """
    __table_args__ = (
        db.UniqueConstraint('source_game_id', name='uq_crew_source_game'),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    # Kept as an integer rather than a circular FK: Game already points back to
    # Crew for immutable provenance and create routes validate the source row.
    source_game_id = db.Column(db.Integer, nullable=True, index=True)
    default_court_id = db.Column(db.Integer, db.ForeignKey('court.id'), index=True)
    roster_version = db.Column(db.Integer, nullable=False, default=1)
    archived_at = db.Column(db.DateTime, index=True)

    owner = db.relationship('User', foreign_keys=[owner_id])
    default_court = db.relationship('Court', foreign_keys=[default_court_id])
    members = db.relationship(
        'CrewMember', back_populates='crew', lazy='selectin',
        cascade='all, delete-orphan',
    )
    invites = db.relationship(
        'CrewInvite', back_populates='crew', lazy='selectin',
        cascade='all, delete-orphan',
    )

    def member_ids(self):
        return {self.owner_id} | {member.user_id for member in self.members}

    def is_member(self, user_id):
        return user_id in self.member_ids()

    def to_summary_dict(self, current_user_id=None):
        member_ids = self.member_ids()
        return {
            'id': self.id,
            'name': self.name,
            'owner_id': self.owner_id,
            'is_owner': current_user_id == self.owner_id,
            'joined': current_user_id in member_ids if current_user_id else False,
            'member_count': len(member_ids),
            'pending_count': sum(1 for invite in self.invites if invite.status == 'pending'),
            'source_game_id': self.source_game_id,
            'default_court_id': self.default_court_id,
            'default_court_name': self.default_court.name if self.default_court else None,
            'default_court_city': self.default_court.city if self.default_court else None,
            'roster_version': self.roster_version,
            'created_at': iso(self.created_at),
        }


class CrewMember(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('crew_id', 'user_id', name='uq_crew_member'),
    )

    id = db.Column(db.Integer, primary_key=True)
    crew_id = db.Column(db.Integer, db.ForeignKey('crew.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    crew = db.relationship('Crew', back_populates='members')
    user = db.relationship('User')


class CrewInvite(TimestampMixin, db.Model):
    """Durable Crew consent; notification rows are only delivery hints."""
    __table_args__ = (
        db.UniqueConstraint('crew_id', 'invitee_id', name='uq_crew_invitee'),
    )

    id = db.Column(db.Integer, primary_key=True)
    crew_id = db.Column(db.Integer, db.ForeignKey('crew.id'), nullable=False, index=True)
    invitee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='pending', index=True)
    resolved_at = db.Column(db.DateTime)

    crew = db.relationship('Crew', back_populates='invites')
    invitee = db.relationship('User', foreign_keys=[invitee_id])
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])


class CrewChatRead(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('user_id', 'crew_id', name='uq_crew_chat_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    crew_id = db.Column(db.Integer, db.ForeignKey('crew.id'), nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)
    # Per-player room preference. Keeping it beside the read marker gives both
    # owners and accepted members one durable, unique preference row.
    # all = every room message, mentions = direct @mentions only, off = quiet.
    notification_level = db.Column(db.String(16), nullable=False, default='all')


class PushSubscription(TimestampMixin, db.Model):
    """A browser push endpoint for one of a user's devices. Dead endpoints
    (410/404 from the push service) are pruned automatically on send."""
    __table_args__ = (
        db.Index('uq_push_subscription_endpoint', 'endpoint', unique=True),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)

    user = db.relationship('User')

    def subscription_info(self):
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }


class PushOutbox(TimestampMixin, db.Model):
    """Durable web-push intent drained by scheduled maintenance.

    The row is written in the same transaction as its Notification, so a
    rollback cannot leak a push and a serverless process ending cannot lose it.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    payload = db.Column(db.Text, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    available_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    sent_at = db.Column(db.DateTime, index=True)
    failed_at = db.Column(db.DateTime, index=True)
    last_error = db.Column(db.String(500), nullable=False, default='')
    delivered_subscription_ids = db.Column(db.Text, nullable=False, default='[]')

    user = db.relationship('User')

    def delivered_ids(self):
        try:
            values = json.loads(self.delivered_subscription_ids or '[]')
        except (TypeError, ValueError):
            return set()
        return {int(value) for value in values if str(value).isdigit()}


LEAGUE_STATUSES = ['registration', 'active', 'completed', 'cancelled']


class League(TimestampMixin, db.Model):
    """A box league: players are grouped into rating-seeded boxes and play a
    round robin within their box each round; box winners move up, last place
    moves down. Runs until the organizer completes it."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    court_id = db.Column(db.Integer, db.ForeignKey('court.id'), nullable=False, index=True)
    organizer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    # Set when the league runs under a club's banner.
    club_id = db.Column(db.Integer, db.ForeignKey('club.id'), index=True)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    box_size = db.Column(db.Integer, nullable=False, default=4)
    round_days = db.Column(db.Integer, nullable=False, default=7)
    max_players = db.Column(db.Integer, nullable=False, default=16)
    status = db.Column(db.String(20), nullable=False, default='registration', index=True)
    current_round = db.Column(db.Integer, nullable=False, default=0)
    # When the current round opened — the auto-advance sweep closes the round
    # once round_days have elapsed.
    round_started_at = db.Column(db.DateTime)
    deadline_alerted_round = db.Column(db.Integer, nullable=False, default=0)
    champion_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    completed_at = db.Column(db.DateTime)

    court = db.relationship('Court')
    organizer = db.relationship('User', foreign_keys=[organizer_id])
    club = db.relationship('Club', foreign_keys=[club_id])
    champion = db.relationship('User', foreign_keys=[champion_user_id])
    members = db.relationship(
        'LeagueMember', back_populates='league', lazy='selectin',
        order_by='LeagueMember.id', cascade='all, delete-orphan',
    )
    matches = db.relationship(
        'LeagueMatch', back_populates='league', lazy='selectin',
        order_by='(LeagueMatch.round, LeagueMatch.box, LeagueMatch.id)',
        cascade='all, delete-orphan',
    )

    def member_for(self, user_id):
        return next((m for m in self.members if m.user_id == user_id), None)

    def to_dict(self, current_user_id=None, detail=False, detail_match_id=None):
        mine = self.member_for(current_user_id) if current_user_id else None
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'court': self.court.to_summary_dict() if self.court else None,
            'organizer_id': self.organizer_id,
            'organizer_name': self.organizer.display_name if self.organizer else None,
            'club_id': self.club_id,
            'club_name': self.club.name if self.club else None,
            'starts_at': iso(self.starts_at),
            'box_size': self.box_size,
            'round_days': self.round_days,
            'max_players': self.max_players,
            'status': self.status,
            'current_round': self.current_round,
            'member_count': len(self.members),
            'joined': mine is not None,
            'my_box': mine.box if mine else None,
            'is_organizer': self.organizer_id == current_user_id,
            'round_started_at': iso(self.round_started_at),
            'champion_user_id': self.champion_user_id,
            'champion_name': self.champion.display_name if self.champion else None,
            'completed_at': iso(self.completed_at),
        }
        if detail:
            data['members'] = [m.to_dict() for m in self.members]
            current_matches = [
                m for m in self.matches if m.round == self.current_round
            ]
            if detail_match_id:
                requested = next(
                    (m for m in self.matches if m.id == detail_match_id), None,
                )
                if requested is not None and requested not in current_matches:
                    current_matches.append(requested)
            event_groups = CompetitionResultEvent.grouped_for_matches(
                'league', [m.id for m in current_matches],
            )
            data['matches'] = [
                m.to_dict(
                    current_user_id,
                    result_events=event_groups.get(m.id, []),
                )
                for m in current_matches
            ]
        return data


class LeagueMember(TimestampMixin, db.Model):
    __table_args__ = (
        db.UniqueConstraint('league_id', 'user_id', name='uq_league_member'),
    )

    id = db.Column(db.Integer, primary_key=True)
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    box = db.Column(db.Integer)  # 1-based; NULL until the league starts
    points = db.Column(db.Integer, nullable=False, default=0)
    wins = db.Column(db.Integer, nullable=False, default=0)
    losses = db.Column(db.Integer, nullable=False, default=0)
    # Last round we nagged this member about unplayed matches (deadline pings).
    reminded_round = db.Column(db.Integer, nullable=False, default=0)

    league = db.relationship('League', back_populates='members')
    user = db.relationship('User')

    def to_dict(self):
        return {
            'user': self.user.to_public_dict() if self.user else None,
            'box': self.box,
            'points': self.points,
            'wins': self.wins,
            'losses': self.losses,
        }


class LeagueMatch(TimestampMixin, db.Model):
    __table_args__ = (
        db.Index(
            'ix_league_match_result_state_reported_at',
            'result_state', 'reported_at',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'), nullable=False, index=True)
    round = db.Column(db.Integer, nullable=False)
    box = db.Column(db.Integer, nullable=False)
    player1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score1 = db.Column(db.Integer)
    score2 = db.Column(db.Integer)
    winner_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reported_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    result_state = db.Column(
        db.String(32), nullable=False, default='unreported', index=True,
    )
    result_version = db.Column(db.Integer, nullable=False, default=0)
    reported_at = db.Column(db.DateTime)
    confirmed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    confirmed_at = db.Column(db.DateTime)
    disputed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    disputed_at = db.Column(db.DateTime)
    dispute_reason = db.Column(db.String(500), nullable=False, default='')
    resolution_kind = db.Column(db.String(32), nullable=False, default='')
    review_reminded_at = db.Column(db.DateTime)
    stall_alerted_at = db.Column(db.DateTime)
    last_nudged_at = db.Column(db.DateTime)

    league = db.relationship('League', back_populates='matches')
    player1 = db.relationship('User', foreign_keys=[player1_id])
    player2 = db.relationship('User', foreign_keys=[player2_id])
    reported_by = db.relationship('User', foreign_keys=[reported_by_id])
    confirmed_by = db.relationship('User', foreign_keys=[confirmed_by_id])
    disputed_by = db.relationship('User', foreign_keys=[disputed_by_id])

    def effective_result_state(self):
        # Until the score route adopts the lifecycle writer, its persisted
        # winner remains a backward-compatible confirmed result.
        if self.winner_id is not None:
            return 'confirmed'
        return self.result_state or 'unreported'

    def to_dict(self, current_user_id=None, result_events=None):
        def player(u):
            return {'id': u.id, 'display_name': u.display_name,
                    'avatar_color': u.avatar_color, 'avatar_url': u.avatar_url or '',
                    'rating': u.rating} if u else None

        league = self.league
        active = bool(
            league and league.status == 'active'
            and self.round == league.current_round
        )
        organizer = bool(
            current_user_id and league and league.organizer_id == current_user_id
        )
        is_participant = current_user_id in (self.player1_id, self.player2_id)
        state = self.effective_result_state()
        awaiting_mine = bool(
            active and state == 'awaiting_confirmation' and is_participant
            and current_user_id != self.reported_by_id
        )
        can_audit = bool(organizer or is_participant)
        if result_events is None and can_audit and self.id is not None:
            result_events = CompetitionResultEvent.grouped_for_matches(
                'league', [self.id],
            ).get(self.id, [])
        history = [
            event.to_dict(include_reason=True)
            for event in (result_events or [])
        ] if can_audit else []

        return {
            'id': self.id,
            'round': self.round,
            'box': self.box,
            'player1': player(self.player1),
            'player2': player(self.player2),
            'score1': self.score1,
            'score2': self.score2,
            'winner_id': self.winner_id,
            'status': state,
            'result_state': state,
            'result_version': self.result_version or 0,
            'reported_by_id': self.reported_by_id,
            'reported_by_name': self.reported_by.display_name if self.reported_by else None,
            'reported_at': iso(self.reported_at),
            'confirmed_by_id': self.confirmed_by_id,
            'confirmed_by_name': self.confirmed_by.display_name if self.confirmed_by else None,
            'confirmed_at': iso(self.confirmed_at),
            'disputed_by_id': self.disputed_by_id,
            'disputed_by_name': self.disputed_by.display_name if self.disputed_by else None,
            'disputed_at': iso(self.disputed_at),
            'dispute_reason': self.dispute_reason if can_audit else None,
            'resolution_kind': self.resolution_kind,
            'confirmed_automatically': bool(
                state == 'confirmed'
                and self.resolution_kind == 'automatic_timeout'
            ),
            'awaiting_your_confirmation': awaiting_mine,
            'can_report_result': bool(
                active and is_participant
                and state in ('unreported', 'disputed')
            ),
            'can_confirm_result': awaiting_mine,
            'can_dispute_result': awaiting_mine,
            'can_resolve_result': bool(
                active and organizer
                and state in ('awaiting_confirmation', 'disputed')
            ),
            'can_correct_result': bool(
                active and organizer and state == 'confirmed'
            ),
            'result_history': history,
        }


class LeagueChatRead(TimestampMixin, db.Model):
    """How far a member has read their league's chat — powers unread badges.
    No row until they open that chat once."""
    __table_args__ = (
        db.UniqueConstraint('user_id', 'league_id', name='uq_league_chat_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    league_id = db.Column(db.Integer, db.ForeignKey('league.id'), nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)
