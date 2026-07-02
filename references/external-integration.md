# External Integration — tendata-customer-enricher

> 外部系统如何调用 tendata-customer-enricher：OpenClaw 提交任务、影刀调度登录、结果回传闭环。

## 1. 整体调用关系

```
                    ┌──────────────┐
                    │   OpenClaw   │  通过 HTTP API 提交/查询/取结果
                    │  (飞书机器人) │
                    └──────┬───────┘
                           │ HTTP (POST/GET)
                           ▼
┌──────────┐      ┌──────────────────┐      ┌──────────────────┐
│   影刀   │─────▶│  task_server.py  │─────▶│  task_store.py   │
│  登录+   │ 触发  │  HTTP API 接口    │      │  SQLite 队列      │
│  验证码  │      └──────────────────┘      └────────┬─────────┘
└──────────┘                    ▲                     │
                                │                     ▼
                     ┌──────────────────┐      ┌──────────────────┐
                     │  结果回传         │      │  queue_worker.py │
                     │  callback.py     │◀─────│  消费 + 抓取      │
                     │  webhook/feishu  │      └──────────────────┘
                     └──────────────────┘
```

## 2. OpenClaw 调用接口设计

### 2.1 任务提交（OpenClaw → task_server）

**方式**：HTTP POST

**端点**：`POST http://<执行机IP>:8080/api/task/create`

**请求体**：
```json
{
  "task_id": "OC-20260421-001",
  "customers": [
    {
      "customer_name": "SCOPE METALS GROUP LTD",
      "country_region": "Israel"
    }
  ],
  "source": "openclaw",
  "options": {
    "generate_report": true,
    "batch_size": 10
  },
  "callback": {
    "callback_mode": "webhook",
    "callback_target": "https://your-webhook-url.com/callback",
    "submitted_by": "openclaw-session-xxx"
  }
}
```

**响应**：
```json
{
  "task_id": "OC-20260421-001",
  "status": "queued",
  "message": "task created and enqueued"
}
```

### 2.2 状态查询（OpenClaw → task_server）

**方式**：HTTP GET（轮询）

**端点**：`GET http://<执行机IP>:8080/api/task/status?task_id=OC-20260421-001`

**响应**：
```json
{
  "task_id": "OC-20260421-001",
  "source": "openclaw",
  "status": "running",
  "created_at": "2026-04-21T10:00:00",
  "started_at": "2026-04-21T10:00:05"
}
```

### 2.3 获取结果（OpenClaw → task_server）

**方式**：HTTP GET（任务完成后调用）

**端点**：`GET http://<执行机IP>:8080/api/task/result?task_id=OC-20260421-001`

**响应**：
```json
{
  "task_id": "OC-20260421-001",
  "status": "completed",
  "results": [
    {
      "matched_company_name": "...",
      "match_status": "confirmed",
      "match_confidence": 95,
      "top_products": [...],
      "top_suppliers": [...],
      ...
    }
  ],
  "artifacts": {
    "excel_path": "output/result_XXX.xlsx",
    "json_path": "output/result_XXX.json",
    "report_path": "output/report_XXX.md"
  }
}
```

### 2.4 OpenClaw 调用流程

```
OpenClaw (飞书侧)                          tendata 执行机
     │                                         │
     │  1. POST /api/task/create               │
     │────────────────────────────────────────▶│ task_store.create() → QUEUED
     │  201 {"task_id":"OC-001","status":"queued"}│
     │◀────────────────────────────────────────│
     │                                         │ queue_worker 出队 → RUNNING
     │  3. GET /api/task/status?task_id=OC-001 │
     │────────────────────────────────────────▶│
     │  200 {"status":"running"}               │ queue_worker 抓取...
     │◀────────────────────────────────────────│
     │  4. GET /api/task/status?task_id=OC-001 │
     │────────────────────────────────────────▶│
     │  200 {"status":"completed"}             │ queue_worker 写回 → COMPLETED
     │◀────────────────────────────────────────│
     │  5. GET /api/task/result?task_id=OC-001 │
     │────────────────────────────────────────▶│
     │  200 {"status":"completed","results":...}│
     │◀────────────────────────────────────────│
     │  6. 飞书消息推送结果                      │
     ▼                                         ▼
```

### 2.5 OpenClaw MCP 集成（预留）

OpenClaw 可通过 MCP tool 直接调用 HTTP API，无需手动 curl：

```yaml
# agents/openai.yaml 预留配置
tools:
  - name: tendata_submit_task
    description: "提交腾道海关数据抓取任务"
    input_schema:
      task_id: string
      customers: array
    http:
      method: POST
      url: "http://localhost:8080/api/task/create"

  - name: tendata_get_result
    description: "获取腾道任务结果"
    input_schema:
      task_id: string
    http:
      method: GET
      url: "http://localhost:8080/api/task/result?task_id={task_id}"
```

## 3. 影刀排队调度设计

### 3.1 影刀职责

