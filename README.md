# Filter Life Tracker · 滤芯寿命管理

[English](#english) | [中文](#中文)

---

## 中文

一个 Home Assistant 自定义集成：无需改造净水器硬件，复用已有 HA 实体（热水器、洗衣机等）的状态变化作为用量积分源，通过**双轨判定（时间 + 用量）**输出滤芯剩余寿命百分比，支持多级滤芯级联与全屋总前置聚合。

### 特性

- **无代码侵入**：复用已有 HA 设备实体作为积分源，不改造任何硬件
- **状态边界驱动**：基于 `state_changed` 事件边沿采集，非轮询，低 CPU 开销
- **双轨寿命**：主寿命 = min(时间剩余%, 用量剩余%)，先到期的维度主导，保证不超时
- **两种积分类型**：时间型（热水器持续加热）与次数型（洗衣机运行周期，带去抖）
- **多级级联**：1 级滤芯用量耗尽（物理堵塞）后，2 级用量积分按级联系数自动加速（负荷转嫁）
- **总前置聚合**：全屋总前置通过增量推送聚合下游设备用量，解耦不联动
- **合并落盘**：10 分钟批量持久化 + 关闭强刷，对 SD 卡友好
- **中英文界面**：完整的 Config Flow / Options Flow 双语配置界面

### 实体（每级滤芯）

| 实体 | 说明 |
|------|------|
| `sensor.*_life_{n}` | 主寿命%（0–100） |
| `sensor.*_life_{n}_time` | 时间轨道剩余%（诊断用） |
| `sensor.*_life_{n}_usage` | 用量轨道剩余%（诊断用） |
| `binary_sensor.*_warn_{n}` | 低于预警阈值（默认 20%）时置 ON |
| `binary_sensor.*_expired_{n}` | 寿命归零时置 ON（可用于自动化） |
| `button.*_reset_{n}` | 更换滤芯后手动重置 |

### 安装

**HACS（推荐）**：HACS → 自定义存储库 → 添加本仓库 URL → 类别选 Integration → 安装 → 重启 HA。

**手动**：将 `custom_components/filter_life_tracker` 复制到 HA 配置目录的 `custom_components/` 下，重启 HA。

### 配置

设置 → 设备与服务 → 添加集成 → 搜索「滤芯寿命管理」：

1. **用水设备**：选积分源实体（如 `water_heater.kitchen`）→ 填目标状态（如 `heating`）→ 选积分类型 → 配置 1 级/2 级滤芯
2. **全屋总前置**：多选下游设备条目（积分类型需一致）→ 配置滤芯

修正系数首次建议保持 1.0，换一次滤芯后按「实际使用时长 ÷ 积分显示时长」反推校准。

---

## English

A Home Assistant custom integration that tracks water filter life **without any hardware modification**. It reuses existing HA entities (water heaters, washing machines, …) as usage sources, applies **dual-track estimation (time + usage)**, supports multi-stage cascade acceleration, and aggregates downstream usage into a whole-house pre-filter entry.

### Features

- **Zero hardware changes**: existing HA entities drive usage accumulation
- **Boundary-driven**: `state_changed` edge-based tracking, no polling, minimal CPU
- **Dual-track life**: main life = min(time remaining %, usage remaining %)
- **Two tracking types**: duration (water heater) and count (washing machine, debounced)
- **Cascade**: when the stage-1 filter's usage track is exhausted, stage 2 accumulates faster (load transfer)
- **Pre-filter aggregation**: increment-push model, decoupled from downstream resets
- **Batched persistence**: 10-minute flush + forced flush on shutdown, SD-card friendly
- **Bilingual UI**: full Chinese / English config & options flow

### Installation

**HACS**: add this repository as a custom repository (Integration type), install, restart HA.

**Manual**: copy `custom_components/filter_life_tracker` into your HA `custom_components/` directory and restart.

### Configuration

Settings → Devices & Services → Add Integration → "Filter Life Tracker":

1. **Device entry**: pick a source entity → enter its target state (e.g. `heating`) → choose tracking type → configure filter stages
2. **Total pre-filter entry**: multi-select downstream device entries (same tracking type required) → configure the cartridge

Keep the correction coefficient at 1.0 initially; after the first filter replacement, calibrate it as `actual hours ÷ tracked hours`.

### License

MIT

## 更新日志 / Changelog

### 1.2.0
- 修复：`FilterRuntime` 初始化引用未定义变量（`entry_id` → `self.entry_id`）——此前任何配置条目建立即崩溃，这是 FLT 无法完成首次配置的直接原因  
  Fixed: `FilterRuntime.__init__` referenced an undefined variable (`entry_id` → `self.entry_id`) — entry setup crashed on first configuration, the root cause of FLT never being configurable
- 修复：存储迁移钩子改为覆写 `Store._async_migrate_func`（HA 新版已移除 `migrate_func=` 构造参数，按旧计划实现会直接 TypeError）  
  Fixed: storage migration hook now overrides `Store._async_migrate_func` (recent HA removed the `migrate_func=` constructor argument)
- 新增：设备级创建流程末尾的确认页（设计文档 §11.1 第 8 项）：汇总积分源/目标状态/系数/各级滤芯参数后提交  
  Added: confirmation page at the end of the device-entry flow (design doc §11.1 item 8) summarizing source/target/coefficient/filter stages
- 新增：单元测试 18 项（设计文档 §14）：duration/count 状态机、级联触发与解除、重启抑制、时钟回拨、去抖取消、总前置聚合、存储 dirty-flag 重试  
  Added: 18 unit tests (design doc §14): duration/count state machines, cascade trigger/release, restart suppression, clock rollback, debounce cancel, total-prefilter aggregation, storage dirty-flag retry
- 修复：重启抑制标志在观测到真实离开目标状态后清除（否则吞掉下一个真实周期的计数）；最后一个条目卸载后 store 实例一并释放（否则批量落盘定时器永久失效）；确认页摘要改纯符号单位；迁移钩子对未实现的旧大版本显式拒绝  
  Fixed: restart-suppression flag cleared after a real exit from the target state; shared store released with its timers on last unload; symbol-only confirm summary; loud failure on unmigrated storage versions  
- 清理：ruff check 告警清零  
  Chores: ruff check warnings cleared

### 1.1.0（2026-09-05 审计修复批次，随 V1.0.2 之后提交）
- 存储单例竞态、重启计数抑制、options 校验等审计修复  
  September audit fixes: storage singleton race, restart count suppression, options validation
