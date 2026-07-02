"""
merge_external_results.py — 合并外部核验结果并生成清单
"""

from __future__ import annotations

import os
import glob
from datetime import datetime
from pathlib import Path

import pandas as pd


def create_manifest(results_dir: str) -> pd.DataFrame:
    """创建批次清单"""
    manifest_data = []

    # 读取输入批次行数作为基准
    input_dir = Path(results_dir).parent / "input_external"
    input_counts = {}
    for f in glob.glob(str(input_dir / "external_all_batch_*.xlsx")):
        batch_name = Path(f).stem.replace("external_all_batch_", "")
        try:
            df = pd.read_excel(f)
            input_counts[batch_name] = len(df)
        except:
            input_counts[batch_name] = 0

    # 扫描结果文件
    result_files = sorted(glob.glob(str(Path(results_dir) / "external_result_batch_*.xlsx")))

    for result_file in result_files:
        batch_name = Path(result_file).stem.replace("external_result_batch_", "")

        # 获取修改时间
        mtime = os.path.getmtime(result_file)
        modified_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

        try:
            df = pd.read_excel(result_file)
            row_count = len(df)

            # 统计各项
            unique_customer_count = df['internal_customer_id'].nunique()
            website_confirmed_count = len(df[df['website_match_status'] == 'confirmed'])
            website_inaccessible_count = len(df[df['website_match_status'] == 'inaccessible'])
            linkedin_confirmed_count = len(df[df['linkedin_clean_status'] == 'confirmed'])
            linkedin_no_match_count = len(df[df['linkedin_clean_status'] == 'no_match'])
            linkedin_access_limited_count = len(df[df['linkedin_clean_status'] == 'access_limited'])
            manual_review_count = len(df[df['manual_review_flag'] == 'yes'])
            error_message_count = df['error_message'].notna().sum() if 'error_message' in df.columns else 0

            # 检查无效情况
            invalid_reasons = []

            # 1. 行数不匹配
            expected_count = input_counts.get(batch_name, 0)
            if expected_count > 0 and row_count != expected_count:
                invalid_reasons.append(f"行数不匹配(期望{expected_count},实际{row_count})")

            # 2. error_message 超过 20%
            if row_count > 0 and error_message_count / row_count > 0.2:
                invalid_reasons.append(f"error_message比例过高({error_message_count/row_count:.1%})")

            # 3. LinkedIn access_limited 超过 50%
            if row_count > 0 and linkedin_access_limited_count / row_count > 0.5:
                invalid_reasons.append(f"access_limited比例过高({linkedin_access_limited_count/row_count:.1%})")

            # 4. 检查浏览器关闭/pages=0/登录失效
            if 'error_message' in df.columns:
                browser_errors = df[df['error_message'].astype(str).str.contains('浏览器关闭|pages=0|登录失效|session', case=False, na=False)]
                if len(browser_errors) > 0:
                    invalid_reasons.append(f"浏览器/会话错误({len(browser_errors)}条)")

            is_valid = "yes" if len(invalid_reasons) == 0 else "no"
            invalid_reason = "; ".join(invalid_reasons) if invalid_reasons else ""

        except Exception as e:
            row_count = 0
            unique_customer_count = 0
            website_confirmed_count = 0
            website_inaccessible_count = 0
            linkedin_confirmed_count = 0
            linkedin_no_match_count = 0
            manual_review_count = 0
            error_message_count = 0
            is_valid = "no"
            invalid_reason = f"读取失败: {str(e)}"

        manifest_data.append({
            'result_file': Path(result_file).name,
            'modified_time': modified_time,
            'row_count': row_count,
            'unique_customer_count': unique_customer_count,
            'website_confirmed_count': website_confirmed_count,
            'website_inaccessible_count': website_inaccessible_count,
            'linkedin_confirmed_count': linkedin_confirmed_count,
            'linkedin_no_match_count': linkedin_no_match_count,
            'manual_review_count': manual_review_count,
            'error_message_count': error_message_count,
            'is_valid_for_merge': is_valid,
            'invalid_reason': invalid_reason,
        })

    return pd.DataFrame(manifest_data)


