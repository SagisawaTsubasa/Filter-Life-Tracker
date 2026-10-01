"""Persistence layer for Filter Life Tracker.

Storage keeps only runtime state (accumulated usage + install date).
Configuration lives in the Config Entry. See design doc section 10.

Schema migrations: HA's ``Store`` wraps the payload with its own version
envelope. Recent HA removed the ``migrate_func=`` constructor argument —
migration is done by subclassing and overriding ``_async_migrate_func``.
STORAGE_VERSION has always been 1, so the hook is an identity; raise
STORAGE_VERSION and convert ``old_data`` there when the payload ever
changes shape.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import ENTRY_TYPE_DEVICE, STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


class _FilterLifeStore(Store[dict[str, Any]]):
    """Store with an explicit (identity) migration hook for future versions."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """STORAGE_VERSION has always been 1 — nothing to convert yet.

        Guard: a future STORAGE_VERSION bump without updating this hook must
        fail loudly instead of silently treating old-shaped data as current.
        """
        if old_major_version != 1:
            raise NotImplementedError(
                f"no migration from filter_life_tracker storage v{old_major_version}"
            )
        return old_data


class FilterLifeStore:
    """Shared JSON store with batched (10 min) flushing."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store: Store[dict[str, Any]] = _FilterLifeStore(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self.data: dict[str, Any] = {
            "device_entries": {},
            "total_prefilter_entries": {},
        }
        self._dirty_entries: set[str] = set()
        self._flush_lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load stored data."""
        data = await self._store.async_load()
        if not data:
            return
        self.data["device_entries"] = data.get("device_entries", {})
        self.data["total_prefilter_entries"] = data.get("total_prefilter_entries", {})

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
        """Flush to disk if anything is dirty.

        Dirty flags survive a failed save so the next flush retries, and
        concurrent triggers (timer / reset / unload) are serialized to avoid
        double writes.
        """
        async with self._flush_lock:
            if not self._dirty_entries:
                return
            try:
                await self._store.async_save(self.data)
            except Exception:
                _LOGGER.exception("Failed to persist filter state — will retry on next flush")
                return
            self._dirty_entries.clear()
