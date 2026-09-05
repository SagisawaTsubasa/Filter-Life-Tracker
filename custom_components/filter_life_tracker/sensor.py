"""Sensor platform for Filter Life Tracker."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTRY_TYPE, DOMAIN, SIGNAL_UPDATED
from .engine import FilterRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a config entry."""
    runtime: FilterRuntime = hass.data[DOMAIN]["entries"][entry.entry_id]
    entities: list[SensorEntity] = []
    for level in sorted(runtime.filters):
        entities.append(FilterLifeMainSensor(runtime, level))
        entities.append(FilterLifeTrackSensor(runtime, level, "life_time"))
        entities.append(FilterLifeTrackSensor(runtime, level, "life_usage"))
    async_add_entities(entities)


class FilterLifeBaseSensor(SensorEntity):
    """Base sensor bound to a runtime."""

    _attr_has_entity_name = True
    _attr_should_poll = False  # values are dispatcher-pushed, polling is a no-op
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:percent"

    def __init__(self, runtime: FilterRuntime, level: int, key: str) -> None:
        """Initialize."""
        self._runtime = runtime
        self._level = level
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


class FilterLifeMainSensor(FilterLifeBaseSensor):
    """Main life percentage: min(time track, usage track)."""

    def __init__(self, runtime: FilterRuntime, level: int) -> None:
        """Initialize."""
        super().__init__(runtime, level, "life")

    @property
    def native_value(self) -> float:
        """Return the main life percentage."""
        return round(self._runtime.main_pct(self._level), 1)


class FilterLifeTrackSensor(FilterLifeBaseSensor):
    """Auxiliary track sensor (time or usage)."""

    @property
    def native_value(self) -> float:
        """Return the track remaining percentage."""
        if self._attr_translation_key == "life_time":
            return round(self._runtime.time_remaining_pct(self._level), 1)
        return round(self._runtime.usage_remaining_pct(self._level), 1)
