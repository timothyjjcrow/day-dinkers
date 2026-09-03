"""Explicit registry; an unregistered vendor is never presented as connected."""
from __future__ import annotations

from backend.integrations.base import ProviderAdapter, ProviderDescriptor
from backend.integrations.errors import ProviderNotAvailable


class ProviderRegistry:
    def __init__(self):
        self._adapters: dict[str, ProviderAdapter] = {}
        self._unavailable: dict[str, ProviderDescriptor] = {}

    def register(self, adapter: ProviderAdapter):
        key = adapter.descriptor.key
        if adapter.descriptor.availability != 'active':
            raise ValueError('registered adapters must be active')
        if key in self._adapters:
            raise ValueError(f'provider already registered: {key}')
        self._adapters[key] = adapter
        self._unavailable.pop(key, None)

    def declare_unavailable(self, *, key, name, note):
        if key in self._adapters:
            return
        self._unavailable[key] = ProviderDescriptor(
            key=key,
            name=name,
            availability='not_available',
            auth_mode='unavailable',
            capabilities=(),
            note=note,
        )

    def get(self, key) -> ProviderAdapter:
        adapter = self._adapters.get(str(key or '').strip().lower())
        if adapter is None:
            raise ProviderNotAvailable()
        return adapter

    def descriptors(self, *, include_unavailable=True):
        values = [adapter.descriptor for adapter in self._adapters.values()]
        if include_unavailable:
            values.extend(self._unavailable.values())
        return sorted(values, key=lambda item: (item.availability != 'active', item.name))


provider_registry = ProviderRegistry()

# These are honest capability notices, not partnership claims. They stop free
# text support requests from being mistaken for installed vendor connectors.
for _key, _name in (
    ('courtreserve', 'CourtReserve'),
    ('mindbody', 'Mindbody'),
    ('playbypoint', 'Playbypoint'),
    ('podplay', 'PodPlay'),
):
    provider_registry.declare_unavailable(
        key=_key,
        name=_name,
        note='No vendor API adapter is implemented or certified yet.',
    )
