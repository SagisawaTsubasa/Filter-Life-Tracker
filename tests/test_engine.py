"""Engine unit tests (design doc §14: 状态机迁移、级联触发/解除、重启恢复、
时钟回拨、去抖取消、增量聚合)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.filter_life_tracker import engine
from custom_components.filter_life_tracker.const import (
    CONF_CASCADE_FACTOR,
    CONF_COEFFICIENT,
    CONF_DEBOUNCE,
    CONF_FILTERS,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_TYPE,
    CONF_TARGET_STATE,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_TOTAL,
    SIGNAL_INCREMENT,
    SOURCE_TYPE_COUNT,
    SOURCE_TYPE_DURATION,
    STATE_ACCUMULATED_USAGE,
)
from custom_components.filter_life_tracker.engine import FilterRuntime


class FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


class FakeEvent:
    def __init__(self, new_state: str | None, old_state: str | None = None) -> None:
        old = FakeState(old_state) if old_state is not None else None
        new = FakeState(new_state) if new_state is not None else None
        self.data = {"old_state": old, "new_state": new}


class FakeEntry:
    def __init__(self, data: dict, options: dict | None = None) -> None:

        self.data = data
        self.options = options or {}
        self.entry_id = "test_entry"


class FakeHass:
    def __init__(self, states: dict[str, str] | None = None) -> None:
        self._states = states or {}

        class _States:
            def __init__(inner, mapping):
                inner.mapping = mapping

            def get(inner, entity_id):
                s = inner.mapping.get(entity_id)
                return FakeState(s) if s is not None else None

        self.states = _States(self._states)


class FakeStore:
    """Records persisted per-entry state without touching disk."""

    def __init__(self) -> None:
        self.data: dict = {}

    def get_entry_state(self, entry_id: str, entry_type: str) -> dict:
        return self.data.setdefault(entry_id, {"filters": {}})

    def mark_dirty(self, entry_id: str) -> None:
        pass


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start
        self._real = engine._utcnow

    def __call__(self) -> datetime:
        return datetime.fromtimestamp(self.t, tz=UTC)

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def attach(self, monkeypatch) -> FakeClock:
        monkeypatch.setattr(engine, "_utcnow", self)
        return self


def make_runtime(
    source_type: str = SOURCE_TYPE_DURATION,
    entry_type: str = ENTRY_TYPE_DEVICE,
    coefficient: float = 1.0,
    filters: dict | None = None,
    level2: dict | None = None,
    target_state: str = "heating",
    source_entity: str = "switch.heater",
    states: dict[str, str] | None = None,
) -> FilterRuntime:
    level1 = {"rated_time_days": 180, "rated_usage": 500.0, "warn_threshold": 20.0}
    all_filters = {"1": {**level1, **(filters or {})}}
    if level2:
        all_filters["2"] = {
            "rated_time_days": 180,
            "rated_usage": 500.0,
            "warn_threshold": 20.0,
            **level2,
        }
    data = {
        "entry_type": entry_type,
        CONF_SOURCE_ENTITY: source_entity,
        CONF_SOURCE_TYPE: source_type,
        CONF_TARGET_STATE: target_state,
        CONF_COEFFICIENT: coefficient,
        CONF_DEBOUNCE: 10,
        CONF_FILTERS: all_filters,
    }
    if entry_type == ENTRY_TYPE_TOTAL:
        data[CONF_SOURCE_ENTITY] = None
    return FilterRuntime(FakeHass(states), FakeEntry(data), FakeStore())


# ---------------------------------------------------------------------
# 状态机迁移（duration 采集器）
# ---------------------------------------------------------------------


def test_duration_accumulates_on_leaving_target(monkeypatch):
    clock = FakeClock().attach(monkeypatch)
    rt = make_runtime(coefficient=2.0)

    rt._on_duration_event(FakeEvent("heating"))
    assert rt._tracking_since is not None
    clock.advance(3600)
    rt._on_duration_event(FakeEvent("idle"))

    # +3600 s × 系数 2.0
    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == pytest.approx(7200.0)


def test_duration_unavailable_discards_segment(monkeypatch):
    clock = FakeClock().attach(monkeypatch)
    rt = make_runtime()

    rt._on_duration_event(FakeEvent("heating"))
    clock.advance(1800)
    rt._on_duration_event(FakeEvent(None))  # unavailable
    assert rt._tracking_since is None

    # 恢复后只累计新片段
    rt._on_duration_event(FakeEvent("heating"))
    clock.advance(600)
    rt._on_duration_event(FakeEvent("idle"))
    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == pytest.approx(600.0)


# ---------------------------------------------------------------------
# 时钟回拨
# ---------------------------------------------------------------------


def test_duration_negative_delta_clamped(monkeypatch):
    clock = FakeClock().attach(monkeypatch)
    rt = make_runtime()

    rt._on_duration_event(FakeEvent("heating"))
    clock.advance(-5000)  # NTP 回拨
    rt._on_duration_event(FakeEvent("idle"))

    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == 0.0


# ---------------------------------------------------------------------
# 去抖（count 采集器）
# ---------------------------------------------------------------------


def test_count_debounce_confirm(monkeypatch):
    clock = FakeClock().attach(monkeypatch)
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT, states={"switch.heater": "heating"})
    scheduled = []
    monkeypatch.setattr(
        engine, "async_call_later",
        lambda hass, delay, cb: scheduled.append((delay, cb)) or (lambda: None),
    )

    rt._on_count_event(FakeEvent("heating", old_state="idle"))
    assert len(scheduled) == 1
    delay, cb = scheduled[0]
    assert delay == 10

    clock.advance(10)
    cb(None)  # 去抖窗口结束后确认
    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == pytest.approx(1.0)


def test_count_debounce_cancelled_on_leaving_target(monkeypatch):
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT, states={"switch.heater": "heating"})
    cancelled = {"n": 0}
    monkeypatch.setattr(
        engine, "async_call_later",
        lambda hass, delay, cb: (lambda: cancelled.__setitem__("n", cancelled["n"] + 1)),
    )

    rt._on_count_event(FakeEvent("heating", old_state="idle"))
    rt._on_count_event(FakeEvent("idle", old_state="heating"))  # 窗口内离开

    assert cancelled["n"] == 1
    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == 0.0


# ---------------------------------------------------------------------
# 重启恢复（count 抑制边沿）
# ---------------------------------------------------------------------


def test_count_restart_suppression(monkeypatch):
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT, states={"switch.heater": "heating"})
    scheduled = []
    monkeypatch.setattr(
        engine, "async_call_later",
        lambda hass, delay, cb: scheduled.append(cb) or (lambda: None),
    )
    rt._suppress_next_rise = True  # async_setup 检测到已处于目标状态时置位

    # 重启回声：同一条上升沿被吞掉
    rt._on_count_event(FakeEvent("heating", old_state="idle"))
    assert not scheduled
    assert rt._suppress_next_rise is False

    # 第二次上升沿正常计数
    rt._on_count_event(FakeEvent("idle", old_state="heating"))
    rt._on_count_event(FakeEvent("heating", old_state="idle"))
    assert len(scheduled) == 1


# ---------------------------------------------------------------------
# 级联触发/解除
# ---------------------------------------------------------------------


def test_count_suppression_released_after_real_exit(monkeypatch):
    """重启抑制在观测到真实离开目标状态后必须解除，否则吞掉下一个新周期。"""
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT, states={"switch.heater": "heating"})
    scheduled = []
    monkeypatch.setattr(
        engine, "async_call_later",
        lambda hass, delay, cb: scheduled.append(cb) or (lambda: None),
    )
    rt._suppress_next_rise = True

    # 周期正常结束：target → 其他（真实离开）
    rt._on_count_event(FakeEvent("idle", old_state="heating"))
    assert rt._suppress_next_rise is False

    # 全新周期：其他 → target，应正常进入去抖计数
    rt._on_count_event(FakeEvent("heating", old_state="idle"))
    assert len(scheduled) == 1


def test_cascade_accelerates_when_level1_exhausted(monkeypatch):
    # count 型：额定用量 500 即 500，便于直接构造耗尽
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT, level2={CONF_CASCADE_FACTOR: 2.0})
    rt.filters[1][STATE_ACCUMULATED_USAGE] = 500.0  # 用量轨道耗尽

    rt._apply_usage_increment(10.0)

    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == pytest.approx(510.0)
    assert rt.filters[2][STATE_ACCUMULATED_USAGE] == pytest.approx(20.0)  # 10 × 2.0


def test_cascade_released_after_reset(monkeypatch):
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT, level2={CONF_CASCADE_FACTOR: 2.0})
    rt.filters[1][STATE_ACCUMULATED_USAGE] = 500.0
    rt._apply_usage_increment(10.0)
    assert rt.filters[2][STATE_ACCUMULATED_USAGE] == pytest.approx(20.0)

    # 重置一级（用量清零）→ 后续增量不再加速（ADR-003：不回滚已加速部分）；
    # 二级仍收到基础增量 10，只是没有 ×2 加速
    rt.filters[1][STATE_ACCUMULATED_USAGE] = 0.0
    rt._apply_usage_increment(10.0)
    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == pytest.approx(10.0)
    assert rt.filters[2][STATE_ACCUMULATED_USAGE] == pytest.approx(30.0)


# ---------------------------------------------------------------------
# 增量聚合（总前置）与广播
# ---------------------------------------------------------------------


def test_total_prefilter_aggregates_without_rebroadcast(monkeypatch):
    rt = make_runtime(entry_type=ENTRY_TYPE_TOTAL, source_type=SOURCE_TYPE_COUNT)
    sent = []
    monkeypatch.setattr(engine, "dispatcher_send", lambda hass, sig, *a: sent.append((sig, a)))

    rt._apply_usage_increment(3.0)  # 走默认广播路径，钉住 ADR-004 的类型守卫

    assert rt.filters[1][STATE_ACCUMULATED_USAGE] == pytest.approx(3.0)
    # 总前置不再向下广播增量信号（ADR-004）；SIGNAL_UPDATED 通知不受限
    assert all(sig != SIGNAL_INCREMENT.format(rt.entry_id) for sig, _ in sent)


def test_device_entry_broadcasts_increment(monkeypatch):
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT)
    sent = []
    monkeypatch.setattr(engine, "dispatcher_send", lambda hass, sig, *a: sent.append((sig, a)))

    rt._apply_usage_increment(2.0)

    assert sent and sent[0][0] == SIGNAL_INCREMENT.format(rt.entry_id)
    assert sent[0][1] == (2.0,)


# ---------------------------------------------------------------------
# 双轨计算边界
# ---------------------------------------------------------------------


def test_remaining_pct_clamped(monkeypatch):
    clock = FakeClock().attach(monkeypatch)
    # count 型：额定 500 次即 500，直接构造超量
    rt = make_runtime(source_type=SOURCE_TYPE_COUNT)

    # 用量超额定 → 0%；时间刚装 → 100%
    rt.filters[1][STATE_ACCUMULATED_USAGE] = 600.0
    assert rt.usage_remaining_pct(1) == 0.0
    assert rt.time_remaining_pct(1) == 100.0
    assert rt.main_pct(1) == 0.0
    assert rt.expired_on(1) is True
    assert rt.warn_on(1) is True

    clock.advance(181 * 86400)  # 时间轨道也过期
    assert rt.time_remaining_pct(1) == 0.0


def test_rated_usage_unit_follows_source_type():
    duration = make_runtime(source_type=SOURCE_TYPE_DURATION)
    count = make_runtime(source_type=SOURCE_TYPE_COUNT)
    assert duration.rated_usage(1) == pytest.approx(500.0 * 3600)
    assert count.rated_usage(1) == pytest.approx(500.0)
