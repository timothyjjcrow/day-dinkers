"""Vercel's root Flask entrypoint."""

from backend.wsgi import app

__all__ = ['app']
