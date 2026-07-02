# 实施操作清单 — tendata-customer-enricher

> 面向实施同事的按步骤操作清单。打印出来逐项勾选。

## 联调前准备（执行机侧）

- [ ] **1.1** 确认 Python >= 3.9：`python --version`
- [ ] **1.2** 确认依赖已安装：`python -c "import playwright; import pandas; print('OK')"`
- [ ] **1.3** 确认项目目录完整：`ls scripts/` 至少包含 `task_server.py`, `queue_worker.py`, `run_batch.py`

## 启动 Chrome（影刀侧）

- [ ] **2.1** 双击 `start_tendata_helper.bat` 启动 Chrome
- [ ] **2.2** 在 Chrome 中完成腾道登录（账号密码 + 滑条验证码）
- [ ] **2.3** 确认进入腾道业务页：`https://bizr.tendata.cn/search#/index`
- [ ] **2.4** 验证 CDP 端口：`curl http://localhost:9222/json/version` → 返回 Browser 信息

## 启动服务（执行机侧）

- [ ] **3.1** 一键启动：双击 `start_services.bat`
- [ ] **3.2** 确认出现两个窗口标题：
  - "TenData Task Server"
  - "TenData Queue Worker"
- [ ] **3.3** 健康检查：`python scripts/check_health.py` → 全部 `[OK]`

## 首次联调测试

- [ ] **4.1** 提交测试任务：
  ```bash
  curl -X POST http://localhost:8080/api/task/create ^
    -H "Content-Type: application/json" ^
    -d "{\"task_id\":\"TEST-001\",\"customers\":[{\"customer_name\":\"Test Corp\",\"country_region\":\"US\"}]}"
  ```
  预期：`{"task_id":"TEST-001","status":"queued"}`

- [ ] **4.2** 查询状态：
  ```bash
  curl http://localhost:8080/api/task/status?task_id=TEST-001
  ```
  预期：先 `running`，约 1-3 分钟后变 `partial_failed` 或 `completed`

- [ ] **4.3** 获取结果：
  ```bash
  curl http://localhost:8080/api/task/result?task_id=TEST-001
  ```
  预期：`results` 数组非空，包含 `matched_company_name`

## 异常处理

- [ ] **5.1** 任务一直是 `queued` → 检查 queue_worker 窗口是否在运行
- [ ] **任务失败 + `TEN_LOGIN_REQUIRED`** → 影刀重新登录腾道，然后运行：
  ```bash
  python scripts/check_health.py
  ```
- [ ] **任务超时（30分钟）** → 检查腾道页面是否响应慢，可能需要重启 Chrome

## OpenClaw 正式联调

- [ ] **6.1** 将 `<执行机IP>:8080` 配置到 OpenClaw 侧
- [ ] **6.2** OpenClaw 提交真实客户任务
- [ ] **6.3** 轮询获取结果
- [ ] **6.4** 确认结果数据完整
- [ ] **6.5** 飞书推送结果给发起人（如已接入）

## 完成标准

以下全部打勾才算联调完成：

- [ ] `check_health.py` 全部 `[OK]`
- [ ] 测试任务能从 `queued` → `running` → `completed`/`partial_failed`
- [ ] 能获取到 `results` 且 `matched_company_name` 非空
- [ ] OpenClaw 能独立通过 API 完成提交→轮询→取结果全流程
