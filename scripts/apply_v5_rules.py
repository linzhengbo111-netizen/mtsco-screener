"""
apply_v5_rules.py — 对 V4 结果应用 V5 字段判定规则

不重新抓取，只修正输出字段判定。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd


def is_domain_candidate(name: str) -> bool:
    """检测是否是域名/目录站候选。"""
    if not name:
        return False
    name_lower = name.lower()
    domain_patterns = [r'\.com', r'\.org', r'\.net', r'\.io', r'\.co\.', r'\.tr', r'\.com\.tr', r'\.co\.uk']
    for pattern in domain_patterns:
        if re.search(pattern, name_lower):
            # 检查是否有公司后缀
            has_company_suffix = any(suffix in name_lower for suffix in [
                'ltd', 'llc', 'inc', 'corp', 'gmbh', 'srl', 'spa', 'co.', 'limited', 'company'
            ])
            if not has_company_suffix:
                return True
    return False


def clear_field(df, idx, field):
    """安全清除字段值，处理不同数据类型。"""
    if field not in df.columns:
        return
    dtype = df[field].dtype
    if pd.api.types.is_numeric_dtype(dtype):
        df.at[idx, field] = pd.NA
    else:
        df.at[idx, field] = ''


def set_field(df, idx, field, value):
    """安全设置字段值，处理不同数据类型。"""
    if field not in df.columns:
        return
    dtype = df[field].dtype
    if pd.api.types.is_numeric_dtype(dtype):
        # 数值类型，尝试转换
        try:
            df.at[idx, field] = float(value)
        except (ValueError, TypeError):
            df.at[idx, field] = pd.NA
    else:
        df.at[idx, field] = value


def apply_v5_rules(df: pd.DataFrame) -> pd.DataFrame:
    """对 DataFrame 应用 V5 规则。"""

    # 需要清除的字段
    fields_to_clear = [
        'latest_import_date', 'last_12m_import_count', 'last_24m_import_count',
        'last_36m_import_count', 'total_shipment_count', 'supplier_count',
        'top_import_products', 'related_hs_codes'
    ]

    for idx, row in df.iterrows():
        match_status = row.get('match_status', '')
        matched_name = str(row.get('matched_company_name', ''))

        # ---- 1. conflict 客户清除进口活跃度字段 ----
        if match_status == 'conflict':
            for field in fields_to_clear:
                clear_field(df, idx, field)
            df.at[idx, 'product_relevance_level'] = 'unknown'
            set_field(df, idx, 'product_relevance_score', '0')
            df.at[idx, 'import_frequency_level'] = 'unknown'
            df.at[idx, 'import_active_status'] = 'invalid_for_target'

        # ---- 2. confirmed/likely_match 但无进口数据 ----
        elif match_status in ('confirmed', 'likely_match'):
            has_import_data = bool(
                row.get('total_shipment_count')
                or row.get('top_import_products')
                or row.get('related_hs_codes')
                or row.get('supplier_count')
            )
            has_latest_date = bool(row.get('latest_import_date'))
            has_trade_counts = bool(
                row.get('last_12m_import_count')
                or row.get('last_24m_import_count')
                or row.get('last_36m_import_count')
            )

            if has_latest_date and not has_trade_counts:
                df.at[idx, 'analysis_data_status'] = 'partial_import_signal'
                df.at[idx, 'import_frequency_level'] = 'unknown'
                # 避免高活跃度判断，推荐转官网核验
                df.at[idx, 'recommended_action'] = '转官网/LinkedIn核验'
            elif not has_import_data:
                current_status = row.get('analysis_data_status', 'unknown')
                if current_status in ('unknown', 'has_data'):
                    df.at[idx, 'analysis_data_status'] = 'no_import_analysis_data'
                df.at[idx, 'import_active_status'] = 'unknown'
                df.at[idx, 'import_frequency_level'] = 'unknown'
                df.at[idx, 'recommended_action'] = '转官网/LinkedIn核验'

        # ---- 3. 目录站/网站候选检测 ----
        if is_domain_candidate(matched_name) and match_status not in ('excluded_internal_record', 'no_result', 'candidate_found_not_entered'):
            df.at[idx, 'match_status'] = 'unconfirmed'
            df.at[idx, 'match_confidence'] = min(int(row.get('match_confidence', 30)), 30)
            df.at[idx, 'manual_review_flag'] = 'no'
            df.at[idx, 'manual_review_reason'] = '命中目录/网站候选，非企业主体'
            df.at[idx, 'recommended_action'] = '转官网/LinkedIn核验'
            df.at[idx, 'import_active_status'] = 'unknown'
            df.at[idx, 'import_frequency_level'] = 'unknown'
            for field in fields_to_clear:
                clear_field(df, idx, field)
            df.at[idx, 'product_relevance_level'] = 'unknown'
            set_field(df, idx, 'product_relevance_score', '0')

    return df


def main():
    import argparse
    parser = argparse.ArgumentParser(description='对 V4 结果应用 V5 字段判定规则')
    parser.add_argument('--input', '-i', required=True, help='输入 V4 Excel 文件路径')
    parser.add_argument('--output', '-o', required=True, help='输出 V5 Excel 文件路径')
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"错误: 输入文件不存在: {input_path}")
        sys.exit(1)

    print(f"读取 V4 结果: {input_path}")
    df = pd.read_excel(input_path)
    print(f"总行数: {len(df)}")

    print("\n应用 V5 规则...")
    df = apply_v5_rules(df)

    print(f"\n保存 V5 结果: {output_path}")
    df.to_excel(output_path, index=False)

    # 统计
    print("\n=== V5 结果统计 ===")
    print(f"match_status 分布:")
    print(df['match_status'].value_counts())
    print(f"\nrecommended_action 分布:")
    print(df['recommended_action'].value_counts())
    print(f"\nmanual_review_flag 分布:")
    print(df['manual_review_flag'].value_counts())
    print(f"\nanalysis_data_status 分布:")
    print(df['analysis_data_status'].value_counts())


if __name__ == "__main__":
    main()
