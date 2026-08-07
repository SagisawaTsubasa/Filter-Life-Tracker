"""Button platform for Filter Life Tracker."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .engine import FilterRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up reset buttons for a config entry."""
    runtime: FilterRuntime = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(
        FilterLifeResetButton(runtime, level) for level in sorted(runtime.filters)
    )


class FilterLifeResetButton(ButtonEntity):
    """Manual reset button for one filter level."""

    _attr_has_entity_name = True
    _attr_translation_key = "reset"
    _attr_icon = "mdi:filter-refresh"

    def __init__(self, runtime: FilterRuntime, level: int) -> None:
        """Initialize."""
        self._runtime = runtime
        self._level = level
        self._attr_translation_placeholders = {"level": str(level)}
        self._attr_unique_id = f"{runtime.entry_id}_reset_{level}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry_id)},
            name=runtime.entry.title,
        )

    async def async_press(self) -> None:
        """Reset this filter level."""
        await self._runtime.async_reset_filter(self._level)
