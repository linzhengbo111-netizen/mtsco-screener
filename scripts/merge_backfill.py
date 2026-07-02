"""
merge_backfill.py — 将腾道回填结果合并回客户候选池

功能：
1. 读取客户候选池原始文件
2. 读取腾道回填结果
3. 按 internal_customer_id / 客户聚合 Key 匹配并回填
4. 重新计算最终总分和最终优先级
5. 生成新文件，不修改原始文件

用法：
    python scripts/merge_backfill.py --source 客户候选池_正式版.xlsx --backfill output/tendata_backfill_A_10.xlsx
"""

from __future__ import annotations

import argparse
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

# 回填字段映射（回填文件字段 → 客户候选池字段）
BACKFILL_FIELD_MAP = {
    "tendata_check_status": "腾道排查状态",
    "tendata_score": "腾道评分",
    "tendata_note": "腾道备注",
    "matched_company_name": "腾道匹配客户名",
    "matched_country": "腾道匹配国家",
    "match_status": "腾道匹配状态",
    "match_confidence": "腾道匹配置信度",
    "last_12m_import_count": "近一年进口次数",
    "latest_import_date": "最近进口日期",
    "top_import_products": "主要进口产品",
    "import_frequency_level": "进口频率等级",
    "estimated_trade_volume_level": "采购体量等级",
    "manual_review_flag": "人工复核标记",
    "manual_review_reason": "人工复核原因",
}


# ============================================================================
# 计算函数
# ============================================================================

def calculate_final_priority(total_score: int, tendata_status: str) -> str:
    """计算最终优先级。

    规则：
    - 排除-内部公司 → D
    - >= 100 → A+
    - >= 80 → A
    - >= 60 → B
    - >= 40 → C
    - < 40 → D
    """
    if tendata_status == "排除-内部公司":
        return "D"

    if total_score >= 100:
        return "A+"
    elif total_score >= 80:
        return "A"
    elif total_score >= 60:
        return "B"
    elif total_score >= 40:
        return "C"
    else:
        return "D"


def calculate_recommended_action(
    tendata_status: str,
    current_action: str,
    final_priority: str,
) -> str:
    """计算推荐动作。

    规则：
    - 排除-内部公司 → 排除-内部公司
    - 待人工复核 → 待人工复核后再决定
    - 已查-未确认 → 暂不采纳腾道结果
    - 其他 → 保持原推荐动作
    """
    if tendata_status == "排除-内部公司":
        return "排除-内部公司"
    elif tendata_status == "待人工复核":
        return "待人工复核后再决定"
    elif tendata_status == "已查-未确认":
        return "暂不采纳腾道结果"
    else:
        return current_action


# ============================================================================
# 主处理函数
# ============================================================================

