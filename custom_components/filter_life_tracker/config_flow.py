"""Config flow for Filter Life Tracker."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_CASCADE_FACTOR,
    CONF_COEFFICIENT,
    CONF_DEBOUNCE,
    CONF_DOWNSTREAM_ENTRIES,
    CONF_ENABLE_LEVEL2,
    CONF_ENTRY_TYPE,
    CONF_FILTERS,
    CONF_RATED_TIME_DAYS,
    CONF_RATED_USAGE,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_TYPE,
    CONF_TARGET_STATE,
    CONF_TEMPLATE,
    CONF_WARN_THRESHOLD,
    DEFAULT_CASCADE_FACTOR,
    DEFAULT_COEFFICIENT,
    DEFAULT_DEBOUNCE,
    DEFAULT_WARN_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_TOTAL,
    MAX_COEFFICIENT,
    MAX_DEBOUNCE,
    MIN_COEFFICIENT,
    MIN_DEBOUNCE,
    SOURCE_TYPE_COUNT,
    SOURCE_TYPE_DURATION,
    TEMPLATE_DEFAULTS,
    TEMPLATE_PP_COTTON,
    TEMPLATES,
)

_LOGGER = logging.getLogger(__name__)


def _coefficient_selector(default: float = DEFAULT_COEFFICIENT) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_COEFFICIENT, max=MAX_COEFFICIENT, step=0.01, mode=NumberSelectorMode.BOX
        )
    )


def _filter_schema(source_type: str, level: int) -> vol.Schema:
    """Build the schema for one filter level. Usage unit follows source type."""
    defaults = TEMPLATE_DEFAULTS[TEMPLATE_PP_COTTON]
    usage_default = (
        defaults["rated_usage_count"]
        if source_type == SOURCE_TYPE_COUNT
        else defaults["rated_usage_duration"]
    )
    usage_unit = "次" if source_type == SOURCE_TYPE_COUNT else "h"
    schema: dict[Any, Any] = {
        vol.Required(CONF_TEMPLATE, default=TEMPLATE_PP_COTTON): SelectSelector(
            SelectSelectorConfig(options=TEMPLATES, mode=SelectSelectorMode.DROPDOWN, translation_key="template")
        ),
        vol.Required(CONF_RATED_TIME_DAYS, default=defaults[CONF_RATED_TIME_DAYS]): NumberSelector(
            NumberSelectorConfig(min=1, max=3650, step=1, unit_of_measurement="d", mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_RATED_USAGE, default=usage_default): NumberSelector(
            NumberSelectorConfig(min=0.1, max=100000, step=1, unit_of_measurement=usage_unit, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_WARN_THRESHOLD, default=DEFAULT_WARN_THRESHOLD): NumberSelector(
            NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="%", mode=NumberSelectorMode.BOX)
        ),
    }
    if level > 1:
        schema[vol.Required(CONF_CASCADE_FACTOR, default=DEFAULT_CASCADE_FACTOR)] = NumberSelector(
            NumberSelectorConfig(min=1.0, max=5.0, step=0.1, mode=NumberSelectorMode.BOX)
        )
    return vol.Schema(schema)


class FilterLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Entry point: menu between device entry and total prefilter entry
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show entry-type menu."""
        return self.async_show_menu(step_id="user", menu_options=["device", "total"])

    # ------------------------------------------------------------------
    # Device entry flow
    # ------------------------------------------------------------------

    def _entity_in_use(self, entity_id: str) -> bool:
        """Return True if another device entry already uses this source entity (ADR-008)."""
        for entry in self._async_current_entries():
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE and (
                entry.data.get(CONF_SOURCE_ENTITY) == entity_id
            ):
                return True
        return False

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device entry: name, source entity, target state, source type, coefficient."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._entity_in_use(user_input[CONF_SOURCE_ENTITY]):
                errors[CONF_SOURCE_ENTITY] = "entity_in_use"
            else:
                self._data = dict(user_input)
                return await self.async_step_filter1()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_SOURCE_ENTITY): EntitySelector(),
                vol.Required(CONF_TARGET_STATE): TextSelector(),
                vol.Required(CONF_SOURCE_TYPE, default=SOURCE_TYPE_DURATION): SelectSelector(
                    SelectSelectorConfig(
                        options=[SOURCE_TYPE_DURATION, SOURCE_TYPE_COUNT],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="source_type",
                    )
                ),
                vol.Required(CONF_COEFFICIENT, default=DEFAULT_COEFFICIENT): _coefficient_selector(),
                vol.Optional(CONF_DEBOUNCE, default=DEFAULT_DEBOUNCE): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_DEBOUNCE, max=MAX_DEBOUNCE, step=1,
                        unit_of_measurement="s", mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema, errors=errors)

    async def async_step_filter1(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device entry: level-1 filter configuration."""
        if user_input is not None:
            self._data.setdefault(CONF_FILTERS, {})["1"] = dict(user_input)
            return await self.async_step_filter2()
        return self.async_show_form(
            step_id="filter1",
            data_schema=_filter_schema(self._data[CONF_SOURCE_TYPE], 1),
        )

    async def async_step_filter2(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device entry: optional level-2 filter configuration."""
        if user_input is not None:
            if user_input.pop(CONF_ENABLE_LEVEL2):
                self._data[CONF_FILTERS]["2"] = dict(user_input)
            return self._create_device_entry()
        schema = _filter_schema(self._data[CONF_SOURCE_TYPE], 2).extend(
            {vol.Required(CONF_ENABLE_LEVEL2, default=False): BooleanSelector()}
        )
        return self.async_show_form(step_id="filter2", data_schema=schema)

    def _create_device_entry(self) -> ConfigFlowResult:
        """Create the device config entry."""
        data = {
            CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE,
            CONF_SOURCE_ENTITY: self._data[CONF_SOURCE_ENTITY],
            CONF_TARGET_STATE: self._data[CONF_TARGET_STATE],
            CONF_SOURCE_TYPE: self._data[CONF_SOURCE_TYPE],
            CONF_COEFFICIENT: self._data[CONF_COEFFICIENT],
            CONF_DEBOUNCE: self._data.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE),
            CONF_FILTERS: self._data[CONF_FILTERS],
        }
        return self.async_create_entry(title=self._data[CONF_NAME], data=data)

    # ------------------------------------------------------------------
    # Total prefilter flow
    # ------------------------------------------------------------------

    def _device_entries(self) -> dict[str, str]:
        """Return {entry_id: title} of all configured device entries."""
        return {
            entry.entry_id: entry.title
            for entry in self._async_current_entries()
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE
        }

    async def async_step_total(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Total prefilter: name + downstream selection + type-consistency check."""
        errors: dict[str, str] = {}
        device_entries = self._device_entries()
        if not device_entries:
            return self.async_abort(reason="no_device_entries")

        if user_input is not None:
            selected: list[str] = user_input[CONF_DOWNSTREAM_ENTRIES]
            source_types = {
                entry.data.get(CONF_SOURCE_TYPE)
                for entry in self._async_current_entries()
                if entry.entry_id in selected
            }
            if not selected:
                errors[CONF_DOWNSTREAM_ENTRIES] = "no_downstream"
            elif len(source_types) > 1:
                # ADR-006: mixed duration/count cannot be summed.
                errors[CONF_DOWNSTREAM_ENTRIES] = "mixed_source_types"
            else:
                self._data = dict(user_input)
                return await self.async_step_total_filter()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_DOWNSTREAM_ENTRIES): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": eid, "label": title}
                            for eid, title in device_entries.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="total", data_schema=schema, errors=errors)

    async def async_step_total_filter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Total prefilter: filter configuration (usually level 1 only)."""
        if user_input is not None:
            data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_TOTAL,
                CONF_DOWNSTREAM_ENTRIES: self._data[CONF_DOWNSTREAM_ENTRIES],
                CONF_FILTERS: {"1": dict(user_input)},
            }
            return self.async_create_entry(title=self._data[CONF_NAME], data=data)

        # Usage unit of the total prefilter follows the uniform downstream type.
        source_type = next(
            entry.data.get(CONF_SOURCE_TYPE, SOURCE_TYPE_DURATION)
            for entry in self._async_current_entries()
            if entry.entry_id in self._data[CONF_DOWNSTREAM_ENTRIES]
        )
        return self.async_show_form(
            step_id="total_filter", data_schema=_filter_schema(source_type, 1)
        )

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FilterLifeOptionsFlow:
        """Return the options flow."""
        return FilterLifeOptionsFlow()


