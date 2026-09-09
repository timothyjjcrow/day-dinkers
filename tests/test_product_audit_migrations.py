"""Exercise additive migration against an existing league table, not only manifests."""
from sqlalchemy import inspect, text
from backend.app import create_app, db, _upgrade_schema


def test_existing_league_results_survive_idempotent_schedule_migration():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE league_match'))
            connection.execute(text('''CREATE TABLE league_match (
                id INTEGER PRIMARY KEY, league_id INTEGER, round INTEGER, box INTEGER,
                player1_id INTEGER, player2_id INTEGER, score1 INTEGER, score2 INTEGER,
                winner_id INTEGER, updated_at DATETIME
            )'''))
            connection.execute(text('''INSERT INTO league_match
                (id,league_id,round,box,player1_id,player2_id,score1,score2,winner_id,updated_at)
                VALUES (41,7,2,1,9,10,11,7,9,'2026-09-01 12:00:00')'''))
        _upgrade_schema(app)
        _upgrade_schema(app)
        inspector = inspect(db.engine)
        assert {'scheduled_at','scheduled_court_id','scheduled_duration_minutes','schedule_version',
                'schedule_proposals','schedule_proposed_by_id','schedule_day_reminded_at',
                'schedule_hour_reminded_at'} <= {c['name'] for c in inspector.get_columns('league_match')}
        assert 'ix_league_match_scheduled_at' in {i['name'] for i in inspector.get_indexes('league_match')}
        assert {('scheduled_court_id','court'),('schedule_proposed_by_id','user')} <= {
            (fk['constrained_columns'][0],fk['referred_table']) for fk in inspector.get_foreign_keys('league_match')}
        with db.engine.connect() as connection:
            row = connection.execute(text('SELECT * FROM league_match WHERE id=41')).mappings().one()
            assert (row['score1'],row['score2'],row['winner_id']) == (11,7,9)
            assert row['result_state'] == 'confirmed'
            assert row['scheduled_at'] is None and row['scheduled_court_id'] is None
            assert row['scheduled_duration_minutes'] == 60 and row['schedule_version'] == 0
            assert row['schedule_proposals'] == '[]'
            assert row['closed_round_review'] == '{}'
        db.session.remove()
        db.drop_all()


def test_existing_waitlist_stays_queued_and_new_consent_tables_do_not_invent_attendance():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE game_host_handoff'))
            connection.execute(text('DROP TABLE game_session_attendance'))
            connection.execute(text('DROP TABLE game_waitlist'))
            connection.execute(text('''CREATE TABLE game_waitlist (
                id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )'''))
            connection.execute(text('''INSERT INTO game_waitlist VALUES
                (55,7,9,'2026-09-01 12:00:00','2026-09-01 12:00:00')'''))
        _upgrade_schema(app)
        _upgrade_schema(app)
        inspector = inspect(db.engine)
        assert {'game_host_handoff','game_session_attendance'} <= set(inspector.get_table_names())
        assert 'uq_game_host_handoff_pending' in {row['name'] for row in inspector.get_indexes('game_host_handoff')}
        assert 'uq_game_session_attendance' in {row['name'] for row in inspector.get_unique_constraints('game_session_attendance')}
        for table, count in [('game_host_handoff',3),('game_session_attendance',3)]:
            assert len(inspector.get_foreign_keys(table)) == count
        with db.engine.connect() as connection:
            row=connection.execute(text('SELECT * FROM game_waitlist WHERE id=55')).mappings().one()
            assert (row['game_id'],row['user_id'],row['offer_status']) == (7,9,'queued')
            assert row['offered_at'] is None and row['offer_expires_at'] is None
            assert connection.execute(text('SELECT count(*) FROM game_session_attendance')).scalar_one() == 0
        db.session.remove()
        db.drop_all()


