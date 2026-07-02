# 客户优先级打分规则 — 第一版

> 版本：v1.0
> 范围：仅使用腾道返回字段，不接 CRM

---

## 输入

每条客户来自 batch result 的 `EnrichmentResult` JSON，使用以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `match_status` | string | matched / no_result / ambiguous |
| `match_confidence` | int | 0-100 |
| `latest_import_date` | string | YYYY-MM-DD 格式，可能为空 |
| `import_active_status` | string | active / inactive / unknown |
| `target_hs_amount_json` | string | JSON array of {hs_code, usd_amount} |
| `top_suppliers_json` | string | JSON array of {supplier_name, trade_count, usd_amount} |
| `phone` | string | 可能为空 |
| `email` | string | 可能为空 |
| `linkedin` | string | 可能为空 |
| `trade_count` | int | 贸易记录数 |

---

## 评分规则

**基础分：50 分**

### 1. 进口活跃度（`import_active_status`）

| 条件 | 分数 |
|------|------|
| `active` | +30 |
| `inactive` | +5 |
| `unknown` 或空 | 0 |

### 2. 进口日期新鲜度（`latest_import_date`）

计算距离今天的月份差：

| 条件 | 分数 |
|------|------|
| ≤ 6 个月 | +20 |
| 6-18 个月 | +10 |
| > 18 个月 | 0 |
| 空 | 0 |

### 3. 采购金额量级（`target_hs_amount_json`）

将 JSON 数组中所有 `usd_amount` 相加（解析为数字，忽略非数字值）：

| 条件 | 分数 |
|------|------|
| 合计 > $1,000,000 | +20 |
| 合计 $100,000 - $1,000,000 | +10 |
| 合计 > 0 但 < $100,000 | +5 |
| 数组为空或解析失败 | 0 |

### 4. 供应商数量（`top_suppliers_json`）

统计 JSON 数组长度：

| 条件 | 分数 |
|------|------|
| ≥ 3 家 | +10 |
| 1-2 家 | +5 |
| 数组为空或解析失败 | 0 |

### 5. 联系方式齐全度

检查 `phone` / `email` / `linkedin` 至少有一个非空：

| 条件 | 分数 |
|------|------|
| ≥ 1 个非空 | +10 |
| 全空 | 0 |

### 6. 匹配置信度（`match_confidence`）

| 条件 | 分数 |
|------|------|
| ≥ 80 | +5 |
| 60-79 | 0 |
| < 60 | -10 |

### 7. 匹配状态（`match_status`）

| 条件 | 分数 |
|------|------|
| `no_result` | -30 |
| `ambiguous` | -10 |
| `matched` | 0（已包含在置信度中） |

---

## 优先级分级

| 总分区间 | priority_level | priority_score 标签 | 含义 |
|---------|---------------|-------------------|------|
| ≥ 100 | `high` | 高 | 近期活跃采购，值得重点开发 |
| 70-99 | `medium` | 中 | 有采购潜力，可以跟进 |
| 40-69 | `low` | 低 | 信息不足或采购不活跃，保持关注 |
| < 40 | `skip` | 暂不跟进 | 未匹配到或无进口数据 |

---

## 推荐动作（`recommended_action`）

| priority_level | 动作 |
|---------------|------|
| `high` | `主动开发` |
| `medium` | `保持跟进` |
| `low` | `保持关注` |
| `skip` | `暂不跟进` |

---

## 优先级原因（`priority_reason`）

用一句话总结主要打分依据，中文。

格式模板：

```
{进口活跃度描述}，{金额描述}，{联系方式描述}
```

示例：
- "近6月有进口记录，采购金额>$100万，联系方式齐全"
- "18个月内有进口记录，现有供应商3家，但无有效联系方式"
- "未匹配到腾道企业记录"

---

## 推荐切入点（`recommended_entry_point`）

根据可用数据生成一句话：

| 数据情况 | 切入点模板 |
|---------|-----------|
| 有 `top_suppliers_json` | "从 {top_supplier_name} 现有供应关系切入" |
| 有 `target_hs_amount_json` | "从 HS 编码 {top_hs_code} 产品线切入，该公司年采购额约 ${amount}" |
| 两者都有 | "从 HS 编码 {top_hs_code} 产品线切入，该公司现有供应商为 {top_supplier_name}" |
| 都无 | "当前缺乏供应商和产品数据，建议先通过 {website/phone} 建立联系" |

---

## 沟通方向（`recommended_script_direction`）

根据数据特征组合生成：

| 特征组合 | 话术方向 |
|---------|---------|
| 有明确供应商 + 高金额 | "强调替代方案，指出当前供应商 {name} 的采购额 ${amount}，我们有更优价格和交期" |
| 进口活跃但供应商少 | "该公司近期有采购但供应商较少，可能存在供应单一化风险，可强调供应链多元化价值" |
| 进口不活跃 + 有历史日期 | "该公司曾有采购记录（最近 {date}），可了解是否暂停或转移，重新激活需求" |
| 无进口数据 | "暂未获取到采购数据，建议先通过基础信息建立联系，了解当前需求" |
| 未匹配到企业 | "腾道未找到匹配企业，建议通过其他渠道核实公司名称后重新查询" |
