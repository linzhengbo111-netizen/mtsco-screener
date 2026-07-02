"""
export_results.py — 腾道抓取结果导出

功能：
- 将 EnrichmentRow 列表写入新 .xlsx 结果表
- 保留原始输入列 + 追加抓取结果列 + 技术字段
- 文件名带批次 ID 和时间戳
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import asdict

import pandas as pd


# 输出列顺序
OUTPUT_COLUMNS = [
    # ── 原始输入列 ──
    "internal_customer_id",
    "customer_name",
    "country_region",
    "website_input",
    "email_domain",
    "product_keywords",
    "search_keyword",
    "search_variants",
    # ── 基础匹配字段 ──
    "used_search_variant",
    "matched_company_name",
    "matched_country",
    "match_status",
    "match_confidence",
    "candidate_score",
    "name_match_level",
    "country_match",
    "domain_match",
    "product_match_level",
    "conflict_reason",
    "website_result",
    "company_status",
    "contact_name",
    "phone",
    "email",
    "address",
    "location",
    "whatsapp",
    "linkedin",
    # ── 采购活跃度字段 ──
    "latest_import_date",
    "raw_candidate_latest_import_date",
    "last_12m_import_count",
    "last_24m_import_count",
    "last_36m_import_count",
    "import_active_status",
    "import_frequency_level",
    "import_activity_summary",
    # ── 产品相关字段 ──
    "top_import_products",
    "matched_product_keywords",
    "related_hs_codes",
    "product_relevance_level",
    "product_relevance_score",
    "top_products_json",
    "target_hs_amount_json",
    # ── 供应链字段 ──
    "supplier_count",
    "top_suppliers",
    "main_supplier_countries",
    "china_supplier_signal",
    "supplier_stability_level",
    "top_suppliers_json",
    "top_3_import_countries_json",
    # ── 体量字段 ──
    "total_shipment_count",
    "estimated_trade_volume_level",
    "buyer_activity_level",
    "total_import_volume",
    "hs_product",
    # ── 推荐字段 ──
    "recommended_action",
    "evidence_excerpt",
    "current_url",
    "error_message",
    "elapsed_seconds",
    # ── 其他 ──
    "business_summary",
    "source_page_title",
    "analysis_entry_status",
    "analysis_data_status",
    # ── 技术字段 ──
    "source_system",
    "source_capture_time",
    "source_search_keyword",
    "source_candidate_rank",
    "source_page_url",
    "manual_review_flag",
    "manual_review_reason",
    "run_batch_id",
    # ── 候选摘要 ──
    "candidate_summary_json",
]


def export_results(
    rows: list[dict],
    output_path: str | None = None,
    batch_id: str = "",
) -> str:
    """
    将结果行列表导出为 .xlsx 文件。

    Args:
        rows: 每条为一个 dict，键为 OUTPUT_COLUMNS 中的字段名
        output_path: 输出文件路径；为空时自动生成
        batch_id: 运行批次 ID

    Returns:
        输出文件绝对路径
    """
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

    if not output_path:
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"tendata_result_{batch_id}_{ts}.xlsx"

    df.to_excel(output_path, index=False)
    return str(Path(output_path).resolve())


if __name__ == "__main__":
    # 骨架测试：创建一条示例数据并导出
    sample_row = {
        "customer_name": "测试公司",
        "country_region": "United States",
        "website_input": "test.com",
        "email_domain": "",
        "product_keywords": "",
        "internal_customer_id": "TEST-001",
        "matched_company_name": "Test Company Inc.",
        "match_status": "likely_match",
        "match_confidence": 70,
        "website_result": "https://www.test.com",
        "company_status": "unknown",
        "contact_name": "",
        "phone": "",
        "email": "",
        "address": "",
        "location": "",
        "whatsapp": "",
        "linkedin": "",
        "import_active_status": "unknown",
        "latest_import_date": "",
        "import_activity_summary": "",
        "business_summary": "Test Company Inc.，贸易记录: 0 次",
        "evidence_excerpt": "",
        "source_page_title": "贸易数据搜索结果页",
        "analysis_entry_status": "entry_not_found",
        "analysis_data_status": "unknown",
        "top_products_json": "",
        "target_hs_amount_json": "",
        "top_suppliers_json": "",
        "recommended_action": "待人工复核",
        "source_system": "tendata",
        "source_capture_time": "2026-04-17T10:00:00",
        "source_search_keyword": "测试公司",
        "source_candidate_rank": 1,
        "source_page_url": "",
        "manual_review_flag": "yes",
        "manual_review_reason": "骨架测试数据",
        "run_batch_id": "BATCH-001",
    }

    out = export_results([sample_row], batch_id="BATCH-001")
    print(f"示例结果已导出至: {out}")