def test_score_and_tournament_upgrade_preserves_legacy_results_without_inventing_arrival():
    from datetime import timedelta
    from backend.models import Court, Game, Tournament, TournamentEntry, TournamentMatch, User, utcnow
    app=create_app('testing')
    fields={
        'game':['score_version','score_history','score_correction_pending'],
        'tournament':['schedule_version','schedule_history','rest_minutes','entry_fee_cents','payment_method','withdrawal_policy'],
        'tournament_entry':['player1_arrived_at','player2_arrived_at','arrival_history',
                            'partner_response_deadline_at','partner_history'],
        'tournament_match':['play_state','called_at','started_at'],
    }
    with app.app_context():
        db.create_all()
        user=User(email='legacy-operations@example.test',display_name='Legacy',password_hash='unused')
        court=Court(name='Legacy court',latitude=45,longitude=-122)
        db.session.add_all([user,court]);db.session.flush()
        game=Game(court_id=court.id,creator_id=user.id,scheduled_at=utcnow(),status='completed',
            score_team1=11,score_team2=7,completed_at=utcnow())
        event=Tournament(name='Legacy open',court_id=court.id,organizer_id=user.id,
            starts_at=utcnow()+timedelta(days=1),status='active')
        db.session.add_all([game,event]);db.session.flush()
        entry=TournamentEntry(tournament_id=event.id,player1_id=user.id,checked_in_at=utcnow())
        db.session.add(entry);db.session.flush()
        match=TournamentMatch(tournament_id=event.id,entry1_id=entry.id,score1=11,score2=7,
            winner_entry_id=entry.id,result_state='confirmed')
        db.session.add(match);db.session.commit()
        game_id,event_id,entry_id,match_id=game.id,event.id,entry.id,match.id
        db.session.remove()
        with db.engine.begin() as connection:
            for table,columns in fields.items():
                for column in columns:
                    connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        _upgrade_schema(app);_upgrade_schema(app)
        inspector=inspect(db.engine)
        for table,columns in fields.items():
            assert set(columns)<={c['name'] for c in inspector.get_columns(table)}
        with db.engine.connect() as connection:
            game=connection.execute(text('SELECT * FROM game WHERE id=:id'),{'id':game_id}).mappings().one()
            assert (game['score_team1'],game['score_team2'],game['status'])==(11,7,'completed')
            assert (game['score_version'],game['score_history'],game['score_correction_pending'])==(0,'[]',0)
            entry=connection.execute(text('SELECT * FROM tournament_entry WHERE id=:id'),{'id':entry_id}).mappings().one()
            assert entry['checked_in_at'] is not None
            assert entry['player1_arrived_at'] is None and entry['player2_arrived_at'] is None
            assert entry['arrival_history']=='[]'
            assert entry['partner_response_deadline_at'] is None and entry['partner_history']=='[]'
            match=connection.execute(text('SELECT * FROM tournament_match WHERE id=:id'),{'id':match_id}).mappings().one()
            assert (match['score1'],match['score2'],match['result_state'])==(11,7,'confirmed')
            assert match['play_state']=='estimated' and match['called_at'] is None and match['started_at'] is None
            event=connection.execute(text('SELECT * FROM tournament WHERE id=:id'),{'id':event_id}).mappings().one()
            assert event['rest_minutes']==5 and event['entry_fee_cents'] is None
            assert event['schedule_history']=='[]' and event['schedule_version']==0
        db.session.remove();db.drop_all()


def test_legacy_booking_click_gets_unknown_subject_and_safe_session_delete():
    from backend.models import BusinessProfile, BusinessScheduleItem, Court, User
    app=create_app('testing')
    with app.app_context():
        db.create_all()
        user=User(email='legacy-booking@example.test',display_name='Legacy owner',password_hash='unused')
        court=Court(name='Legacy venue',latitude=45,longitude=-122)
        db.session.add_all([user,court]);db.session.flush()
        profile=BusinessProfile(name='Legacy venue',owner_id=user.id,court_id=court.id)
        db.session.add(profile);db.session.flush()
        session=BusinessScheduleItem(business_id=profile.id,title='Evening play',day_of_week=1,
            start_time='18:00',end_time='19:00')
        db.session.add(session);db.session.commit()
        profile_id,session_id=profile.id,session.id
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE business_booking_event'))
            connection.execute(text('''CREATE TABLE business_booking_event (
                id INTEGER PRIMARY KEY, business_id INTEGER NOT NULL,
                event_type VARCHAR(16) NOT NULL,event_key VARCHAR(64) NOT NULL,
                action VARCHAR(40) NOT NULL,occurred_at DATETIME NOT NULL
            )'''))
            connection.execute(text('''INSERT INTO business_booking_event
                VALUES(91,:business,'click','historic-key','booking','2026-09-01 12:00:00')'''),{'business':profile_id})
        _upgrade_schema(app);_upgrade_schema(app)
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            row=connection.execute(text('SELECT * FROM business_booking_event WHERE id=91')).mappings().one()
            assert (row['event_key'],row['event_type'],row['action'])==('historic-key','click','booking')
            assert row['schedule_item_id'] is None and row['schedule_occurrence_on'] is None and row['subject_label']==''
            connection.execute(text("UPDATE business_booking_event SET schedule_item_id=:id,subject_label='Evening play',schedule_occurrence_on='2026-09-09' WHERE id=91"),{'id':session_id})
            connection.execute(text('DELETE FROM business_schedule_item WHERE id=:id'),{'id':session_id})
            row=connection.execute(text('SELECT * FROM business_booking_event WHERE id=91')).mappings().one()
            assert row['schedule_item_id'] is None and row['subject_label']=='Evening play'
            connection.commit()
        db.session.remove();db.drop_all()


