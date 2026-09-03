"""Provider-neutral business integration foundation.

Only the ``link_catalog`` adapter is active today.  Proprietary provider names
may appear in support requests, but they are deliberately not registered here
until a real, tested contract exists.
"""

from backend.integrations.registry import provider_registry
from backend.integrations.link_catalog import LinkCatalogAdapter


provider_registry.register(LinkCatalogAdapter())


__all__ = ['provider_registry']
