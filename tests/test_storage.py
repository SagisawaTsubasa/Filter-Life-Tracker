"""Storage layer unit tests: dirty-flag batching, retry after failed save."""

from __future__ import annotations

import asyncio

from custom_components.filter_life_tracker import storage
from custom_components.filter_life_tracker.const import STORAGE_KEY, STORAGE_VERSION
from custom_components.filter_life_tracker.storage import FilterLifeStore


def make_store(monkeypatch) -> tuple[FilterLifeStore, list]:
    saved: list = []
    fail = {"on": False}

    class _RecordingStore(storage._FilterLifeStore):
        async def async_save(self, data):
            if fail["on"]:
                raise OSError("disk full")
            saved.append(data)

    monkeypatch.setattr(storage, "_FilterLifeStore", _RecordingStore)
    return FilterLifeStore(hass=None), saved, fail


def test_flush_skipped_when_clean(monkeypatch):
    store, saved, _ = make_store(monkeypatch)
    asyncio.run(store.async_flush())
    assert saved == []


def test_flush_persists_dirty_state(monkeypatch):
    store, saved, _ = make_store(monkeypatch)
    store.get_entry_state("e1", "device")["filters"]["1"] = {"accumulated_usage": 5.0}
    store.mark_dirty("e1")
    asyncio.run(store.async_flush())
    assert len(saved) == 1
    assert saved[0]["device_entries"]["e1"]["filters"]["1"]["accumulated_usage"] == 5.0
    # 落盘成功后 dirty 清空：再 flush 不重复写
    asyncio.run(store.async_flush())
    assert len(saved) == 1


def test_failed_save_keeps_dirty_for_retry(monkeypatch):
    store, saved, fail = make_store(monkeypatch)
    store.get_entry_state("e1", "device")["filters"]["1"] = {"accumulated_usage": 1.0}
    store.mark_dirty("e1")

    fail["on"] = True
    asyncio.run(store.async_flush())
    assert saved == []

    fail["on"] = False
    asyncio.run(store.async_flush())
    assert len(saved) == 1


def test_store_version_and_key():
    store = FilterLifeStore(hass=None)
    assert store._store.version == STORAGE_VERSION
    assert store._store.key == STORAGE_KEY


def test_migrate_hook_contract():
    """三参签名命中 HA 的 3 参调用分支；恒等返回且旧版本显式拒绝。"""
    import inspect

    st = storage._FilterLifeStore(None, STORAGE_VERSION, STORAGE_KEY)
    assert len(inspect.signature(st._async_migrate_func).parameters) == 3
    assert asyncio.run(st._async_migrate_func(1, 1, {"x": 1})) == {"x": 1}
    try:
        asyncio.run(st._async_migrate_func(2, 0, {"x": 1}))
        raised = False
    except NotImplementedError:
        raised = True
    assert raised, "未实现的旧大版本必须显式拒绝而不是静默透传"
