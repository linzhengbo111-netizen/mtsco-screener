"""
merge_final.py — 腾道两轮结果合并

规则：
1. 以 internal_customer_id 为主键
2. 第一轮 confirmed/likely_match → 保留第一轮
3. 第一轮 conflict → 保留 conflict，不被第二轮覆盖
4. 第一轮 no_result/unconfirmed/detail_page_failed：
   - 第二轮 confirmed/likely_match → 用第二轮覆盖
   - 第二轮 conflict → final=conflict，保留双状态
   - 第二轮 detail_page_failed → final=needs_manual_tendata_check
   - 其余 → final=no_tendata_signal
5. 输出 5 个 sheet
"""

from __future__ import annotations

import pandas as pd
from pathlib import Path
from datetime import datetime

INPUT_DIR = Path(__file__).parent.parent
FIRST_ROUND = INPUT_DIR / "tendata_result_BATCH-D49001FA_20260521_113400.xlsx"
RETRY_ROUND  = INPUT_DIR / "tendata_retry_noresult_unconfirmed.xlsx"
OUTPUT_PATH  = INPUT_DIR / "tendata_final_merged.xlsx"

# ── 输出列定义 ──
MERGED_COLUMNS = [
    "internal_customer_id",
    "customer_name",
    "country_region",
    "website_input",
    "email_domain",
    "first_round_match_status",
    "retry_match_status",
    "final_tendata_status",
    "final_matched_company_name",
    "final_matched_country",
    "final_match_confidence",
    "final_import_active_status",
    "final_latest_import_date",
    "final_raw_candidate_latest_import_date",
    "final_used_search_variant",
    "final_evidence_excerpt",
    "final_manual_review_flag",
    "final_manual_review_reason",
    "final_recommended_action",
]


def safe_str(val, default: str = "") -> str:
    import pandas as pd
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip() if str(val).strip() else default


