---
name: tendata-customer-enricher
description: 腾道海关数据抓取与报告生成 — 本地执行端，支持 OpenClaw 任务调用与影刀调度
---

# tendata-customer-enricher

腾道（TenData）海关数据抓取与报告生成 Skill。

> 本版本为产品化升级版，在原有本地抓取能力基础上，增加了任务调度、报告生成与标准化接口层，为后续接入 OpenClaw / 影刀调度做准备。

## 架构分层

```
外部调用层    → OpenClaw / 影刀 / 人工 / HTTP API
任务调度层    → task_server.py + queue_worker.py + run_task.py
报告生成层    → generate_report.py + export_results.py
抓取内核      → run_batch.py + extract_tendata_fields.py + normalize_input.py
基础设施      → Chrome (CDP 9222) + 影刀(登录/验证码)
```

详见 [references/architecture.md](references/architecture.md)。

## 前置要求

- **首版必须人工登录腾道并完成验证码**，skill 只复用已登录的浏览器会话
- 不输入账号密码、不处理验证码、不尝试自动过验证码
- Python 3.9+，依赖：`playwright pandas openpyxl`

## 快速开始

### 方式一：本地人工操作（现有流程）

```bash
# 1. 启动 Chrome（双击或手动）
./start_tendata_helper.bat

# 2. 在浏览器中登录腾道

# 3. 运行抓取
python scripts/run_batch.py --input <客户名单.xlsx>
```

### 方式二：任务调用（新增）

```bash
# JSON 任务输入
python scripts/run_task.py --input task.json

# Excel 直接创建任务
python scripts/run_task.py --input customers.xlsx

# 单条快速测试
python scripts/run_task.py --name "SCOPE METALS GROUP LTD" --country Israel
```

### 方式三：HTTP API 调用

```bash
# 启动任务服务
python scripts/task_server.py --port 8080

# 创建任务
curl -X POST http://localhost:8080/api/task/create \
  -H "Content-Type: application/json" \
  -d '{"task_id":"T-001","customers":[{"customer_name":"Test Corp","country_region":"US"}]}'

# 查询状态
curl http://localhost:8080/api/task/status?task_id=T-001

# 获取结果
curl http://localhost:8080/api/task/result?task_id=T-001
```

### 方式四：HS 编码两段式搜索（新增）

```bash
# CLI 方式
# 阶段 1：快速搜索，返回候选列表
python scripts/run_hs_search.py quick --hs-code 730723 --country 加拿大

# 阶段 2：深挖选定公司（默认完成后关闭浏览器）
python scripts/run_hs_search.py enrich --task-id HS-001 --select 1,2,3

# 阶段 2：深挖选定公司 + 保留浏览器窗口（供后续操作继续使用）
python scripts/run_hs_search.py enrich --task-id HS-001 --select 1,2,3 --keep-browser

# 一步到位：自动全量
python scripts/run_hs_search.py auto --hs-code 730723 --country 加拿大 --max 5

# HTTP API 方式
# 阶段 1：快速搜索（浏览器保持活跃，搜索结果页不关闭）
curl -X POST http://localhost:8080/api/task/hs_quick_search \
  -H "Content-Type: application/json" \
  -d '{"task_id":"HS-001","hs_code":"730723","country_filter":"加拿大","max_companies":20}'

# 阶段 2：深挖选定（默认完成后关闭浏览器）
curl -X POST http://localhost:8080/api/task/hs_enrich_selected \
  -H "Content-Type: application/json" \
  -d '{"task_id":"HS-001","selections":[1,2,3]}'

# 阶段 2：深挖选定 + 保留浏览器窗口（keep_browser=True）
curl -X POST http://localhost:8080/api/task/hs_enrich_selected \
  -H "Content-Type: application/json" \
  -d '{"task_id":"HS-001","selections":[1,2,3],"keep_browser":true}'

# 放弃流程：关闭浏览器（如果用户看完候选列表后决定不深挖）
curl -X POST http://localhost:8080/api/task/hs_cancel

# 自动全量模式（通过 task/create + enrich_mode，一步完成）
curl -X POST http://localhost:8080/api/task/create \
  -H "Content-Type: application/json" \
  -d '{"task_id":"HS-002","customers":[{"product_keywords":"730723","country_region":"加拿大"}],"enrich_mode":"hs_auto_enrich"}'
```

