"""
merge_and_rank_business_list.py — 合并客户候选池与官网/LinkedIn验证结果，生成业务参考客户清单

功能：
1. 合并客户候选池主文件与官网/LinkedIn查询结果
2. 计算官网LinkedIn加分
3. 计算最终业务参考分
4. 划分业务参考层级
5. 生成风险提示
6. 按层级分sheet导出

用法：
    python scripts/merge_and_rank_business_list.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

# 确保控制台 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ============================================================================
# 配置常量
# ============================================================================

# 输入文件
MAIN_FILE = "output/客户候选池_正式版_含A10_B256腾道回填.xlsx"
VERIFY_FILE = "output/opencli_company_verify_AB_priority.xlsx"
PUBLIC_INFO_FILE = "output/public_info_query_AB_combined_271.xlsx"

# 输出文件
OUTPUT_FILE = "output/业务参考客户清单_AB_含公开信息验证_完整版.xlsx"


# ============================================================================
# 字段映射
# ============================================================================

# 官网/LinkedIn 字段映射（从验证文件 -> 新字段名）
VERIFY_FIELD_MAP = {
    "official_website_url": "官网 URL",
    "website_company_name": "官网公司名",
    "website_country": "官网国家",
    "website_products": "官网产品",
    "website_match_status": "官网匹配状态",
    "linkedin_company_url": "LinkedIn 公司主页",
    "linkedin_company_name": "LinkedIn 公司名",
    "linkedin_country": "LinkedIn 国家",
    "linkedin_industry": "LinkedIn 行业",
    "linkedin_employee_size": "LinkedIn 员工规模",
    "linkedin_match_status": "LinkedIn 匹配状态",
    "web_linkedin_confidence": "官网LinkedIn置信度",
    "web_linkedin_note": "官网LinkedIn备注",
}

# 公开信息字段映射（从 public_info_query 结果 -> 新字段名）
PUBLIC_INFO_FIELD_MAP = {
    "official_website_url": "公开信息-官网URL",
    "website_summary": "公开信息-官网简介",
    "website_products_or_services": "公开信息-官网产品",
    "linkedin_company_url": "公开信息-LinkedIn URL",
    "linkedin_employee_size": "公开信息-员工规模",
    "linkedin_summary": "公开信息-LinkedIn简介",
    "third_party_source_url": "公开信息-第三方来源",
    "public_company_summary": "公开信息-综合简介",
    "public_main_business": "公开信息-主营业务",
    "public_products_or_services": "公开信息-产品服务",
    "public_company_scale": "公开信息-公司规模",
    "public_info_confidence": "公开信息置信度",
    "public_info_query_status": "公开信息查询状态",
    "public_info_sources": "公开信息来源",
    "public_info_note": "公开信息备注",
}


# ============================================================================
# 核心函数
# ============================================================================

def calculate_web_linkedin_bonus(row: pd.Series) -> int:
    """计算官网LinkedIn加分（综合旧验证 + 新公开信息）"""
    confidence = row.get("官网LinkedIn置信度", 0)
    website_status = str(row.get("官网匹配状态", "")).strip()
    linkedin_status = str(row.get("LinkedIn 匹配状态", "")).strip()
    pub_confidence = str(row.get("公开信息置信度", "")).strip()
    pub_sources = str(row.get("公开信息来源", "")).strip()

    # 如果是 NaN 或空值，设为 0
    if pd.isna(confidence) or confidence == "" or confidence == "nan":
        confidence = 0
    else:
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            confidence = 0

    # 不匹配扣分
    if website_status == "不匹配" or linkedin_status == "不匹配":
        return -20

    # 基于旧验证的加分
    base_bonus = 0
    if confidence >= 60:
        base_bonus = 10
    elif confidence >= 30:
        base_bonus = 5

    # 基于新公开信息的额外加分
    pub_bonus = 0
    if pub_confidence == "高":
        pub_bonus = 5
    elif pub_confidence == "中":
        pub_bonus = 3
    # 官网+LinkedIn 双来源额外加
    if "+" in pub_sources:
        pub_bonus += 2

    return base_bonus + pub_bonus


def calculate_final_business_score(row: pd.Series) -> float:
    """计算最终业务参考分"""
    def safe_float(val, default=0):
        if pd.isna(val) or val == "" or val == "nan":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # 尝试使用最终总分
    final_score = safe_float(row.get("最终总分", np.nan), np.nan)
    mail_score = safe_float(row.get("邮件线索评分", 0))
    tendata_score = safe_float(row.get("腾道评分", 0))
    web_linkedin_bonus = safe_float(row.get("官网LinkedIn加分", 0))

    if not np.isnan(final_score):
        return final_score + web_linkedin_bonus
    else:
        return mail_score + tendata_score + web_linkedin_bonus


def determine_business_level(row: pd.Series) -> str:
    """判定业务参考层级"""
    final_priority = str(row.get("最终优先级", "")).strip()
    tendata_status = str(row.get("腾道排查状态", "")).strip()

    def safe_float(val, default=0):
        if pd.isna(val) or val == "" or val == "nan":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    final_business_score = safe_float(row.get("最终业务参考分", 0))
    website_status = str(row.get("官网匹配状态", "")).strip()
    linkedin_status = str(row.get("LinkedIn 匹配状态", "")).strip()

    # 1. 不建议优先
    if final_priority == "D":
        return "不建议优先"
    if tendata_status == "排除-内部公司":
        return "不建议优先"
    if website_status == "不匹配" or linkedin_status == "不匹配":
        return "不建议优先"

    # 2. 强推荐
    if final_priority in ["A+", "A"] and tendata_status == "已查-确认匹配":
        return "强推荐"

    # 3. 优先参考
    if final_priority in ["A+", "A", "B"]:
        if tendata_status in ["已查-确认匹配", "已查-可能匹配", "待人工复核"]:
            if final_business_score >= 70:
                return "优先参考"

    # 4. 谨慎参考
    if tendata_status in ["已查-未确认", "已查-未找到", "排查失败-可重试"]:
        return "谨慎参考"
    if website_status == "未找到" or linkedin_status == "未找到":
        # 只有当官网和LinkedIn都查了且都是未找到时才归为谨慎参考
        if website_status == "未找到" and linkedin_status == "未找到":
            return "谨慎参考"

    # 5. 可跟进观察
    if final_business_score >= 60:
        return "可跟进观察"

    # 默认归为谨慎参考
    return "谨慎参考"


def generate_risk_note(row: pd.Series) -> str:
    """生成风险提示"""
    tendata_status = str(row.get("腾道排查状态", "")).strip()
    website_status = str(row.get("官网匹配状态", "")).strip()
    linkedin_status = str(row.get("LinkedIn 匹配状态", "")).strip()
    web_verified = row.get("官网LinkedIn置信度", "")
    pub_confidence = str(row.get("公开信息置信度", "")).strip()
    pub_sources = str(row.get("公开信息来源", "")).strip()

    # 内部公司
    if tendata_status == "排除-内部公司":
        return "内部公司，排除"

    # 官网/LinkedIn 不匹配
    if website_status == "不匹配" or linkedin_status == "不匹配":
        return "官网/LinkedIn 疑似不匹配，谨慎使用"

    # 腾道确认 + 公开信息有验证
    if tendata_status == "已查-确认匹配":
        if pub_confidence == "高":
            return f"腾道确认，公开信息高置信度验证（来源: {pub_sources}）"
        elif pub_confidence == "中":
            return f"腾道确认，公开信息中等置信度（来源: {pub_sources}）"
        elif not pd.isna(web_verified) and web_verified != "" and web_verified >= 30:
            return "腾道确认，官网/LinkedIn 有辅助验证"
        else:
            return "腾道确认，但官网/LinkedIn 未找到"

    # 腾道可能匹配
    if tendata_status == "已查-可能匹配":
        if pub_confidence in ["高", "中"]:
            return f"腾道可能匹配，公开信息辅助确认（{pub_sources}）"
        return "腾道可能匹配，建议业务跟进前核对公司名"

    # 待人工复核
    if tendata_status == "待人工复核":
        if pub_confidence == "高":
            return f"腾道匹配存冲突，但公开信息高置信度（{pub_sources}）"
        return "腾道匹配存在冲突，仅作参考"

    # 腾道未确认/未找到
    if tendata_status in ["已查-未确认", "已查-未找到"]:
        if pub_confidence in ["高", "中"]:
            return f"腾道未确认，但公开信息有线索（{pub_sources}）"
        return "腾道未确认，主要参考邮件线索"

    # 官网/LinkedIn 未查询
    if pd.isna(web_verified) or web_verified == "":
        if pub_confidence:
            return f"公开信息置信度: {pub_confidence}（{pub_sources}）"
        return "未做官网/LinkedIn 验证"

    return ""


def generate_recommendation(row: pd.Series) -> str:
    """生成推荐动作"""
    level = row.get("业务参考层级", "")

    if level == "强推荐":
        return "优先安排业务跟进，发送产品介绍和报价"
    elif level == "优先参考":
        return "建议近期跟进，确认采购需求"
    elif level == "可跟进观察":
        return "可定期发送产品更新，保持联系"
    elif level == "谨慎参考":
        return "建议先核实公司信息再跟进"
    else:
        return "暂不建议主动跟进"


# ============================================================================
# 主处理函数
# ============================================================================

def main():
    print("=" * 70)
    print("业务参考客户清单生成")
    print("=" * 70)

    # 1. 读取文件
    print("\n[1/6] 读取输入文件...")
    df_main = pd.read_excel(MAIN_FILE)
    df_verify = pd.read_excel(VERIFY_FILE)
    df_public = pd.read_excel(PUBLIC_INFO_FILE)
    print(f"  客户候选池: {len(df_main)} 条")
    print(f"  官网/LinkedIn验证: {len(df_verify)} 条")
    print(f"  公开信息查询: {len(df_public)} 条")

    # 2. 重命名验证文件字段
    print("\n[2/6] 处理官网/LinkedIn验证字段...")
    df_verify_renamed = df_verify.rename(columns=VERIFY_FIELD_MAP)

    # 添加标记字段
    df_verify_renamed["官网LinkedIn验证状态"] = "已验证"

    # 重命名公开信息字段
    df_public_renamed = df_public.rename(columns=PUBLIC_INFO_FIELD_MAP)

    # 3. 合并数据
    print("\n[3/6] 合并数据...")
    # 使用 客户聚合 Key 匹配 internal_customer_id
    df_merged = df_main.merge(
        df_verify_renamed,
        left_on="客户聚合 Key",
        right_on="internal_customer_id",
        how="left"
    )

    # 合并公开信息（也用 客户聚合 Key 匹配 internal_customer_id）
    df_merged = df_merged.merge(
        df_public_renamed,
        left_on="客户聚合 Key",
        right_on="internal_customer_id",
        how="left",
        suffixes=("", "_pub")
    )

    # 填充未验证客户的字段
    verify_fields = list(VERIFY_FIELD_MAP.values()) + ["官网LinkedIn验证状态"]
    for field in verify_fields:
        if field not in df_merged.columns:
            df_merged[field] = ""
        df_merged[field] = df_merged[field].fillna("")
        # 未验证的标记
        if field == "官网LinkedIn验证状态":
            df_merged[field] = df_merged[field].replace("", "未验证")

    print(f"  合并后: {len(df_merged)} 条")

    # 4. 计算新增字段
    print("\n[4/6] 计算新增字段...")

    # 官网LinkedIn加分
    df_merged["官网LinkedIn加分"] = df_merged.apply(calculate_web_linkedin_bonus, axis=1)

    # 最终业务参考分
    df_merged["最终业务参考分"] = df_merged.apply(calculate_final_business_score, axis=1)

    # 业务参考层级
    df_merged["业务参考层级"] = df_merged.apply(determine_business_level, axis=1)

    # 风险提示
    df_merged["风险提示"] = df_merged.apply(generate_risk_note, axis=1)

    # 推荐动作
    df_merged["推荐动作"] = df_merged.apply(generate_recommendation, axis=1)

    # 5. 统计各层级数量
    print("\n[5/6] 统计各层级数量...")
    level_counts = df_merged["业务参考层级"].value_counts()
    print(f"  强推荐: {level_counts.get('强推荐', 0)}")
    print(f"  优先参考: {level_counts.get('优先参考', 0)}")
    print(f"  可跟进观察: {level_counts.get('可跟进观察', 0)}")
    print(f"  谨慎参考: {level_counts.get('谨慎参考', 0)}")
    print(f"  不建议优先: {level_counts.get('不建议优先', 0)}")

    verified_count = len(df_merged[df_merged["官网LinkedIn验证状态"] == "已验证"])
    print(f"  已做官网/LinkedIn验证: {verified_count}")

    # 公开信息统计
    pub_count = df_merged["公开信息查询状态"].notna().sum()
    pub_high = (df_merged["公开信息置信度"] == "高").sum()
    pub_mid = (df_merged["公开信息置信度"] == "中").sum()
    pub_web = df_merged["公开信息-官网URL"].notna().sum()
    pub_li = df_merged["公开信息-LinkedIn URL"].notna().sum()
    print(f"\n【公开信息统计】")
    print(f"  已查公开信息: {pub_count}")
    print(f"  找到官网: {pub_web}  找到LinkedIn: {pub_li}")
    print(f"  置信度: 高={pub_high} 中={pub_mid}")

    # 6. 导出 Excel
    print("\n[6/6] 导出 Excel...")

    # 定义业务 sheet 要保留的字段
    business_fields = [
        "业务参考层级",
        "客户名标准化",
        "客户名候选",
        "国家/地区",
        "联系人邮箱",
        "客户邮箱域名",
        "产品关键词汇总",
        "体量描述汇总",
        "历史邮件数",
        "有效询价次数",
        "订单相关次数",
        "最高体量等级",
        "最高购买意向",
        "邮件线索评分",
        "腾道排查状态",
        "腾道评分",
        "最终总分",
        "官网LinkedIn加分",
        "最终业务参考分",
        "最终优先级",
        "推荐动作",
        "风险提示",
        "腾道匹配客户名",
        "腾道匹配国家",
        "近一年进口次数",
        "最近进口日期",
        "主要进口产品",
        "官网 URL",
        "LinkedIn 公司主页",
        "官网LinkedIn备注",
        "公开信息-官网URL",
        "公开信息-官网简介",
        "公开信息-LinkedIn URL",
        "公开信息-综合简介",
        "公开信息-主营业务",
        "公开信息-产品服务",
        "公开信息-公司规模",
        "公开信息置信度",
        "公开信息来源",
    ]

    # 过滤存在的字段
    business_fields_exist = [f for f in business_fields if f in df_merged.columns]

    # 创建各层级 DataFrame
    df_strong = df_merged[df_merged["业务参考层级"] == "强推荐"][business_fields_exist].copy()
    df_priority = df_merged[df_merged["业务参考层级"] == "优先参考"][business_fields_exist].copy()
    df_follow = df_merged[df_merged["业务参考层级"] == "可跟进观察"][business_fields_exist].copy()
    df_caution = df_merged[df_merged["业务参考层级"] == "谨慎参考"][business_fields_exist].copy()
    df_not_recommend = df_merged[df_merged["业务参考层级"] == "不建议优先"][business_fields_exist].copy()

    # 排序
    sort_cols = ["最终业务参考分", "最终总分", "邮件线索评分"]
    sort_col = None
    for col in sort_cols:
        if col in df_strong.columns:
            sort_col = col
            break

    if sort_col:
        df_strong = df_strong.sort_values(sort_col, ascending=False)
        df_priority = df_priority.sort_values(sort_col, ascending=False)
        df_follow = df_follow.sort_values(sort_col, ascending=False)
        df_caution = df_caution.sort_values(sort_col, ascending=False)
        df_not_recommend = df_not_recommend.sort_values(sort_col, ascending=False)

    # 官网LinkedIn验证明细
    verify_detail_fields = [
        "客户聚合 Key",
        "客户名标准化",
        "国家/地区",
        "官网 URL",
        "官网公司名",
        "官网国家",
        "官网产品",
        "官网匹配状态",
        "LinkedIn 公司主页",
        "LinkedIn 公司名",
        "LinkedIn 国家",
        "LinkedIn 行业",
        "LinkedIn 员工规模",
        "LinkedIn 匹配状态",
        "官网LinkedIn置信度",
        "官网LinkedIn加分",
        "官网LinkedIn备注",
        "业务参考层级",
    ]
    verify_detail_fields_exist = [f for f in verify_detail_fields if f in df_merged.columns]
    df_verify_detail = df_merged[df_merged["官网LinkedIn验证状态"] == "已验证"][verify_detail_fields_exist].copy()
    if sort_col and sort_col in df_verify_detail.columns:
        df_verify_detail = df_verify_detail.sort_values(sort_col, ascending=False)

    # 业务总览
    overview_fields = [
        "业务参考层级",
        "客户名标准化",
        "国家/地区",
        "邮件线索评分",
        "腾道评分",
        "最终总分",
        "官网LinkedIn加分",
        "最终业务参考分",
        "最终优先级",
        "腾道排查状态",
        "官网匹配状态",
        "LinkedIn 匹配状态",
        "风险提示",
        "推荐动作",
    ]
    overview_fields_exist = [f for f in overview_fields if f in df_merged.columns]
    df_overview = df_merged[overview_fields_exist].copy()
    if sort_col and sort_col in df_overview.columns:
        df_overview = df_overview.sort_values(sort_col, ascending=False)

    # 公开信息验证明细
    pub_detail_fields = [
        "客户聚合 Key",
        "客户名标准化",
        "国家/地区",
        "公开信息-官网URL",
        "公开信息-官网简介",
        "公开信息-官网产品",
        "公开信息-LinkedIn URL",
        "公开信息-员工规模",
        "公开信息-综合简介",
        "公开信息-主营业务",
        "公开信息-产品服务",
        "公开信息-公司规模",
        "公开信息置信度",
        "公开信息查询状态",
        "公开信息来源",
        "公开信息备注",
        "业务参考层级",
    ]
    pub_detail_fields_exist = [f for f in pub_detail_fields if f in df_merged.columns]
    df_pub_detail = df_merged[df_merged["公开信息查询状态"].notna() & (df_merged["公开信息查询状态"] != "")][pub_detail_fields_exist].copy()
    if sort_col and sort_col in df_pub_detail.columns:
        df_pub_detail = df_pub_detail.sort_values(sort_col, ascending=False)

    # 写入 Excel
    with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl') as writer:
        df_overview.to_excel(writer, sheet_name="业务总览", index=False)
        df_strong.to_excel(writer, sheet_name="强推荐客户", index=False)
        df_priority.to_excel(writer, sheet_name="优先参考客户", index=False)
        df_follow.to_excel(writer, sheet_name="可跟进观察", index=False)
        df_caution.to_excel(writer, sheet_name="谨慎参考客户", index=False)
        df_not_recommend.to_excel(writer, sheet_name="不建议优先", index=False)
        df_verify_detail.to_excel(writer, sheet_name="官网LinkedIn验证明细", index=False)
        df_pub_detail.to_excel(writer, sheet_name="公开信息明细", index=False)
        df_merged.to_excel(writer, sheet_name="全量客户候选池", index=False)

    print(f"  输出文件: {OUTPUT_FILE}")

    # 打印汇总
    print("\n" + "=" * 70)
    print("汇总报告")
    print("=" * 70)

    print(f"\n【基本统计】")
    print(f"  总客户数: {len(df_merged)}")
    print(f"  已做官网/LinkedIn验证客户数: {verified_count}")

    print(f"\n【层级分布】")
    print(f"  强推荐客户: {len(df_strong)}")
    print(f"  优先参考客户: {len(df_priority)}")
    print(f"  可跟进观察: {len(df_follow)}")
    print(f"  谨慎参考客户: {len(df_caution)}")
    print(f"  不建议优先: {len(df_not_recommend)}")

    # Top 30 强推荐/优先参考客户
    print(f"\n【Top 30 强推荐/优先参考客户】")
    top_customers = pd.concat([df_strong.head(30), df_priority.head(30)]).head(30)
    if len(top_customers) > 0:
        for i, (_, row) in enumerate(top_customers.iterrows(), 1):
            name = row.get("客户名标准化", "")[:30]
            country = row.get("国家/地区", "")
            score = row.get("最终业务参考分", 0)
            level = row.get("业务参考层级", "")
            print(f"  {i:2}. {name:<30} | {country:<10} | {score:>5.0f}分 | {level}")
    else:
        print("  （无符合条件的客户）")

    print(f"\n【输出文件】")
    print(f"  {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