def merge_results(results_dir: str, manifest: pd.DataFrame) -> pd.DataFrame:
    """合并所有有效批次结果"""
    all_dfs = []

    for _, row in manifest.iterrows():
        if row['is_valid_for_merge'] == 'yes':
            file_path = Path(results_dir) / row['result_file']
            df = pd.read_excel(file_path)
            df['_source_file'] = row['result_file']
            df['_modified_time'] = row['modified_time']
            all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    merged = pd.concat(all_dfs, ignore_index=True)

    # 去重：按 internal_customer_id，保留最新的有效记录
    if 'internal_customer_id' in merged.columns:
        # 先按修改时间排序
        merged = merged.sort_values('_modified_time', ascending=False)

        # 创建去重键
        merged['_merge_key'] = merged['internal_customer_id'].fillna('')
        # 如果 internal_customer_id 为空，用 customer_name + country_region 兜底
        empty_key_mask = merged['_merge_key'] == ''
        if empty_key_mask.any():
            merged.loc[empty_key_mask, '_merge_key'] = (
                merged.loc[empty_key_mask, 'customer_name'].fillna('') + '_' +
                merged.loc[empty_key_mask, 'country_region'].fillna('')
            )

        # 去重：优先保留 error_message 为空的记录
        merged['_has_error'] = merged['error_message'].notna().astype(int)
        merged = merged.sort_values(['_merge_key', '_has_error', '_modified_time'], ascending=[True, True, False])
        merged = merged.drop_duplicates(subset=['_merge_key'], keep='first')

        # 清理临时列
        merged = merged.drop(columns=['_merge_key', '_has_error'])

    return merged


def create_summary(merged: pd.DataFrame) -> dict:
    """生成汇总统计"""
    total = len(merged)

    summary = {
        '统计项': [],
        '数值': [],
        '占比': [],
    }

    # 基本统计
    stats = [
        ('总客户数', total, '100.0%'),
        ('官网 confirmed', len(merged[merged['website_match_status'] == 'confirmed']), ''),
        ('官网 partial_match', len(merged[merged['website_match_status'] == 'partial_match']), ''),
        ('官网 unconfirmed', len(merged[merged['website_match_status'] == 'unconfirmed']), ''),
        ('官网 inaccessible', len(merged[merged['website_match_status'] == 'inaccessible']), ''),
        ('官网 search_required', len(merged[merged['website_match_status'] == 'search_required']), ''),
        ('官网 invalid_directory', len(merged[merged['website_match_status'] == 'invalid_directory']), ''),
        ('官网 not_checked', len(merged[merged['website_match_status'] == 'not_checked']), ''),
        ('LinkedIn confirmed', len(merged[merged['linkedin_clean_status'] == 'confirmed']), ''),
        ('LinkedIn likely_match', len(merged[merged['linkedin_clean_status'] == 'likely_match']), ''),
        ('LinkedIn no_match', len(merged[merged['linkedin_clean_status'] == 'no_match']), ''),
        ('LinkedIn access_limited', len(merged[merged['linkedin_clean_status'] == 'access_limited']), ''),
        ('LinkedIn uncertain', len(merged[merged['linkedin_clean_status'] == 'uncertain']), ''),
        ('manual_review', len(merged[merged['manual_review_flag'] == 'yes']), ''),
        ('error_message', merged['error_message'].notna().sum() if 'error_message' in merged.columns else 0, ''),
    ]

    for name, count, pct in stats:
        summary['统计项'].append(name)
        summary['数值'].append(count)
        summary['占比'].append(f"{count/total:.1%}" if total > 0 and not pct else pct)

    return pd.DataFrame(summary)


def create_country_stats(merged: pd.DataFrame) -> pd.DataFrame:
    """按国家统计"""
    if 'country_region' not in merged.columns:
        return pd.DataFrame()

    country_stats = merged.groupby('country_region').agg({
        'internal_customer_id': 'count',
        'website_match_status': lambda x: (x == 'confirmed').sum(),
        'linkedin_clean_status': lambda x: (x == 'confirmed').sum(),
    }).reset_index()

    country_stats.columns = ['country_region', 'total_count', 'website_confirmed', 'linkedin_confirmed']
    country_stats = country_stats.sort_values('total_count', ascending=False)

    return country_stats


