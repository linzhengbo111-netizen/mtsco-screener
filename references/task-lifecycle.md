# Task Lifecycle — tendata-customer-enricher

> 描述一个完整任务从创建到回传的生命周期。
> **统一任务仓库**: `scripts/task_store.py`（SQLite，`data/tasks.db`）

## 1. 整体架构

```
OpenClaw (飞书服务器)
    │
    ▼
影刀调度器 (RPA + 排队)
    │
    ├──▶ 登录腾道 + 处理滑条验证码 ──▶ Chrome 9222
    │
    ▼
tendata-customer-enricher (本地/专机)
    │
    ├──▶ task_server.py (HTTP)  ──┐
    ├──▶ run_task.py    (CLI)    ├──▶ task_store.py (SQLite)
    └──▶ queue_worker.py (后台) ─┘
         │
         ├──▶ run_batch.py (抓取内核)
         ├──▶ generate_report.py (报告)
         ▼
结果产物 → task_store 持久化 → 回传
```

## 2. 任务状态机

```
PENDING ──▶ QUEUED ──▶ RUNNING ──▶ COMPLETED
  │           │          │
  │           │          ├──▶ PARTIAL_FAILED  (部分成功)
  │           │          │
  │           │          └──▶ FAILED          (全部失败)
  │           │
  │           └──▶ CANCELLED  (上游取消)
  │
  └──▶ REJECTED  (输入校验失败)
```

## 3. 生命周期阶段

### 3.1 创建（PENDING → QUEUED）

**入口**：
- `POST /api/task/create` → `task_server.py` → `task_store.create()`
- `python run_task.py --input ...` → `run_task.py` → `task_store.create()`

**行为**：
- 输入通过 `io-contract.md` 校验
- 校验失败 → REJECTED（仍写入 SQLite，可追溯）
- 校验通过 → QUEUED（写入 `data/tasks.db`）
- **不执行抓取**，只持久化

### 3.2 出队（QUEUED → RUNNING）

**入口**：`queue_worker.py` 轮询 `task_store.dequeue()`

**行为**：
- SQLite 原子操作：`UPDATE tasks SET status='running' WHERE task_id=(SELECT ... WHERE status='queued' LIMIT 1)`
- 并发安全：同一任务只被一个 worker 取出
- 无任务时 sleep 2s 继续轮询

### 3.3 执行（RUNNING）

由 `queue_worker.execute_task()` 调度：

1. 从 `task_store.get(task_id)` 加载任务
2. 检查 Chrome 9222 端口在线
3. 检查腾道登录态
4. 调用 `run_batch.py` 执行抓取
5. 抓取完成 → 生成 Excel + JSON + Markdown 报告
6. `task_store.update_status()` 写回结果 + 最终状态

### 3.4 完成（COMPLETED / PARTIAL_FAILED / FAILED）

- 结果已通过 `task_store.update_status(results_json=...)` 持久化
- `GET /api/task/result` 或 `run_task.py --status TASK-XXX` 可查询

### 3.5 回传

- 结果已通过 `task_store.update_status(results_json=...)` 持久化到 SQLite
- `GET /api/task/result` 或 `run_task.py --status TASK-XXX` 可查询结果
- **当前状态**：结果已持久化，回传字段（callback_*）已预留，自动推送待外部系统接入

## 4. 统一任务仓库

| 属性 | 值 |
|---|---|
| 位置 | `scripts/task_store.py` |
| 持久化 | `data/tasks.db`（SQLite） |
| 全局实例 | `task_store`（单例） |
| 线程安全 | 线程本地连接 + WAL 模式 |

**所有组件必须通过 `task_store` 交互，不得各自维护内存任务对象。**

| 组件 | 使用方式 |
|---|---|
| `task_server.py` | `task_store.create()`, `task_store.get()`, `task_store.list()` |
| `run_task.py` | `task_store.create()`（提交），`task_store.get()`（查询） |
| `queue_worker.py` | `task_store.dequeue()`（原子出队），`task_store.get()`, `task_store.update_status()` |

## 5. 并发约束

| 约束 | 值 | 说明 |
|---|---|---|
| 最大并发任务数 | 1 | 腾道单浏览器会话 |
| 队列容量（排队中任务数） | 50 | 最多同时有 50 个 QUEUED 任务等待执行 |
| 单任务最大客户数 | 50 | 一个任务里最多包含 50 家公司 |
| 任务超时 | 30 min | 可配置 |

## 6. 文件约定

| 路径 | 用途 |
|---|---|
| `data/tasks.db` | SQLite 任务队列（持久化） |
| `data/input/` | 待处理输入文件 |
| `output/` | 结果输出目录 |
| `logs/` | 运行日志 |

## 7. 实现状态

### 已实现（代码完成，可本地运行）

- **任务创建**：`task_store.create()` → SQLite 持久化，状态设为 QUEUED
- **重复 task_id 保护**：`task_store.create()` 检测已存在的 task_id，明确拒绝
- **原子出队**：`task_store.dequeue()` → `UPDATE ... RETURNING`，一次原子操作完成出队并返回
- **状态更新**：`task_store.update_status()` → 写回结果 + 产物路径 + 最终状态
- **任务取消**：`task_store.cancel()` → 仅允许 PENDING/QUEUED，RUNNING/COMPLETED/FAILED 不可取消
- **队列容量控制**：`max_queue_size=50`（最多 50 个 QUEUED 任务），超限拒绝
- **僵死任务回收**：`task_store.recover_stale_running(timeout=1800)` → 超时 RUNNING 重置为 QUEUED
- **HTTP 接口**：`task_server.py` 提供 create/status/result/list/cancel/health
- **--wait 模式**：`run_task.py --wait` 启动内嵌 worker 线程，**仅限本地调试使用**

### 可联调（接口已就绪，待外部系统接入）

- 通过 `curl` / Postman 调用 task_server HTTP API 进行联调
- OpenClaw / 影刀可通过 HTTP API 提交任务、查询状态、获取结果

### 已预留（代码就绪，待业务接入）

- **飞书消息推送**：`callback_*` 字段已写入 task_store，queue_worker 写回后可触发
- **OpenClaw 回传**：`submitted_by` / `callback_mode` / `callback_target` / `delivery_status` / `delivered_at`

### 后续阶段（未实现）

- 多执行机分布式队列
- 定时任务自动刷新
- OpenClaw Skill 注册
