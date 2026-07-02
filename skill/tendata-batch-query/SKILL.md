---
name: tendata-batch-query
description: Batch customer enrichment via TenData. Supports company name search, HS code two-stage search (quick search + deep enrich), and Excel file uploads. Calls a remote execution engine API.
version: 0.3.0
---

# TenData Batch Query

## What this skill does

Queries TenData customs database to enrich company information. Two modes:
- **Company name search** — traditional batch query by company names
- **HS code search** — two-stage flow: quick search returns candidate companies, then deep enrichment on selected ones

## Configuration

**BASE_URL**: `https://alike-quench-entwine.ngrok-free.dev`

> This URL is stable and does not change.

Every HTTP request to BASE_URL MUST include the header:
```
ngrok-skip-browser-warning: true
```

## Input Detection — How to Choose the Right Flow

| User input | Detected mode | Flow to use |
|-----------|--------------|-------------|
| Company names (text, one per line) | Company name search | [Company Name Flow](#company-name-search-flow) |
| Excel file with company names | Company name search | [Company Name Flow](#company-name-search-flow) |
| HS code (e.g. "730723", "查询 HS 730723") | HS code search | [HS Code Flow](#hs-code-two-stage-search-flow) |
| "HS" / "hs code" + 4-10 digit number | HS code search | [HS Code Flow](#hs-code-two-stage-search-flow) |

### HS Code Detection Rules

A user input is an HS code query when:
- Contains 4-10 consecutive digits that match HS code format
- Accompanied by keywords: "HS", "hs code", "海关编码", "hs 编码"
- Example triggers: "HS 730723", "hs code 8471", "海关编码 730723 加拿大"

If unclear, ask the user: "请问您是要按公司名查询，还是按 HS 编码查询？"

---

## Company Name Search Flow

### Step 1: Parse Input

#### Text list
Convert user input to a JSON array:
```json
[
  {"customer_name": "公司A", "country_region": "US"},
  {"customer_name": "公司B", "country_region": "", "website": ""}
]
```

Rules:
- `customer_name` is required. Skip blank lines.
- `country_region` and `website` are optional — extract from the line if present

#### Excel upload
Use the xlsx skill to read the uploaded file, map headers per [Header Mapping Rules](#header-mapping-rules).

### Step 2: Create Batch

```
POST {BASE_URL}/api/batch/create
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "customers": [...parsed JSON...],
  "source": "openclaw"
}
```

Save the returned `batch_id`.

### Step 3: Poll Status (aggressive-then-relaxed)

Poll immediately after create, then follow this schedule:

| Phase | Interval | Duration |
|-------|----------|-------------|
| Phase 1 | every **5 seconds** | 0-30s (~6 attempts) |
| Phase 2 | every **10 seconds** | 30-120s (~9 attempts) |
| Phase 3 | every **15 seconds** | >120s, max 30 total attempts |

```
GET {BASE_URL}/api/batch/status?batch_id={batch_id}
ngrok-skip-browser-warning: true
```

**Stop condition:** As soon as `status` is `completed` or `partial_failed`, **immediately** proceed to Step 4.

Maximum total attempts: 30. If still running after 30 polls, tell user: "任务仍在执行中，请稍后手动查询".

### Step 4: Get Result

```
GET {BASE_URL}/api/batch/result?batch_id={batch_id}
ngrok-skip-browser-warning: true
```

If it times out, retry once.

### Step 5: Data Summary Cards

Every customer in the batch result MUST display a **data summary card**. Do NOT return a generic "query completed".

For each customer, show:

| Field | Description |
|-------|------------|
| `matched_company_name` | 腾道匹配企业名称 |
| `match_status` | confirmed / likely_match / unconfirmed / no_result |
| `match_confidence` | 0-100 |
| `location` | 企业地址/位置 |
| `website_result` | 企业官网 |
| `phone` | 联系电话 |
| `linkedin` | LinkedIn 链接 |
| `latest_import_date` | 最近进口日期 |
| `import_active_status` | active / inactive / unknown |
| `top_products` | Top 产品（解析 `top_products_json`） |
| `top_suppliers` | Top 供应商（解析 `top_suppliers_json`，默认前 3 条） |

If `top_suppliers_json` or `target_hs_amount_json` is populated, also show **最近 3 条进口记录** from `top_suppliers_json`.

### Step 5b: Priority Scoring (auto-applied)

After formatting the data summary, automatically run priority scoring per `priority_rules_v1.md`. Append to each customer card:

| Field | Description |
|-------|------------|
| `priority_score` | Numeric score (base 50, +/- adjustments) |
| `priority_level` | high / medium / low / skip |
| `priority_reason` | One-line scoring rationale (Chinese) |
| `recommended_action` | 主动开发 / 保持跟进 / 保持关注 / 暂不跟进 |
| `recommended_entry_point` | Suggested entry point |
| `recommended_script_direction` | Communication direction |

Example:
```
【优先级评估】
  优先级得分：78
  优先级等级：高
  评估原因：近6月有进口记录，采购金额>$100万，联系方式齐全
  推荐动作：主动开发
  推荐切入点：从 HS 编码 123456 产品线切入，该公司现有供应商为 ABC Electronics
  沟通方向：强调替代方案，指出当前供应商的采购额，我们有更优价格和交期
```

### Step 6: Expanded Detail Mode (on user request)

Trigger phrases: "展开明细" / "进口记录明细" / "import details" / "supplier details" / "HS breakdown"

When triggered, for each customer with data:
1. **All suppliers** from `top_suppliers_json` (not limited to 3)
2. **HS breakdown** from `target_hs_amount_json`: `hs_code`, `usd_amount`
3. **Latest import date**

If no data: "当前未提取到可展示的进口记录明细".

---

## HS Code Two-Stage Search Flow

HS code search uses a two-stage pattern: **Quick Search** (returns candidates) → **Deep Enrichment** (user selects which to expand).

### Stage 1: Quick Search

Returns a list of candidate companies with brief summaries. **Only scrapes search result cards, does NOT enter any detail page.**

```
POST {BASE_URL}/api/task/hs_quick_search
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "task_id": "HS-001",
  "hs_code": "730723",
  "country_filter": "加拿大",
  "max_companies": 20
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | No | Custom task ID for stage 2 linkage. Auto-generated if omitted |
| `hs_code` | string | **Yes** | HS code, e.g. "730723" |
| `country_filter` | string | No | Country filter, e.g. "加拿大", "US" |
| `max_companies` | int | No | Max candidates (default 20, max 20) |

**Response:**

```json
{
  "task_id": "HS-001",
  "status": "completed",
  "mode": "hs_quick_search",
  "total_candidates": 15,
  "candidates": [
    {
      "card_index": 1,
      "company_name": "FIDELITY PAC METALS LTD",
      "hs_product_desc": "STEEL WELDED PIPE",
      "hs_trade_count": 548,
      "hs_supplier_count": 12,
      "recent_trade_date": "2026-03-11",
      "page_url": "...",
      "text_preview": "FIDELITY PAC METALS LTD — 主营 STEEL WELDED PIPE。贸易 548 次，供应商 12 家，最近进口 2026-03-11。",
      "summary": "进口次数 548 / 供应商 12 / 最近进口 2026-03-11 / 产品: STEEL WELDED PIPE"
    }
  ],
  "browser_session": "active — call /api/task/hs_cancel to close"
}
```

**Display to user:** Show candidates as a numbered list:

```
【HS 快速搜索结果】HS 编码 730723 | 加拿大 | 共 15 家候选

  #1 FIDELITY PAC METALS LTD
     产品: STEEL WELDED PIPE
     贸易: 548次 | 供应商: 12家 | 最近: 2026-03-11

  #2 ANOTHER COMPANY NAME
     产品: ...
     贸易: ... | 供应商: ... | 日期: ...
```

Then ask: "请选择需要深挖的公司编号（如 1,2,3），或输入"全部"展开所有。"

**Browser session:** After quick_search, the browser stays **active** with the search result page open. Stage 2 will reuse this page directly, avoiding re-running the HS search.

### Stage 2: Deep Enrichment on Selected Companies

After user selects, call:

```
POST {BASE_URL}/api/task/hs_enrich_selected
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "task_id": "HS-001",
  "selections": [1, 2, 3]
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | **Yes** | Must match the task_id from hs_quick_search |
| `selections` | int[] | No | Array of card_index to enrich. Omit for ALL |
| `keep_browser` | bool | No | Default `false`. If `true`, browser stays open after enrich — useful for multi-round deep dives |

**Response:**

```json
{
  "task_id": "HS-001",
  "status": "completed",
  "total_enriched": 3,
  "results": [
    {
      "card_index": 1,
      "company_name": "FIDELITY PAC METALS LTD",
      "website": "example.com",
      "phone": "+1...",
      "address": "123 Main St",
      "LinkedIn": "linkedin.com/company/...",
      "import_status": "active",
      "product_list": ["STEEL WELDED PIPE", "STEEL SEAMLESS PIPE"],
      "last_import_date": "2026-03-11",
      "comments": "进口次数 548 / 供应商 12 / 加拿大 / 美国"
    }
  ],
  "browser_session": "closed"
}
```

With `"keep_browser": true`, the response includes `"browser_session": "active"` instead.

Display results using the same [Data Summary Cards](#step-5-data-summary-cards) + [Priority Scoring](#step-5b-priority-scoring-auto-applied) format.

**Browser session lifecycle:**
- Default (`keep_browser=false`): browser **automatically closed** after enrich_selected
- `keep_browser=true`: browser **stays open** — you can call enrich_selected again with different selections, or call `/api/task/hs_cancel` to close when done
- On exception: browser **always closed** for safety

### Index Mapping Rules

- `card_index` starts from 1 (`candidates[0]` = `card_index: 1`)
- `selections` uses `card_index` values: `[1,2,3]` means first 3 companies
- Out-of-range indices are skipped with a warning

### Abandon Flow

If user sees candidates but decides NOT to deep-enrich:

```
POST {BASE_URL}/api/task/hs_cancel
```

This closes the browser session and frees resources.

### HS Auto Mode (One-Step Full Enrich)

For fully automated pipelines (no human review):

```
POST {BASE_URL}/api/task/create
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "task_id": "HS-AUTO-001",
  "customers": [
    {"product_keywords": "730723", "country_region": "加拿大"}
  ],
  "enrich_mode": "hs_auto_enrich"
}
```

Then poll `/api/task/status` and get results from `/api/task/result`.

---

## API Reference

### Batch Endpoints (Company Name Search)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/batch/create` | Create batch task |
| GET | `/api/batch/status?batch_id=xxx` | Query batch status |
| GET | `/api/batch/result?batch_id=xxx` | Get batch results |

### Task Endpoints (HS Search + Single Task)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/task/create` | Create task (supports `enrich_mode`) |
| GET | `/api/task/status?task_id=xxx` | Query task status |
| GET | `/api/task/result?task_id=xxx` | Get task results |
| POST | `/api/task/hs_quick_search` | HS quick search (browser stays active) |
| POST | `/api/task/hs_enrich_selected` | HS deep enrich (closes after by default; `keep_browser=true` to keep open) |
| POST | `/api/task/hs_cancel` | Close browser session |
| POST | `/api/task/cancel?task_id=xxx` | Cancel a task |
| GET | `/api/health` | Health check |

---

## Header Mapping Rules

### Priority 1: Standard template headers
If columns exactly match: `company_name`, `country`, `website` → direct mapping.

### Priority 2: Alias recognition (case-insensitive, trimmed)

| Target field | Accepted aliases |
|-------------|-----------------|
| **customer_name** | 公司名, 客户名称, 企业名称, 公司名称, 公司, 客户, Company Name, Customer Name, companyname, customername |
| **country_region** | 国家, 国家地区, 地区, Country, Region, countryregion, 国家/地区, 国别 |
| **website** | 官网, 网站, 网址, Website, URL, 公司网址, companywebsite |
| **product_keywords** | 产品关键词, 产品, 关键词, Product, Keywords |

Matching order:
1. Exact match (case-insensitive, trimmed)
2. Contains match (e.g., "客户名称(中文)" matches "客户名称")

### Ambiguous headers → ask user

- No column resembles company name → ask: "未识别到公司名列，请确认哪一列是公司名称"
- Two columns both look like company name → ask: "发现两列都可能公司名，请确认用哪一列"
- No header row detected → ask: "请确认第一行是否为表头，或提供列号对应关系"

---

## Important Rules

### Real-time Query vs Cached Results

1. **Default: always create a new batch/task.** Whenever the user submits, you MUST call `create` → `status` → `result` in full. Never return cached or historical results.
2. **Never reuse a previous `batch_id` or `task_id`** unless the user explicitly says: "基于上一次结果继续" / "不要重新查" / "看刚才的结果" / "use the last result".
3. If the user asks for a result you already have from earlier, still re-query by default.

### Query Report (required in every real-time query response)

Every real-time query response MUST end with a query report block:

```
--- Query Report ---
- batch_id/task_id: xxx
- Called create: Yes
- Polling status: Yes (N polls, ~X min)
- Result batch_id/task_id: xxx
- Query time: YYYY-MM-DD HH:MM:SS (UTC+8)
-------------------
```

### Other Rules

4. **Always include** `ngrok-skip-browser-warning: true` header in every request
5. **Retry `result` once** if it times out, but don't poll aggressively
6. **`source` must be `"openclaw"`**
7. If polling 30 times without completion → tell user: "任务仍在执行中，请稍后手动查询"
8. Max 50 customers per batch/task
9. Max 20 candidates per HS quick search

---

## Excel Template

Standard batch template format:

| company_name | country | website |
|-------------|---------|---------|
| 上海某某公司 | CN | https://example.com |

The skill also accepts user's own Excel files — it will auto-detect headers.
