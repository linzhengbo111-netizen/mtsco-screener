# 联调排障手册 — tendata-customer-enricher

> 面向实施同事的常见问题排查指南。

## 1. 快速诊断流程

```
第一步：跑健康检查
  python scripts/check_health.py
  └── 全部 [OK] → 执行机就绪，转入第 2 步
  └── 有 [!!]   → 按下方第 3 节逐项处理

第二步：跑集成测试
  python scripts/test_external_api.py --auto-start
  └── 18/18 PASS → API 链路正常
  └── 有 FAIL    → 按下方第 4 节排查

第三步：Chrome 未运行时启动浏览器
  .\start_tendata_helper.bat
  然后人工登录腾道
```

## 2. 执行状态快速判断

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `curl /api/health` 不通 | task_server 未启动 | `python scripts/task_server.py --port 8080` |
| 任务一直是 `queued` | queue_worker 未启动 | `python scripts/queue_worker.py` |
| 任务变 `failed` + `TEN_LOGIN_REQUIRED` | Chrome 未启动或腾道登录过期 | 启动 Chrome + 重新登录 |
| 任务变 `partial_failed` | 无 Chrome 或搜索无结果 | 确认 Chrome 9222 在线 + 登录态有效 |
| 任务 30 分钟未完成 | 网络慢 / 腾道页面慢 | 正常，单条抓取约 1-3 分钟 |

## 3. 按 [!!] 项逐一处理

### 3.1 HTTP 服务不通

**症状**：`check_health.py` 报 `[!!] 服务状态`

**排查**：
```bash
# 检查端口是否被占用
netstat -ano | findstr 8080

# 手动启动
python scripts/task_server.py --port 8080

# 确认启动成功
curl http://localhost:8080/api/health
# 预期: {"status": "ok", "queue": {...}}
```

**常见原因**：
- 端口 8080 被其他进程占用 → 换一个端口：`--port 8090`
- Python 环境不对 → 确认 `python --version >= 3.9`

### 3.2 Chrome CDP 9222 不可用

**症状**：`check_health.py` 报 `[!!] Chrome CDP`

**排查**：
```bash
# 检查 9222 端口
curl http://localhost:9222/json/version

# 如果返回空或连接拒绝 → Chrome 未启动
# 启动 Chrome：
.\start_tendata_helper.bat

# 或手动启动：
chrome.exe --remote-debugging-port=9222 --user-data-dir=".tendata-chrome-profile"
```

**常见原因**：
- Chrome 已关闭 → 重新启动
- Chrome 进程残留 → 任务管理器结束所有 chrome.exe 后重开
- 端口被占用 → 换一个端口并同步修改 extract_tendata_fields.py

### 3.3 腾道登录过期

**症状**：任务状态 `partial_failed` + error_code `TEN_LOGIN_REQUIRED`

**排查**：
```bash
# 1. 打开 Chrome 9222 对应的浏览器窗口
# 2. 访问 https://bizr.tendata.cn/search#/index
# 3. 如果跳转到登录页 → 重新输入账号密码 + 处理滑条验证码
# 4. 确认进入腾道业务首页
```

**恢复后回收僵死任务**：
```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
from task_store import task_store
recovered = task_store.recover_stale_running(timeout_seconds=1800)
print(f'回收 {len(recovered)} 个僵死任务')
task_store.close()
"
```

## 4. 集成测试排查

### 4.1 测试全部 FAIL

**可能原因**：task_server 和 queue_worker 未启动

**处理**：
```bash
# 一键启动
python scripts/test_external_api.py --auto-start

# 或手动启动后测试
start /B python scripts\task_server.py --port 8090
start /B python scripts\queue_worker.py
python scripts/test_external_api.py --base-url http://localhost:8090
```

### 4.2 Test 5 results 为空

**原因**：无 Chrome 登录态时的预期行为。任务能执行完，但抓取不到数据。

**处理**：启动 Chrome 9222 并登录腾道后重测。

### 4.3 Test 8 取消返回 404

**原因**：worker 消费速度太快，任务在被取消前已完成。

**处理**：这是正常时序行为，不影响功能。取消功能本身已验证通过。

## 5. 日志查看

queue_worker 输出在启动它的终端窗口。如果窗口已关闭：
```bash
# 查看最近的 task_store 记录
python -c "
import sys; sys.path.insert(0, 'scripts')
from task_store import task_store
tasks = task_store.list(limit=5)
for t in tasks:
    s = t.status.value if hasattr(t.status, 'value') else str(t.status)
    print(f'{t.task_id:30s} {s:15s} created={t.created_at}')
    if t.error_code:
        print(f'  error: {t.error_code} — {t.error_message}')
task_store.close()
"
```

## 6. 紧急回滚

如果某个版本出现问题，可以快速回退到上一个稳定的 dist 包：
```bash
# 解压上一个版本
unzip -o dist/tendata-customer-enricher-user.zip -d ./rollback/
cd rollback
python scripts/task_server.py --port 8080 &
python scripts/queue_worker.py &
```

## 7. 联系信息

| 角色 | 职责 |
|---|---|
| 实施同事 | 启动服务 + Chrome 登录 + 运行 check_health.py |
| OpenClaw 方 | 通过 HTTP API 提交/查询/取结果 |
| 影刀 RPA 工程师 | 维护 Chrome 登录态 + 处理滑条验证码 |
