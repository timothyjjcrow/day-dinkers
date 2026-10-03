"""Public context for reviewed records omitted from court discovery."""
from backend.app import db
from backend.models import CourtDirectoryExclusion


def exclusion_context(court_id):
    exclusion = db.session.get(CourtDirectoryExclusion, court_id)
    if not exclusion or not exclusion.active:
        return None
    # Explain the directory decision without publishing internal review metadata.
    messages = {
        'foreign_venue': 'This venue is outside the United States and is not listed in the US court directory.',
        'invalid_test_record': ('This listing was created as a test record and does not represent a pickleball venue. '
                                'It is not listed in the court directory.'),
        'pickleball_prohibited': ('The venue operator prohibits pickleball at these courts. '
                                 'This listing is not shown in the court directory.'),
    }
    return {
        'listed': False,
        'message': messages[exclusion.reason_code],
    }
