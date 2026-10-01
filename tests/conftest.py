"""Home Assistant stubs so the engine unit tests run without a HA install.

Only the surface ``engine.py``/``storage.py`` actually touch is faked here;
behavior under test (state machines, cascade, debounce, aggregation) is pure
Python. Tests drive the runtime's callbacks directly and monkeypatch the
module-level helpers they need (``_utcnow``, ``dispatcher_send``,
``async_call_later``).
"""

from __future__ import annotations

# ``datetime.UTC`` is 3.11+; shim it for older local interpreters so the
# engine module (which targets HA's modern Python) still imports.
import datetime as _datetime
import sys
import types
from datetime import timezone
from pathlib import Path
from typing import Any

if not hasattr(_datetime, "UTC"):
    _datetime.UTC = timezone.utc  # type: ignore[attr-defined]

from typing import Generic, TypeVar

# Make ``custom_components.filter_life_tracker`` importable from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 已安装真实 homeassistant 时不打桩，避免遮蔽依赖真实行为的测试
_INSTALL_STUBS = "homeassistant" not in sys.modules


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def _identity_decorator(func):
    return func


_T = TypeVar("_T")


class _Store(Generic[_T]):
    """Minimal stand-in for homeassistant.helpers.storage.Store."""

    def __init__(self, hass: Any, version: int, key: str, **kwargs: Any) -> None:
        self.hass = hass
        self.version = version
        self.key = key
        self.kwargs = kwargs
        self.saved: list[Any] = []
        self.load_result: Any = None

    async def async_save(self, data: Any) -> None:
        self.saved.append(data)

    async def async_load(self) -> Any:
        return self.load_result


if _INSTALL_STUBS:
    _module("homeassistant")
    _module("homeassistant.config_entries", ConfigEntry=type("ConfigEntry", (), {}))


    class _Platform:
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"


    _module(
        "homeassistant.const",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        EVENT_HOMEASSISTANT_STOP="homeassistant_stop",
        Platform=_Platform,
    )
    _module(
        "homeassistant.core",
        Event=type("Event", (), {}),
        EventStateChangedData=type("EventStateChangedData", (), {}),
        HomeAssistant=type("HomeAssistant", (), {}),
        callback=_identity_decorator,
    )
    _module(
        "homeassistant.helpers.dispatcher",
        async_dispatcher_connect=lambda *args, **kwargs: lambda: None,
        dispatcher_send=lambda *args, **kwargs: None,
    )
    _module(
        "homeassistant.helpers.event",
        async_call_later=lambda *args, **kwargs: lambda: None,
        async_track_state_change_event=lambda *args, **kwargs: lambda: None,
        async_track_time_interval=lambda *args, **kwargs: lambda: None,
    )
    _module("homeassistant.helpers.storage", Store=_Store)
