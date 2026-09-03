#!/usr/bin/env python3
"""Apply and verify Third Shot's additive schema upgrades on PostgreSQL.

Runtime/serverless instances intentionally never run DDL. This operator-only
command reads Neon's direct connection URL from TARGET_DATABASE_URL, refuses a
pooled endpoint or a database without the existing ``picklepals`` app schema,
runs the application's idempotent additive migrations, and verifies every
table, column, index, foreign key, and uniqueness constraint required by this
release.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PG_SCHEMA = 'picklepals'
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_TABLES = {
    'user', 'court', 'check_in', 'game', 'message', 'notification',
}
REQUIRED_COLUMNS = {
    'user': {
        'auth_version', 'operator_role', 'mfa_secret_encrypted',
        'mfa_enabled', 'mfa_enabled_at', 'mfa_recovery_codes',
        'skill_rating', 'dupr_rating', 'dupr_id', 'email_verified_at', 'avatar_data',
        'onboarding_completed_at',
        'nearby_visibility',
        'invited_by_user_id',
        'suspended_at', 'suspension_reason', 'suspended_by_id',
    },
    'court': {
        'structured_hours', 'hours_dawn_to_dusk', 'reservation_url',
        'fee_type', 'open_play_schedule_rows',
    },
    'account_action_token': {
        'id', 'created_at', 'updated_at', 'user_id', 'purpose', 'token_hash',
        'pending_email', 'expires_at', 'consumed_at',
    },
    'push_subscription': {
        'id', 'created_at', 'updated_at', 'user_id', 'endpoint', 'p256dh', 'auth',
    },
    'push_outbox': {
        'id', 'created_at', 'updated_at', 'user_id', 'payload', 'attempts',
        'available_at', 'sent_at', 'failed_at', 'last_error',
        'delivered_subscription_ids',
    },
    'user_report': {
        'id', 'created_at', 'updated_at', 'reporter_id', 'reported_id',
        'reason', 'details', 'content_type', 'content_id', 'content_snapshot',
        'status', 'assigned_operator_id', 'outcome', 'resolved_at',
    },
    'player_feedback': {
        'id', 'created_at', 'updated_at', 'user_id', 'message', 'context',
        'status', 'assigned_operator_id', 'outcome', 'resolved_at',
    },
    'moderation_action': {
        'id', 'created_at', 'updated_at', 'actor_id', 'target_user_id',
        'user_report_id', 'feedback_id', 'action', 'reason',
    },
    'business_profile': {
        'id', 'created_at', 'updated_at', 'owner_id', 'court_id', 'name',
        'claimant_role', 'claim_status', 'verified_at', 'published',
        'description', 'announcement', 'contact_email', 'contact_phone',
        'hours', 'amenities', 'website_url', 'booking_url',
        'membership_url', 'logo_url', 'logo_data', 'organization_id',
        'governance_status', 'suspension_reason', 'suspended_at',
        'suspended_by', 'content_review_status', 'content_reviewed_at',
    },
    'business_claim': {
        'id', 'created_at', 'updated_at', 'user_id', 'court_id',
        'business_id', 'role', 'status', 'reviewed_at',
        'assigned_operator_id', 'assigned_operator_identifier', 'due_at',
        'claimant_feedback',
    },
    'business_claim_review_event': {
        'id', 'claim_id', 'reviewer_identifier', 'verification_method',
        'decision', 'review_note', 'ownership_transferred',
        'previous_owner_id', 'created_at',
    },
    'business_offering': {
        'id', 'created_at', 'updated_at', 'business_id', 'name', 'category',
        'description', 'price_text', 'duration_minutes', 'booking_url',
        'active', 'sort_order',
    },
    'business_schedule_item': {
        'id', 'created_at', 'updated_at', 'business_id', 'title', 'kind',
        'day_of_week', 'start_time', 'end_time', 'skill_level', 'booking_url',
        'active', 'sort_order', 'timezone', 'recurrence', 'start_date',
        'end_date', 'event_date', 'capacity', 'spots_remaining', 'status',
        'location_note', 'instructor', 'source_updated_at',
    },
    'business_integration_request': {
        'id', 'created_at', 'updated_at', 'business_id', 'requested_by_id',
        'provider', 'capabilities', 'details', 'contact_email', 'status',
        'handled_by', 'status_message', 'status_changed_at',
        'assigned_operator_id', 'assigned_operator_identifier', 'due_at',
    },
    'business_organization': {
        'id', 'name', 'created_by_id', 'created_at', 'updated_at',
    },
    'business_organization_member': {
        'id', 'organization_id', 'user_id', 'role', 'accepted_at',
        'created_at', 'updated_at',
    },
    'business_staff_invitation': {
        'id', 'organization_id', 'invited_by_id', 'email', 'role',
        'token_hash', 'status', 'expires_at', 'accepted_by_id', 'accepted_at',
        'created_at', 'updated_at',
    },
    'business_verification_evidence': {
        'id', 'claim_id', 'submitted_by_id', 'evidence_type',
        'evidence_value', 'note', 'domain_match', 'status',
        'challenge_token_hash', 'challenge_expires_at',
        'challenge_verified_at', 'challenge_failed_attempts',
        'challenge_locked_at', 'reviewed_by', 'review_note', 'reviewed_at',
        'created_at', 'updated_at',
    },
    'business_profile_revision': {
        'id', 'business_id', 'actor_user_id', 'action', 'change_summary',
        'previous_snapshot', 'snapshot', 'sensitive', 'review_status',
        'reviewer_identifier', 'review_note', 'reviewed_at',
        'restored_from_id', 'created_at',
    },
    'business_governance_event': {
        'id', 'business_id', 'actor_user_id', 'operator_identifier',
        'event_type', 'details', 'created_at',
    },
    'business_profile_report': {
        'id', 'business_id', 'reporter_id', 'category', 'details', 'status',
        'handled_by', 'status_message', 'status_changed_at',
        'assigned_operator_id', 'assigned_operator_identifier', 'due_at',
        'created_at', 'updated_at',
    },
    'business_operator_action': {
        'id', 'business_id', 'claim_id', 'action_type', 'payload', 'status',
        'proposed_by_id', 'confirmed_by_id', 'expires_at', 'created_at',
        'confirmed_at',
    },
    'operator_security_event': {
        'id', 'actor_identifier', 'target_user_id', 'action',
        'previous_role', 'new_role', 'reason', 'created_at',
    },
    'message': {'crew_id', 'conversation_id'},
    'community_group': {
        'id', 'created_at', 'updated_at', 'kind', 'privacy',
        'legacy_scope_id', 'name', 'description', 'owner_id',
        'home_court_id', 'archived_at',
    },
    'conversation': {
        'id', 'created_at', 'updated_at', 'kind', 'scope_id', 'group_id',
    },
    'conversation_read': {
        'id', 'created_at', 'updated_at', 'user_id', 'conversation_id',
        'last_read_message_id',
    },
    'game': {
        'crew_id', 'crew_roster_version', 'is_challenge', 'is_instant',
        'assembly_closed_at', 'creator_id', 'client_attempt_id',
        'client_attempt_fingerprint', 'title', 'description',
        'duration_minutes', 'cost_cents', 'court_number', 'court_count',
        'auto_fill_waitlist', 'score_dispute_count', 'score_dispute_reason',
        'score_confirmation_kind', 'score_confirmed_by_id',
        'score_confirmation_reminded_at',
        'recurrence_timezone', 'recurrence_local_time',
        'recurrence_weekdays', 'recurrence_ends_on',
        'level_min', 'level_max',
    },
    'game_recurrence_rsvp': {
        'id', 'created_at', 'updated_at', 'game_id', 'user_id',
        'standing_rsvp', 'skipped_occurrence_on',
        'last_rsvp_occurrence_on',
    },
    'game_score_line': {
        'id', 'created_at', 'updated_at', 'game_id', 'game_number',
        'score_team1', 'score_team2',
    },
    'check_in': {
        'user_id', 'court_id', 'looking_for_game', 'checked_in_at',
        'checked_out_at', 'last_presence_ping_at',
    },
    'game_arrival_intent': {
        'id', 'created_at', 'updated_at', 'game_id', 'user_id',
        'eta_minutes', 'declared_at', 'arrives_at', 'expires_at',
        'active', 'ended_at', 'end_reason', 'client_attempt_id',
        'client_attempt_fingerprint', 'last_announced_at',
    },
    'play_availability_pulse': {
        'id', 'created_at', 'updated_at', 'user_id', 'court_id',
        'declared_at', 'expires_at', 'active', 'ended_at', 'end_reason',
        'client_attempt_id', 'client_attempt_fingerprint', 'accepted_by_id',
        'accept_client_attempt_id', 'accept_client_attempt_fingerprint',
        'accepted_game_id',
    },
    'game_open_call': {
        'id', 'created_at', 'updated_at', 'game_id', 'created_by_id',
        'court_message_id', 'client_attempt_id',
        'client_attempt_fingerprint', 'active', 'ended_at', 'end_reason',
    },
    'notification': {'related_crew_id'},
    'crew': {
        'id', 'created_at', 'updated_at', 'owner_id', 'name',
        'source_game_id', 'default_court_id', 'roster_version', 'archived_at',
    },
    'crew_member': {'id', 'created_at', 'updated_at', 'crew_id', 'user_id'},
    'crew_invite': {
        'id', 'created_at', 'updated_at', 'crew_id', 'invitee_id',
        'invited_by_id', 'status', 'resolved_at',
    },
    'crew_chat_read': {
        'id', 'created_at', 'updated_at', 'user_id', 'crew_id',
        'last_read_message_id', 'notification_level',
    },
    'court_chat_subscription': {
        'id', 'created_at', 'updated_at', 'user_id', 'court_id',
        'joined_at', 'muted_at',
    },
    'direct_chat_preference': {
        'id', 'created_at', 'updated_at', 'user_id', 'partner_id', 'muted_at',
    },
    'club': {
        'id', 'announcement', 'announcement_author_id',
        'announcement_posted_at', 'join_policy', 'archived_at',
    },
    'club_member': {'id', 'club_id', 'user_id', 'role', 'notification_level'},
    'club_join_request': {
        'id', 'created_at', 'updated_at', 'club_id', 'user_id', 'status',
        'resolved_by_id', 'resolved_at',
    },
    'club_ban': {
        'id', 'created_at', 'updated_at', 'club_id', 'user_id',
        'banned_by_id', 'reason',
    },
    'tournament_entry': {
        'id', 'tournament_id', 'player1_id', 'player2_id', 'checked_in_at',
        'partner_invitee_id', 'partner_status', 'partner_pending_on',
    },
    'tournament': {
        'id', 'division_name', 'division_min_rating', 'division_max_rating',
        'game_format', 'court_count', 'match_minutes',
    },
    'tournament_match': {
        'id', 'tournament_id', 'result_state', 'result_version',
        'reported_by_id', 'reported_at', 'confirmed_by_id', 'confirmed_at',
        'disputed_by_id', 'disputed_at', 'dispute_reason', 'resolution_kind',
        'review_reminded_at', 'stall_alerted_at', 'last_nudged_at',
        'scheduled_at', 'court_number', 'game_scores_json',
    },
    'league': {
        'id', 'current_round', 'round_started_at', 'deadline_alerted_round',
    },
    'league_match': {
        'id', 'league_id', 'result_state', 'result_version',
        'reported_by_id', 'reported_at', 'confirmed_by_id', 'confirmed_at',
        'disputed_by_id', 'disputed_at', 'dispute_reason', 'resolution_kind',
        'review_reminded_at', 'stall_alerted_at', 'last_nudged_at',
    },
    'competition_result_event': {
        'id', 'competition_type', 'match_id', 'actor_id', 'action', 'version',
        'score1', 'score2', 'reason', 'created_at',
    },
}
REQUIRED_INDEXES = {
    'user': {
        'ix_user_operator_role', 'ix_user_mfa_enabled',
        'ix_user_suspended_at', 'ix_user_invited_by_user_id',
        'ix_user_nearby_visibility',
    },
    'account_action_token': {
        'ix_account_action_token_user_id',
        'ix_account_action_token_purpose',
        'ix_account_action_token_token_hash',
        'ix_account_action_token_expires_at',
    },
    'push_subscription': {'ix_push_subscription_user_id'},
    'push_outbox': {
        'ix_push_outbox_user_id', 'ix_push_outbox_available_at',
        'ix_push_outbox_sent_at', 'ix_push_outbox_failed_at',
    },
    'user_report': {
        'ix_user_report_reporter_id', 'ix_user_report_reported_id',
        'ix_user_report_status', 'ix_user_report_assigned_operator_id',
        'ix_user_report_content_type', 'ix_user_report_content_id',
    },
    'player_feedback': {
        'ix_player_feedback_user_id', 'ix_player_feedback_status',
        'ix_player_feedback_assigned_operator_id',
    },
    'moderation_action': {
        'ix_moderation_action_actor_id', 'ix_moderation_action_target_user_id',
        'ix_moderation_action_user_report_id',
        'ix_moderation_action_feedback_id', 'ix_moderation_action_action',
    },
    'game_recurrence_rsvp': {
        'ix_game_recurrence_rsvp_game_id',
        'ix_game_recurrence_rsvp_user_id',
    },
    'game_score_line': {'ix_game_score_line_game_id'},
    'business_profile': {
        'ix_business_profile_owner_id', 'ix_business_profile_court_id',
        'ix_business_profile_claim_status', 'ix_business_profile_published',
        'ix_business_profile_organization_id',
        'ix_business_profile_governance_status',
        'ix_business_profile_content_review_status',
    },
    'business_claim': {
        'ix_business_claim_user_id', 'ix_business_claim_court_id',
        'ix_business_claim_business_id', 'ix_business_claim_status',
        'ix_business_claim_assigned_operator_id', 'ix_business_claim_due_at',
    },
    'business_claim_review_event': {
        'ix_business_claim_review_event_claim_id',
        'ix_business_claim_review_event_previous_owner_id',
    },
    'business_offering': {
        'ix_business_offering_business_id', 'ix_business_offering_active',
    },
    'business_schedule_item': {
        'ix_business_schedule_item_business_id',
        'ix_business_schedule_item_active',
        'ix_business_schedule_item_event_date',
        'ix_business_schedule_item_status',
    },
    'business_integration_request': {
        'ix_business_integration_request_business_id',
        'ix_business_integration_request_requested_by_id',
        'ix_business_integration_request_status',
        'ix_business_integration_request_assigned_operator_id',
        'ix_business_integration_request_due_at',
    },
    'business_organization': {'ix_business_organization_created_by_id'},
    'business_organization_member': {
        'ix_business_organization_member_organization_id',
        'ix_business_organization_member_user_id',
        'ix_business_organization_member_role',
    },
    'business_staff_invitation': {
        'ix_business_staff_invitation_organization_id',
        'ix_business_staff_invitation_invited_by_id',
        'ix_business_staff_invitation_accepted_by_id',
        'ix_business_staff_invitation_email',
        'ix_business_staff_invitation_token_hash',
        'ix_business_staff_invitation_status',
        'ix_business_staff_invitation_expires_at',
    },
    'business_verification_evidence': {
        'ix_business_verification_evidence_claim_id',
        'ix_business_verification_evidence_submitted_by_id',
        'ix_business_verification_evidence_status',
    },
    'business_profile_revision': {
        'ix_business_profile_revision_business_id',
        'ix_business_profile_revision_actor_user_id',
        'ix_business_profile_revision_review_status',
        'ix_business_profile_revision_sensitive',
        'ix_business_profile_revision_restored_from_id',
        'ix_business_profile_revision_created_at',
    },
    'business_governance_event': {
        'ix_business_governance_event_business_id',
        'ix_business_governance_event_actor_user_id',
        'ix_business_governance_event_event_type',
        'ix_business_governance_event_created_at',
    },
    'business_profile_report': {
        'ix_business_profile_report_business_id',
        'ix_business_profile_report_reporter_id',
        'ix_business_profile_report_status',
        'ix_business_profile_report_assigned_operator_id',
        'ix_business_profile_report_due_at',
    },
    'business_operator_action': {
        'ix_business_operator_action_business_id',
        'ix_business_operator_action_claim_id',
        'ix_business_operator_action_action_type',
        'ix_business_operator_action_status',
        'ix_business_operator_action_proposed_by_id',
        'ix_business_operator_action_confirmed_by_id',
        'ix_business_operator_action_expires_at',
        'ix_business_operator_action_created_at',
    },
    'operator_security_event': {
        'ix_operator_security_event_target_user_id',
        'ix_operator_security_event_action',
        'ix_operator_security_event_created_at',
    },
    'message': {'ix_message_crew_id', 'ix_message_conversation_id'},
    'community_group': {
        'ix_community_group_kind', 'ix_community_group_privacy',
        'ix_community_group_legacy_scope_id', 'ix_community_group_owner_id',
        'ix_community_group_home_court_id', 'ix_community_group_archived_at',
    },
    'conversation': {
        'ix_conversation_kind', 'ix_conversation_scope_id',
        'ix_conversation_group_id',
    },
    'conversation_read': {
        'ix_conversation_read_user_id',
        'ix_conversation_read_conversation_id',
    },
    'game': {'ix_game_crew_id', 'ix_game_is_instant'},
    'notification': {'ix_notification_related_crew_id'},
    'game_arrival_intent': {
        'ix_game_arrival_intent_game_id',
        'ix_game_arrival_intent_user_id',
        'ix_game_arrival_intent_expires_at',
    },
    'play_availability_pulse': {
        'ix_play_availability_pulse_user_id',
        'ix_play_availability_pulse_court_id',
        'ix_play_availability_pulse_expires_at',
        'ix_play_availability_pulse_active',
        'ix_play_availability_pulse_accepted_by_id',
        'ix_play_availability_pulse_accepted_game_id',
    },
    'game_open_call': {
        'ix_game_open_call_game_id',
        'ix_game_open_call_created_by_id',
        'ix_game_open_call_active',
    },
    'court_chat_subscription': {
        'ix_court_chat_subscription_user_id',
        'ix_court_chat_subscription_court_id',
    },
    'direct_chat_preference': {
        'ix_direct_chat_preference_user_id',
        'ix_direct_chat_preference_partner_id',
    },
    'club': {'ix_club_archived_at'},
    'club_join_request': {
        'ix_club_join_request_club_id', 'ix_club_join_request_user_id',
        'ix_club_join_request_status',
    },
    'club_ban': {'ix_club_ban_club_id', 'ix_club_ban_user_id'},
    'tournament_match': {
        'ix_tournament_match_result_state',
        'ix_tournament_match_result_state_reported_at',
    },
    'league_match': {
        'ix_league_match_result_state',
        'ix_league_match_result_state_reported_at',
    },
    'competition_result_event': {
        'ix_competition_result_event_competition_type',
        'ix_competition_result_event_match_id',
        'ix_competition_result_event_actor_id',
    },
}
REQUIRED_PARTIAL_UNIQUE_INDEXES = {
    'check_in': {
        'uq_check_in_active_user': (
            ('user_id',), 'checked_out_at is null',
        ),
    },
    'game_arrival_intent': {
        'uq_game_arrival_active_user': (
            ('user_id',), 'active is true',
        ),
    },
    'play_availability_pulse': {
        'uq_play_availability_pulse_active_user': (
            ('user_id',), 'active is true',
        ),
    },
    'game_open_call': {
        'uq_game_open_call_active_game': (
            ('game_id',), 'active is true',
        ),
    },
}
FORBIDDEN_INDEXES = {
    # This old partial unique index limited an entire rally to one traveler.
    # Its presence makes the multi-arrival capacity contract impossible even
    # when the application code correctly serializes admission on the Game row.
    'game_arrival_intent': {'uq_game_arrival_active_game'},
}
REQUIRED_EXACT_UNIQUE_INDEXES = {
    'push_subscription': {
        'uq_push_subscription_endpoint': ('endpoint',),
    },
    'game': {
        'uq_game_creator_attempt': ('creator_id', 'client_attempt_id'),
    },
}
REQUIRED_UNIQUES = {
    'business_profile': {'uq_business_profile_court'},
    'business_claim': {'uq_business_claim_user_court'},
    'business_organization_member': {'uq_business_organization_member'},
    'business_staff_invitation': {'uq_business_staff_invitation_token'},
    'crew': {'uq_crew_source_game'},
    'crew_member': {'uq_crew_member'},
    'crew_invite': {'uq_crew_invitee'},
    'crew_chat_read': {'uq_crew_chat_read'},
    'community_group': {'uq_community_group_legacy_scope'},
    'conversation': {'uq_conversation_scope'},
    'conversation_read': {'uq_conversation_read'},
    'court_chat_subscription': {'uq_court_chat_subscription'},
    'direct_chat_preference': {'uq_direct_chat_preference'},
    'club_join_request': {'uq_club_join_request'},
    'club_ban': {'uq_club_ban'},
    'game_arrival_intent': {'uq_game_arrival_user_attempt'},
    'play_availability_pulse': {
        'uq_play_availability_pulse_user_attempt',
        'uq_play_availability_pulse_accept_attempt',
    },
    'game_open_call': {
        'uq_game_open_call_creator_attempt',
        'uq_game_open_call_game_creator',
        'uq_game_open_call_message',
    },
    'game_recurrence_rsvp': {'uq_game_recurrence_rsvp'},
    'game_score_line': {'uq_game_score_line_number'},
    'competition_result_event': {'uq_competition_result_event_version'},
}
REQUIRED_CHECK_CONSTRAINTS = {
    'community_group': {
        'ck_community_group_kind', 'ck_community_group_privacy',
    },
    'conversation': {'ck_conversation_kind'},
    'business_profile': {
        'ck_business_profile_claim_status',
        'ck_business_profile_governance_status',
        'ck_business_profile_content_review_status',
    },
    'business_claim': {'ck_business_claim_status'},
    'business_claim_review_event': {
        'ck_business_claim_review_event_decision',
        'ck_business_claim_review_event_method',
    },
    'business_integration_request': {
        'ck_business_integration_request_status',
    },
    'business_schedule_item': {
        'ck_business_schedule_item_capacity',
        'ck_business_schedule_item_spots',
        'ck_business_schedule_item_spots_capacity',
        'ck_business_schedule_item_status',
    },
    'business_organization_member': {
        'ck_business_organization_member_role',
    },
    'business_staff_invitation': {
        'ck_business_staff_invitation_role',
        'ck_business_staff_invitation_status',
    },
    'business_verification_evidence': {
        'ck_business_verification_evidence_status',
        'ck_business_verification_evidence_type',
    },
    'business_profile_revision': {
        'ck_business_profile_revision_review_status',
    },
    'business_profile_report': {
        'ck_business_profile_report_category',
        'ck_business_profile_report_status',
    },
    'business_operator_action': {
        'ck_business_operator_action_status',
        'ck_business_operator_action_type',
    },
    'play_availability_pulse': {
        'ck_play_availability_pulse_positive_window',
    },
}
REQUIRED_FOREIGN_KEYS = {
    'user': {
        'user_invited_by_user_id_fkey': (
            ('invited_by_user_id',), 'user', ('id',),
        ),
        'user_suspended_by_id_fkey': (
            ('suspended_by_id',), 'user', ('id',),
        ),
    },
    'account_action_token': {
        'account_action_token_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'push_subscription': {
        'push_subscription_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'push_outbox': {
        'push_outbox_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'user_report': {
        'user_report_assigned_operator_id_fkey': (
            ('assigned_operator_id',), 'user', ('id',),
        ),
    },
    'player_feedback': {
        'player_feedback_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'player_feedback_assigned_operator_id_fkey': (
            ('assigned_operator_id',), 'user', ('id',),
        ),
    },
    'moderation_action': {
        'moderation_action_actor_id_fkey': (
            ('actor_id',), 'user', ('id',),
        ),
        'moderation_action_target_user_id_fkey': (
            ('target_user_id',), 'user', ('id',),
        ),
        'moderation_action_user_report_id_fkey': (
            ('user_report_id',), 'user_report', ('id',),
        ),
        'moderation_action_feedback_id_fkey': (
            ('feedback_id',), 'player_feedback', ('id',),
        ),
    },
    'game_recurrence_rsvp': {
        'game_recurrence_rsvp_game_id_fkey': (
            ('game_id',), 'game', ('id',),
        ),
        'game_recurrence_rsvp_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'game_score_line': {
        'game_score_line_game_id_fkey': (
            ('game_id',), 'game', ('id',),
        ),
    },
    'business_profile': {
        'business_profile_owner_id_fkey': (
            ('owner_id',), 'user', ('id',),
        ),
        'business_profile_court_id_fkey': (
            ('court_id',), 'court', ('id',),
        ),
        'business_profile_organization_id_fkey': (
            ('organization_id',), 'business_organization', ('id',),
        ),
    },
    'business_claim': {
        'business_claim_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'business_claim_court_id_fkey': (
            ('court_id',), 'court', ('id',),
        ),
        'business_claim_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_claim_assigned_operator_id_fkey': (
            ('assigned_operator_id',), 'user', ('id',),
        ),
    },
    'business_claim_review_event': {
        'business_claim_review_event_claim_id_fkey': (
            ('claim_id',), 'business_claim', ('id',),
        ),
        'business_claim_review_event_previous_owner_id_fkey': (
            ('previous_owner_id',), 'user', ('id',),
        ),
    },
    'business_offering': {
        'business_offering_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
    },
    'business_schedule_item': {
        'business_schedule_item_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
    },
    'business_integration_request': {
        'business_integration_request_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_integration_request_requested_by_id_fkey': (
            ('requested_by_id',), 'user', ('id',),
        ),
        'business_integration_request_assigned_operator_id_fkey': (
            ('assigned_operator_id',), 'user', ('id',),
        ),
    },
    'business_organization': {
        'business_organization_creator_id_fkey': (
            ('created_by_id',), 'user', ('id',),
        ),
    },
    'business_organization_member': {
        'business_organization_member_organization_id_fkey': (
            ('organization_id',), 'business_organization', ('id',),
        ),
        'business_organization_member_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'business_staff_invitation': {
        'business_staff_invitation_organization_id_fkey': (
            ('organization_id',), 'business_organization', ('id',),
        ),
        'business_staff_invitation_inviter_id_fkey': (
            ('invited_by_id',), 'user', ('id',),
        ),
        'business_staff_invitation_acceptor_id_fkey': (
            ('accepted_by_id',), 'user', ('id',),
        ),
    },
    'business_verification_evidence': {
        'business_verification_evidence_claim_id_fkey': (
            ('claim_id',), 'business_claim', ('id',),
        ),
        'business_verification_evidence_submitter_id_fkey': (
            ('submitted_by_id',), 'user', ('id',),
        ),
    },
    'business_profile_revision': {
        'business_profile_revision_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_profile_revision_actor_id_fkey': (
            ('actor_user_id',), 'user', ('id',),
        ),
        'business_profile_revision_restored_from_id_fkey': (
            ('restored_from_id',), 'business_profile_revision', ('id',),
        ),
    },
    'business_governance_event': {
        'business_governance_event_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_governance_event_actor_id_fkey': (
            ('actor_user_id',), 'user', ('id',),
        ),
    },
    'business_profile_report': {
        'business_profile_report_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_profile_report_reporter_id_fkey': (
            ('reporter_id',), 'user', ('id',),
        ),
        'business_profile_report_assigned_operator_id_fkey': (
            ('assigned_operator_id',), 'user', ('id',),
        ),
    },
    'business_operator_action': {
        'business_operator_action_business_id_fkey': (
            ('business_id',), 'business_profile', ('id',),
        ),
        'business_operator_action_claim_id_fkey': (
            ('claim_id',), 'business_claim', ('id',),
        ),
        'business_operator_action_proposer_id_fkey': (
            ('proposed_by_id',), 'user', ('id',),
        ),
        'business_operator_action_confirmer_id_fkey': (
            ('confirmed_by_id',), 'user', ('id',),
        ),
    },
    'operator_security_event': {
        'operator_security_event_target_user_id_fkey': (
            ('target_user_id',), 'user', ('id',),
        ),
    },
    'message': {
        'message_crew_id_fkey': (
            ('crew_id',), 'crew', ('id',),
        ),
        'message_conversation_id_fkey': (
            ('conversation_id',), 'conversation', ('id',),
        ),
    },
    'community_group': {
        'community_group_owner_id_fkey': (
            ('owner_id',), 'user', ('id',),
        ),
        'community_group_home_court_id_fkey': (
            ('home_court_id',), 'court', ('id',),
        ),
    },
    'conversation': {
        'conversation_group_id_fkey': (
            ('group_id',), 'community_group', ('id',),
        ),
    },
    'conversation_read': {
        'conversation_read_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'conversation_read_conversation_id_fkey': (
            ('conversation_id',), 'conversation', ('id',),
        ),
    },
    'game': {
        'game_crew_id_fkey': (
            ('crew_id',), 'crew', ('id',),
        ),
        'game_score_confirmed_by_id_fkey': (
            ('score_confirmed_by_id',), 'user', ('id',),
        ),
    },
    'notification': {
        'notification_related_crew_id_fkey': (
            ('related_crew_id',), 'crew', ('id',),
        ),
    },
    'game_arrival_intent': {
        'game_arrival_intent_game_id_fkey': (
            ('game_id',), 'game', ('id',),
        ),
        'game_arrival_intent_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
    },
    'play_availability_pulse': {
        'play_availability_pulse_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'play_availability_pulse_court_id_fkey': (
            ('court_id',), 'court', ('id',),
        ),
        'play_availability_pulse_accepted_by_id_fkey': (
            ('accepted_by_id',), 'user', ('id',),
        ),
        'play_availability_pulse_accepted_game_id_fkey': (
            ('accepted_game_id',), 'game', ('id',),
        ),
    },
    'game_open_call': {
        'game_open_call_game_id_fkey': (
            ('game_id',), 'game', ('id',),
        ),
        'game_open_call_created_by_id_fkey': (
            ('created_by_id',), 'user', ('id',),
        ),
        'game_open_call_court_message_id_fkey': (
            ('court_message_id',), 'message', ('id',),
        ),
    },
    'court_chat_subscription': {
        'court_chat_subscription_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'court_chat_subscription_court_id_fkey': (
            ('court_id',), 'court', ('id',),
        ),
    },
    'direct_chat_preference': {
        'direct_chat_preference_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'direct_chat_preference_partner_id_fkey': (
            ('partner_id',), 'user', ('id',),
        ),
    },
    'club_join_request': {
        'club_join_request_club_id_fkey': (
            ('club_id',), 'club', ('id',),
        ),
        'club_join_request_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'club_join_request_resolved_by_id_fkey': (
            ('resolved_by_id',), 'user', ('id',),
        ),
    },
    'club_ban': {
        'club_ban_club_id_fkey': (
            ('club_id',), 'club', ('id',),
        ),
        'club_ban_user_id_fkey': (
            ('user_id',), 'user', ('id',),
        ),
        'club_ban_banned_by_id_fkey': (
            ('banned_by_id',), 'user', ('id',),
        ),
    },
    'tournament_entry': {
        'tournament_entry_partner_invitee_id_fkey': (
            ('partner_invitee_id',), 'user', ('id',),
        ),
    },
    'club': {
        'club_announcement_author_id_fkey': (
            ('announcement_author_id',), 'user', ('id',),
        ),
    },
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _normalize_postgres_url(url: str) -> str:
    if url.startswith('postgres://'):
        return 'postgresql+psycopg://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


def _validated_target_url() -> str:
    from sqlalchemy.engine import make_url

    target = _normalize_postgres_url(
        os.getenv('TARGET_DATABASE_URL', '').strip(),
    )
    if not target.startswith('postgresql+psycopg://'):
        raise RuntimeError(
            'TARGET_DATABASE_URL must be a PostgreSQL connection string.'
        )
    host = (make_url(target).host or '').lower()
    if '-pooler.' in host:
        raise RuntimeError(
            "TARGET_DATABASE_URL must be Neon's direct/unpooled URL; "
            'the deployed DATABASE_URL remains pooled.'
        )
    return target


def _schema_gaps(inspector, schema=PG_SCHEMA) -> list[str]:
    tables = set(inspector.get_table_names(schema=schema))
    gaps = []
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            gaps.append(f'missing table {table}')
            continue
        actual = {
            column['name']
            for column in inspector.get_columns(table, schema=schema)
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing columns {missing}')
    for table, required in REQUIRED_INDEXES.items():
        if table not in tables:
            continue
        actual = {
            index.get('name')
            for index in inspector.get_indexes(table, schema=schema)
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing indexes {missing}')
    for table, forbidden in FORBIDDEN_INDEXES.items():
        if table not in tables:
            continue
        actual = {
            index.get('name')
            for index in inspector.get_indexes(table, schema=schema)
        }
        present = sorted(forbidden & actual)
        if present:
            gaps.append(f'{table} has obsolete indexes {present}')
    for table, required in REQUIRED_PARTIAL_UNIQUE_INDEXES.items():
        if table not in tables:
            continue
        reflected = {
            index.get('name'): index
            for index in inspector.get_indexes(table, schema=schema)
        }
        for name, (expected_columns, expected_predicate) in required.items():
            index = reflected.get(name)
            if index is None:
                gaps.append(f'{table} missing partial unique index {name}')
                continue
            options = index.get('dialect_options') or {}
            predicate = options.get('postgresql_where')
            if predicate is None:
                predicate = options.get('sqlite_where')
            normalized_predicate = ' '.join(
                str(predicate if predicate is not None else '')
                .lower()
                .replace('"', '')
                .replace('(', ' ')
                .replace(')', ' ')
                .split()
            )
            if (
                not index.get('unique')
                or tuple(index.get('column_names') or ()) != expected_columns
                or normalized_predicate != expected_predicate
            ):
                gaps.append(
                    f'{table} index {name} must be unique on '
                    f'{list(expected_columns)} where {expected_predicate}'
                )
    for table, required in REQUIRED_EXACT_UNIQUE_INDEXES.items():
        if table not in tables:
            continue
        reflected = {
            index.get('name'): index
            for index in inspector.get_indexes(table, schema=schema)
        }
        for name, expected_columns in required.items():
            index = reflected.get(name)
            if index is None:
                gaps.append(f'{table} missing exact unique index {name}')
                continue
            options = index.get('dialect_options') or {}
            if (
                not index.get('unique')
                or tuple(index.get('column_names') or ()) != expected_columns
                or options.get('postgresql_where') is not None
                or options.get('sqlite_where') is not None
                or bool(options.get('postgresql_nulls_not_distinct'))
            ):
                gaps.append(
                    f'{table} index {name} must be a nonpartial unique index '
                    f'on {list(expected_columns)} with distinct nulls'
                )
    for table, required in REQUIRED_UNIQUES.items():
        if table not in tables:
            continue
        actual = {
            constraint.get('name')
            for constraint in inspector.get_unique_constraints(
                table, schema=schema,
            )
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing unique constraints {missing}')
    for table, required in REQUIRED_CHECK_CONSTRAINTS.items():
        if table not in tables:
            continue
        actual = {
            constraint.get('name')
            for constraint in inspector.get_check_constraints(
                table, schema=schema,
            )
        }
        missing = sorted(required - actual)
        if missing:
            gaps.append(f'{table} missing check constraints {missing}')
    for table, required in REQUIRED_FOREIGN_KEYS.items():
        if table not in tables:
            continue
        actual = [
            (
                constraint.get('name'),
                tuple(constraint.get('constrained_columns') or ()),
                constraint.get('referred_table'),
                tuple(constraint.get('referred_columns') or ()),
                constraint.get('referred_schema'),
            )
            for constraint in inspector.get_foreign_keys(table, schema=schema)
        ]
        for name, expected in required.items():
            local_columns, referred_table, referred_columns = expected
            if any(
                found[1:4]
                == (local_columns, referred_table, referred_columns)
                and found[4] in (None, schema)
                for found in actual
            ):
                continue
            if any(found[0] == name for found in actual):
                gaps.append(
                    f'{table} foreign key {name} has wrong target or columns'
                )
            else:
                gaps.append(f'{table} missing foreign key {name}')
    return gaps


def _integration_schema_gaps(inspector, schema=PG_SCHEMA) -> list[str]:
    """Include provider-sync tables in the release-wide migration contract."""
    from scripts.migrate_business_integration_foundation import schema_gaps

    return schema_gaps(inspector, schema)


def _preflight_existing_app(engine) -> None:
    from sqlalchemy import inspect, text

    with engine.connect() as connection:
        if connection.scalar(
            text('SELECT to_regnamespace(:schema)'), {'schema': PG_SCHEMA},
        ) is None:
            raise RuntimeError(
                f'Target is missing the existing {PG_SCHEMA!r} schema; '
                'refusing to initialize an unexpected database.'
            )
    tables = set(inspect(engine).get_table_names(schema=PG_SCHEMA))
    missing = sorted(BASE_TABLES - tables)
    if missing:
        raise RuntimeError(
            'Target is not the expected Third Shot database; missing base '
            f'tables: {missing}'
        )


def _configure_runtime_role_search_path(connection) -> None:
    from sqlalchemy import text

    role_name = connection.scalar(text('SELECT current_user'))
    preparer = connection.dialect.identifier_preparer
    connection.execute(text(
        f'ALTER ROLE {preparer.quote(role_name)} SET search_path TO '
        f'{preparer.quote(PG_SCHEMA)}, public'
    ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Apply verified additive schema upgrades to Third Shot PostgreSQL.',
    )
    parser.add_argument(
        '--check-only', action='store_true',
        help='verify the deployed schema without applying migrations',
    )
    args = parser.parse_args()

    from sqlalchemy import create_engine, inspect

    target_url = _validated_target_url()
    engine = create_engine(
        target_url,
        pool_pre_ping=True,
        connect_args={'options': f'-csearch_path={PG_SCHEMA}'},
    )
    try:
        _preflight_existing_app(engine)
        inspector = inspect(engine)
        before = (
            _schema_gaps(inspector)
            + _integration_schema_gaps(inspector)
        )
    finally:
        engine.dispose()

    if args.check_only:
        if before:
            raise RuntimeError('Schema verification failed: ' + '; '.join(before))
        print('Production schema check passed: release schema is ready.')
        return 0

    # Import only after the target has passed read-only identity checks. App
    # startup then runs the same idempotent additive migration path exercised by
    # local recovery tests, without creating unrelated missing application data.
    os.environ.update({
        'APP_ENV': 'production',
        'MFA_ENCRYPTION_KEY': 'cptEwcGPWoQwTRpx7LZH3BaiGR5MbnTsyqs1PjdFGgA=',
        'BUSINESS_CREDENTIAL_VAULT': 'disabled',
        'SERVERLESS_RUNTIME': 'false',
        'SCHEMA_MANAGEMENT_ENABLED': 'true',
        'AUTO_CREATE_DB': 'false',
        'AUTO_SEED_COURTS': 'false',
        'RATE_LIMIT_ENABLED': 'false',
        'PUSH_DELIVERY_ENABLED': 'false',
        'DATABASE_URL': target_url,
        'SECRET_KEY': 'migration-only-process-secret-not-used-for-serving',
    })
    from backend.app import app, db

    with app.app_context():
        from scripts.migrate_business_integration_foundation import (
            _upgrade_existing_foundation,
        )
        _upgrade_existing_foundation(db.engine, PG_SCHEMA)

        inspector = inspect(db.engine)
        gaps = (
            _schema_gaps(inspector)
            + _integration_schema_gaps(inspector)
        )
        if gaps:
            raise RuntimeError(
                'Schema verification failed after migration: ' + '; '.join(gaps)
            )
        with db.engine.begin() as connection:
            _configure_runtime_role_search_path(connection)

    print('Production schema migration completed and verified: release schema is ready.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Migration failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
