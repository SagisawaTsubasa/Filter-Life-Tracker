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

### 设计文档

完整技术设计（状态机、ADR 决策记录、边界处理）见 [HA_净水器滤芯寿命管理集成_技术设计文档_v1.1.md](HA_净水器滤芯寿命管理集成_技术设计文档_v1.1.md)。

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
