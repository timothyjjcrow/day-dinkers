"""Ranked competitive play — blueprint registration."""
from flask import Blueprint

ranked_bp = Blueprint('ranked', __name__)

# Route modules register their routes by importing ranked_bp.
# These imports MUST come after ranked_bp is defined.
from backend.routes.ranked import queue, lobby, challenges, match, views, tournaments  # noqa: E402, F401