**enrich_mode 说明：**

| 值 | 含义 | 适用场景 |
|---|---|---|
| `company_name` | 按公司名搜索（默认） | 传统客户名搜索 |
| `hs_auto_enrich` | HS 编码自动全量深挖 | OpenClaw 自动化，无需人工审核 |
| `hs_manual_select` | HS 编码需人工选择 | 需人工审核候选后再深挖 |

## HS 编码两段式搜索详解

### 为什么需要两段式？

HS 编码搜索返回的是一家公司的 HS 产品摘要（如"730723 钢管"），但同一 HS 编码可能有数十家关联公司。两段式让上游系统先看到候选列表，再选择需要深挖的公司，避免无意义的详情抓取。

### 阶段 1：快速搜索（quick_search）

**只抓搜索结果页的卡片摘要，不进入任何详情页。**

**端点：** `POST /api/task/hs_quick_search`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 否 | 自定义任务 ID，用于阶段 2 关联。不传则自动生成 |
| `hs_code` | string | **是** | 6 位 HS 编码，如 `"730723"` |
| `country_filter` | string | 否 | 国家过滤，中文名如 `"加拿大"` 或英文如 `"Canada"` |
| `max_companies` | int | 否 | 最大返回数，默认 20，最大 20 |

**返回字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 任务 ID（阶段 2 必须使用同一个） |
| `status` | string | `"completed"` |
| `mode` | string | `"hs_quick_search"` |
| `total_candidates` | int | 候选公司总数 |
| `candidates` | array | 候选列表（见下方） |
| `browser_session` | string | `"active — call /api/task/hs_cancel to close"` |

**候选卡片字段（candidates 数组中每项）：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `card_index` | int | 卡片序号（1-based，阶段 2 selections 使用此序号） |
| `company_name` | string | 公司名称 |
| `hs_product_desc` | string | HS 产品描述 |
| `hs_trade_count` | int | 贸易次数 |
| `hs_supplier_count` | int | 供应商数量 |
| `recent_trade_date` | string | 最近贸易日期 |
| `page_url` | string | 详情页 URL |
| `text_preview` | string | 卡片摘要预览（200-300 字符自然语言） |
| `summary` | string | 自然语言摘要，如"进口次数 5 / 供应商 2 / 最近进口 2026-04-17 / 产品: 钢管" |

**浏览器状态：** quick_search 完成后**不关闭浏览器**，搜索结果页保持活跃。这样阶段 2 可以直接点击卡片进入详情页，避免重复执行 HS 搜索。

### 阶段 2：深度抓取（enrich_selected）

**接收阶段 1 的 task_id + 用户选择，只抓选中公司的详情页。**

**端点：** `POST /api/task/hs_enrich_selected`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | **是** | 必须与 quick_search 返回的 task_id 一致 |
| `selections` | int[] | 否 | 要深挖的 card_index 列表，如 `[1,2,3]`。不传则全部深挖 |
| `keep_browser` | bool | 否 | 默认 False。True=深挖后保留浏览器窗口，False=完成后自动关闭 |

**返回字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 任务 ID |
| `status` | string | `"completed"` |
| `total_enriched` | int | 实际深挖公司数 |
| `results` | array | 深度抓取结果（见下方） |
| `browser_session` | string | `"active"`（keep_browser=True）或 `"closed"` |

**深度抓取结果字段（results 数组中每项）：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `card_index` | int | 卡片序号（与 quick_search 中 1:1 对应） |
| `company_name` | string | 原始公司名 |
| `website` | string | 公司官网 |
| `phone` | string | 联系电话 |
| `address` | string | 公司地址 |
| `LinkedIn` | string | LinkedIn 链接 |
| `import_status` | enum | `active` / `inactive` / `unknown` |
| `product_list` | array | 主营产品列表（从 top_products_json 解析） |
| `last_import_date` | string | 最近进口日期 |
| `comments` | string | 自然语言备注，如"进口次数 5 / 供应商 2 / 巴西" |