def test_booking_subject_fk_requires_set_null_without_weakening_other_foreign_keys():
    from scripts.migrate_business_integration_foundation import _foreign_key_matches
    fk={'name':'business_booking_event_schedule_item_id_fkey','constrained_columns':['schedule_item_id'],
        'referred_table':'business_schedule_item','referred_columns':['id'],'options':{'ondelete':'SET NULL'}}
    args=(fk['name'],('schedule_item_id',),'business_schedule_item',('id',),None)
    assert _foreign_key_matches(fk,*args)
    for deletion in ['NO ACTION','CASCADE','RESTRICT']:
        assert not _foreign_key_matches(dict(fk,options={'ondelete':deletion}),*args)
    other=dict(fk,name='another_fk')
    assert not _foreign_key_matches(other,'another_fk',('schedule_item_id',),'business_schedule_item',('id',),None)


def test_legacy_hours_rounds_and_private_links_start_unknown_without_changing_existing_facts():
    from backend.models import BusinessProfile, Court, CourtPhoto, Game, League, LeagueMember, User, utcnow
    app = create_app('testing')
    fields = {
        'business_profile': ['structured_hours', 'hours_dawn_to_dusk', 'visitor_info'],
        'court': ['visitor_info'],
        'court_photo': ['category', 'captured_on'],
        'user': ['away_until'],
        'league': ['total_rounds', 'round_version', 'round_history', 'round_deadline_override_at'],
        'league_member': ['unavailable_round', 'withdraw_after_round', 'withdrawn_at', 'availability_history'],
        'game': ['invite_link_version', 'invite_link_expires_at'],
    }
    with app.app_context():
        db.create_all()
        user = User(email='legacy-round@example.test', display_name='Legacy', password_hash='unused')
        court = Court(name='Old court', latitude=45, longitude=-122, structured_hours='{"mon":[{"open":"08:00","close":"20:00"}]}')
        db.session.add_all([user, court]); db.session.flush()
        photo = CourtPhoto(court_id=court.id,user_id=user.id,photo_data='data:image/png;base64,legacy',caption='Old entrance')
        db.session.add(photo)
        profile = BusinessProfile(name='Old venue', owner_id=user.id, court_id=court.id, hours='Weekdays 8–8')
        league = League(name='Old league', court_id=court.id, organizer_id=user.id, starts_at=utcnow(), status='active', current_round=2)
        game = Game(court_id=court.id, creator_id=user.id, scheduled_at=utcnow(), visibility='private')
        db.session.add_all([profile, league, game]); db.session.flush()
        member = LeagueMember(league_id=league.id, user_id=user.id, points=12, wins=4, losses=2)
        db.session.add(member); db.session.commit(); db.session.remove()
        with db.engine.begin() as connection:
            for table, columns in fields.items():
                for column in columns:
                    connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        _upgrade_schema(app); _upgrade_schema(app)
        with db.engine.connect() as connection:
            profile = connection.execute(text('SELECT * FROM business_profile')).mappings().one()
            assert profile['hours'] == 'Weekdays 8–8' and profile['structured_hours'] == '{}'
            assert profile['hours_dawn_to_dusk'] == 0
            assert profile['visitor_info'] == '{}'
            assert connection.execute(text('SELECT visitor_info FROM court')).scalar_one() == '{}'
            user_row = connection.execute(text('SELECT * FROM "user"')).mappings().one()
            assert user_row['away_until'] is None and user_row['display_name'] == 'Legacy'
            photo_row = connection.execute(text('SELECT * FROM court_photo')).mappings().one()
            assert photo_row['category'] == '' and photo_row['captured_on'] is None
            assert photo_row['caption'] == 'Old entrance' and photo_row['photo_data'] == 'data:image/png;base64,legacy'
            assert '08:00' in connection.execute(text('SELECT structured_hours FROM court')).scalar_one()
            league = connection.execute(text('SELECT * FROM league')).mappings().one()
            assert league['current_round'] == 2 and league['total_rounds'] is None
            assert (league['round_version'], league['round_history'], league['round_deadline_override_at']) == (0, '[]', None)
            member = connection.execute(text('SELECT * FROM league_member')).mappings().one()
            assert (member['points'], member['wins'], member['losses']) == (12, 4, 2)
            assert member['unavailable_round'] is None and member['withdrawn_at'] is None
            assert member['availability_history'] == '[]'
            game = connection.execute(text('SELECT * FROM game')).mappings().one()
            assert game['visibility'] == 'private' and game['invite_link_version'] == 0 and game['invite_link_expires_at'] is None
        db.session.remove(); db.drop_all()


