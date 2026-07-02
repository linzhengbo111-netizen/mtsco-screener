"""
generate_report.py — 海关数据报告生成

功能：
- 将 EnrichmentResult 转换为 Markdown 报告
- 将 EnrichmentResult 转换为 JSON 结果文件
- 不依赖浏览器，纯离线生成
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from models import EnrichmentResult, Task


def _slug(name: str) -> str:
    """将公司名转为安全的文件名。"""
    import re
    s = re.sub(r"[^a-zA-Z0-9一-鿿]", "_", name)
    return s[:60] or "company"


def generate_customer_report(result: EnrichmentResult, output_dir: Path) -> str:
    """为单条客户生成 Markdown 报告。

    Returns:
        报告文件路径
    """
    slug = _slug(result.matched_company_name or result.customer_name)
    filename = f"report_{slug}.md"
    out_path = output_dir / filename

    def _dash(v: str) -> str:
        return v if v and v != "unknown" else "—"

    # 解析 JSON 子字段
    hs_rows = result.target_hs_amounts[:20]
    suppliers = result.top_suppliers[:3]
    products = result.top_products[:3]

    lines = [
        f"# 海关数据报告：{result.matched_company_name or result.customer_name}",
        "",
        f"> 报告生成时间：{result.source_capture_time or datetime.now().isoformat()}",
        f"> 数据来源：腾道（tendata.cn）",
        f"> 匹配状态：{result.match_status} (置信度 {result.match_confidence})",
        "",
        "---",
        "",
        "## 1. 企业基本信息",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 标准公司名 | {_dash(result.matched_company_name)} |",
        f"| 所在国家/地区 | {_dash(result.location)} |",
        f"| 公司状态 | {_dash(result.company_status)} |",
        f"| 官网 | {_dash(result.website_result)} |",
        f"| 地址 | {_dash(result.address)} |",
        f"| 电话 | {_dash(result.phone)} |",
        f"| 邮箱 | {_dash(result.email)} |",
        f"| WhatsApp | {_dash(result.whatsapp)} |",
        f"| LinkedIn | {_dash(result.linkedin)} |",
        "",
        "## 2. 主营产品",
        "",
    ]

    if products:
        lines.append("| 排名 | 产品名称 | 贸易次数 |")
        lines.append("|---|---|---|")
        for i, p in enumerate(products, 1):
            lines.append(f"| {i} | {_dash(p.get('product_name', ''))} | {_dash(p.get('trade_count', ''))} |")
    else:
        lines.append("未提取到产品信息。")

    lines.extend([
        "",
        "## 3. 进口分析",
        "",
        "### 3.1 进口概况",
        "",
        f"- 最近进口日期：{_dash(result.latest_import_date)}",
        f"- 进口活跃状态：{_dash(result.import_active_status)}",
        "",
    ])

    if hs_rows:
        lines.append("### 3.2 HS 编码明细")
        lines.append("")
        lines.append("| HS 编码 | 美元金额 | 供应商 | 日期 |")
        lines.append("|---|---|---|---|")
        for row in hs_rows:
            lines.append(
                f"| {row.get('hs_code', '')} | {row.get('usd_amount', '')} "
                f"| {row.get('supplier_name', '')} | {row.get('date', '')} |"
            )
        lines.append("")

    if suppliers:
        lines.append("### 3.3 主要供应商")
        lines.append("")
        lines.append("| 供应商 | 贸易次数 | 美元总额 |")
        lines.append("|---|---|---|")
        for s in suppliers:
            lines.append(
                f"| {s.get('supplier_name', '')} | {s.get('trade_count', '')} "
                f"| {s.get('usd_amount', '')} |"
            )
        lines.append("")

    lines.extend([
        "## 4. 综合评估",
        "",
        f"- **匹配状态**：{result.match_status} ({result.match_confidence}/100)",
        f"- **建议行动**：{result.recommended_action}",
        f"- **人工复核**：{result.manual_review_flag}",
        "",
        "---",
        "",
        "*本报告由 tendata-customer-enricher 自动生成，仅供参考。*",
        "",
    ])

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


def generate_task_report(task: Task, output_dir: Path) -> tuple[str, str]:
    """为整个任务生成 JSON 结果文件 + Markdown 汇总报告。

    Returns:
        (json_path, markdown_summary_path)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON 结果
    json_path = output_dir / f"result_{task.task_id}.json"
    json_path.write_text(task.to_json(), encoding="utf-8")

    # Markdown 汇总
    md_path = output_dir / f"report_{task.task_id}.md"
    lines = [
        f"# 海关数据报告 — 批次 {task.task_id}",
        "",
        f"> 生成时间：{datetime.now().isoformat()}",
        f"> 任务来源：{task.source}",
        f"> 客户总数：{len(task.customers)}",
        "",
        "---",
        "",
    ]

    # 汇总统计
    confirmed = sum(1 for r in task.results if r.match_status == "confirmed")
    likely = sum(1 for r in task.results if r.match_status == "likely_match")
    no_result = sum(1 for r in task.results if r.match_status == "no_result")
    failed = sum(1 for r in task.results if r.error_message)

    lines.extend([
        "## 汇总统计",
        "",
        f"- 成功匹配（confirmed）：{confirmed}",
        f"- 可能匹配（likely_match）：{likely}",
        f"- 无结果（no_result）：{no_result}",
        f"- 失败：{failed}",
        "",
        "---",
        "",
        "## 逐条结果",
        "",
    ])

    for r in task.results:
        lines.append(f"### {r.matched_company_name or r.customer_name}")
        lines.append("")
        lines.append(f"- 匹配状态：{r.match_status} ({r.match_confidence}/100)")
        lines.append(f"- 公司状态：{r.company_status}")
        lines.append(f"- 进口活跃：{r.import_active_status}")
        lines.append(f"- 建议行动：{r.recommended_action}")
        if r.error_message:
            lines.append(f"- 错误：{r.error_message}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "*本报告由 tendata-customer-enricher 自动生成，仅供参考。*",
        "",
    ])

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return str(json_path), str(md_path)


if __name__ == "__main__":
    # 最小自测
    r = EnrichmentResult(
        customer_name="Test Corp",
        matched_company_name="Test Corp Ltd",
        match_status="confirmed",
        match_confidence=95,
        company_status="active",
        location="United States",
        website_result="testcorp.com",
        phone="+1-555-0100",
        address="123 Test St",
    )
    p = generate_customer_report(r, Path(__file__).parent.parent / "output")
    print(f"Report generated: {p}")
