# 字段 Schema

## 业务必须字段（18 个）

| 字段名 | 类型 | 说明 |
|---|---|---|
| customer_name | string | 原始输入公司名 |
| country_region | string | 原始输入国家/地区 |
| matched_company_name | string | 腾道匹配到的标准公司名（详情页优先） |
| match_status | enum | `confirmed` / `likely_match` / `unconfirmed` / `no_result` |
| match_confidence | int(0-100) | 匹配置信度数值 |
| website_result | string | 匹配到的公司官网（已排除 tendata 平台域名） |
| company_status | string | 公司运营状态，无明确值填 `unknown` |
| contact_name | string | 联系人姓名，未提取到留空 |
| phone | string | 联系电话，未提取到留空 |
| email | string | 联系邮箱，未提取到留空 |
| address | string | 公司地址，未提取到留空 |
| import_active_status | enum | `active` / `inactive` / `unknown` |
| latest_import_date | date/string | 最近一次进口记录日期 |
| import_activity_summary | string | 进口活动一句话摘要 |
| business_summary | string | 综合企业详情+进口分析的一句话到两句话摘要 |
| evidence_excerpt | string | 最关键证据短摘录 |
| source_page_title | string | 证据主要来源页面标题 |
| recommended_action | enum | `建议继续跟进` / `待人工复核` / `暂不跟进` |

## 进口分析状态字段

| 字段名 | 类型 | 说明 |
|---|---|---|
| analysis_entry_status | enum | 进口分析页进入状态，见下表 |
| analysis_data_status | enum | 进口数据状态，见下表 |

### analysis_entry_status 枚举

| 值 | 说明 |
|---|---|
| entered_confirmed | 确认进入了进口分析页（URL/标题/tab激活/空态等多重验证） |
| clicked_not_confirmed | 点击了进口分析入口但未确认进入 |
| entry_not_found | 未找到进口分析入口 |

### analysis_data_status 枚举

| 值 | 说明 |
|---|---|
| has_data | 检测到进口数据（有日期/表格记录） |
| no_data | 明确显示"暂无进口数据"或无数据 |
| extraction_failed | 进入了但提取失败 |
| unknown | 未进入，未知 |

## 建议技术字段

| 字段名 | 类型 | 说明 |
|---|---|---|
| internal_customer_id | string | 原始输入内部 ID，透传 |
| source_system | string | 固定值 `tendata` |
| source_capture_time | datetime | 数据抓取时间戳 |
| source_search_keyword | string | 搜索使用的关键词 |
| source_candidate_rank | int | 候选公司排名（top1=1） |
| source_page_url | string | 详情页或进口分析页 URL |
| manual_review_flag | enum | `yes` / `no` |
| manual_review_reason | string | 人工复核原因（flag=yes 时填写） |
| run_batch_id | string | 本次运行批次 ID |

## match_confidence 区间

| 区间 | 对应 status |
|---|---|
| 85–100 | confirmed |
| 60–84 | likely_match |
| 30–59 | 低置信候选（unconfirmed） |
| 0–29 | unconfirmed / no_result |

## import_active_status 判定

- `active`：近 12 个月内有明确进口记录
- `inactive`：有历史记录但近 12 个月无明显活跃
- `unknown`：页面缺乏足够证据

## recommended_action 逻辑

| 条件 | 值 |
|---|---|
| confirmed + 近一年进口活跃 | 建议继续跟进 |
| likely_match 或冲突明显 | 待人工复核 |
| no_result / 明显不匹配 / 无活跃且证据弱 | 暂不跟进 |
