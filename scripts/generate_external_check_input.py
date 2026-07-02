"""
generate_external_check_input.py — 生成官网/LinkedIn 核验输入表

从 tendata_final_merged.xlsx 提取 A_腾道强信号 + B_详情页失败需人工，
生成 external_check_input.xlsx（3 个 sheet）。
"""

from __future__ import annotations

import pandas as pd
from pathlib import Path

INPUT_DIR = Path(__file__).parent.parent
MERGED_FILE = INPUT_DIR / "tendata_final_merged.xlsx"
OUTPUT_FILE  = INPUT_DIR / "external_check_input.xlsx"

# ── 源字段（来自 merged） ──
SOURCE_FIELDS = [
    "internal_customer_id",
    "customer_name",
    "country_region",
    "website_input",
    "email_domain",
    "linkedin",
    "final_tendata_status",
    "final_matched_company_name",
    "final_matched_country",
    "final_import_active_status",
    "final_latest_import_date",
    "final_used_search_variant",
    "final_evidence_excerpt",
    "final_manual_review_reason",
]

# ── 新增待填写字段（默认留空） ──
NEW_FIELDS = [
    "website_accessible",
    "website_match_status",
    "website_business_status",
    "website_product_relevance",
    "website_contact_found",
    "website_evidence_url",
    "website_evidence_summary",
    "linkedin_company_found",
    "linkedin_company_url",
    "linkedin_employee_range",
    "linkedin_country_match",
    "linkedin_recent_activity",
    "contact_still_at_company",
    "external_check_confidence",
    "external_check_summary",
    "followup_priority_candidate",
    "manual_review_flag",
    "manual_review_reason",
]

ALL_COLUMNS = SOURCE_FIELDS + NEW_FIELDS


def _safe_str(val, default: str = "") -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip() if str(val).strip() else default


def generate():
    df_merged = pd.read_excel(MERGED_FILE, sheet_name="ALL_最终合并结果")
    df_a = pd.read_excel(MERGED_FILE, sheet_name="A_腾道强信号")
    df_b = pd.read_excel(MERGED_FILE, sheet_name="B_详情页失败需人工")

    def _build_sheet(source_df, source_name):
        rows = []
        for _, row in source_df.iterrows():
            new_row = {}
            for col in ALL_COLUMNS:
                if col in SOURCE_FIELDS:
                    new_row[col] = _safe_str(row.get(col))
                else:
                    new_row[col] = ""
            rows.append(new_row)
        return pd.DataFrame(rows, columns=ALL_COLUMNS)

    # Sheet 1: Batch_A
    sheet_a = _build_sheet(df_a, "A_腾道强信号")

    # Sheet 2: Batch_B
    sheet_b = _build_sheet(df_b, "B_详情页失败需人工")

    # Sheet 3: External_Check_Template = A + B 合并
    sheet_combined = pd.concat([sheet_a, sheet_b], ignore_index=True)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        sheet_a.to_excel(writer, sheet_name="Batch_A_Tendata_Strong", index=False)
        sheet_b.to_excel(writer, sheet_name="Batch_B_Manual_Tendata", index=False)
        sheet_combined.to_excel(writer, sheet_name="External_Check_Template", index=False)

    print(f"已导出: {OUTPUT_FILE}")
    print(f"  Batch_A_Tendata_Strong: {len(sheet_a)} 条")
    print(f"  Batch_B_Manual_Tendata: {len(sheet_b)} 条")
    print(f"  External_Check_Template: {len(sheet_combined)} 条")
    print(f"  字段数: {len(ALL_COLUMNS)}")


if __name__ == "__main__":
    generate()