def create_level_stats(merged: pd.DataFrame) -> pd.DataFrame:
    """按客户等级统计"""
    if 'tendata_customer_level' not in merged.columns:
        return pd.DataFrame()

    level_stats = merged.groupby('tendata_customer_level').agg({
        'internal_customer_id': 'count',
        'website_match_status': lambda x: (x == 'confirmed').sum(),
        'linkedin_clean_status': lambda x: (x == 'confirmed').sum(),
    }).reset_index()

    level_stats.columns = ['tendata_customer_level', 'total_count', 'website_confirmed', 'linkedin_confirmed']
    level_stats = level_stats.sort_values('total_count', ascending=False)

    return level_stats


def main():
    base_dir = Path(__file__).parent.parent
    results_dir = base_dir / "results_external"
    output_file = results_dir / "external_all_merged_cleaned.xlsx"

    print("="*70)
    print("外部核验结果合并")
    print("="*70)

    # 1. 创建清单
    print("\n1. 创建批次清单...")
    manifest = create_manifest(str(results_dir))

    valid_count = len(manifest[manifest['is_valid_for_merge'] == 'yes'])
    print(f"   有效批次: {valid_count}/{len(manifest)}")

    if manifest[manifest['is_valid_for_merge'] == 'no'].shape[0] > 0:
        print("   无效批次:")
        for _, row in manifest[manifest['is_valid_for_merge'] == 'no'].iterrows():
            print(f"     - {row['result_file']}: {row['invalid_reason']}")

    # 2. 合并结果
    print("\n2. 合并批次结果...")
    merged = merge_results(str(results_dir), manifest)
    print(f"   合并后总行数: {len(merged)}")

    # 3. 生成各分类sheet
    print("\n3. 生成分类sheet...")

    # Website Confirmed
    ws_confirmed = merged[merged['website_match_status'] == 'confirmed'].copy()
    print(f"   Website_Confirmed: {len(ws_confirmed)} 行")

    # Website Inaccessible
    ws_inaccessible = merged[merged['website_match_status'] == 'inaccessible'].copy()
    print(f"   Website_Inaccessible: {len(ws_inaccessible)} 行")

    # LinkedIn Confirmed
    li_confirmed = merged[merged['linkedin_clean_status'] == 'confirmed'].copy()
    print(f"   LinkedIn_Confirmed: {len(li_confirmed)} 行")

    # LinkedIn No Match
    li_no_match = merged[merged['linkedin_clean_status'] == 'no_match'].copy()
    print(f"   LinkedIn_No_Match: {len(li_no_match)} 行")

    # Manual Review
    manual_review = merged[merged['manual_review_flag'] == 'yes'].copy()
    print(f"   Manual_Review: {len(manual_review)} 行")

    # 4. 生成Summary
    print("\n4. 生成Summary...")
    summary = create_summary(merged)
    country_stats = create_country_stats(merged)
    level_stats = create_level_stats(merged)

    # 5. 输出到Excel
    print(f"\n5. 输出到: {output_file}")

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        manifest.to_excel(writer, sheet_name='External_Result_Manifest', index=False)
        merged.to_excel(writer, sheet_name='ALL_外部核验合并结果', index=False)
        ws_confirmed.to_excel(writer, sheet_name='Website_Confirmed', index=False)
        ws_inaccessible.to_excel(writer, sheet_name='Website_Inaccessible', index=False)
        li_confirmed.to_excel(writer, sheet_name='LinkedIn_Confirmed', index=False)
        li_no_match.to_excel(writer, sheet_name='LinkedIn_No_Match', index=False)
        manual_review.to_excel(writer, sheet_name='Manual_Review', index=False)
        summary.to_excel(writer, sheet_name='Summary', index=False)

        # 将国家统计和等级统计追加到Summary sheet后面
        startrow = len(summary) + 3
        country_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按国家统计:')

        startrow = startrow + len(country_stats) + 3
        level_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按客户等级统计:')

    print("\n完成!")
    print(f"输出文件: {output_file}")


if __name__ == "__main__":
    main()
