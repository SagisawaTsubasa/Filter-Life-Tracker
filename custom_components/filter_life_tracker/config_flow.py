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
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
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
    _GENERIC_STATE_SUGGESTIONS,
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
    DOMAIN_STATE_SUGGESTIONS,
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


def _coefficient_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_COEFFICIENT, max=MAX_COEFFICIENT, step=0.01, mode=NumberSelectorMode.BOX
        )
    )


def _filter_schema(
    source_type: str, level: int, template: str = TEMPLATE_PP_COTTON
) -> vol.Schema:
    """Build the schema for one filter level.

    The selected template provides the prefilled defaults (doc section 8.1);
    the usage unit follows the source type.
    """
    defaults = TEMPLATE_DEFAULTS.get(template, TEMPLATE_DEFAULTS[TEMPLATE_PP_COTTON])
    usage_default = (
        defaults["rated_usage_count"]
        if source_type == SOURCE_TYPE_COUNT
        else defaults["rated_usage_duration"]
    )
    usage_unit = "次" if source_type == SOURCE_TYPE_COUNT else "h"
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_RATED_TIME_DAYS, default=defaults[CONF_RATED_TIME_DAYS]
        ): NumberSelector(
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


# Attributes that enumerate the values an entity can take as its own state
# (not every mode attribute qualifies: e.g. climate preset_modes are not
# the entity state, so they are intentionally excluded).
_STATE_ENUM_ATTRIBUTES = (
    "options",  # select / input_select
    "hvac_modes",  # climate
    "operation_list",  # water_heater
    "operation_modes",  # water_heater (some integrations)
    "modes",  # humidifier-style integrations that map modes to state
)


def _state_suggestions(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Build target-state suggestions: current state first, then the
    entity's own declared states (from its attributes), then known
    states for the entity's domain. Custom values remain allowed."""
    suggestions: list[str] = []
    state = hass.states.get(entity_id)
    if state is not None:
        if state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            suggestions.append(state.state)
        # Entity-declared states (e.g. select options, water_heater
        # operation_list) take precedence over generic domain guesses.
        for attr in _STATE_ENUM_ATTRIBUTES:
            values = state.attributes.get(attr)
            if isinstance(values, (list, tuple)):
                suggestions.extend(str(v) for v in values)
    domain = entity_id.split(".")[0]
    suggestions.extend(DOMAIN_STATE_SUGGESTIONS.get(domain, _GENERIC_STATE_SUGGESTIONS))
    # Deduplicate, preserving order.
    return list(dict.fromkeys(suggestions))


class FilterLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1
    MINOR_VERSION = 1

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
        """Device entry step 1: name, source entity, source type."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._entity_in_use(user_input[CONF_SOURCE_ENTITY]):
                errors[CONF_SOURCE_ENTITY] = "entity_in_use"
            else:
                self._data = dict(user_input)
                return await self.async_step_device_state()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_SOURCE_ENTITY): EntitySelector(),
                vol.Required(CONF_SOURCE_TYPE, default=SOURCE_TYPE_DURATION): SelectSelector(
                    SelectSelectorConfig(
                        options=[SOURCE_TYPE_DURATION, SOURCE_TYPE_COUNT],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="source_type",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema, errors=errors)

    async def async_step_device_state(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device entry step 2: target state (auto-suggested), coefficient, debounce."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not str(user_input[CONF_TARGET_STATE]).strip():
                errors[CONF_TARGET_STATE] = "target_state_empty"
            else:
                self._data.update(user_input)
                return await self.async_step_filter_template()

        suggestions = _state_suggestions(self.hass, self._data[CONF_SOURCE_ENTITY])
        schema = vol.Schema(
            {
                vol.Required(CONF_TARGET_STATE): SelectSelector(
                    SelectSelectorConfig(
                        options=suggestions,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
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
        return self.async_show_form(step_id="device_state", data_schema=schema, errors=errors)

    async def async_step_filter_template(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step: cartridge template selects the prefilled defaults.

        Used by both the device flow (continues to level-1 filter) and the
        total flow (continues to the pre-filter cartridge page).
        """
        if user_input is not None:
            self._data[CONF_TEMPLATE] = user_input[CONF_TEMPLATE]
            if self._data.get("_after_template") == "total_filter":
                return await self.async_step_total_filter()
            return await self.async_step_filter1()
        schema = vol.Schema(
            {
                vol.Required(CONF_TEMPLATE, default=TEMPLATE_PP_COTTON): SelectSelector(
                    SelectSelectorConfig(
                        options=TEMPLATES,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="template",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="filter_template", data_schema=schema)

    async def async_step_filter1(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device entry: level-1 filter configuration."""
        template = self._data.get(CONF_TEMPLATE, TEMPLATE_PP_COTTON)
        if user_input is not None:
            self._data.setdefault(CONF_FILTERS, {})["1"] = {
                CONF_TEMPLATE: template,
                **dict(user_input),
            }
            return await self.async_step_filter2()
        return self.async_show_form(
            step_id="filter1",
            data_schema=_filter_schema(self._data[CONF_SOURCE_TYPE], 1, template),
        )

    async def async_step_filter2(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Device entry: optional level-2 filter configuration."""
        template = self._data.get(CONF_TEMPLATE, TEMPLATE_PP_COTTON)
        if user_input is not None:
            if user_input.pop(CONF_ENABLE_LEVEL2):
                self._data[CONF_FILTERS]["2"] = {
                    CONF_TEMPLATE: template,
                    **dict(user_input),
                }
            return await self.async_step_device_confirm()
        schema = _filter_schema(self._data[CONF_SOURCE_TYPE], 2, template).extend(
            {vol.Required(CONF_ENABLE_LEVEL2, default=False): BooleanSelector()}
        )
        return self.async_show_form(step_id="filter2", data_schema=schema)

    def _confirm_summary(self) -> dict[str, str]:
        """Value-only summary lines for the confirmation page (doc §11.1 item 8)."""
        d = self._data
        source_type = d[CONF_SOURCE_TYPE]
        usage_unit = "×" if source_type == SOURCE_TYPE_COUNT else "h"
        lines = [f"{d[CONF_SOURCE_ENTITY]} → {d[CONF_TARGET_STATE]} ({source_type})"]
        debounce = (
            f" · {d.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE):g}s"
            if source_type == SOURCE_TYPE_COUNT
            else ""
        )
        lines.append(f"×{d[CONF_COEFFICIENT]:g}{debounce}")
        for level_str in sorted(d[CONF_FILTERS], key=int):
            f = d[CONF_FILTERS][level_str]
            line = (
                f"L{level_str} [{f[CONF_TEMPLATE]}]: {f[CONF_RATED_TIME_DAYS]:g}d / "
                f"{f[CONF_RATED_USAGE]:g} {usage_unit} / {f[CONF_WARN_THRESHOLD]:g}%"
            )
            if int(level_str) > 1:
                line += f" · ×{f.get(CONF_CASCADE_FACTOR, DEFAULT_CASCADE_FACTOR):g}"
            lines.append(line)
        return {"summary": "\n".join(lines)}

    async def async_step_device_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirmation page (doc §11.1 item 8): summary, then create."""
        if user_input is not None:
            return self._create_device_entry()
        return self.async_show_form(
            step_id="device_confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self._confirm_summary(),
        )

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
                self._data["_after_template"] = "total_filter"
                return await self.async_step_filter_template()

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
            template = self._data.get(CONF_TEMPLATE, TEMPLATE_PP_COTTON)
            data = {
                CONF_ENTRY_TYPE: ENTRY_TYPE_TOTAL,
                # Fixate the validated uniform downstream type so deleting a
                # downstream entry later cannot flip the usage unit.
                CONF_SOURCE_TYPE: self._data.get(
                    "_total_source_type", SOURCE_TYPE_DURATION
                ),
                CONF_DOWNSTREAM_ENTRIES: self._data[CONF_DOWNSTREAM_ENTRIES],
                CONF_FILTERS: {"1": {CONF_TEMPLATE: template, **dict(user_input)}},
            }
            return self.async_create_entry(title=self._data[CONF_NAME], data=data)

        # Usage unit of the total prefilter follows the uniform downstream type.
        downstream = self._data[CONF_DOWNSTREAM_ENTRIES]
        source_type = SOURCE_TYPE_DURATION
        for entry in self._async_current_entries():
            if entry.entry_id in downstream:
                source_type = entry.data.get(CONF_SOURCE_TYPE, SOURCE_TYPE_DURATION)
                break
        self._data["_total_source_type"] = source_type

        return self.async_show_form(
            step_id="total_filter",
            data_schema=_filter_schema(
                source_type, 1, self._data.get(CONF_TEMPLATE, TEMPLATE_PP_COTTON)
            ),
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
            errors: dict[str, str] = {}
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE and not str(
                user_input.get(CONF_TARGET_STATE, "")
            ).strip():
                errors[CONF_TARGET_STATE] = "target_state_empty"
            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._build_schema(entry),
                    errors=errors,
                )
            return self.async_create_entry(data=user_input)

        return self.async_show_form(step_id="init", data_schema=self._build_schema(entry))

    def _build_schema(self, entry: ConfigEntry) -> vol.Schema:
        """Build the options schema from the current entry state."""
        schema: dict[Any, Any] = {}
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DEVICE:
            suggestions = _state_suggestions(self.hass, entry.data[CONF_SOURCE_ENTITY])
            current_target = entry.options.get(
                CONF_TARGET_STATE, entry.data[CONF_TARGET_STATE]
            )
            if current_target not in suggestions:
                suggestions.insert(0, current_target)
            schema[vol.Required(CONF_TARGET_STATE, default=current_target)] = SelectSelector(
                SelectSelectorConfig(
                    options=suggestions,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            )
            schema[vol.Required(
                CONF_COEFFICIENT,
                default=float(entry.options.get(CONF_COEFFICIENT, entry.data[CONF_COEFFICIENT])),
            )] = _coefficient_selector()
            # Debounce only affects count-type sources — hide it otherwise.
            if entry.data.get(CONF_SOURCE_TYPE) == SOURCE_TYPE_COUNT:
                schema[vol.Optional(
                    CONF_DEBOUNCE,
                    default=int(entry.options.get(
                        CONF_DEBOUNCE, entry.data.get(CONF_DEBOUNCE, DEFAULT_DEBOUNCE)
                    )),
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

        return vol.Schema(schema)
