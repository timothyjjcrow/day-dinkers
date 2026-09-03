"""Stable contracts implemented by business data providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from typing import Any

from backend.integrations.errors import ProviderNotAvailable


@dataclass(frozen=True)
class ProviderDescriptor:
    key: str
    name: str
    availability: str
    auth_mode: str
    capabilities: tuple[str, ...]
    supports_push: bool = False
    supports_pull: bool = False
    supports_webhooks: bool = False
    note: str = ''

    def to_dict(self):
        data = asdict(self)
        data['capabilities'] = list(self.capabilities)
        return data


@dataclass(frozen=True)
class NormalizedOccurrence:
    external_id: str
    title: str
    kind: str
    timezone: str
    recurrence: str = ''
    start_date: date | None = None
    end_date: date | None = None
    event_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    capacity: int | None = None
    spots_remaining: int | None = None
    status: str = 'scheduled'
    skill_level: str = 'all'
    location_note: str = ''
    instructor: str = ''
    price_text: str = ''
    booking_url: str = ''
    source_updated_at: datetime | None = None


@dataclass(frozen=True)
class NormalizedConversion:
    external_event_id: str
    occurred_at: datetime
    occurrence_external_id: str = ''
    value_minor: int | None = None
    currency: str = ''


@dataclass(frozen=True)
class CatalogSnapshot:
    schema_version: int
    source_version: str
    generated_at: datetime
    occurrences: tuple[NormalizedOccurrence, ...] = field(default_factory=tuple)
    conversions: tuple[NormalizedConversion, ...] = field(default_factory=tuple)
    authoritative: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ProviderAdapter(ABC):
    descriptor: ProviderDescriptor

    def validate_public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return dict(config)

    @abstractmethod
    def normalize_snapshot(self, payload: dict[str, Any]) -> CatalogSnapshot:
        """Convert provider input into the provider-neutral catalog contract."""

    def health_urls(self, config: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        return ()

    def authorization_url(self, *args, **kwargs):
        raise ProviderNotAvailable('oauth_not_supported_by_provider')

    def exchange_oauth_code(self, *args, **kwargs):
        raise ProviderNotAvailable('oauth_not_supported_by_provider')
