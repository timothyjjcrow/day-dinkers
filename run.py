#!/usr/bin/env python3
"""Entry point for the Third Shot application."""
import os
from backend.app import create_app, socketio, db

config_name = os.environ.get('FLASK_ENV', 'development')
app = create_app(config_name)

with app.app_context():
    from backend.services.court_seeder import seed_courts, seed_missing_states
    count = seed_courts()
    if count:
        print(f"Seeded {count} pickleball courts across all states")
    else:
        added = seed_missing_states()
        if added:
            print(f"Added {added} courts from new states")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"Third Shot starting on http://localhost:{port}")
    socketio.run(
        app, host='0.0.0.0', port=port,
        debug=(config_name == 'development'),
        allow_unsafe_werkzeug=True,
    )