def merge():
    df1 = pd.read_excel(FIRST_ROUND)
    df2 = pd.read_excel(RETRY_ROUND)

    # 建索引字典
    r1_map = {row["internal_customer_id"]: row for _, row in df1.iterrows()}
    r2_map = {row["internal_customer_id"]: row for _, row in df2.iterrows()}

    # 所有内码（取并集）
    all_ids = sorted(set(r1_map.keys()) | set(r2_map.keys()))

    merged_rows = []

    for cid in all_ids:
        r1 = r1_map.get(cid, {})
        r2 = r2_map.get(cid, {})

        s1 = safe_str(r1.get("match_status", ""))
        s2 = safe_str(r2.get("match_status", ""))

        # 默认值提取函数
        def r1_val(col, default=""):
            return safe_str(r1.get(col, default))

        def r2_val(col, default=""):
            return safe_str(r2.get(col, default))

        row = {c: "" for c in MERGED_COLUMNS}
        row["internal_customer_id"] = cid
        row["customer_name"] = safe_str(r1.get("customer_name", r2.get("customer_name", "")))
        row["country_region"] = safe_str(r1.get("country_region", r2.get("country_region", "")))
        row["website_input"] = safe_str(r1.get("website_input", r2.get("website_input", "")))
        row["email_domain"] = safe_str(r1.get("email_domain", r2.get("email_domain", "")))
        row["first_round_match_status"] = s1
        row["retry_match_status"] = s2

        # ── 规则判定 ──
        if s1 in ("confirmed", "likely_match"):
            # 规则 2：保留第一轮
            row["final_tendata_status"] = s1
            row["final_matched_company_name"] = r1_val("matched_company_name")
            row["final_matched_country"] = r1_val("location")
            row["final_match_confidence"] = r1_val("match_confidence")
            row["final_import_active_status"] = r1_val("import_active_status")
            row["final_latest_import_date"] = r1_val("latest_import_date")
            row["final_raw_candidate_latest_import_date"] = r1_val("raw_candidate_latest_import_date")
            row["final_evidence_excerpt"] = r1_val("evidence_excerpt")
            row["final_manual_review_flag"] = r1_val("manual_review_flag")
            row["final_manual_review_reason"] = r1_val("manual_review_reason")
            row["final_recommended_action"] = r1_val("recommended_action")

        elif s1 == "conflict":
            # 规则 3：保留 conflict，不被第二轮覆盖
            row["final_tendata_status"] = "conflict"
            row["final_matched_company_name"] = r1_val("matched_company_name")
            row["final_matched_country"] = r1_val("location")
            row["final_match_confidence"] = r1_val("match_confidence")
            row["final_import_active_status"] = r1_val("import_active_status")
            row["final_latest_import_date"] = r1_val("latest_import_date")
            row["final_raw_candidate_latest_import_date"] = r1_val("raw_candidate_latest_import_date")
            row["final_evidence_excerpt"] = r1_val("evidence_excerpt")
            row["final_manual_review_flag"] = r1_val("manual_review_flag")
            row["final_manual_review_reason"] = r1_val("manual_review_reason")
            row["final_recommended_action"] = r1_val("recommended_action")

        else:
            # s1 in (no_result, unconfirmed, detail_page_failed)
            if s2 in ("confirmed", "likely_match"):
                # 规则 4：用第二轮覆盖
                row["final_tendata_status"] = s2
                row["final_matched_company_name"] = r2_val("matched_company_name")
                row["final_matched_country"] = r2_val("location")
                row["final_match_confidence"] = r2_val("match_confidence")
                row["final_import_active_status"] = r2_val("import_active_status")
                row["final_latest_import_date"] = r2_val("latest_import_date")
                row["final_raw_candidate_latest_import_date"] = r2_val("raw_candidate_latest_import_date")
                row["final_used_search_variant"] = r2_val("used_search_variant")
                row["final_evidence_excerpt"] = r2_val("evidence_excerpt")
                row["final_manual_review_flag"] = r2_val("manual_review_flag")
                row["final_manual_review_reason"] = r2_val("manual_review_reason")
                row["final_recommended_action"] = r2_val("recommended_action")

            elif s2 == "conflict":
                # 规则 5：最终 conflict，保留双状态
                row["final_tendata_status"] = "conflict"
                row["final_matched_company_name"] = r2_val("matched_company_name")
                row["final_matched_country"] = r2_val("location")
                row["final_match_confidence"] = r2_val("match_confidence")
                row["final_import_active_status"] = r2_val("import_active_status")
                row["final_latest_import_date"] = r2_val("latest_import_date")
                row["final_raw_candidate_latest_import_date"] = r2_val("raw_candidate_latest_import_date")
                row["final_used_search_variant"] = r2_val("used_search_variant")
                row["final_evidence_excerpt"] = r2_val("evidence_excerpt")
                row["final_manual_review_flag"] = r2_val("manual_review_flag")
                row["final_manual_review_reason"] = r2_val("manual_review_reason")
                row["final_recommended_action"] = r2_val("recommended_action")

            elif s2 == "detail_page_failed":
                # 规则 6：详情页失败 → 人工腾道检查
                row["final_tendata_status"] = "needs_manual_tendata_check"
                row["final_matched_company_name"] = r2_val("matched_company_name")
                row["final_used_search_variant"] = r2_val("used_search_variant")
                row["final_manual_review_flag"] = "yes"
                reason_parts = []
                r = r2_val("manual_review_reason")
                if r:
                    reason_parts.append(r)
                reason_parts.append("详情页进入失败，需人工腾道核验")
                row["final_manual_review_reason"] = "; ".join(reason_parts)
                row["final_recommended_action"] = "转官网/LinkedIn核验"

            else:
                # 规则 7：无腾道信号
                row["final_tendata_status"] = "no_tendata_signal"
                row["final_manual_review_flag"] = "no"
                row["final_recommended_action"] = "转官网/LinkedIn核验"

        merged_rows.append(row)

    merged_df = pd.DataFrame(merged_rows, columns=MERGED_COLUMNS)

    # ── 统计 ──
    print("=== 最终状态统计 ===")
    for s, c in merged_df["final_tendata_status"].value_counts().items():
        print(f"  {s}: {c} 条")
    print()

    # ── 生成 5 个 sheet ──
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        # Sheet 1: A_腾道强信号
        strong = merged_df[merged_df["final_tendata_status"].isin(["confirmed", "likely_match"])]
        strong.to_excel(writer, sheet_name="A_腾道强信号", index=False)

        # Sheet 2: B_详情页失败需人工
        manual = merged_df[merged_df["final_tendata_status"] == "needs_manual_tendata_check"]
        manual.to_excel(writer, sheet_name="B_详情页失败需人工", index=False)

        # Sheet 3: C_无腾道信号转官网领英
        no_signal = merged_df[merged_df["final_tendata_status"] == "no_tendata_signal"]
        no_signal.to_excel(writer, sheet_name="C_无腾道信号转官网领英", index=False)

        # Sheet 4: D_冲突错配
        conflict = merged_df[merged_df["final_tendata_status"] == "conflict"]
        conflict.to_excel(writer, sheet_name="D_冲突错配", index=False)

        # Sheet 5: ALL_最终合并结果
        merged_df.to_excel(writer, sheet_name="ALL_最终合并结果", index=False)

    print(f"结果已导出: {OUTPUT_PATH}")
    print(f"  A_腾道强信号: {len(strong)} 条")
    print(f"  B_详情页失败需人工: {len(manual)} 条")
    print(f"  C_无腾道信号转官网领英: {len(no_signal)} 条")
    print(f"  D_冲突错配: {len(conflict)} 条")
    print(f"  ALL_最终合并结果: {len(merged_df)} 条")


if __name__ == "__main__":
    merge()
