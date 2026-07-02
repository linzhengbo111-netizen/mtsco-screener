"""
merge_tendata_results.py — 合并腾道结果文件

基于 tendata_result_manifest.xlsx 合并所有 is_valid_for_merge = yes 的结果文件。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd


# match_status 优先级 (数值越大越优先)
STATUS_PRIORITY = {
    "confirmed": 100,
    "likely_match": 90,
    "unconfirmed": 70,
    "candidate_found_not_entered": 60,
    "no_result": 50,
    "conflict": 40,
    "excluded_internal_record": 30,
    "detail_page_failed": 20,
    "system_error": 10,
}

# 需要清除的进口相关字段 (conflict 客户)
IMPORT_FIELDS_TO_CLEAR = [
    "latest_import_date", "last_12m_import_count", "last_24m_import_count",
    "last_36m_import_count", "total_shipment_count", "supplier_count",
    "top_import_products", "related_hs_codes", "product_relevance_level",
    "product_relevance_score", "import_frequency_level",
]


def get_customer_key(row: pd.Series) -> str:
    """生成客户唯一键。"""
    internal_id = str(row.get("internal_customer_id", "") or "").strip()
    if internal_id:
        return f"ID:{internal_id}"

    name = str(row.get("customer_name", "") or "").strip().lower()
    country = str(row.get("country_region", "") or "").strip().lower()
    return f"NAME:{name}|{country}"


def get_status_priority(status: str) -> int:
    """获取 match_status 优先级。"""
    return STATUS_PRIORITY.get(status, 0)


def clear_import_fields(row: pd.Series) -> pd.Series:
    """清除 conflict 客户的进口相关字段。"""
    for field in IMPORT_FIELDS_TO_CLEAR:
        if field in row.index:
            row[field] = ""
    row["import_active_status"] = "invalid_for_target"
    return row


def merge_results(manifest_path: str, root_dir: str) -> tuple[pd.DataFrame, dict]:
    """合并结果文件。"""
    # 读取 manifest
    manifest_df = pd.read_excel(manifest_path, sheet_name="Merge_Decision")

    # 获取有效文件列表
    valid_files = manifest_df[manifest_df["is_valid_for_merge"] == "yes"]["result_file"].tolist()
    print(f"找到 {len(valid_files)} 个有效结果文件")

    # 读取所有结果
    all_rows = []
    for file_name in valid_files:
        file_path = Path(root_dir) / file_name
        if not file_path.exists():
            print(f"[WARN] 文件不存在: {file_name}")
            continue

        df = pd.read_excel(file_path)
        print(f"  读取 {file_name}: {len(df)} 行")
        all_rows.append(df)

    if not all_rows:
        raise ValueError("没有有效的结果文件")

    # 合并所有数据
    combined = pd.concat(all_rows, ignore_index=True)
    print(f"合并后总行数: {len(combined)}")

    # 按客户键分组，选择优先级最高的记录
    customer_best = {}

    for idx, row in combined.iterrows():
        key = get_customer_key(row)
        status = str(row.get("match_status", "no_result"))
        priority = get_status_priority(status)

        if key not in customer_best:
            customer_best[key] = (priority, idx, row)
        else:
            existing_priority, existing_idx, existing_row = customer_best[key]
            if priority > existing_priority:
                customer_best[key] = (priority, idx, row)

    # 构建最终数据框
    final_rows = []
    for key, (priority, idx, row) in customer_best.items():
        row = row.copy()

        # conflict 客户清除进口字段
        if row.get("match_status") == "conflict":
            row = clear_import_fields(row)

        final_rows.append(row)

    result_df = pd.DataFrame(final_rows)
    print(f"去重后客户数: {len(result_df)}")

    # 统计
    stats = {
        "total": len(result_df),
        "status_counts": result_df["match_status"].value_counts().to_dict(),
    }

    return result_df, stats


def classify_results(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """分类结果到不同 sheet。"""
    sheets = {}

    # 1. 全部合并结果
    sheets["ALL_腾道合并结果"] = df

    # 2. A_腾道强采购信号
    condition_a = (
        df["match_status"].isin(["confirmed", "likely_match"]) &
        ~df["analysis_data_status"].isin(["partial_import_signal", "no_import_analysis_data", "partial_base_info_only"]) &
        df["import_active_status"].isin(["active", "recent"])
    )
    sheets["A_腾道强采购信号"] = df[condition_a].copy()

    # 3. B_主体确认但进口信号弱
    condition_b = (
        df["match_status"].isin(["confirmed", "likely_match"]) &
        (
            df["analysis_data_status"].isin(["partial_import_signal", "no_import_analysis_data", "partial_base_info_only"]) |
            df["import_active_status"].isin(["unknown", "inactive"])
        )
    )
    sheets["B_主体确认但进口信号弱"] = df[condition_b].copy()

    # 4. C_候选弱或未确认
    condition_c = df["match_status"].isin(["candidate_found_not_entered", "unconfirmed", "no_result"])
    sheets["C_候选弱或未确认"] = df[condition_c].copy()

    # 5. D_冲突错配
    condition_d = df["match_status"] == "conflict"
    sheets["D_冲突错配"] = df[condition_d].copy()

    # 6. E_内部记录排除
    condition_e = df["match_status"] == "excluded_internal_record"
    sheets["E_内部记录排除"] = df[condition_e].copy()

    # 7. Manual_Review
    if "manual_review_flag" in df.columns:
        condition_m = df["manual_review_flag"] == "yes"
        sheets["Manual_Review"] = df[condition_m].copy()
    else:
        sheets["Manual_Review"] = pd.DataFrame()

    return sheets


def generate_summary(df: pd.DataFrame, sheets: dict) -> pd.DataFrame:
    """生成汇总统计。"""
    summary_data = []

    # 基本统计
    summary_data.append(["总客户数", len(df)])

    status_counts = df["match_status"].value_counts().to_dict()
    for status in ["confirmed", "likely_match", "unconfirmed", "candidate_found_not_entered",
                   "no_result", "conflict", "excluded_internal_record", "system_error"]:
        summary_data.append([f"{status} 数量", status_counts.get(status, 0)])

    # 分类统计
    summary_data.append(["A_腾道强采购信号数量", len(sheets.get("A_腾道强采购信号", []))])
    summary_data.append(["B_主体确认但进口信号弱数量", len(sheets.get("B_主体确认但进口信号弱", []))])
    summary_data.append(["C_候选弱或未确认数量", len(sheets.get("C_候选弱或未确认", []))])
    summary_data.append(["D_冲突错配数量", len(sheets.get("D_冲突错配", []))])
    summary_data.append(["E_内部记录排除数量", len(sheets.get("E_内部记录排除", []))])

    # weak_candidate_ignored 统计
    if "manual_review_reason" in df.columns:
        weak_count = df["manual_review_reason"].str.contains("weak_candidate_ignored", na=False).sum()
        summary_data.append(["weak_candidate_ignored 数量", weak_count])

    # 需要人工复核
    summary_data.append(["需要人工复核数量", len(sheets.get("Manual_Review", []))])

    summary_df = pd.DataFrame(summary_data, columns=["指标", "数值"])

    # 按国家统计
    if "country_region" in df.columns:
        country_stats = df.groupby(["country_region", "match_status"]).size().unstack(fill_value=0)
        summary_data.append(["", ""])
        summary_data.append(["=== 按国家统计 ===", ""])
        for country in country_stats.index:
            for status in country_stats.columns:
                count = country_stats.loc[country, status]
                if count > 0:
                    summary_data.append([f"{country} - {status}", count])

    # 按客户等级统计
    if "customer_level" in df.columns:
        level_stats = df.groupby(["customer_level", "match_status"]).size().unstack(fill_value=0)
        summary_data.append(["", ""])
        summary_data.append(["=== 按客户等级统计 ===", ""])
        for level in level_stats.index:
            for status in level_stats.columns:
                count = level_stats.loc[level, status]
                if count > 0:
                    summary_data.append([f"{level} - {status}", count])

    return pd.DataFrame(summary_data, columns=["指标", "数值"])


def main():
    root_dir = Path(__file__).parent.parent
    manifest_path = root_dir / "tendata_result_manifest.xlsx"
    output_path = root_dir / "tendata_all_merged.xlsx"

    print("=" * 60)
    print("合并腾道结果文件")
    print("=" * 60)

    # 合并结果
    print("\n[1/3] 读取并合并结果文件...")
    merged_df, stats = merge_results(manifest_path, root_dir)

    # 分类结果
    print("\n[2/3] 分类结果...")
    sheets = classify_results(merged_df)
    for name, sheet_df in sheets.items():
        print(f"  {name}: {len(sheet_df)} 行")

    # 生成汇总
    print("\n[3/3] 生成汇总...")
    summary_df = generate_summary(merged_df, sheets)
    sheets["Summary"] = summary_df

    # 输出 Excel
    print(f"\n保存合并结果: {output_path}")
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, sheet_df in sheets.items():
            sheet_df.to_excel(writer, sheet_name=name, index=False)

    # 打印汇总
    print("\n" + "=" * 60)
    print("合并完成")
    print("=" * 60)
    print(f"总客户数: {len(merged_df)}")
    print(f"\nmatch_status 分布:")
    for status, count in stats["status_counts"].items():
        print(f"  {status}: {count}")

    print(f"\n分类统计:")
    print(f"  A_腾道强采购信号: {len(sheets['A_腾道强采购信号'])}")
    print(f"  B_主体确认但进口信号弱: {len(sheets['B_主体确认但进口信号弱'])}")
    print(f"  C_候选弱或未确认: {len(sheets['C_候选弱或未确认'])}")
    print(f"  D_冲突错配: {len(sheets['D_冲突错配'])}")
    print(f"  E_内部记录排除: {len(sheets['E_内部记录排除'])}")
    print(f"  Manual_Review: {len(sheets['Manual_Review'])}")

    print(f"\n输出文件: {output_path}")


if __name__ == "__main__":
    main()
