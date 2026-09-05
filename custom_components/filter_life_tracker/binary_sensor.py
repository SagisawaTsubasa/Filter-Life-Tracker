"""Binary sensor platform for Filter Life Tracker."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATED
from .engine import FilterRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for a config entry."""
    runtime: FilterRuntime = hass.data[DOMAIN]["entries"][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for level in sorted(runtime.filters):
        entities.append(FilterLifeBinarySensor(runtime, level, "warn"))
        entities.append(FilterLifeBinarySensor(runtime, level, "expired"))
    async_add_entities(entities)


class FilterLifeBinarySensor(BinarySensorEntity):
    """Warning / expired binary sensor for one filter level."""

    _attr_has_entity_name = True
    _attr_should_poll = False  # values are dispatcher-pushed, polling is a no-op
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime: FilterRuntime, level: int, key: str) -> None:
        """Initialize."""
        self._runtime = runtime
        self._level = level
        self._key = key
        self._attr_translation_key = key
        self._attr_translation_placeholders = {"level": str(level)}
        self._attr_unique_id = f"{runtime.entry_id}_{key}_{level}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry_id)},
            name=runtime.entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATED.format(self._runtime.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return the binary state."""
        if self._key == "warn":
            return self._runtime.warn_on(self._level)
        return self._runtime.expired_on(self._level)
