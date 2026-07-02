# Architecture — tendata-customer-enricher

> 系统架构设计，描述抓取内核与任务/报告/回传层的分离。

## 1. 分层架构

```
┌─────────────────────────────────────────────────┐
│               外部调用层                         │
│  OpenClaw / 影刀 / 人工 / CI                    │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│            任务调度层 (Task Layer)               │
│                                                   │
│  task_server.py     — HTTP 任务接口              │
│                     所有操作通过 task_store        │
│  queue_worker.py    — 队列消费器                  │
│                     原子出队 + 执行 + 写回         │
│  run_task.py        — 任务编排入口               │
│                     只提交，不执行                │
│  task_store.py      — ★ 统一任务仓库              │
│                     SQLite 持久化，所有组件共享     │
│  models.py          — 数据模型                    │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│         报告生成层 (Report Layer)                │
│                                                   │
│  generate_report.py  — Markdown 报告生成         │
│  export_results.py   — Excel 结果导出            │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│           抓取内核 (Core Scraper)                │
│                                                   │
│  run_batch.py           — 批处理编排             │
│  extract_tendata_fields.py — 浏览器抓取 + 匹配   │
│  normalize_input.py     — 输入归一化             │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│           基础设施层 (Infra)                     │
│                                                   │
│  Chrome (CDP 9222)     — 浏览器                  │
│  影刀                   — 登录 + 验证码           │
│  Playwright            — CDP 连接                │
└─────────────────────────────────────────────────┘
```

## 2. 组件职责

### 2.1 抓取内核（Core Scraper）

- **职责**：连接腾道、搜索公司、提取数据、返回结构化结果
- **入口**：`run_batch.py`（批处理）/ `extract_tendata_fields.py`（单条）
- **依赖**：Playwright、Chrome CDP
- **契约**：输入 EnrichmentRow，输出 EnrichmentRow（带抓取字段）
- **独立性**：不依赖任务层、不感知调度

### 2.2 统一任务仓库（Task Store）★

- **文件**：`scripts/task_store.py`
- **持久化**：`data/tasks.db`（SQLite，WAL 模式）
- **全局实例**：`task_store`（单例）
- **核心操作**：
  - `create(task)` — 创建任务，写入 SQLite，状态设为 QUEUED
  - `get(task_id)` — 按 ID 查询
  - `dequeue()` — 原子出队（QUEUED → RUNNING，SQLite 事务）
  - `update_status(task_id, status, ...)` — 更新状态 + 结果 + 产物路径
  - `list(status)` — 列出任务
  - `stats()` — 各状态计数

**所有组件必须通过 task_store 交互，不得各自维护内存任务对象。**

### 2.3 任务调度层（Task Layer）

三个入口 + 一个仓库：

| 组件 | 职责 | 读写关系 |
|---|---|---|
| `task_server.py` | HTTP API，接收外部请求 | 写 create() / 读 get() / 读 list() |
| `run_task.py` | CLI 任务提交，支持 --wait 模式 | 写 create() / 读 get() |
| `queue_worker.py` | 后台消费，单线程串行 | 写 dequeue() / 写 update_status() |
| `task_store.py` | 统一仓库（SQLite） | 所有读写 |

**创建任务后，任务进入 SQLite 队列，由 queue_worker 异步消费。**

### 2.4 报告生成层（Report Layer）

- **职责**：将抓取结果转换为可读报告
- **格式**：Markdown（人工可读）+ JSON（机器可读）+ Excel（业务使用）
- **触发**：queue_worker 执行抓取完成后自动调用

## 3. 数据流

```
输入 Excel/JSON
    │
    ▼
task_server.py / run_task.py → task_store.create() → SQLite (QUEUED)
                                                     │
                                                     ▼
                                        queue_worker.dequeue() (QUEUED→RUNNING)
                                                     │
                                                     ▼
                                        run_batch.run_batch_for_task() → EnrichmentResult[]
                                                     │
                                                     ▼
                                        generate_report.py → Markdown + JSON
                                                     │
                                                     ▼
                                        task_store.update_status(COMPLETED, results_json=...)
                                                     │
                                                     ▼
output/ 目录 + SQLite → 回传给调用方
```

## 6. 实现状态

### 6.1 已实现（代码完成，可本地运行）

| 组件 | 文件 | 说明 |
|---|---|---|
| 统一任务仓库 | `task_store.py` | SQLite 持久化，原子 dequeue (RETURNING)，cancel，stale recovery，重复 task_id 保护 |
| HTTP 任务接口 | `task_server.py` | 创建/查询/列表/取消/健康检查 |
| 队列消费器 | `queue_worker.py` | 轮询出队 + 执行 + 写回 |
| 任务提交 | `run_task.py` | JSON/Excel/CLI 提交，--wait 仅本地调试 |
| 报告生成 | `generate_report.py` | Markdown 单条报告 + 任务级 JSON/Markdown 汇总 |
| 结果导出 | `export_results.py` | Excel 结果导出 |
| 抓取内核 | `run_batch.py` + `extract_tendata_fields.py` | 浏览器抓取 + 匹配计算 |
| 数据模型 | `models.py` | Task / TaskStatus / CustomerInput / EnrichmentResult |

### 6.2 可联调（接口已就绪，待外部系统接入）

| 能力 | 说明 |
|---|---|
| HTTP API 联调 | 通过 `curl` / Postman 调用 task_server，提交任务、查询状态、获取结果 |
| CLI 联调 | `run_task.py --input` 提交 → `queue_worker.py` 消费 → 查看 output/ 产物 |

### 6.3 已预留（代码就绪，待业务接入）

| 功能 | 预留方式 | 说明 |
|---|---|---|
| 飞书消息推送 | `callback_mode` / `callback_target` / `delivery_status` / `delivered_at` / `submitted_by` | models.py 已含字段，task_store 已序列化，queue_worker 写回后可触发 |
| 任务取消 | `task_store.cancel()` + `task_server POST /api/task/cancel` | 逻辑已实现，仅允许取消 PENDING/QUEUED |
| 队列容量控制 | `task_store.create(max_queue_size=50)` | 最多 50 个 QUEUED 任务排队，超限抛 ValueError |
| 单任务最大客户数 | `task_server._validate_task()` + `task_server.py` 校验 | 一个任务最多 50 家公司 |
| 僵死任务回收 | `task_store.recover_stale_running(timeout_seconds=1800)` | 超时 RUNNING → 重新 QUEUED |
| 超时控制 | 代码中 `timeout_seconds` 参数 | run_task --wait 默认 1800s |

### 6.4 后续阶段（未实现）

| 功能 | 说明 |
|---|---|
| OpenClaw Skill 注册 | 注册为 OpenClaw 可调用的 skill |
| 多执行机分布式队列 | 当前单机单 worker，未来支持多执行机竞争出队 |
| 定时任务 | 定期自动刷新海关数据 |

## 4. 与影刀的协作模式

详见 [references/external-integration.md](references/external-integration.md)。

## 5. 未来扩展方向

| 方向 | 说明 | 当前状态 |
|---|---|---|
| OpenClaw Skill 注册 | 注册为 OpenClaw 可调用的 skill | 预留 |
| 飞书消息推送 | 结果自动推送飞书 | 预留 (callback_mode) |
| 多执行机支持 | 分布式执行机注册 | 预留 |
| 定时任务 | 定期自动刷新海关数据 | 预留 |
