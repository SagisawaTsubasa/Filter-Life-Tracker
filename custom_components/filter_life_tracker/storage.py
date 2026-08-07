"""Persistence layer for Filter Life Tracker.

Storage keeps only runtime state (accumulated usage + install date).
Configuration lives in the Config Entry. See design doc section 10.

Migration (section 10.4): the stored file carries a top-level ``version``.
On load, if it is older than STORAGE_VERSION, registered migration
functions are applied in order, then the file is re-saved immediately.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import ENTRY_TYPE_DEVICE, STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


def _migrate_0_to_1(data: dict[str, Any]) -> dict[str, Any]:
    """Placeholder migration chain entry for future schema versions."""
    return data


STORAGE_MIGRATIONS = {
    0: _migrate_0_to_1,
}


class FilterLifeStore:
    """Shared JSON store with batched (10 min) flushing."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = {
            "device_entries": {},
            "total_prefilter_entries": {},
        }
        self._dirty_entries: set[str] = set()

    async def async_load(self) -> None:
        """Load and migrate stored data."""
        data = await self._store.async_load()
        if not data:
            return
        version = data.get("version", STORAGE_VERSION)
        migrated = data
        while version < STORAGE_VERSION:
            migrate = STORAGE_MIGRATIONS.get(version)
            if migrate is None:
                _LOGGER.warning("No storage migration from version %s; starting fresh", version)
                return
            migrated = migrate(migrated)
            version += 1
        if version != data.get("version", STORAGE_VERSION):
            await self._store.async_save(migrated)
        self.data["device_entries"] = migrated.get("device_entries", {})
        self.data["total_prefilter_entries"] = migrated.get("total_prefilter_entries", {})

    def _section(self, entry_type: str) -> str:
        return "device_entries" if entry_type == ENTRY_TYPE_DEVICE else "total_prefilter_entries"

    def get_entry_state(self, entry_id: str, entry_type: str) -> dict[str, Any]:
        """Return the persisted runtime state for an entry (creating if missing)."""
        return self.data.setdefault(self._section(entry_type), {}).setdefault(
            entry_id, {"filters": {}}
        )

    def remove_entry_state(self, entry_id: str, entry_type: str) -> None:
        """Drop persisted state for a removed entry."""
        self.data[self._section(entry_type)].pop(entry_id, None)
        self._dirty_entries.add(entry_id)

    def mark_dirty(self, entry_id: str) -> None:
        """Mark an entry as needing a flush."""
        self._dirty_entries.add(entry_id)

    async def async_flush(self, *_: Any) -> None:
        """Flush to disk if anything is dirty."""
        if not self._dirty_entries:
            return
        self._dirty_entries.clear()
        await self._store.async_save(self.data)
