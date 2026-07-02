"""
generate_feishu_import.py — 生成飞书导入版业务参考客户清单

从合并后的业务参考清单中，提取业务核心字段，生成适合导入飞书多维表格的 Excel。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

INPUT_FILE = "output/业务参考客户清单_AB_含公开信息验证_完整版.xlsx"
OUTPUT_FILE = "output/飞书导入_业务参考客户清单_AB_公开信息增强版.xlsx"

# 保留的原始 sheet
KEEP_SHEETS = [
    "业务总览",
    "强推荐客户",
    "优先参考客户",
    "可跟进观察",
    "谨慎参考客户",
    "全量客户候选池",
]

# 层级排序权重
LEVEL_ORDER = {
    "强推荐": 1,
    "优先参考": 2,
    "可跟进观察": 3,
    "谨慎参考": 4,
    "不建议优先": 5,
}


def infer_stainless_relevance(row: pd.Series) -> str:
    """从产品关键词、进口产品等推断是否不锈钢相关。"""
    keywords_fields = [
        str(row.get("产品关键词汇总", "")),
        str(row.get("主要进口产品", "")),
        str(row.get("公开信息-产品服务", "")),
        str(row.get("公开信息-主营业务", "")),
        str(row.get("公开信息-官网简介", "")),
    ]
    text = " ".join(keywords_fields).lower()
    terms = [
        "stainless", "不锈钢", "inox", "ss ", "ss304", "ss316",
        "steel pipe", "steel tube", "steel fitting", "steel flange",
        "steel plate", "steel coil", "steel sheet", "steel bar",
        "alloy steel", "carbon steel", "special steel",
        "金属", "钢材", "钢管", "管件", "阀门", "法兰",
    ]
    matched = [t for t in terms if t in text]
    if matched:
        return "是"
    return "待确认"


def infer_product_match(row: pd.Series) -> str:
    """推断与我司产品匹配度（基于产品关键词和进口产品）。"""
    keywords = str(row.get("产品关键词汇总", ""))
    imports = str(row.get("主要进口产品", ""))
    pub_products = str(row.get("公开信息-产品服务", ""))
    pub_biz = str(row.get("公开信息-主营业务", ""))

    text = f"{keywords} {imports} {pub_products} {pub_biz}".lower()
    if not text.strip() or text.strip() in ["nan nan nan nan", "nan", ""]:
        return "待确认"

    # 产品相关关键词
    product_terms = [
        "rubber", "橡胶", "o-ring", "oring", "gasket", "密封",
        "pipe", "tube", "fitting", "flange", "valve",
        "管", "管件", "阀门", "法兰",
        "steel", "钢", "metal", "金属",
        "industrial", "industrial supply",
    ]
    matched = [t for t in product_terms if t in text]
    if len(matched) >= 3:
        return "高"
    elif len(matched) >= 1:
        return "中"
    return "低"


def infer_purchase_activity(row: pd.Series) -> str:
    """推断采购活跃度。"""
    def safe_int(val, default=0):
        try:
            if pd.isna(val):
                return default
            return int(val)
        except (ValueError, TypeError):
            return default

    mail_count = safe_int(row.get("历史邮件数", 0))
    inquiry_count = safe_int(row.get("有效询价次数", 0))
    order_count = safe_int(row.get("订单相关次数", 0))
    import_count = safe_int(row.get("近一年进口次数", 0))

    score = mail_count * 0.5 + inquiry_count * 2 + order_count * 3 + import_count * 1.5
    if score >= 20:
        return "高"
    elif score >= 5:
        return "中"
    elif score > 0:
        return "低"
    return "无记录"


def build_recommendation_reason(row: pd.Series) -> str:
    """构建推荐理由。"""
    level = str(row.get("业务参考层级", ""))
    tendata_status = str(row.get("腾道排查状态", ""))
    pub_conf = str(row.get("公开信息置信度", ""))
    pub_sources = str(row.get("public_info_sources", ""))
    score = row.get("最终业务参考分", 0)

    parts = []
    if tendata_status == "已查-确认匹配":
        parts.append("腾道确认匹配")
    elif tendata_status == "已查-可能匹配":
        parts.append("腾道可能匹配")

    if pub_conf == "高":
        parts.append(f"公开信息高置信度")
    elif pub_conf == "中":
        parts.append(f"公开信息中等置信度")

    if pub_sources and pub_sources != "nan":
        parts.append(f"来源: {pub_sources}")

    parts.append(f"参考分 {score:.0f}")
    return "；".join(parts) if parts else ""


def main():
    print("=" * 60)
    print("生成飞书导入版业务参考客户清单")
    print("=" * 60)

    # 1. 读取全量数据
    print(f"\n[1/4] 读取: {INPUT_FILE}")
    df_all = pd.read_excel(INPUT_FILE, sheet_name="全量客户候选池")
    print(f"  全量客户: {len(df_all)} 条")

    # 2. 构建飞书导入 sheet
    print("\n[2/4] 构建飞书导入 sheet...")

    # 合并官网 URL：优先用验证过的，退回公开信息的
    def merge_url(row, primary_col, fallback_col):
        v = row.get(primary_col, "")
        if pd.notna(v) and str(v).strip() and str(v).strip() != "nan":
            return str(v).strip()
        v2 = row.get(fallback_col, "")
        if pd.notna(v2) and str(v2).strip() and str(v2).strip() != "nan":
            return str(v2).strip()
        return ""

    feishu_data = {
        "业务参考层级": df_all["业务参考层级"],
        "客户名标准化": df_all["客户名标准化"],
        "公司简介": df_all.apply(
            lambda r: str(r.get("公开信息-综合简介", "")) if pd.notna(r.get("公开信息-综合简介", "")) and str(r.get("公开信息-综合简介", "")).strip() != "nan"
            else str(r.get("公开信息-官网简介", "")) if pd.notna(r.get("公开信息-官网简介", "")) and str(r.get("公开信息-官网简介", "")).strip() != "nan"
            else "", axis=1),
        "主营业务": df_all.apply(
            lambda r: str(r.get("公开信息-主营业务", "")) if pd.notna(r.get("公开信息-主营业务", "")) and str(r.get("公开信息-主营业务", "")).strip() != "nan" else "", axis=1),
        "国家/地区": df_all["国家/地区"],
        "联系人邮箱": df_all["联系人邮箱"],
        "客户邮箱域名": df_all["客户邮箱域名"],
        "官网 URL": df_all.apply(lambda r: merge_url(r, "官网 URL", "公开信息-官网URL"), axis=1),
        "LinkedIn URL": df_all.apply(lambda r: merge_url(r, "LinkedIn 公司主页", "公开信息-LinkedIn URL"), axis=1),
        "是否不锈钢相关": df_all.apply(infer_stainless_relevance, axis=1),
        "与我司产品匹配度": df_all.apply(infer_product_match, axis=1),
        "客户规模判断": df_all.apply(
            lambda r: str(r.get("公开信息-公司规模", "")) if pd.notna(r.get("公开信息-公司规模", "")) and str(r.get("公开信息-公司规模", "")).strip() not in ["nan", ""]
            else str(r.get("最高体量等级", "")) if pd.notna(r.get("最高体量等级", "")) and str(r.get("最高体量等级", "")).strip() not in ["nan", ""]
            else "", axis=1),
        "采购活跃度": df_all.apply(infer_purchase_activity, axis=1),
        "历史询价产品": df_all.apply(
            lambda r: str(r.get("产品关键词汇总", "")) if pd.notna(r.get("产品关键词汇总", "")) and str(r.get("产品关键词汇总", "")).strip() != "nan" else "", axis=1),
        "腾道主要进口产品": df_all.apply(
            lambda r: str(r.get("主要进口产品", "")) if pd.notna(r.get("主要进口产品", "")) and str(r.get("主要进口产品", "")).strip() != "nan" else "", axis=1),
        "历史邮件数": df_all["历史邮件数"],
        "有效询价次数": df_all["有效询价次数"],
        "订单相关次数": df_all["订单相关次数"],
        "首次询价时间": df_all["首次询价时间"],
        "最近询价时间": df_all["最近询价时间"],
        "邮件线索评分": df_all["邮件线索评分"],
        "腾道排查状态": df_all["腾道排查状态"],
        "腾道评分": df_all["腾道评分"],
        "公开信息加分": df_all["官网LinkedIn加分"],
        "最终业务参考分": df_all["最终业务参考分"],
        "最终优先级": df_all["最终优先级"],
        "推荐理由": df_all.apply(build_recommendation_reason, axis=1),
        "风险提示": df_all["风险提示"],
        "跟进建议": df_all.apply(
            lambda r: str(r.get("推荐动作", "")) if pd.notna(r.get("推荐动作", "")) and str(r.get("推荐动作", "")).strip() != "nan" else "", axis=1),
        "近一年进口次数": df_all["近一年进口次数"],
        "最近进口日期": df_all["最近进口日期"],
        "public_info_sources": df_all.apply(
            lambda r: str(r.get("公开信息来源", "")) if pd.notna(r.get("公开信息来源", "")) and str(r.get("公开信息来源", "")).strip() != "nan" else "", axis=1),
        "public_info_confidence": df_all.apply(
            lambda r: str(r.get("公开信息置信度", "")) if pd.notna(r.get("公开信息置信度", "")) and str(r.get("公开信息置信度", "")).strip() != "nan" else "", axis=1),
        "public_info_note": df_all.apply(
            lambda r: str(r.get("公开信息备注", "")) if pd.notna(r.get("公开信息备注", "")) and str(r.get("公开信息备注", "")).strip() != "nan" else "", axis=1),
    }

    df_feishu = pd.DataFrame(feishu_data)
    print(f"  飞书导入 sheet: {len(df_feishu)} 行, {len(df_feishu.columns)} 列")

    # 3. 排序
    print("\n[3/4] 排序...")
    df_feishu["_level_order"] = df_feishu["业务参考层级"].map(LEVEL_ORDER).fillna(99)

    def safe_num(val):
        try:
            if pd.isna(val):
                return 0
            return float(val)
        except (ValueError, TypeError):
            return 0

    df_feishu["_biz_score"] = df_feishu["最终业务参考分"].apply(safe_num)
    df_feishu["_tendata_score"] = df_feishu["腾道评分"].apply(safe_num)
    df_feishu["_mail_score"] = df_feishu["邮件线索评分"].apply(safe_num)

    df_feishu = df_feishu.sort_values(
        by=["_level_order", "_biz_score", "_tendata_score", "_mail_score"],
        ascending=[True, False, False, False]
    )

    # 删除排序辅助列
    df_feishu = df_feishu.drop(columns=["_level_order", "_biz_score", "_tendata_score", "_mail_score"])
    print(f"  排序完成")

    # 4. 导出
    print(f"\n[4/4] 导出: {OUTPUT_FILE}")
    Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl') as writer:
        # 飞书导入 sheet 放第一个
        df_feishu.to_excel(writer, sheet_name="飞书导入_业务参考客户", index=False)

        # 保留的原始 sheet
        for sheet_name in KEEP_SHEETS:
            try:
                df_sheet = pd.read_excel(INPUT_FILE, sheet_name=sheet_name)
                df_sheet.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  ✓ {sheet_name}: {len(df_sheet)} 行")
            except Exception as e:
                print(f"  ✗ {sheet_name}: 跳过 ({e})")

    # 统计
    print(f"\n{'=' * 60}")
    print("汇总")
    print(f"{'=' * 60}")
    print(f"\n输出文件: {OUTPUT_FILE}")
    print(f"飞书导入 sheet 行数: {len(df_feishu)}")
    print(f"\n层级分布:")
    for level in ["强推荐", "优先参考", "可跟进观察", "谨慎参考", "不建议优先"]:
        count = (df_feishu["业务参考层级"] == level).sum()
        print(f"  {level}: {count}")

    # 快速质量检查
    print(f"\n字段覆盖:")
    web_filled = (df_feishu["官网 URL"] != "").sum()
    li_filled = (df_feishu["LinkedIn URL"] != "").sum()
    summary_filled = (df_feishu["公司简介"] != "").sum()
    print(f"  有官网: {web_filled}/{len(df_feishu)}")
    print(f"  有LinkedIn: {li_filled}/{len(df_feishu)}")
    print(f"  有公司简介: {summary_filled}/{len(df_feishu)}")


if __name__ == "__main__":
    main()
