"""Filter Life Tracker integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import CONFIG_ENTRY_VERSION, DOMAIN, FLUSH_INTERVAL
from .engine import FilterRuntime
from .storage import FilterLifeStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def _async_get_store(hass: HomeAssistant) -> FilterLifeStore:
    """Return the shared store, loading and scheduling flushes once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if "store" not in domain_data:
        store = FilterLifeStore(hass)
        await store.async_load()
        domain_data["store"] = store
        # ADR-007: batched flush every 10 minutes; forced flush on shutdown.
        async_track_time_interval(hass, store.async_flush, FLUSH_INTERVAL)
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, store.async_flush)
    return domain_data["store"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain data."""
    hass.data.setdefault(DOMAIN, {}).setdefault("entries", {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    store = await _async_get_store(hass)
    runtime = FilterRuntime(hass, entry, store)
    await runtime.async_setup()
    hass.data[DOMAIN].setdefault("entries", {})[entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: FilterRuntime = hass.data[DOMAIN]["entries"].pop(entry.entry_id)
        runtime.async_teardown()
        store: FilterLifeStore = hass.data[DOMAIN]["store"]
        await store.async_flush()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persisted state when an entry is removed."""
    store: FilterLifeStore | None = hass.data.get(DOMAIN, {}).get("store")
    if store is not None:
        store.remove_entry_state(entry.entry_id, entry.data.get("entry_type", "device"))
        await store.async_flush()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry data schema across versions (doc section 10.4)."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    if entry.version < CONFIG_ENTRY_VERSION:
        # Future per-version migrations go here, e.g.:
        # if entry.version == 1: ... upgrade data ...
        hass.config_entries.async_update_entry(entry, version=CONFIG_ENTRY_VERSION)
    return True