**浏览器状态：**
- 默认（`keep_browser=False`）：enrich_selected 完成后**自动关闭浏览器**，释放资源
- `keep_browser=True`：完成后**保留浏览器窗口**，可继续深挖其他公司或调用 `/api/task/hs_cancel` 手动关闭
- 异常或中断时始终关闭浏览器，确保安全

### 索引映射规则

- `card_index` 从 1 开始（`candidates[0]` = `card_index: 1`）
- `selections` 使用 `card_index` 值，即 `[1,2,3]` 表示前 3 家
- 超出范围的序号会被跳过并打印警告

### 异常情况

| 场景 | 行为 |
|---|---|
| quick_search 后不调用 enrich_selected | 浏览器保持活跃，调用 `/api/task/hs_cancel` 关闭 |
| enrich_selected 时浏览器已关闭 | 自动重建搜索（用缓存的 hs_code + country_filter），性能稍慢但不影响正确性 |
| enrich_selected + keep_browser=True | 深挖后保留浏览器，可再次调用 enrich_selected 深挖其他公司 |
| 序号超出候选列表范围 | 跳过该序号，打印警告 |
| task_id 不存在 | 返回 404 错误 |
| 异常或中断 | 始终关闭浏览器，确保资源安全 |

### 放弃流程

如果用户看完候选列表后决定不深挖，调用：

```
POST /api/task/hs_cancel
```

这将关闭浏览器会话，释放资源。

## 输入输出

- **输入**：Excel (.xlsx) 或 JSON（详见 [references/io-contract.md](references/io-contract.md)）
- **输出**：Excel + JSON + Markdown 报告
- 详见 [references/io-contract.md](references/io-contract.md)

## 抓取字段

详见 [references/field-schema.md](references/field-schema.md)。

## 报告模板

详见 [references/report-template.md](references/report-template.md)。

## 任务生命周期

详见 [references/task-lifecycle.md](references/task-lifecycle.md)。

## 目录结构

```
tendata-customer-enricher/
├── SKILL.md                          # 技能入口
├── README.md                         # 技术文档
├── README_business_user.md           # 业务员操作指南
├── build.py                          # 打包脚本
├── agents/
│   └── openai.yaml                   # Agent 配置
├── references/
│   ├── field-schema.md               # 字段定义
│   ├── input-template.md             # 输入规范
│   ├── matching-rules.md             # 匹配规则
│   ├── page-flow.md                  # 页面链路
│   ├── io-contract.md                # 输入输出契约（新增）
│   ├── task-lifecycle.md             # 任务生命周期（新增）
│   ├── report-template.md            # 报告模板（新增）
│   └── architecture.md               # 系统架构（新增）
├── scripts/
│   ├── normalize_input.py            # 表头归一化
│   ├── extract_tendata_fields.py     # 浏览器抓取 + 匹配计算
│   ├── export_results.py             # Excel 结果导出
│   ├── run_batch.py                  # 批处理主流程（抓取内核）
│   ├── models.py                     # 数据模型
│   ├── generate_report.py            # Markdown 报告生成
│   ├── task_store.py                 # ★ 统一任务仓库（SQLite）
│   ├── task_server.py                # HTTP 任务接口
│   ├── queue_worker.py               # 队列消费器
│   ├── run_task.py                   # 任务编排入口
│   ├── run_hs_search.py              # ★ HS 编码两段式搜索 CLI
│   ├── make_single_test.py           # 生成单条测试 Excel
│   └── clean_runtime_artifacts.py    # 清理运行时缓存
└── dist/                             # 打包产物
```

## 运行方式

```bash
# 方式一（推荐）：双击 start_tendata_helper.bat 启动 Chrome，然后运行
python scripts/run_batch.py --input <客户名单.xlsx> [--output <结果.xlsx>] [--headless]

# 方式二：任务调用
python scripts/run_task.py --input <客户名单.xlsx>

# 方式三：启动 HTTP 服务
python scripts/task_server.py --port 8080
```
