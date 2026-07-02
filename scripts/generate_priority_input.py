"""
generate_priority_input.py — 从客户候选池生成腾道输入文件

功能：
1. 从客户候选池读取数据
2. 按优先级筛选客户
3. 排除内部公司
4. 清洗 email_domain（过滤内部/公共邮箱）
5. 映射字段并生成腾道抓取输入文件
6. 不启动抓取，只生成输入文件

用法：
    python scripts/generate_priority_input.py --priority A --limit 10
    python scripts/generate_priority_input.py --priority B --limit 50
    python scripts/generate_priority_input.py --source output/客户候选池_正式版_含A10腾道回填.xlsx --priority B --limit 50
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

# 核心输入字段映射（源字段 → 目标字段）
CORE_FIELD_MAP = {
    "客户名标准化": "customer_name",
    "国家/地区": "country_region",
    "客户邮箱域名": "email_domain",
    "产品关键词汇总": "product_keywords",
    "客户聚合 Key": "internal_customer_id",
}

# 辅助字段（保留用于回填和人工核对，不参与腾道搜索）
AUXILIARY_FIELDS = [
    "联系人邮箱",
    "客户名候选",
    "邮件线索评分",
    "最终总分",
    "最终优先级",
    "历史邮件数",
    "有效询价次数",
    "最高体量等级",
    "最高购买意向",
]

# 筛选字段
FILTER_STATUS_FIELD = "腾道排查状态"
FILTER_PRIORITY_FIELD = "最终优先级"

# ============================================================================
# 内部公司识别配置
# ============================================================================

# 内部公司关键词（客户名标准化 / 客户名候选）
INTERNAL_NAME_KEYWORDS = [
    "jiaxing mt",
    "mt stainless",
    "mtsco",
    "嘉兴",
    "maytun",
    "mt flange",
]

# 内部公司 Key 关键词
INTERNAL_KEY_KEYWORDS = [
    "jiaxing mt stainless",
    "mtsco",
    "mtstainless",
    "maytun",
]

# 内部邮箱域名
INTERNAL_DOMAINS = [
    "mtstainlesssteel.com",
    "mtsco.com",
    "mtstainless.com",
    "maytun.com",
]

# ============================================================================
# 邮箱域名清洗配置
# ============================================================================

# 公共邮箱域名（过滤掉）
PUBLIC_DOMAINS = [
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "qq.com",
    "163.com",
    "126.com",
    "foxmail.com",
    "icloud.com",
    "aol.com",
    "live.com",
    "msn.com",
    "mail.ru",
    "yandex.com",
    "protonmail.com",
    "zoho.com",
    "gmx.com",
]


# ============================================================================
# 判断函数
# ============================================================================

def is_internal_company(
    customer_name: str,
    customer_name_candidates: str,
    customer_key: str,
    email_domains: str,
) -> bool:
    """判断是否为内部公司（我方公司）。

    规则：
    1. 客户名标准化包含内部关键词
    2. 客户名候选包含内部关键词
    3. 客户聚合 Key 包含内部关键词
    4. 邮箱域名全部为内部域名
    """
    name_lower = (customer_name or "").lower()
    candidates_lower = (customer_name_candidates or "").lower()
    key_lower = (customer_key or "").lower()

    # 检查客户名标准化
    for kw in INTERNAL_NAME_KEYWORDS:
        if kw in name_lower:
            return True

    # 检查客户名候选
    for kw in INTERNAL_NAME_KEYWORDS:
        if kw in candidates_lower:
            return True

    # 检查客户聚合 Key
    for kw in INTERNAL_KEY_KEYWORDS:
        if kw in key_lower:
            return True

    # 检查邮箱域名是否全部为内部域名
    if email_domains:
        domains = [d.strip().lower() for d in re.split(r'[\n,;]+', email_domains) if d.strip()]
        if domains:
            non_internal = [d for d in domains if d not in INTERNAL_DOMAINS]
            if not non_internal:  # 全部是内部域名
                return True

    return False


def clean_email_domains(raw_domains: str, max_domains: int = 2) -> str:
    """清洗邮箱域名。

    规则：
    1. 过滤内部域名
    2. 过滤公共邮箱域名
    3. 只保留前 N 个有效域名
    4. 如果没有可用域名，返回空字符串
    """
    if not raw_domains or pd.isna(raw_domains):
        return ""

    # 拆分域名（支持换行、逗号、分号分隔）
    domains = [d.strip().lower() for d in re.split(r'[\n,;]+', str(raw_domains)) if d.strip()]

    # 过滤内部域名和公共邮箱
    valid_domains = []
    for d in domains:
        # 跳过内部域名
        if d in INTERNAL_DOMAINS:
            continue
        # 跳过公共邮箱
        if d in PUBLIC_DOMAINS:
            continue
        # 跳过以公共邮箱开头的域名
        is_public = False
        for pub in PUBLIC_DOMAINS:
            if d.endswith(pub) or d == pub:
                is_public = True
                break
        if is_public:
            continue

        valid_domains.append(d)

    # 只保留前 N 个
    valid_domains = valid_domains[:max_domains]

    return "\n".join(valid_domains) if valid_domains else ""


def validate_source_fields(df: pd.DataFrame) -> list[str]:
    """校验源文件是否包含必需字段。

    Returns:
        缺失字段列表，空列表表示校验通过
    """
    required_fields = (
        list(CORE_FIELD_MAP.keys())
        + AUXILIARY_FIELDS
        + [FILTER_STATUS_FIELD, FILTER_PRIORITY_FIELD]
    )
    missing = []
    for f in required_fields:
        if f not in df.columns:
            missing.append(f)
    return missing


def generate_input_file(
    source_path: str,
    output_path: str,
    priority: str = "B",
    limit: int = 50,
    status_filter: str = "待查",
    exclude_internal: bool = True,
    source_batch: str = "",
) -> dict:
    """生成腾道输入文件。

    Args:
        source_path: 源文件路径
        output_path: 输出文件路径
        priority: 优先级筛选条件
        limit: 最大导出数量
        status_filter: 腾道排查状态筛选条件
        exclude_internal: 是否排除内部公司
        source_batch: 来源批次标记（如 B50）

    Returns:
        统计信息字典
    """
    # ── 1. 读取源文件 ──
    print(f"[1/5] 读取源文件: {source_path}")
    df = pd.read_excel(source_path, sheet_name="客户候选池_正式版")
    total_rows = len(df)
    print(f"  读取总行数: {total_rows}")

    # ── 2. 校验字段 ──
    print(f"[2/5] 校验字段...")
    missing = validate_source_fields(df)
    if missing:
        print(f"  [错误] 缺失以下必需字段:")
        for f in missing:
            print(f"    - {f}")
        return {"error": "missing_fields", "missing": missing}

    print(f"  所有必需字段存在，校验通过")

    # ── 3. 筛选数据 ──
    print(f"[3/5] 筛选数据...")
    print(f"  筛选条件: {FILTER_STATUS_FIELD}='{status_filter}' AND {FILTER_PRIORITY_FIELD}='{priority}'")

    # 基础筛选
    filtered = df[
        (df[FILTER_STATUS_FIELD] == status_filter) &
        (df[FILTER_PRIORITY_FIELD] == priority)
    ].copy()

    base_count = len(filtered)
    print(f"  基础筛选结果: {base_count} 条")

    # 按最终总分降序排序
    if "最终总分" in filtered.columns:
        filtered = filtered.sort_values("最终总分", ascending=False)
        print(f"  已按最终总分降序排序")

    # 排除内部公司
    internal_count = 0
    if exclude_internal:
        print(f"  正在排除内部公司...")
        before_internal = len(filtered)

        # 判断每条记录是否为内部公司
        internal_mask = filtered.apply(
            lambda row: is_internal_company(
                str(row.get("客户名标准化", "")),
                str(row.get("客户名候选", "")),
                str(row.get("客户聚合 Key", "")),
                str(row.get("客户邮箱域名", "")),
            ),
            axis=1
        )

        filtered = filtered[~internal_mask].copy()
        internal_count = before_internal - len(filtered)
        print(f"  排除内部公司: {internal_count} 条")

    # 截取指定数量
    filtered_count = len(filtered)
    if limit > 0 and filtered_count > limit:
        filtered = filtered.head(limit)
        print(f"  截取前 {limit} 条")

    export_count = len(filtered)
    print(f"  实际导出: {export_count} 条")

    if export_count == 0:
        print(f"  [警告] 无符合条件的记录，不生成输出文件")
        return {
            "total_rows": total_rows,
            "base_count": base_count,
            "internal_count": internal_count,
            "filtered_count": filtered_count,
            "export_count": 0,
            "output_path": None,
        }

    # ── 4. 数据清洗 ──
    print(f"[4/5] 数据清洗...")

    # 清洗 email_domain
    filtered["email_domain_cleaned"] = filtered["客户邮箱域名"].apply(clean_email_domains)
    print(f"  已清洗 email_domain（过滤内部/公共邮箱）")

    # ── 5. 生成输出文件 ──
    print(f"[5/5] 生成输出文件...")

    # 构建输出 DataFrame
    output_data = {}

    # 映射核心字段
    for src_field, dst_field in CORE_FIELD_MAP.items():
        if dst_field == "email_domain":
            # 使用清洗后的域名
            output_data[dst_field] = filtered["email_domain_cleaned"]
        else:
            output_data[dst_field] = filtered[src_field]

    # 保留辅助字段（原名）
    for aux_field in AUXILIARY_FIELDS:
        if aux_field in filtered.columns:
            output_data[aux_field] = filtered[aux_field]

    # 新增来源批次标记
    batch_label = source_batch or f"{priority}{limit}"
    output_data["source_priority_batch"] = batch_label

    output_df = pd.DataFrame(output_data)

    # 确保输出目录存在
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # 导出
    output_df.to_excel(output_path, index=False)
    print(f"  输出文件: {output_path}")
    print(f"  输出列: {list(output_df.columns)}")

    return {
        "total_rows": total_rows,
        "base_count": base_count,
        "internal_count": internal_count,
        "filtered_count": filtered_count,
        "export_count": export_count,
        "output_path": output_path,
        "columns": list(output_df.columns),
    }


def preview_output(output_path: str, n: int = 10) -> None:
    """预览输出文件内容。"""
    print(f"\n=== 输出文件预览 (前 {n} 条) ===")
    df = pd.read_excel(output_path)

    # 核心字段预览
    core_cols = ["customer_name", "country_region", "internal_customer_id", "source_priority_batch"]
    print("\n[核心字段]")
    for i, row in df.head(n).iterrows():
        # 简化国家显示（取第一个）
        country = str(row["country_region"]).split("\n")[0] if row["country_region"] else ""
        email_dom = str(row["email_domain"]).split("\n")[0] if row["email_domain"] else "(无)"
        print(f"  {i+1}. {row['customer_name'][:40]} | 国家: {country[:15]} | 域名: {email_dom[:25]}")

    # 辅助字段预览
    aux_cols = ["最终优先级", "最终总分", "邮件线索评分"]
    print("\n[辅助字段]")
    print(df[aux_cols].head(n).to_string())


def main():
    parser = argparse.ArgumentParser(
        description="从客户候选池生成腾道输入文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python scripts/generate_priority_input.py --priority A --limit 10
    python scripts/generate_priority_input.py --priority B --limit 50
    python scripts/generate_priority_input.py --source output/客户候选池_正式版_含A10腾道回填.xlsx --priority B --limit 50
        """
    )
    parser.add_argument(
        "--source",
        default="客户候选池_正式版.xlsx",
        help="源文件路径 (默认: 客户候选池_正式版.xlsx)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出文件路径 (默认: input/tendata_priority_{PRIORITY}_{LIMIT}.xlsx)",
    )
    parser.add_argument(
        "--priority",
        default="B",
        choices=["A", "B", "C", "D"],
        help="筛选优先级 (默认: B)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="最大导出数量 (默认: 50)",
    )
    parser.add_argument(
        "--status",
        default="待查",
        help="腾道排查状态筛选 (默认: 待查)",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=10,
        help="预览行数 (默认: 10)",
    )
    parser.add_argument(
        "--source-batch",
        default="",
        help="来源批次标记 (默认: 自动生成，如 B50)",
    )
    parser.add_argument(
        "--include-internal",
        action="store_true",
        help="包含内部公司（默认排除）",
    )
    args = parser.parse_args()

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        output_path = f"input/tendata_priority_{args.priority}_{args.limit}.xlsx"

    print("=" * 60)
    print(f"腾道输入文件生成器")
    print("=" * 60)

    # 执行生成
    result = generate_input_file(
        source_path=args.source,
        output_path=output_path,
        priority=args.priority,
        limit=args.limit,
        status_filter=args.status,
        exclude_internal=not args.include_internal,
        source_batch=args.source_batch,
    )

    # 结果汇总
    print("\n" + "=" * 60)
    print("生成完成")
    print("=" * 60)

    if "error" in result:
        print(f"状态: 失败 ({result['error']})")
        if "missing" in result:
            print(f"缺失字段: {result['missing']}")
        sys.exit(1)

    print(f"源文件总行数: {result['total_rows']}")
    print(f"基础筛选数量 ({args.priority} 级待查): {result['base_count']}")
    print(f"排除内部公司数量: {result['internal_count']}")
    print(f"实际导出数量: {result['export_count']}")
    if result['output_path']:
        print(f"输出文件路径: {result['output_path']}")

    # 预览输出
    if result['output_path'] and result['export_count'] > 0:
        preview_output(result['output_path'], args.preview)

    print("\n[提示] 输入文件已生成，请确认后运行:")
    print(f"  python scripts/run_batch.py --input {result['output_path']}")


if __name__ == "__main__":
    main()
