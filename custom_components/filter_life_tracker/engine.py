"""Runtime engine for Filter Life Tracker.

One FilterRuntime per config entry. Implements:
- Duration / Count boundary-driven collectors (design doc section 6)
- Dual-track life computation (section 7)
- Usage-exhaustion-triggered cascade (section 4.3 / 9.1, ADR-003)
- Total-prefilter increment-push aggregation (section 6.3, ADR-004)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CONF_CASCADE_FACTOR,
    CONF_COEFFICIENT,
    CONF_DEBOUNCE,
    CONF_DOWNSTREAM_ENTRIES,
    CONF_FILTERS,
    CONF_RATED_TIME_DAYS,
    CONF_RATED_USAGE,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_TYPE,
    CONF_TARGET_STATE,
    CONF_WARN_THRESHOLD,
    DEFAULT_CASCADE_FACTOR,
    DEFAULT_COEFFICIENT,
    DEFAULT_DEBOUNCE,
    DEFAULT_WARN_THRESHOLD,
    ENTRY_TYPE_DEVICE,
    EVENT_FILTER_RESET,
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    SENSOR_REFRESH_INTERVAL,
    SIGNAL_INCREMENT,
    SIGNAL_UPDATED,
    SOURCE_TYPE_COUNT,
    STATE_ACCUMULATED_USAGE,
    STATE_INSTALL_DATE,
)
from .storage import FilterLifeStore

_LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


_UNSET = object()


def _parse_dt(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        # Corrupted storage must not crash every refresh; treat as "just now".
        _LOGGER.warning("Invalid install date %r — falling back to now", value)
        return _utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class FilterRuntime:
    """Runtime state and collection logic for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: FilterLifeStore) -> None:
        """Initialize runtime."""
        self.hass = hass
        self.entry = entry
        self.store = store
        self.entry_id = entry.entry_id
        self.entry_type: str = entry.data["entry_type"]

        persisted = store.get_entry_state(self.entry_id, self.entry_type)
        self.filters: dict[int, dict[str, Any]] = {}
        for level_str in entry.data[CONF_FILTERS]:
            level = int(level_str)
            fsaved = persisted["filters"].get(level_str, {})
            self.filters[level] = {
                STATE_INSTALL_DATE: fsaved.get(STATE_INSTALL_DATE, _utcnow().isoformat()),
                STATE_ACCUMULATED_USAGE: float(fsaved.get(STATE_ACCUMULATED_USAGE, 0.0)),
            }

        self._tracking_since: datetime | None = None
        self._debounce_cancel: Callable[[], None] | None = None
        self._unsubs: list[Callable[[], None]] = []
        # Count sources: suppress the first rising edge when the entity is
        # already in the target state right after (re)start, so a cycle that
        # began before a restart is not counted twice.
        self._suppress_next_rise = False
        # Notification throttling (see _notify_updated).
        self._last_signature: tuple | None = None
        self._last_notify_mono: float = 0.0
        # Total prefilters: cached downstream source type for legacy entries
        # that do not store it in entry.data.
        self._legacy_total_source_type: str | None | object = _UNSET

    # ------------------------------------------------------------------
    # Config helpers (options override data)
    # ------------------------------------------------------------------

    def _opt(self, key: str, default: Any = None) -> Any:
        return self.entry.options.get(key, self.entry.data.get(key, default))

    def _filter_opt(self, level: int, key: str, default: Any = None) -> Any:
        opt_key = f"filter_{level}_{key}"
        if opt_key in self.entry.options:
            return self.entry.options[opt_key]
        return self.entry.data[CONF_FILTERS][str(level)].get(key, default)

    @property
    def coefficient(self) -> float:
        """Return current correction coefficient."""
        return float(self._opt(CONF_COEFFICIENT, DEFAULT_COEFFICIENT))

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Start collectors, aggregation subscriptions and periodic refresh."""
        if self.entry_type == ENTRY_TYPE_DEVICE:
            source_entity: str = self.entry.data[CONF_SOURCE_ENTITY]
            if self.entry.data[CONF_SOURCE_TYPE] == SOURCE_TYPE_COUNT:
                self._unsubs.append(
                    async_track_state_change_event(self.hass, source_entity, self._on_count_event)
                )
                # Restart recovery for count sources: if the entity is already
                # in the target state, suppress the first rising edge so the
                # cycle that started before the restart is not counted twice.
                state = self.hass.states.get(source_entity)
                if state is not None and state.state == self._opt(CONF_TARGET_STATE, ""):
                    self._suppress_next_rise = True
                    _LOGGER.debug(
                        "[%s] restart while in target state — next rise suppressed",
                        self.entry_id,
                    )
            else:
                self._unsubs.append(
                    async_track_state_change_event(self.hass, source_entity, self._on_duration_event)
                )
                # Restart recovery: if entity is already in target state, start
                # tracking from now (doc 4.1: short-restart assumption).
                state = self.hass.states.get(source_entity)
                if state is not None and state.state == self._opt(CONF_TARGET_STATE, ""):
                    self._tracking_since = _utcnow()
        else:
            # Total prefilter: subscribe to downstream increment signals (doc 6.3).
            for downstream_id in self.entry.data[CONF_DOWNSTREAM_ENTRIES]:
                self._unsubs.append(
                    async_dispatcher_connect(
                        self.hass,
                        SIGNAL_INCREMENT.format(downstream_id),
                        self._on_downstream_increment,
                    )
                )

        self._unsubs.append(
            async_track_time_interval(
                self.hass, lambda _now: self._notify_updated(), SENSOR_REFRESH_INTERVAL
            )
        )

    @callback
    def async_teardown(self) -> None:
        """Stop all listeners."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_debounce()

    # ------------------------------------------------------------------
    # Collectors
    # ------------------------------------------------------------------

    @callback
    def _on_duration_event(self, event: Event[EventStateChangedData]) -> None:
        """Handle state change for duration-type sources."""
        new = event.data["new_state"]
        target = self._opt(CONF_TARGET_STATE, "")
        if new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # Entity unavailable: discard the current tracking segment (doc 4.1).
            self._tracking_since = None
            return
        if self._tracking_since is None and new.state == target:
            self._tracking_since = _utcnow()
        elif self._tracking_since is not None and new.state != target:
            # Negative delta clamped to 0 (NTP clock rollback protection).
            delta = max(0.0, (_utcnow() - self._tracking_since).total_seconds())
            self._tracking_since = None
            if delta > 0:
                self._apply_usage_increment(delta * self.coefficient)

    @callback
    def _on_count_event(self, event: Event[EventStateChangedData]) -> None:
        """Handle state change for count-type sources with debounce."""
        old = event.data["old_state"]
        new = event.data["new_state"]
        target = self._opt(CONF_TARGET_STATE, "")
        if new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._cancel_debounce()
            return
        old_state = old.state if old is not None else None
        if old_state != target and new.state == target:
            if self._suppress_next_rise:
                # Entity was already in the target state at setup; this event
                # is the restart echo of that same cycle, not a new one.
                self._suppress_next_rise = False
                _LOGGER.debug("[%s] suppressed rising edge after restart", self.entry_id)
                return
            # Rising edge: start debounce timer.
            self._cancel_debounce()
            debounce = float(self._opt(CONF_DEBOUNCE, DEFAULT_DEBOUNCE))
            self._debounce_cancel = async_call_later(self.hass, debounce, self._confirm_count)
        elif old_state == target and new.state != target:
            # Left target state within debounce window: cancel count.
            self._cancel_debounce()
            # 观测到一次真实离开：抑制使命已完成，清除标志——否则下一个
            # 全新周期的上升沿会被误当重启回声吞掉（unavailable 分支不清，
            # 继续压制 unavailable→target 的设备重联回声）。
            self._suppress_next_rise = False

    @callback
    def _confirm_count(self, _now: datetime) -> None:
        """Confirm a count after the debounce window."""
        self._debounce_cancel = None
        state = self.hass.states.get(self.entry.data[CONF_SOURCE_ENTITY])
        if state is not None and state.state == self._opt(CONF_TARGET_STATE, ""):
            self._apply_usage_increment(1.0 * self.coefficient)

    @callback
    def _cancel_debounce(self) -> None:
        if self._debounce_cancel is not None:
            self._debounce_cancel()
            self._debounce_cancel = None

    # ------------------------------------------------------------------
    # Aggregation (total prefilter)
    # ------------------------------------------------------------------

    @callback
    def _on_downstream_increment(self, amount: float) -> None:
        """Accumulate an increment pushed by a downstream device entry."""
        self._apply_usage_increment(amount, broadcast=False)

    # ------------------------------------------------------------------
    # Core accumulation and cascade
    # ------------------------------------------------------------------

    @callback
    def _apply_usage_increment(self, amount: float, broadcast: bool = True) -> None:
        """Add usage to all filters, applying cascade acceleration for level 2+."""
        if amount <= 0:
            return
        _LOGGER.debug(
            "[%s] usage increment %.3f (broadcast=%s)",
            self.entry_id,
            amount,
            broadcast,
        )
        for level in sorted(self.filters):
            increment = amount
            # Cascade: only triggered by the *usage track* exhaustion of the
            # previous level (ADR-003). Time-track expiry never cascades.
            if level > 1 and self.usage_exhausted(level - 1):
                increment = amount * float(
                    self._filter_opt(level, CONF_CASCADE_FACTOR, DEFAULT_CASCADE_FACTOR)
                )
            self.filters[level][STATE_ACCUMULATED_USAGE] += increment

        self.store.get_entry_state(self.entry_id, self.entry_type)["filters"] = {
            str(level): {
                STATE_INSTALL_DATE: f[STATE_INSTALL_DATE],
                STATE_ACCUMULATED_USAGE: f[STATE_ACCUMULATED_USAGE],
            }
            for level, f in self.filters.items()
        }
        self.store.mark_dirty(self.entry_id)

        # Only device entries broadcast increments; totals do not re-broadcast
        # (doc 6.3: single hop, no chained amplification).
        if broadcast and self.entry_type == ENTRY_TYPE_DEVICE:
            dispatcher_send(self.hass, SIGNAL_INCREMENT.format(self.entry_id), amount)

        self._notify_updated()

    # ------------------------------------------------------------------
    # Life computation (dual track, doc section 7)
    # ------------------------------------------------------------------

    def rated_time_seconds(self, level: int) -> float:
        """Return rated lifetime in seconds."""
        return float(self._filter_opt(level, CONF_RATED_TIME_DAYS, 180)) * SECONDS_PER_DAY

    def rated_usage(self, level: int) -> float:
        """Return rated usage in internal units (seconds for duration, count otherwise)."""
        rated = float(self._filter_opt(level, CONF_RATED_USAGE, 500.0))
        source_type = (
            self.entry.data[CONF_SOURCE_TYPE]
            if self.entry_type == ENTRY_TYPE_DEVICE
            else self._total_source_type()
        )
        if source_type != SOURCE_TYPE_COUNT:
            return rated * SECONDS_PER_HOUR  # user configures hours for duration type
        return rated

    def _total_source_type(self) -> str | None:
        """Return the source type of this entry (device) or its downstream (total).

        New total entries store the validated uniform type in entry.data, so a
        downstream entry being deleted can no longer flip the unit of the
        accumulated usage. Older entries fall back to scanning downstream
        entries (cached after the first lookup).
        """
        stored = self.entry.data.get(CONF_SOURCE_TYPE)
        if stored is not None:
            return stored
        if self._legacy_total_source_type is not _UNSET:
            return self._legacy_total_source_type  # type: ignore[return-value]
        found: str | None = None
        for other in self.hass.config_entries.async_entries(self.entry.domain):
            if other.entry_id in self.entry.data.get(CONF_DOWNSTREAM_ENTRIES, []):
                found = other.data.get(CONF_SOURCE_TYPE)
                break
        self._legacy_total_source_type = found
        return found

    def time_remaining_pct(self, level: int) -> float:
        """Return time-track remaining percentage, clamped to [0, 100]."""
        f = self.filters[level]
        rated = self.rated_time_seconds(level)
        if rated <= 0:
            return 100.0
        elapsed = (_utcnow() - _parse_dt(f[STATE_INSTALL_DATE])).total_seconds()
        elapsed = max(0.0, elapsed)
        return max(0.0, min(100.0, (rated - elapsed) / rated * 100.0))

    def usage_remaining_pct(self, level: int) -> float:
        """Return usage-track remaining percentage, clamped to [0, 100]."""
        f = self.filters[level]
        rated = self.rated_usage(level)
        if rated <= 0:
            return 100.0
        return max(
            0.0, min(100.0, (rated - f[STATE_ACCUMULATED_USAGE]) / rated * 100.0)
        )

    def usage_exhausted(self, level: int) -> bool:
        """Return True if the usage track is exhausted (unclamped check)."""
        f = self.filters[level]
        rated = self.rated_usage(level)
        return rated > 0 and f[STATE_ACCUMULATED_USAGE] >= rated

    def main_pct(self, level: int) -> float:
        """Return the main life percentage."""
        return min(self.time_remaining_pct(level), self.usage_remaining_pct(level))

    def warn_on(self, level: int) -> bool:
        """Return True if main life is below the warning threshold."""
        threshold = float(self._filter_opt(level, CONF_WARN_THRESHOLD, DEFAULT_WARN_THRESHOLD))
        return self.main_pct(level) < threshold

    def expired_on(self, level: int) -> bool:
        """Return True if main life has reached zero."""
        return self.main_pct(level) <= 0.0

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    async def async_reset_filter(self, level: int) -> None:
        """Reset one filter: zero usage, refresh install date, flush immediately."""
        if level not in self.filters:
            return
        self.filters[level][STATE_ACCUMULATED_USAGE] = 0.0
        self.filters[level][STATE_INSTALL_DATE] = _utcnow().isoformat()
        persisted = self.store.get_entry_state(self.entry_id, self.entry_type)
        persisted["filters"][str(level)] = {
            STATE_INSTALL_DATE: self.filters[level][STATE_INSTALL_DATE],
            STATE_ACCUMULATED_USAGE: 0.0,
        }
        self.store.mark_dirty(self.entry_id)
        await self.store.async_flush()
        # Doc section 14: fire an event so automations can react to resets.
        self.hass.bus.async_fire(
            EVENT_FILTER_RESET,
            {
                "entry_id": self.entry_id,
                "entry_type": self.entry_type,
                "level": level,
            },
        )
        self._notify_updated(force=True)

    # ------------------------------------------------------------------

    _NOTIFY_MIN_INTERVAL = 1800.0  # force a refresh at least every 30 min

    def _compute_signature(self) -> tuple:
        """Values the derived entities display, rounded so float jitter is ignored."""
        return tuple(
            (
                level,
                round(self.main_pct(level), 3),
                self.warn_on(level),
                self.expired_on(level),
            )
            for level in sorted(self.filters)
        )

    @callback
    def _notify_updated(self, force: bool = False) -> None:
        """Notify entities, skipping no-op refreshes.

        The 60 s timer used to dispatch unconditionally, making every entity
        recompute and rewrite its state each minute even when nothing changed.
        Dispatch is now skipped while the displayed values are unchanged
        (with a periodic forced refresh as a safety net).
        """
        signature = self._compute_signature()
        now_mono = time.monotonic()
        if (
            not force
            and signature == self._last_signature
            and (now_mono - self._last_notify_mono) < self._NOTIFY_MIN_INTERVAL
        ):
            return
        self._last_signature = signature
        self._last_notify_mono = now_mono
        dispatcher_send(self.hass, SIGNAL_UPDATED.format(self.entry_id))