def test_presence_and_court_review_upgrade_does_not_invent_verification_or_review():
    from backend.models import Court, User
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add_all([
            User(id=12,email='legacy-author@example.test',display_name='Author',password_hash='unused'),
            User(id=13,email='legacy-reviewer@example.test',display_name='Reviewer',password_hash='unused'),
            Court(id=11,name='Legacy court',latitude=45,longitude=-122),
        ])
        db.session.commit()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE court_edit_suggestion'))
            connection.execute(text('CREATE TABLE court_edit_suggestion (id INTEGER PRIMARY KEY,court_id INTEGER,user_id INTEGER,payload TEXT,status VARCHAR(16))'))
            connection.execute(text("INSERT INTO court_edit_suggestion VALUES(7,11,12,:payload,'pending')"), {'payload': '{"closed":true}'})
            connection.execute(text('DROP TABLE check_in'))
            connection.execute(text('CREATE TABLE check_in (id INTEGER PRIMARY KEY,user_id INTEGER,court_id INTEGER,looking_for_game BOOLEAN,checked_in_at DATETIME)'))
            connection.execute(text("INSERT INTO check_in VALUES(8,12,11,0,'2026-09-01 12:00:00')"))
        _upgrade_schema(app); _upgrade_schema(app)
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            row = connection.execute(text('SELECT * FROM court_edit_suggestion WHERE id=7')).mappings().one()
            assert row['status'] == 'pending' and row['payload'] == '{"closed":true}'
            assert row['reviewed_by_id'] is None and row['reviewed_at'] is None and row['review_note'] == ''
            row = connection.execute(text('SELECT * FROM check_in WHERE id=8')).mappings().one()
            assert row['checked_in_at'] is not None and row['location_verified_at'] is None
            # SQLite retains ON DELETE in its real constraint even when SQLAlchemy
            # omits options while parsing an inline ADD COLUMN reference.
            foreign_keys = connection.exec_driver_sql('PRAGMA foreign_key_list(court_edit_suggestion)').mappings().all()
            assert any(fk['from'] == 'reviewed_by_id' and fk['on_delete'] == 'SET NULL' for fk in foreign_keys)
            connection.execute(text("UPDATE court_edit_suggestion SET reviewed_by_id=13,review_note='Evidence checked' WHERE id=7"))
            connection.execute(text('DELETE FROM "user" WHERE id=13'))
            row = connection.execute(text('SELECT * FROM court_edit_suggestion WHERE id=7')).mappings().one()
            assert row['reviewed_by_id'] is None and row['review_note'] == 'Evidence checked'
            assert row['user_id'] == 12 and row['payload'] == '{"closed":true}'
            connection.commit()
        db.session.remove(); db.drop_all()