def merge_backfill(
    source_path: str,
    backfill_path: str,
    output_path: str,
) -> dict:
    """执行合并回填。"""
    print(f"[1/4] 读取客户候选池: {source_path}")
    df_source = pd.read_excel(source_path, sheet_name="客户候选池_正式版")
    total_source = len(df_source)
    print(f"  总行数: {total_source}")
    print(f"  总列数: {len(df_source.columns)}")

    print(f"\n[2/4] 读取腾道回填文件: {backfill_path}")
    df_backfill = pd.read_excel(backfill_path)
    total_backfill = len(df_backfill)
    print(f"  回填行数: {total_backfill}")

    # 确保回填文件有 internal_customer_id
    if "internal_customer_id" not in df_backfill.columns:
        raise ValueError("回填文件缺少 internal_customer_id 字段")

    # 确保客户候选池有 客户聚合 Key
    if "客户聚合 Key" not in df_source.columns:
        raise ValueError("客户候选池缺少 客户聚合 Key 字段")

    print(f"\n[3/4] 合并回填...")

    # 创建回填字典（以 internal_customer_id 为 key）
    backfill_dict = {}
    for _, row in df_backfill.iterrows():
        key = str(row["internal_customer_id"]).strip()
        backfill_dict[key] = row.to_dict()

    # 检查客户候选池是否已有腾道相关列，如果没有则添加
    # 先收集所有需要新增的列
    new_columns = {}
    for src_field, dst_field in BACKFILL_FIELD_MAP.items():
        if dst_field not in df_source.columns:
            # 根据字段类型确定默认值
            if dst_field in ["腾道匹配置信度", "腾道评分", "近一年进口次数"]:
                new_columns[dst_field] = 0
            else:
                new_columns[dst_field] = ""
            print(f"  新增列: {dst_field}")

    # 添加新列
    for col, default_val in new_columns.items():
        df_source[col] = default_val

    # 准备回填数据
    backfill_updates = {col: {} for col in BACKFILL_FIELD_MAP.values()}

    # 执行回填
    matched_count = 0
    for idx, row in df_source.iterrows():
        key = str(row.get("客户聚合 Key", "")).strip()
        if key in backfill_dict:
            matched_count += 1
            backfill_data = backfill_dict[key]

            # 收集回填值
            for src_field, dst_field in BACKFILL_FIELD_MAP.items():
                if src_field in backfill_data:
                    value = backfill_data[src_field]
                    # 处理 NaN 值
                    if pd.isna(value):
                        if dst_field in ["腾道匹配置信度", "腾道评分", "近一年进口次数"]:
                            value = 0
                        else:
                            value = ""
                    elif dst_field in ["腾道匹配置信度", "腾道评分", "近一年进口次数"]:
                        value = int(value) if value != "" else 0
                    else:
                        value = str(value)
                    backfill_updates[dst_field][idx] = value

            # 重新计算最终总分和最终优先级
            email_score = row.get("邮件线索评分", 0)
            if pd.isna(email_score):
                email_score = 0
            email_score = int(email_score)

            tendata_score = backfill_data.get("tendata_score", 0)
            if pd.isna(tendata_score):
                tendata_score = 0
            tendata_score = int(tendata_score)

            tendata_status = backfill_data.get("tendata_check_status", "")
            if pd.isna(tendata_status):
                tendata_status = ""

            # 特殊规则：待人工复核时，腾道评分不超过 10
            if tendata_status == "待人工复核" and tendata_score > 10:
                tendata_score = 10
                backfill_updates["腾道评分"][idx] = 10

            # 计算最终总分
            final_score = email_score + tendata_score
            backfill_updates.setdefault("最终总分", {})[idx] = final_score

            # 计算最终优先级
            final_priority = calculate_final_priority(final_score, tendata_status)
            backfill_updates.setdefault("最终优先级", {})[idx] = final_priority

            # 计算推荐动作
            current_action = row.get("推荐动作", "")
            if pd.isna(current_action):
                current_action = ""
            new_action = calculate_recommended_action(tendata_status, current_action, final_priority)
            backfill_updates.setdefault("推荐动作", {})[idx] = new_action

    # 批量应用回填更新
    for col, updates in backfill_updates.items():
        if updates:
            for idx, val in updates.items():
                df_source.at[idx, col] = val

    print(f"  成功回填: {matched_count} 行")
    print(f"  未匹配: {total_backfill - matched_count} 行")

    # 统计未匹配的 internal_customer_id
    unmatched = []
    for _, row in df_backfill.iterrows():
        key = str(row["internal_customer_id"]).strip()
        if key not in df_source["客户聚合 Key"].values:
            unmatched.append(key)
    if unmatched:
        print(f"  未匹配的 Key: {unmatched[:5]}{'...' if len(unmatched) > 5 else ''}")

    print(f"\n[4/4] 导出新文件: {output_path}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df_source.to_excel(output_path, index=False, sheet_name="客户候选池_正式版")

    return {
        "total_source": total_source,
        "total_backfill": total_backfill,
        "matched_count": matched_count,
        "unmatched_count": total_backfill - matched_count,
        "unmatched_keys": unmatched,
    }


def analyze_priority_distribution(
    source_path: str,
    output_path: str,
) -> dict:
    """分析优先级分布变化。"""
    print("\n" + "=" * 60)
    print("优先级分布变化分析")
    print("=" * 60)

    # 读取原始文件
    df_old = pd.read_excel(source_path, sheet_name="客户候选池_正式版")
    # 读取新文件
    df_new = pd.read_excel(output_path, sheet_name="客户候选池_正式版")

    # 原始分布
    old_dist = df_old["最终优先级"].value_counts().to_dict()
    # 新分布
    new_dist = df_new["最终优先级"].value_counts().to_dict()

    # 所有优先级
    all_priorities = ["A+", "A", "B", "C", "D"]

    print("\n【原始分布】")
    for p in all_priorities:
        count = old_dist.get(p, 0)
        print(f"  {p}: {count}")

    print("\n【新分布】")
    for p in all_priorities:
        count = new_dist.get(p, 0)
        change = new_dist.get(p, 0) - old_dist.get(p, 0)
        change_str = f" (+{change})" if change > 0 else (f" ({change})" if change < 0 else "")
        print(f"  {p}: {count}{change_str}")

    return {"old": old_dist, "new": new_dist}


def show_changed_records(output_path: str) -> None:
    """显示被修改的记录。"""
    print("\n" + "=" * 60)
    print("回填记录详情")
    print("=" * 60)

    df = pd.read_excel(output_path, sheet_name="客户候选池_正式版")

    # 筛选有腾道排查状态的记录
    filled = df[df["腾道排查状态"].notna() & (df["腾道排查状态"] != "")]

    print(f"\n共 {len(filled)} 条记录已回填腾道数据:\n")

    for i, (_, row) in enumerate(filled.iterrows(), 1):
        print(f"{i}. {row['客户名标准化'][:45]}")
        print(f"   客户聚合 Key: {row['客户聚合 Key'][:50]}")
        print(f"   腾道排查状态: {row['腾道排查状态']}")
        print(f"   腾道评分: {row['腾道评分']}")
        print(f"   邮件线索评分: {row['邮件线索评分']}")
        print(f"   最终总分: {row['最终总分']} (变化前: {int(row['邮件线索评分'])})")
        print(f"   最终优先级: {row['最终优先级']}")
        print(f"   推荐动作: {row['推荐动作']}")
        if row.get('腾道备注'):
            print(f"   腾道备注: {str(row['腾道备注'])[:60]}...")
        print()


def main():
    parser = argparse.ArgumentParser(description="合并腾道回填结果到客户候选池")
    parser.add_argument(
        "--source",
        default="客户候选池_正式版.xlsx",
        help="客户候选池原始文件路径",
    )
    parser.add_argument(
        "--backfill",
        default="output/tendata_backfill_A_10.xlsx",
        help="腾道回填文件路径",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出文件路径 (默认: output/客户候选池_正式版_含A10腾道回填.xlsx)",
    )
    args = parser.parse_args()

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        output_path = "output/客户候选池_正式版_含A10腾道回填.xlsx"

    print("=" * 60)
    print("腾道回填合并工具")
    print("=" * 60)

    # 执行合并
    result = merge_backfill(args.source, args.backfill, output_path)

    # 分析优先级分布变化
    analyze_priority_distribution(args.source, output_path)

    # 显示修改的记录
    show_changed_records(output_path)

    # 最终汇总
    print("\n" + "=" * 60)
    print("合并完成")
    print("=" * 60)
    print(f"\n【输出文件】")
    print(f"  {output_path}")
    print(f"\n【统计信息】")
    print(f"  客户候选池总行数: {result['total_source']}")
    print(f"  回填文件行数: {result['total_backfill']}")
    print(f"  成功回填行数: {result['matched_count']}")
    print(f"  未匹配行数: {result['unmatched_count']}")

    if result['unmatched_keys']:
        print(f"\n【未匹配的 Key】")
        for key in result['unmatched_keys'][:5]:
            print(f"  - {key}")


if __name__ == "__main__":
    main()
