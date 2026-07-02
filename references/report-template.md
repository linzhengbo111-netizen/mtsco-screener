# Report Template — 海关数据报告

> 腾道抓取结果自动生成 Markdown 格式海关数据报告。

## 报告结构

```markdown
# 海关数据报告：{{company_name}}

> 报告生成时间：{{generated_at}}
> 数据来源：腾道（tendata.cn）
> 匹配状态：{{match_status}} (置信度 {{match_confidence}})

---

## 1. 企业基本信息

| 项目 | 内容 |
|---|---|
| 标准公司名 | {{matched_company_name}} |
| 所在国家/地区 | {{location}} |
| 公司状态 | {{company_status}} |
| 官网 | {{website_result}} |
| 地址 | {{address}} |
| 电话 | {{phone}} |
| 邮箱 | {{email}} |
| WhatsApp | {{whatsapp}} |
| LinkedIn | {{linkedin}} |

## 2. 主营产品

| 排名 | 产品名称 | 贸易次数 |
|---|---|---|
| 1 | {{product_1_name}} | {{product_1_count}} |
| 2 | {{product_2_name}} | {{product_2_count}} |
| 3 | {{product_3_name}} | {{product_3_count}} |

## 3. 进口分析

### 3.1 进口概况

- 最近进口日期：{{latest_import_date}}
- 进口活跃状态：{{import_active_status}}

### 3.2 HS 编码明细

| HS 编码 | 美元金额 | 供应商 | 日期 |
|---|---|---|---|
{% for row in hs_rows %}
| {{row.hs_code}} | {{row.usd_amount}} | {{row.supplier_name}} | {{row.date}} |
{% endfor %}

### 3.3 主要供应商

| 供应商 | 贸易次数 | 美元总额 |
|---|---|---|
{% for s in suppliers %}
| {{s.supplier_name}} | {{s.trade_count}} | {{s.usd_amount}} |
{% endfor %}

## 4. 综合评估

- **匹配状态**：{{match_status}} ({{match_confidence}}/100)
- **建议行动**：{{recommended_action}}
- **人工复核**：{{manual_review_flag}}

{% if manual_review_flag == "yes" %}
### 复核原因

{{manual_review_reason}}
{% endif %}

---

*本报告由 tendata-customer-enricher 自动生成，仅供参考。*
```

## 生成规则

1. 未提取到的字段显示为 `—`（破折号）
2. HS 明细最多展示前 20 条
3. 供应商展示前 3 家
4. 产品排名展示前 3 个
5. 报告文件命名：`report_{{task_id}}_{{company_name_slug}}.md`
