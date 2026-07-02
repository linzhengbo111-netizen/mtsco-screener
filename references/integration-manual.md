# 联调操作手册 — tendata-customer-enricher

> 最小可落地的联调方案：执行机启动服务 → OpenClaw 提交任务 → 影刀处理登录 → 结果回传。

## 0. 架构概览

```
┌──────────────┐          ┌──────────────────────────┐          ┌──────────────┐
│   OpenClaw   │──HTTP───▶│  执行机 (本地/专机)       │          │    影刀      │
│  (飞书侧)    │          │                           │◀──登录──│  (RPA 侧)    │
│              │◀──结果───│  task_server + queue_worker│          │  Chrome 9222 │
│              │   (poll) │                           │          │              │
└──────────────┘          └──────────────────────────┘          └──────────────┘
```

- **OpenClaw**：通过 HTTP API 提交任务、轮询状态、获取结果
- **执行机**：运行 task_server（HTTP 服务）+ queue_worker（抓取消费）
- **影刀**：启动 Chrome、登录腾道、处理滑条验证码、保持浏览器在线

## 1. 执行机启动服务

### 1.1 启动任务服务 + 队列消费

```bash
cd /path/to/tendata-customer-enricher

# 方式一：分别启动（推荐用于调试）
start /B python scripts\task_server.py --port 8080
start /B python scripts\queue_worker.py

# 方式二：一键启动（推荐用于生产）
start /B python scripts\task_server.py --port 8080
start /B python scripts\queue_worker.py

# 确认服务正常
curl http://localhost:8080/api/health
# 预期输出: {"status": "ok", "queue": {"total": 0, "queued": 0, ...}}
```

### 1.2 服务确认清单

| 检查项 | 命令 | 预期 |
|---|---|---|
| HTTP 服务 | `curl http://localhost:8080/api/health` | HTTP 200, status=ok |
| 队列消费 | 提交测试任务后自动消费 | 任务从 queued → running → completed |
| Chrome CDP | `curl http://localhost:9222/json/version` | 返回 Browser 信息 |

## 2. 影刀介入（登录 + 验证码）

### 2.1 影刀在哪一步介入

**必须先于任务执行**：

```
步骤 1: 影刀启动 Chrome (CDP 9222)      ← 影刀
步骤 2: 影刀填入账号密码 + 处理滑条     ← 影刀
步骤 3: 影刀确认登录态有效              ← 影刀
步骤 4: OpenClaw 提交任务              ← OpenClaw (通过 HTTP API)
步骤 5: queue_worker 自动出队抓取       ← 执行机
步骤 6: OpenClaw 轮询获取结果           ← OpenClaw (通过 HTTP API)
```

### 2.2 影刀启动 Chrome

```
影刀操作:
  1. 执行 start_tendata_helper.bat
     或直接启动 Chrome:
     chrome.exe --remote-debugging-port=9222 --user-data-dir=".tendata-chrome-profile" https://bizr.tendata.cn/search#/index

  2. 在 Chrome 窗口中:
     - 输入腾道账号密码
     - 处理滑条验证码
     - 确认进入腾道业务页面

  3. 验证登录态（可选）:
     curl http://localhost:9222/json/version
     确认返回 Browser 信息
```

### 2.3 登录态恢复

当腾道登录过期时（任务返回 `TEN_LOGIN_REQUIRED`）：

```
影刀检测到任务失败（error_code=TEN_LOGIN_REQUIRED）
  → 影刀重新登录腾道
  → 影刀调用 task_store.recover_stale_running() 回收僵死任务
  → 任务重新进入 QUEUED，queue_worker 自动重新消费
```

**手动回收命令**：
```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
from task_store import task_store
recovered = task_store.recover_stale_running(timeout_seconds=1800)
print(f'回收 {len(recovered)} 个僵死任务: {recovered}')
task_store.close()
"
```