| 职责 | 说明 |
|---|---|
| 启动 Chrome | `start_tendata_helper.bat` 或影刀直接启动 Chrome |
| 登录腾道 | 影刀填入账号密码、处理滑条验证码 |
| 保持浏览器在线 | 定期检查页面是否跳转至登录页，必要时重新登录 |
| 触发任务 | 通过 HTTP API 提交任务或直接写 SQLite |
| 查询结果 | 通过 HTTP API 或读取 SQLite |
| 推送飞书 | 读取结果后通过影刀流程推送飞书（当前待接入） |

### 3.2 影刀 RPA 流程（伪代码）

```
┌─────────────────────────────────────────┐
│ 影刀 RPA 主流程                          │
│                                         │
│ 1. 启动 Chrome (CDP 9222)               │
│ 2. 登录腾道 + 处理滑条验证码             │
│ 3. 确认登录态有效（访问 bizr.tendata.cn）│
│ 4. 调用 POST /api/task/create 提交任务  │
│ 5. 轮询 GET /api/task/status            │
│    - running → 继续等待                  │
│    - completed → 进入步骤 6              │
│    - failed → 重新登录 → 重试            │
│ 6. 调用 GET /api/task/result 获取结果   │
│ 7. 推送飞书给发起人                      │
│ 8. 等待新任务（循环回步骤 4）            │
└─────────────────────────────────────────┘
```

### 3.3 登录态维护

```
Chrome 运行 ─────▶ queue_worker 消费任务
                      │
        ┌─────────────┤
        │ 检测到登录过期│ (TEN_LOGIN_REQUIRED)
        ▼             │
影刀重新登录 ─────────┘
        │
        ▼  任务重新 QUEUED
    task_store.recover_stale_running()
    queue_worker 重新消费
```

## 4. 结果回传设计

### 4.1 回传模式

| 模式 | callback_mode | 触发方式 | 说明 |
|---|---|---|---|
| 轮询（默认） | `poll` | 无 | 调用方自行通过 HTTP API 查询结果 |
| Webhook | `webhook` | POST JSON | 抓取完成后自动 POST 结果到指定 URL |
| 飞书 | `feishu` | POST 卡片 | 通过飞书机器人 Webhook 发送富文本卡片 |

### 4.2 任务创建时指定回传方式

```json
{
  "task_id": "T-001",
  "customers": [...],
  "callback": {
    "callback_mode": "webhook",
    "callback_target": "https://your-server.com/tendata-callback",
    "submitted_by": "user-123"
  }
}
```

### 4.3 回传触发点

```
queue_worker.execute_task():
    1. run_batch_for_task(task) → results
    2. generate_report(task) → json_path, report_path
    3. task_store.update_status(COMPLETED) → 写回 SQLite
    4. send_callback(task) ← 此处触发回传
       - if callback_mode == "webhook": POST JSON to callback_target
       - if callback_mode == "feishu": POST 飞书卡片 to callback_target
       - if callback_mode == "poll": 无需操作
```

### 4.4 回传失败处理

- 回传失败**不影响**任务状态（任务已标记 COMPLETED）
- 回传失败信息写入 `callback.delivery_status = "failed"`
- 调用方可通过 API 查询 `delivery_status` 判断回传是否成功
- 后续可增加回传重试队列（预留）

## 5. 稳定服务入口

### 5.1 服务启动脚本

执行机端启动服务：

```bash
# 启动任务服务（HTTP API）
python scripts/task_server.py --port 8080 &

# 启动队列消费（后台）
python scripts/queue_worker.py &

# 健康检查
curl http://localhost:8080/api/health
```

### 5.2 Windows 后台运行

```bat
@echo off
start /B python scripts\task_server.py --port 8080
start /B python scripts\queue_worker.py
echo Services started.
echo   API: http://localhost:8080/api/health
echo   Create: POST /api/task/create
echo   Status: GET /api/task/status?task_id=XXX
echo   Result: GET /api/task/result?task_id=XXX
pause
```

### 5.3 服务约束

| 约束 | 值 |
|---|---|
| 最大并发 | 1（单浏览器） |
| 队列容量 | 50（QUEUED 任务上限） |
| 单任务客户数 | 50（一个任务最多公司数） |
| 任务超时 | 30 min（可配置） |

## 6. 实现状态

| 能力 | 状态 | 说明 |
|---|---|---|
| HTTP API（create/status/result/list/cancel） | **已实现** | task_server.py |
| 任务队列（SQLite 持久化 + 原子出队） | **已实现** | task_store.py |
| 队列消费（抓取 + 报告 + 写回） | **已实现** | queue_worker.py |
| Webhook 回传 | **已实现** | callback.py |
| 飞书回传 | **已实现** | callback.py（待真实飞书环境测试） |
| OpenClaw MCP 集成 | **预留** | agents/openai.yaml 待填充 |
| 影刀 RPA 流程 | **待实现** | 伪代码已设计，需影刀脚本编写 |
| 回传重试 | **后续** | 当前回传失败不重试 |
| 登录态自动检测+恢复 | **后续** | 当前需影刀手动处理 |
