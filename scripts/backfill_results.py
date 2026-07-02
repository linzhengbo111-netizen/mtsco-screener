"""
backfill_results.py — 将腾道抓取结果转换为回填格式

功能：
1. 读取腾道抓取结果
2. 按规则计算 tendata_check_status 和 tendata_score
3. 生成回填格式文件（用于更新客户候选池）

用法：
    python scripts/backfill_results.py --input output/tendata_result_A_10.xlsx
    python scripts/backfill_results.py --input output/tendata_result_B_50.xlsx
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# 确保控制台 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ============================================================================
# 配置常量
# ============================================================================

# 内部公司关键词（需要排除）
INTERNAL_COMPANY_KEYWORDS = [
    "jiaxing mt",
    "mt stainless",
    "mtsco",
    "maytun",
    "mt flange",
    "zhejiang seamless",
]

# 回填输出字段
BACKFILL_COLUMNS = [
    "internal_customer_id",
    "customer_name",
    "matched_company_name",
    "matched_country",
    "match_status",
    "match_confidence",
    "last_12m_import_count",
    "latest_import_date",
    "top_import_products",
    "estimated_trade_volume_level",
    "import_frequency_level",
    "business_summary",
    "manual_review_flag",
    "manual_review_reason",
    "tendata_check_status",
    "tendata_score",
    "tendata_note",
]


# ============================================================================
# 判断函数
# ============================================================================

def is_internal_company(name: str) -> bool:
    """判断是否为内部公司（我方公司）。"""
    if not name:
        return False
    name_lower = name.lower()
    for keyword in INTERNAL_COMPANY_KEYWORDS:
        if keyword in name_lower:
            return True
    return False


def calculate_tendata_score(
    match_status: str,
    match_confidence: int,
    import_active: str,
    latest_import_date: str,
    is_internal: bool,
) -> int:
    """计算腾道评分。

    评分规则（B50 更新版）：
    - 内部公司：0 分
    - confirmed + confidence >= 80：基础 60 分 + 进口活跃加分
    - confirmed + confidence < 80：基础 40 分 + 进口活跃加分
    - likely_match：最高 15 分
    - conflict：最高 5 分
    - 其他：0 分
    """
    if is_internal:
        return 0

    # 确认匹配
    if match_status == "confirmed":
        if match_confidence >= 80:
            score = 60
        else:
            score = 40

        # 进口活跃加分
        if import_active == "active":
            score += 20
        elif import_active == "inactive":
            score += 5

        # 最近进口日期加分
        if latest_import_date:
            try:
                from datetime import datetime
                if isinstance(latest_import_date, str):
                    import_date = datetime.strptime(latest_import_date[:10], "%Y-%m-%d")
                    days_ago = (datetime.now() - import_date).days
                    if days_ago <= 365:
                        score += 15
                    elif days_ago <= 730:
                        score += 8
            except:
                pass
        return min(score, 100)

    # 可能匹配 - 最高 15 分
    if match_status == "likely_match":
        score = min(15, match_confidence // 5)
        return score

    # 冲突 - 最高 5 分
    if match_status == "conflict":
        score = min(5, match_confidence // 10)
        return score

    # 其他状态 - 0 分
    return 0


def determine_check_status(
    match_status: str,
    match_confidence: int,
    is_internal: bool,
    customer_name: str,
    matched_name: str,
) -> str:
    """确定腾道排查状态。

    规则（B50 更新版）：
    - 内部公司 → 排除-内部公司
    - confirmed → 已查-确认匹配
    - likely_match → 待人工复核
    - conflict → 待人工复核
    - candidate_found_not_entered → 已查-未确认
    - no_result → 已查-未找到
    - detail_page_failed → 排查失败-可重试
    """
    if is_internal:
        return "排除-内部公司"

    if match_status == "confirmed":
        return "已查-确认匹配"

    if match_status == "likely_match":
        return "待人工复核"

    if match_status == "conflict":
        return "待人工复核"

    if match_status == "candidate_found_not_entered":
        return "已查-未确认"

    if match_status == "no_result":
        return "已查-未找到"

    if match_status == "detail_page_failed":
        return "排查失败-可重试"

    if match_status in ["unconfirmed"]:
        return "已查-未确认"

    return "已查-未确认"


def determine_manual_review_flag(
    match_status: str,
    is_internal: bool,
) -> str:
    """确定是否需要人工复核标记。"""
    if is_internal:
        return "否"

    # 这些状态需要人工复核
    review_statuses = ["likely_match", "conflict", "candidate_found_not_entered", "detail_page_failed"]
    if match_status in review_statuses:
        return "是"

    return "否"


def generate_tendata_note(
    match_status: str,
    match_confidence: int,
    is_internal: bool,
    business_summary: str,
    conflict_reason: str,
) -> str:
    """生成腾道备注。

    规则（B50 更新版）：
    - 内部公司：排除：为我方内部公司
    - confirmed：匹配置信度 + 业务摘要
    - likely_match：可能匹配，需人工确认
    - conflict：存在冲突 + 具体原因
    - candidate_found_not_entered：候选未进入详情页，暂不采纳腾道结果
    - no_result：腾道未找到匹配公司
    - detail_page_failed：详情页进入失败，建议后续单独重试
    """
    if is_internal:
        return "排除：为我方内部公司"

    if match_status == "confirmed":
        note = f"匹配置信度 {match_confidence}%"
        if business_summary:
            note += f"；{business_summary[:80]}"
        return note

    if match_status == "likely_match":
        note = f"可能匹配（置信度 {match_confidence}%），需人工确认"
        if business_summary:
            note += f"；{business_summary[:50]}"
        return note

    if match_status == "conflict":
        note = "存在冲突需人工复核"
        if conflict_reason:
            # 简化冲突原因
            reasons = []
            if "国家" in conflict_reason or "country" in conflict_reason.lower():
                reasons.append("国家不一致")
            if "官网" in conflict_reason or "website" in conflict_reason.lower():
                reasons.append("官网不匹配")
            if "域名" in conflict_reason or "domain" in conflict_reason.lower():
                reasons.append("邮箱域名不匹配")
            if "公司名" in conflict_reason or "name" in conflict_reason.lower():
                reasons.append("公司名不完全匹配")
            if reasons:
                note += f"：{', '.join(reasons)}"
            else:
                # 提取关键信息
                note += f"：{conflict_reason[:50]}"
        return note

    if match_status == "candidate_found_not_entered":
        return "候选未进入详情页，暂不采纳腾道结果"

    if match_status == "no_result":
        return "腾道未找到匹配公司"

    if match_status == "detail_page_failed":
        return "详情页进入失败，建议后续单独重试"

    return f"状态: {match_status}"


# ============================================================================
# 主处理函数
# ============================================================================

def process_backfill(input_path: str, output_path: str) -> dict:
    """处理回填文件生成。"""
    print(f"[1/3] 读取抓取结果: {input_path}")
    df = pd.read_excel(input_path)
    print(f"  总行数: {len(df)}")

    # 检查必需字段
    required = ["internal_customer_id", "customer_name", "match_status", "match_confidence"]
    missing = [f for f in required if f not in df.columns]
    if missing:
        raise ValueError(f"缺失必需字段: {missing}")

    print(f"[2/3] 计算回填字段...")

    # 准备输出数据
    results = []
    stats = {
        "total": len(df),
        "confirmed": 0,
        "likely_match": 0,
        "conflict": 0,
        "unconfirmed": 0,
        "no_result": 0,
        "internal": 0,
        "detail_failed": 0,
    }

    for i, row in df.iterrows():
        customer_name = str(row.get("customer_name", "")).strip()
        matched_name = str(row.get("matched_company_name", "")).strip()
        match_status = str(row.get("match_status", ""))
        match_confidence = int(row.get("match_confidence", 0))

        # 判断是否内部公司
        is_internal = is_internal_company(customer_name) or is_internal_company(matched_name)

        # 计算字段
        check_status = determine_check_status(
            match_status, match_confidence, is_internal, customer_name, matched_name
        )
        score = calculate_tendata_score(
            match_status,
            match_confidence,
            str(row.get("import_active_status", "")),
            str(row.get("latest_import_date", "")),
            is_internal,
        )
        note = generate_tendata_note(
            match_status,
            match_confidence,
            is_internal,
            str(row.get("business_summary", "")),
            str(row.get("conflict_reason", "")),
        )
        manual_review_flag = determine_manual_review_flag(match_status, is_internal)

        # 统计
        if is_internal:
            stats["internal"] += 1
        elif check_status == "已查-确认匹配":
            stats["confirmed"] += 1
        elif match_status == "likely_match":
            stats["likely_match"] += 1
        elif check_status == "待人工复核":
            stats["conflict"] += 1
        elif check_status == "已查-未找到":
            stats["no_result"] += 1
        elif check_status == "排查失败-可重试":
            stats["detail_failed"] += 1
        else:
            stats["unconfirmed"] += 1

        # 构建输出行
        output_row = {
            "internal_customer_id": row.get("internal_customer_id", ""),
            "customer_name": customer_name,
            "matched_company_name": matched_name,
            "matched_country": row.get("matched_country", ""),
            "match_status": match_status,
            "match_confidence": match_confidence,
            "last_12m_import_count": row.get("last_12m_import_count", ""),
            "latest_import_date": row.get("latest_import_date", ""),
            "top_import_products": row.get("top_import_products", ""),
            "estimated_trade_volume_level": row.get("estimated_trade_volume_level", ""),
            "import_frequency_level": row.get("import_frequency_level", ""),
            "business_summary": row.get("business_summary", ""),
            "manual_review_flag": manual_review_flag,
            "manual_review_reason": row.get("manual_review_reason", "") if manual_review_flag == "是" else "",
            "tendata_check_status": check_status,
            "tendata_score": score,
            "tendata_note": note,
        }
        results.append(output_row)

    print(f"[3/3] 导出回填文件: {output_path}")
    output_df = pd.DataFrame(results, columns=BACKFILL_COLUMNS)

    # 确保输出目录存在
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(output_path, index=False)

    return stats


def print_summary(stats: dict, output_path: str) -> None:
    """打印统计汇总。"""
    print("\n" + "=" * 60)
    print("回填文件生成完成")
    print("=" * 60)
    print(f"\n【统计信息】")
    print(f"  总处理数量: {stats['total']}")
    print(f"  ✅ 已查-确认匹配: {stats['confirmed']}")
    print(f"  🔸 可能匹配 (likely_match): {stats['likely_match']}")
    print(f"  ⚠️ 待人工复核 (conflict): {stats['conflict']}")
    print(f"  🔍 已查-未确认: {stats['unconfirmed']}")
    print(f"  ❌ 已查-未找到: {stats['no_result']}")
    print(f"  🔧 排查失败-可重试: {stats['detail_failed']}")
    print(f"  🏠 排除-内部公司: {stats['internal']}")
    print(f"\n【输出文件】")
    print(f"  {output_path}")


def preview_results(output_path: str, n: int = 5) -> None:
    """预览回填结果。"""
    df = pd.read_excel(output_path)
    print(f"\n【回填结果预览 (前 {n} 条)】")
    print("-" * 80)

    for i, row in df.head(n).iterrows():
        print(f"\n{i+1}. {row['customer_name'][:40]}")
        print(f"   匹配: {row['matched_company_name'][:40] if row['matched_company_name'] else '(无)'}")
        print(f"   状态: {row['tendata_check_status']}")
        print(f"   评分: {row['tendata_score']}")
        print(f"   复核: {row['manual_review_flag']}")
        print(f"   备注: {row['tendata_note'][:60] if row['tendata_note'] else ''}")


def print_score_distribution(output_path: str) -> None:
    """打印评分分布。"""
    df = pd.read_excel(output_path)
    print(f"\n【评分分布】")
    score_counts = df['tendata_score'].value_counts().sort_index(ascending=False)
    for score, count in score_counts.items():
        print(f"  {score} 分: {count} 条")


def print_status_distribution(output_path: str) -> None:
    """打印状态分布。"""
    df = pd.read_excel(output_path)
    print(f"\n【tendata_check_status 分布】")
    status_counts = df['tendata_check_status'].value_counts()
    for status, count in status_counts.items():
        print(f"  {status}: {count} 条")


def main():
    parser = argparse.ArgumentParser(description="生成腾道回填结果文件")
    parser.add_argument(
        "--input",
        default="output/tendata_result_A_10.xlsx",
        help="腾道抓取结果文件路径",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="回填文件输出路径 (默认: output/tendata_backfill_*.xlsx)",
    )
    args = parser.parse_args()

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        # 从输入文件名推断
        input_name = Path(args.input).stem
        output_path = f"output/tendata_backfill_{input_name.replace('tendata_result_', '')}.xlsx"

    print("=" * 60)
    print("腾道回填文件生成器")
    print("=" * 60)

    # 执行处理
    stats = process_backfill(args.input, output_path)

    # 打印汇总
    print_summary(stats, output_path)

    # 打印状态分布
    print_status_distribution(output_path)

    # 打印评分分布
    print_score_distribution(output_path)

    # 预览结果
    preview_results(output_path, n=10)

    # 检查 internal_customer_id
    df = pd.read_excel(output_path)
    id_non_empty = df['internal_customer_id'].notna() & (df['internal_customer_id'] != "")
    print(f"\n【字段完整性检查】")
    print(f"  internal_customer_id 非空: {id_non_empty.sum()}/{len(df)}")

    print("\n" + "=" * 60)
    print("[提示] 回填文件已生成，可用于更新客户候选池")
    print("=" * 60)


if __name__ == "__main__":
    main()
