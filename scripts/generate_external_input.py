"""
generate_external_input.py — 生成官网/LinkedIn全量核验输入表

基于：
1. all/all_customers_unique.xlsx — 客户原始信息
2. tendata_all_merged.xlsx — 腾道合并结果

输出：external_all_customers_input.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


# 客户原始字段映射（中文名 -> 英文名）
CUSTOMER_FIELD_MAP = {
    "客户编码": "internal_customer_id",
    "公司名称": "customer_name",
    "公司简称": "company_short_name",
    "国家地区": "country_region",
    "客户状态": "customer_status",
    "客户等级": "customer_level",
    "公司网站": "website_input",
    "邮箱": "email",
    "公司电话": "phone",
    "联系人": "contact_person",
    "LinkedIn": "linkedin",
    "主营产品": "product_keywords",
    "分管人": "owner",
    "最后跟进总结": "latest_followup_summary",
    "最后联系时间": "last_contact_date",
    "最后一次购买时间": "last_purchase_date",
}

# 腾道结果字段
TENDATA_FIELDS = [
    "match_status",
    "matched_company_name",
    "matched_country",
    "match_confidence",
    "analysis_data_status",
    "latest_import_date",
    "last_12m_import_count",
    "last_24m_import_count",
    "last_36m_import_count",
    "total_shipment_count",
    "supplier_count",
    "top_suppliers",
    "china_supplier_signal",
    "top_import_products",
    "related_hs_codes",
    "product_relevance_level",
    "product_relevance_score",
    "import_active_status",
    "import_frequency_level",
    "candidate_summary_json",
    "manual_review_flag",
    "manual_review_reason",
    "recommended_action",
    "evidence_excerpt",
]

# 新增官网核验字段
WEBSITE_CHECK_FIELDS = [
    "website_accessible",
    "website_match_status",
    "website_business_status",
    "website_product_relevance",
    "website_contact_found",
    "website_contact_email",
    "website_contact_phone",
    "website_evidence_url",
    "website_evidence_summary",
]

# 新增 LinkedIn 核验字段
LINKEDIN_CHECK_FIELDS = [
    "linkedin_company_found",
    "linkedin_company_url",
    "linkedin_company_name",
    "linkedin_employee_range",
    "linkedin_country_match",
    "linkedin_industry",
    "linkedin_recent_activity",
    "linkedin_clean_status",
    "linkedin_clean_reason",
]

# 外部核验综合字段
EXTERNAL_CHECK_FIELDS = [
    "external_check_confidence",
    "external_check_summary",
    "manual_review_reason_external",
    "external_recommended_action",
]


def extract_email_domain(email: str) -> str:
    """从邮箱提取域名。"""
    if not email or "@" not in str(email):
        return ""
    return str(email).split("@")[-1].strip()


def main():
    root_dir = Path(__file__).parent.parent

    print("=" * 60)
    print("生成官网/LinkedIn全量核验输入表")
    print("=" * 60)

    # 1. 读取客户原始信息
    print("\n[1/4] 读取客户原始信息...")
    customers_df = pd.read_excel(root_dir / "all" / "all_customers_unique.xlsx", header=3)
    print(f"  原始行数: {len(customers_df)}")

    # 重命名列
    rename_map = {cn: en for cn, en in CUSTOMER_FIELD_MAP.items() if cn in customers_df.columns}
    customers_df = customers_df.rename(columns=rename_map)

    # 去重（按客户编码，保留第一条）
    if "internal_customer_id" in customers_df.columns:
        print(f"  客户编码唯一值: {customers_df['internal_customer_id'].nunique()}")
        customers_df = customers_df.drop_duplicates(subset=["internal_customer_id"], keep="first")

    print(f"  去重后行数: {len(customers_df)}")

    # 2. 读取腾道合并结果
    print("\n[2/4] 读取腾道合并结果...")
    tendata_df = pd.read_excel(root_dir / "tendata_all_merged.xlsx", sheet_name="ALL_腾道合并结果")
    print(f"  腾道结果行数: {len(tendata_df)}")

    # 只保留腾道字段
    tendata_fields_exist = [f for f in TENDATA_FIELDS if f in tendata_df.columns]
    tendata_df = tendata_df[["internal_customer_id"] + tendata_fields_exist]

    # 3. 合并
    print("\n[3/4] 合并数据...")
    merged_df = customers_df.merge(
        tendata_df,
        on="internal_customer_id",
        how="inner"  # 只保留腾道结果中的456个客户
    )
    print(f"  合并后行数: {len(merged_df)}")

    # 添加 email_domain
    if "email" in merged_df.columns:
        merged_df["email_domain"] = merged_df["email"].apply(extract_email_domain)
    else:
        merged_df["email_domain"] = ""

    # 添加空白核验字段
    for field in WEBSITE_CHECK_FIELDS + LINKEDIN_CHECK_FIELDS + EXTERNAL_CHECK_FIELDS:
        merged_df[field] = ""

    # 4. 生成汇总
    print("\n[4/4] 生成汇总...")

    summary_data = [
        ["总客户数", len(merged_df)],
        ["有官网 website_input 数量", (merged_df["website_input"].notna() & (merged_df["website_input"].astype(str).str.strip() != "")).sum()],
        ["有 email_domain 数量", (merged_df["email_domain"].notna() & (merged_df["email_domain"].astype(str).str.strip() != "")).sum()],
        ["有 LinkedIn 原字段数量", (merged_df["linkedin"].notna() & (merged_df["linkedin"].astype(str).str.strip() != "")).sum()],
    ]

    # match_status 分布
    status_counts = merged_df["match_status"].value_counts().to_dict()
    for status in ["confirmed", "likely_match", "conflict", "candidate_found_not_entered", "no_result"]:
        summary_data.append([f"腾道 {status} 数量", status_counts.get(status, 0)])

    summary_df = pd.DataFrame(summary_data, columns=["指标", "数值"])

    # 输出
    output_path = root_dir / "external_all_customers_input.xlsx"
    print(f"\n保存输出: {output_path}")

    # 整理输出列顺序
    output_columns = (
        ["internal_customer_id", "customer_name", "company_short_name", "country_region",
         "customer_status", "customer_level", "website_input", "email", "email_domain",
         "phone", "contact_person", "linkedin", "product_keywords", "last_purchase_date",
         "last_contact_date", "owner", "latest_followup_summary"] +
        TENDATA_FIELDS +
        WEBSITE_CHECK_FIELDS +
        LINKEDIN_CHECK_FIELDS +
        EXTERNAL_CHECK_FIELDS
    )

    # 只保留存在的列
    output_columns_exist = [c for c in output_columns if c in merged_df.columns]
    output_df = merged_df[output_columns_exist]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        output_df.to_excel(writer, sheet_name="ALL_外部核验输入", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    # 打印汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    for _, row in summary_df.iterrows():
        print(f"  {row['指标']}: {row['数值']}")

    print(f"\n输出文件: {output_path}")


if __name__ == "__main__":
    main()
