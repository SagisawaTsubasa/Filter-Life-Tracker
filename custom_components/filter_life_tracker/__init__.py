"""Filter Life Tracker integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    FLUSH_INTERVAL,
)
from .engine import FilterRuntime
from .storage import FilterLifeStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def _async_get_store(hass: HomeAssistant) -> FilterLifeStore:
    """Return the shared store, loading and scheduling flushes once.

    The store instance is placed into hass.data *synchronously* so that
    parallel entry setups (HA starts entries of one domain concurrently)
    share a single store; every caller then awaits the same load task.
    Previously the slot was filled only after ``await store.async_load()``,
    letting a second entry create its own store and later overwrite the
    shared storage file with partial data.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get("store")
    if store is None:
        store = FilterLifeStore(hass)
        domain_data["store"] = store
        domain_data["store_task"] = hass.async_create_task(store.async_load())
        # ADR-007: batched flush every 10 minutes; forced flush on shutdown.
        # Handles are kept so they can be cancelled when the last entry unloads.
        domain_data["flush_unsub"] = async_track_time_interval(
            hass, store.async_flush, FLUSH_INTERVAL
        )
        domain_data["stop_unsub"] = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, store.async_flush
        )
    task: asyncio.Task | None = domain_data.get("store_task")
    if task is not None:
        try:
            await task
        finally:
            if domain_data.get("store_task") is task:
                domain_data.pop("store_task", None)
    return store


def _maybe_release_store(hass: HomeAssistant) -> None:
    """Cancel the shared flush timer/listener when the last entry unloads."""
    domain_data = hass.data.get(DOMAIN) or {}
    if domain_data.get("entries"):
        return
    flush_unsub = domain_data.pop("flush_unsub", None)
    if flush_unsub is not None:
        flush_unsub()
    stop_unsub = domain_data.pop("stop_unsub", None)
    if stop_unsub is not None:
        stop_unsub()
    # store 实例一并释放：否则下次 _async_get_store 复用旧实例时会跳过
    # 定时器注册分支，批量落盘与关机强刷永久失效（卸载前已 flush，磁盘状态是新的）。
    domain_data.pop("store", None)


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
        runtime = hass.data[DOMAIN].get("entries", {}).pop(entry.entry_id, None)
        if runtime is not None:
            runtime.async_teardown()
        store: FilterLifeStore = hass.data[DOMAIN]["store"]
        await store.async_flush()
        _maybe_release_store(hass)
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
    if (
        entry.version != CONFIG_ENTRY_VERSION
        or entry.minor_version != CONFIG_ENTRY_MINOR_VERSION
    ):
        # Future per-version data migrations go here.
        hass.config_entries.async_update_entry(
            entry,
            version=CONFIG_ENTRY_VERSION,
            minor_version=CONFIG_ENTRY_MINOR_VERSION,
        )
    return True
