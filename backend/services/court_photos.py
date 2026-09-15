"""Keep community cover selection consistent across player and moderator actions."""
from sqlalchemy import case
from backend.app import db
from backend.models import CourtPhoto, CourtPhotoLike


def preferred_court_photo(court_id):
    return (CourtPhoto.query.filter_by(court_id=court_id)
            .order_by(case((CourtPhoto.category == 'court', 0),
                           (CourtPhoto.category == '', 1), else_=2), CourtPhoto.id.desc())
            .first())


def refresh_community_cover(court):
    if court.photo_url and not court.photo_url.startswith('/api/courts/'):
        return  # Preserve a venue-supplied cover.
    cover = preferred_court_photo(court.id)
    court.photo_url = (f'/api/courts/{court.id}/photos/{cover.id}' if cover
                       else f'/api/courts/{court.id}/photo' if court.photo_data else '')


def remove_court_photo(photo):
    court = photo.court
    CourtPhotoLike.query.filter_by(photo_id=photo.id).delete(synchronize_session=False)
    db.session.delete(photo)
    db.session.flush()
    if court:
        refresh_community_cover(court)