## 3. OpenClaw 调用流程（Poll 模式）

### 3.1 完整调用流程

```
OpenClaw                                    执行机
  │                                          │
  │  1. POST /api/task/create                │
  │─────────────────────────────────────────▶│ task_store.create()
  │  201 {"task_id":"T-001","status":"queued"}│
  │◀─────────────────────────────────────────│
  │                                          │ queue_worker 出队 → RUNNING
  │  2. GET /api/task/status?task_id=T-001   │
  │─────────────────────────────────────────▶│
  │  200 {"status":"running"}                │ queue_worker 抓取中...
  │◀─────────────────────────────────────────│
  │  3. GET /api/task/status?task_id=T-001   │ (每 5-10 秒轮询一次)
  │─────────────────────────────────────────▶│
  │  200 {"status":"partial_failed"}         │ queue_worker 完成 → COMPLETED
  │◀─────────────────────────────────────────│
  │  4. GET /api/task/result?task_id=T-001   │
  │─────────────────────────────────────────▶│
  │  200 {"status":"completed","results":...} │
  │◀─────────────────────────────────────────│
  │  5. 飞书推送结果给发起人                   │
  ▼                                          ▼
```

### 3.2 步骤 1：提交任务

```bash
curl -X POST http://<执行机IP>:8080/api/task/create \
  -H "Content-Type: application/json" \
  -d '{
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
      "callback_mode": "poll",
      "submitted_by": "openclaw-session-xxx"
    }
  }'
```

**成功响应**：
```json
{
  "task_id": "OC-20260421-001",
  "status": "queued",
  "message": "task created and enqueued"
}
```

**失败响应**：
```json
// 队列已满
{
  "task_id": "OC-20260421-001",
  "status": "rejected",
  "error": "队列已满 (50/50)，请稍后重试"
}

// 重复 task_id
{
  "error": "task_id 已存在: OC-20260421-001，请勿重复提交同一任务"
}
```

### 3.3 步骤 2：轮询状态

```bash
curl http://<执行机IP>:8080/api/task/status?task_id=OC-20260421-001
```

**响应**（执行中）：
```json
{
  "task_id": "OC-20260421-001",
  "source": "openclaw",
  "status": "running",
  "started_at": "2026-04-21T10:00:05"
}
```

**响应**（已完成）：
```json
{
  "task_id": "OC-20260421-001",
  "status": "partial_failed",
  "finished_at": "2026-04-21T10:01:08"
}
```

**轮询建议**：每 5-10 秒一次，超时时间 30 分钟。

### 3.4 步骤 3：获取结果

```bash
curl http://<执行机IP>:8080/api/task/result?task_id=OC-20260421-001
```

**响应**：
```json
{
  "task_id": "OC-20260421-001",
  "status": "completed",
  "results": [
    {
      "customer_name": "SCOPE METALS GROUP LTD",
      "matched_company_name": "SCOPE METALS GROUP LTD",
      "match_status": "confirmed",
      "match_confidence": 95,
      "company_status": "active",
      "location": "Israel",
      "latest_import_date": "2025-12-15",
      "import_active_status": "active",
      "top_products_json": "[{...}]",
      "top_suppliers_json": "[{...}]",
      ...
    }
  ],
  "artifacts": {
    "excel_path": "output/result_OC-20260421-001.xlsx",
    "json_path": "output/result_OC-20260421-001.json",
    "report_path": "output/report_OC-20260421-001.md"
  }
}
```

### 3.5 OpenClaw 伪代码实现

