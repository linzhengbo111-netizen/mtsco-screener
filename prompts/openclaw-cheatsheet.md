# OpenClaw 单公司一步动作 — 速查卡

> 打印或贴到飞书置顶，随时查阅。

## 前置条件（每次联调前确认）

| 检查项 | 确认方式 |
|---|---|
| Chrome 9222 在线（执行机本地） | `curl http://localhost:9222/json/version` → 有返回 |
| 腾道已登录（执行机本地） | 浏览器能看到腾道业务首页 |
| task_server 运行中（执行机本地） | `curl http://localhost:8080/api/health` → `{"status":"ok"}` |
| queue_worker 运行中（执行机本地） | 终端窗口标题包含 `TenData Queue Worker` |
| ngrok 在线（公网入口） | OpenClaw 可访问 `https://alike-quench-entwine.ngrok-free.dev/api/health` |

## 调用地址

### 执行机本地检查地址

- `http://localhost:8080` — task_server
- `http://localhost:9222` — Chrome CDP

### OpenClaw 云端调用地址

- `https://alike-quench-entwine.ngrok-free.dev`

### 重要提醒

通过 OpenClaw 调用 ngrok 时，所有 HTTP 请求都必须带：

```
ngrok-skip-browser-warning: true
```

## 用户输入 → OpenClaw 动作映射

| 用户说 | OpenClaw 执行 |
|---|---|
| "查一下 {公司名}，国家 {国家}" | 一步动作：创建 → 等待 → 查询 → 回复摘要 |
| "继续查 {task_id}" | 继续查询：查询状态 → 获取结果 → 回复摘要 |
| "帮我查这几家：A, B, C" | 本轮不支持，一次只查一家 |

## 回复模板速查

### 成功（当前真实成功样板）

```
📊 腾道数据报告

任务 ID: OC-NGROK-003
公司: SCOPE METALS GROUP LTD → SCOPE METALS GROUP LTD
匹配度: 90/100 (confirmed)

公司信息:
- 所在地: 以色列
- 官网: scope-metal.com
- 公司状态: active
- 电话: +972528634466
- 地址: P.O. BOX: 3
- 进口活跃: active
- 最新进口: 2026-03-11

Top 产品:
- STEEL WELDED PIPE
- STEEL WELDED SQUARE PIPE
- WELDED STAINLESS STEEL TUBE

Top 供应商:
- MISHRA DHATU NIGAM LTD
- INNOMET ADVANCED MATERIALS LIMITED
- CÔNG TY TNHH MỘT THÀNH VIÊN DAEJIN SANUP VIỆT NAM

建议: 建议继续跟进
```

### 成功（低置信度示例）

```
📊 腾道数据报告

任务 ID: OC-20260422-A1B2C3D4
公司: Test Corp → CÔNG TY TNHH SEMICONDUCTOR TEST CORP VIETNAM
匹配度: 59/100 (unconfirmed)

建议: 待人工复核
⚠️ 匹配置信度较低，建议人工复核。
```

### 仍在执行

```
⏳ 任务 OC-20260422-A1B2C3D4 正在执行中。

建议 2 分钟后再查询。
继续查询命令: 继续查 OC-20260422-A1B2C3D4
```

### 仍在队列

```
⏳ 任务 OC-20260422-A1B2C3D4 仍在队列中等待执行。

可能原因：执行机正在处理其他任务。
建议稍后查询。
继续查询命令: 继续查 OC-20260422-A1B2C3D4
```

### 腾道登录过期

```
❌ 任务执行失败

错误: TEN_LOGIN_REQUIRED
详情: 腾道登录态无效

处理：请通知执行机操作人员在 Chrome 中重新登录腾道，然后重试。
```

### 网络不通

```
❌ 无法连接执行服务

请确认：
- ngrok 是否在线
- 公网访问地址是否可用：https://alike-quench-entwine.ngrok-free.dev
- 执行机服务是否运行
- 网络是否通畅
```

## task_id 格式

```
OC-{YYYYMMDD}-{8位随机字符}
```

例如：`OC-20260422-A1B2C3D4`

## 当前推荐用法

| 场景 | 做法 |
|---|---|
| 首次联调 | 先跑执行机本地健康检查 → 再让 OpenClaw 从 ngrok 提交 |
| 单公司查询 | "查一下 {公司名}，国家 {国家}" |
| 查上次结果 | "继续查 {task_id}" |
| 一次查多家 | 本轮不支持，逐条提交 |