def test_waitlist_migration_adds_an_empty_queue_without_registering_or_offering_anyone():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE tournament_waitlist'))
        _upgrade_schema(app); _upgrade_schema(app)
        inspector = inspect(db.engine)
        assert {'ix_tournament_waitlist_tournament_id', 'ix_tournament_waitlist_user_id'} <= {
            item['name'] for item in inspector.get_indexes('tournament_waitlist')}
        assert 'uq_tournament_waitlist' in {item['name'] for item in inspector.get_unique_constraints('tournament_waitlist')}
        assert {'tournament_waitlist_tournament_id_fkey', 'tournament_waitlist_user_id_fkey'} <= {
            item['name'] for item in inspector.get_foreign_keys('tournament_waitlist')}
        with db.engine.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM tournament_waitlist')).scalar_one() == 0
        db.session.remove(); db.drop_all()


def test_legacy_messages_gain_optional_reply_without_losing_text_or_cascading_deletion():
    app=create_app('testing')
    with app.app_context():
        db.create_all()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE message'))
            connection.execute(text('''CREATE TABLE message (
                id INTEGER PRIMARY KEY, sender_id INTEGER NOT NULL, recipient_id INTEGER,
                body TEXT NOT NULL, read_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )'''))
            connection.execute(text('''INSERT INTO message VALUES
                (91,1,2,'Meet by the north entrance',NULL,'2026-09-01 12:00:00','2026-09-01 12:00:00'),
                (92,2,1,'I will be there',NULL,'2026-09-01 12:01:00','2026-09-01 12:01:00')'''))
        _upgrade_schema(app);_upgrade_schema(app)
        assert 'ix_message_reply_to_id' in {row['name'] for row in inspect(db.engine).get_indexes('message')}
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            rows=connection.execute(text('SELECT id,body,reply_to_id FROM message ORDER BY id')).mappings().all()
            assert rows[0]['body']=='Meet by the north entrance'
            assert all(row['reply_to_id'] is None for row in rows)
            connection.execute(text('UPDATE message SET reply_to_id=91 WHERE id=92'))
            connection.execute(text('DELETE FROM message WHERE id=91'))
            remaining=connection.execute(text('SELECT body,reply_to_id FROM message WHERE id=92')).mappings().one()
            assert remaining['body']=='I will be there' and remaining['reply_to_id'] is None
            connection.commit()
        db.session.remove();db.drop_all()


def test_legacy_venue_date_keeps_identity_when_associated_service_is_removed():
    from backend.models import BusinessOffering, BusinessProfile, Court, User
    app=create_app('testing')
    with app.app_context():
        db.create_all()
        owner=User(email='legacy-service@example.test',display_name='Owner',password_hash='unused')
        court=Court(name='Service court',latitude=45,longitude=-122)
        db.session.add_all([owner,court]);db.session.flush()
        venue=BusinessProfile(name='Service venue',owner_id=owner.id,court_id=court.id)
        db.session.add(venue);db.session.flush()
        service=BusinessOffering(business_id=venue.id,name='Beginner coaching')
        db.session.add(service);db.session.commit()
        venue_id,service_id=venue.id,service.id
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE business_schedule_item'))
            connection.execute(text('''CREATE TABLE business_schedule_item (
                id INTEGER PRIMARY KEY,business_id INTEGER NOT NULL,title VARCHAR(100) NOT NULL,
                day_of_week INTEGER NOT NULL,start_time VARCHAR(5) NOT NULL,end_time VARCHAR(5),
                created_at DATETIME NOT NULL,updated_at DATETIME NOT NULL
            )'''))
            connection.execute(text('''INSERT INTO business_schedule_item VALUES
                (71,:venue,'Tuesday coaching',1,'18:00','19:00','2026-09-01 12:00:00','2026-09-01 12:00:00')'''),{'venue':venue_id})
        _upgrade_schema(app);_upgrade_schema(app)
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            row=connection.execute(text('SELECT * FROM business_schedule_item WHERE id=71')).mappings().one()
            assert row['offering_id'] is None and row['title']=='Tuesday coaching'
            assert row['day_of_week']==1 and row['start_time']=='18:00'
            connection.execute(text('UPDATE business_schedule_item SET offering_id=:id WHERE id=71'),{'id':service_id})
            connection.execute(text('DELETE FROM business_offering WHERE id=:id'),{'id':service_id})
            row=connection.execute(text('SELECT * FROM business_schedule_item WHERE id=71')).mappings().one()
            assert row['offering_id'] is None and row['title']=='Tuesday coaching'
            connection.commit()
        db.session.remove();db.drop_all()