class FilterLifeOptionsFlow(OptionsFlow):
    """Reconfigure mutable parameters.

    Not editable (doc 11.3): source entity, source type, downstream bindings.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show editable options."""
        entry = self.config_entry
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema: dict[Any, Any] = {}
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
            schema[vol.Required(
                CONF_TARGET_STATE,
                default=entry.options.get(CONF_TARGET_STATE, entry.data[CONF_TARGET_STATE]),
            )] = TextSelector()
            schema[vol.Required(
                CONF_COEFFICIENT,
                default=float(entry.options.get(CONF_COEFFICIENT, entry.data[CONF_COEFFICIENT])),
            )] = _coefficient_selector()
            schema[vol.Optional(
                CONF_DEBOUNCE,
                default=int(entry.options.get(CONF_DEBOUNCE, entry.data.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE))),
            )] = NumberSelector(
                NumberSelectorConfig(min=MIN_DEBOUNCE, max=MAX_DEBOUNCE, step=1,
                                     unit_of_measurement="s", mode=NumberSelectorMode.BOX)
            )

        for level_str, fcfg in entry.data[CONF_FILTERS].items():
            def _fopt(key: str, default: Any, _l=level_str, _f=fcfg) -> Any:
                return entry.options.get(f"filter_{_l}_{key}", _f.get(key, default))

            schema[vol.Required(
                f"filter_{level_str}_{CONF_RATED_TIME_DAYS}",
                default=float(_fopt(CONF_RATED_TIME_DAYS, 180)),
            )] = NumberSelector(
                NumberSelectorConfig(min=1, max=3650, step=1, unit_of_measurement="d", mode=NumberSelectorMode.BOX)
            )
            schema[vol.Required(
                f"filter_{level_str}_{CONF_RATED_USAGE}",
                default=float(_fopt(CONF_RATED_USAGE, 500)),
            )] = NumberSelector(
                NumberSelectorConfig(min=0.1, max=100000, step=1, mode=NumberSelectorMode.BOX)
            )
            schema[vol.Required(
                f"filter_{level_str}_{CONF_WARN_THRESHOLD}",
                default=float(_fopt(CONF_WARN_THRESHOLD, DEFAULT_WARN_THRESHOLD)),
            )] = NumberSelector(
                NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="%", mode=NumberSelectorMode.BOX)
            )
            if int(level_str) > 1:
                schema[vol.Required(
                    f"filter_{level_str}_{CONF_CASCADE_FACTOR}",
                    default=float(_fopt(CONF_CASCADE_FACTOR, DEFAULT_CASCADE_FACTOR)),
                )] = NumberSelector(
                    NumberSelectorConfig(min=1.0, max=5.0, step=0.1, mode=NumberSelectorMode.BOX)
                )

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
