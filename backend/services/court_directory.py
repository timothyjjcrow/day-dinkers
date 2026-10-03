"""Public context for records outside the US court directory."""
from backend.app import db
from backend.models import CourtDirectoryExclusion


def exclusion_context(court_id):
    exclusion = db.session.get(CourtDirectoryExclusion, court_id)
    if not exclusion or not exclusion.active:
        return None
    # Publish the useful geographic explanation without internal review metadata.
    message = ('This listing was created as a test record and does not represent a pickleball venue. '
               'It is not listed in the court directory.') if exclusion.reason_code == 'invalid_test_record' else (
        'This venue is outside the United States and is not listed in the US court directory.')
    return {
        'listed': False,
        'message': message,
    }