```python
import requests, time

BASE_URL = "http://<执行机IP>:8080"

def submit_task(customers, task_id=None):
    """提交任务，返回 task_id"""
    resp = requests.post(f"{BASE_URL}/api/task/create", json={
        "task_id": task_id or f"OC-{int(time.time())}",
        "customers": customers,
        "source": "openclaw",
        "callback": {"callback_mode": "poll"},
    })
    data = resp.json()
    if resp.status_code != 201:
        raise Exception(f"Submit failed: {data}")
    return data["task_id"]

def wait_for_result(task_id, timeout=1800, interval=10):
    """轮询等待结果，返回完整结果"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{BASE_URL}/api/task/status",
                          params={"task_id": task_id})
        status = resp.json().get("status")
        if status in ("completed", "partial_failed", "failed", "cancelled"):
            break
        time.sleep(interval)

    # 获取结果
    resp = requests.get(f"{BASE_URL}/api/task/result",
                       params={"task_id": task_id})
    return resp.json()

# 使用示例
task_id = submit_task([
    {"customer_name": "SCOPE METALS GROUP LTD", "country_region": "Israel"}
])
result = wait_for_result(task_id)
print(f"Matched: {result['results'][0]['matched_company_name']}")
```

## 4. 任务状态说明

| 状态 | 含义 | OpenClaw 应做什么 |
|---|---|---|
| `queued` | 在队列中等待执行 | 继续轮询 |
| `running` | 正在执行抓取 | 继续轮询 |
| `partial_failed` | 部分成功（有结果但无 confirmed 匹配） | 获取结果，人工复核 |
| `completed` | 全部成功 | 获取结果，推送飞书 |
| `failed` | 全部失败 | 获取 error_message，可能重试 |
| `cancelled` | 已取消 | 不再处理 |
| `rejected` | 输入校验失败 | 检查错误信息，修正后重提 |

## 5. 错误码说明

| 错误码 | 含义 | 处理方式 |
|---|---|---|
| `TEN_LOGIN_REQUIRED` | 腾道未登录 | 影刀重新登录 + recover_stale_running() |
| `TEN_PAGE_NOT_FOUND` | 腾道页面结构变化 | 检查腾道网站 |
| `TEN_TIMEOUT` | 抓取超时 | 重试或标记失败 |
| `TEN_SINGLE_FAIL` | 单条抓取异常 | 查看 error_message |
| `INPUT_INVALID` | 输入校验失败 | 检查 customer_name |
| `QUEUE_FULL` | 队列已满 | 等待后重试 |

## 6. 完整联调 Checklist

### 执行机侧

- [ ] Chrome 9222 端口可访问：`curl http://localhost:9222/json/version`
- [ ] task_server 健康检查通过：`curl http://localhost:8080/api/health`
- [ ] queue_worker 后台运行中
- [ ] 腾道登录态有效（通过浏览器访问 bizr.tendata.cn 确认）

### OpenClaw 侧

- [ ] 能成功 POST /api/task/create
- [ ] 能轮询 GET /api/task/status 直到终态
- [ ] 能获取 GET /api/task/result 并解析 results
- [ ] 飞书消息推送逻辑就绪

### 影刀侧

- [ ] 能启动 Chrome with --remote-debugging-port=9222
- [ ] 能完成登录 + 滑条验证码处理
- [ ] 能检测登录态过期（TEN_LOGIN_REQUIRED）
- [ ] 能调用 recover_stale_running() 回收僵死任务

## 7. 快速验证命令

```bash
# 一键集成测试（自动启动 server + worker，模拟 OpenClaw 调用）
python scripts/test_external_api.py --auto-start

# 手动最小验证
# 1. 启动服务
start /B python scripts\task_server.py --port 8080
start /B python scripts\queue_worker.py

# 2. 确认服务
curl http://localhost:8080/api/health

# 3. 提交测试任务
curl -X POST http://localhost:8080/api/task/create \
  -H "Content-Type: application/json" \
  -d '{"task_id":"TEST-001","customers":[{"customer_name":"Test Corp","country_region":"US"}]}'

# 4. 查询状态
curl http://localhost:8080/api/task/status?task_id=TEST-001

# 5. 获取结果
curl http://localhost:8080/api/task/result?task_id=TEST-001
```
