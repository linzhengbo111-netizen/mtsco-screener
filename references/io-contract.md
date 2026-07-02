# IO Contract — tendata-customer-enricher

> 定义本 skill 的输入输出契约，供上游系统（OpenClaw、影刀调度、人工调用）统一遵循。

## 1. 输入契约

### 1.1 标准输入格式（Excel）

| 列名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| customer_name | string | 是 | 客户公司名（英文） |
| country_region | string | 否 | 国家/地区 |
| website | string | 否 | 官网域名 |
| email_domain | string | 否 | 邮箱域名 |
| product_keywords | string | 否 | 产品关键词 |
| internal_customer_id | string | 否 | 内部 ID，透传回结果 |

兼容中文表头（公司名/国家/官网/邮箱/产品关键词/内部编号），由 `normalize_input.py` 自动映射。

### 1.2 JSON 任务输入（供 OpenClaw / 影刀调用）

```json
{
  "task_id": "TASK-20260421-001",
  "source": "openclaw",
  "customers": [
    {
      "customer_name": "SCOPE METALS GROUP LTD",
      "country_region": "Israel",
      "internal_customer_id": "C-1001"
    }
  ],
  "options": {
    "batch_size": 10,
    "generate_report": true,
    "report_format": "markdown"
  }
}
```

### 1.3 输入校验规则

- `customers` 数组长度 ≥ 1，单批 ≤ 10
- 每条必须含 `customer_name`（非空字符串）
- `customer_name` 长度 2–200 字符

## 2. 输出契约

### 2.1 标准输出格式（Excel）

保留所有输入列 + 追加以下抓取结果列：

| 字段 | 类型 | 说明 |
|---|---|---|
| matched_company_name | string | 腾道匹配到的标准公司名 |
| match_status | enum | confirmed / likely_match / unconfirmed / no_result |
| match_confidence | int | 0–100 |
| website_result | string | 公司官网 |
| company_status | string | active / inactive / unknown |
| phone | string | 联系电话 |
| email | string | 联系邮箱 |
| address | string | 公司地址 |
| whatsapp | string | WhatsApp 链接 |
| linkedin | string | LinkedIn 链接 |
| latest_import_date | string | 最近进口日期 |
| import_active_status | enum | active / inactive / unknown |
| analysis_entry_status | enum | entered_confirmed / clicked_not_confirmed / entry_not_found |
| analysis_data_status | enum | has_data / no_data / extraction_failed / unknown |
| source_capture_time | datetime | 抓取时间戳 |
| manual_review_flag | enum | yes / no |
| recommended_action | string | 建议继续跟进 / 待人工复核 / 暂不跟进 |

### 2.2 JSON 输出（供上游系统消费）

```json
{
  "task_id": "TASK-20260421-001",
  "status": "completed",
  "started_at": "2026-04-21T10:00:00",
  "finished_at": "2026-04-21T10:05:00",
  "results": [
    {
      "customer_name": "SCOPE METALS GROUP LTD",
      "matched_company_name": "SCOPE METALS GROUP LTD",
      "match_status": "confirmed",
      "match_confidence": 90,
      "website_result": "scope-metal.com",
      "company_status": "active",
      "phone": "+972528634466",
      "address": "P.O. BOX: 3",
      "whatsapp": "https://wa.me/972528634466",
      "linkedin": "https://www.linkedin.com/company/scope-metals-group-ltd-scpe-",
      "latest_import_date": "2026-03-11",
      "import_active_status": "active",
      "analysis_entry_status": "entered_confirmed",
      "analysis_data_status": "has_data",
      "top_products_json": "[{\"product_name\":\"STEEL WELDED PIPE\",\"trade_count\":\"548\"}]",
      "target_hs_amount_json": "[{\"hs_code\":\"72085110\",\"usd_amount\":\"32362.51\"}]",
      "top_suppliers_json": "[{\"supplier_name\":\"MISHRA DHATU NIGAM LTD\",\"trade_count\":\"4\",\"usd_amount\":\"219601.39\"}]",
      "recommended_action": "建议继续跟进"
    }
  ],
  "summary": {
    "total": 1,
    "confirmed": 1,
    "likely_match": 0,
    "no_result": 0,
    "failed": 0
  },
  "artifacts": {
    "excel_path": "output/tendata_result_BATCH-001.xlsx",
    "report_path": "output/report_BATCH-001.md"
  }
}
```

### 2.3 Markdown 报告输出

详见 [report-template.md](./report-template.md)。

## 3. 错误输出契约

任何失败均返回标准错误结构，不抛裸异常：

```json
{
  "task_id": "TASK-20260421-001",
  "status": "failed",
  "error_code": "TEN_LOGIN_REQUIRED",
  "error_message": "腾道登录态无效，请先登录",
  "details": {}
}
```

### 错误码表

| 错误码 | 说明 | 可重试 |
|---|---|---|
| TEN_LOGIN_REQUIRED | 腾道未登录 | 否（需人工登录） |
| TEN_PAGE_NOT_FOUND | 目标页面无法打开 | 是 |
| TEN_TIMEOUT | 操作超时 | 是 |
| TEN_SINGLE_FAIL | 单条抓取失败 | 是 |
| INPUT_INVALID | 输入格式非法 | 否 |
| QUEUE_FULL | 任务队列已满 | 是 |
