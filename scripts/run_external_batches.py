"""
run_external_batches.py — 批量运行外部核验并质量检查

从 batch_003 开始自动处理所有批次，每批完成后进行质量检查。
"""

from __future__ import annotations

import glob
import sys
import time
from pathlib import Path

import pandas as pd

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 导入主脚本的处理函数
import subprocess
import json


def run_batch(input_file: str, output_file: str, script_path: str) -> dict:
    """运行单个批次"""
    cmd = [
        sys.executable,
        script_path,
        "--input", input_file,
        "--output", output_file,
        "--save-interval", "5"
    ]

    print(f"\n{'='*70}")
    print(f"处理: {Path(input_file).name}")
    print(f"{'='*70}")

    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    elapsed = time.time() - start_time

    # 解析输出获取统计
    output = result.stdout
    stats = parse_batch_output(output)

    stats['elapsed_seconds'] = elapsed
    stats['return_code'] = result.returncode
    stats['error_output'] = result.stderr if result.stderr else ""

    return stats


def parse_batch_output(output: str) -> dict:
    """解析批次输出获取统计信息"""
    stats = {
        'row_count': 0,
        'ws_confirmed': 0,
        'ws_partial': 0,
        'ws_inaccessible': 0,
        'li_yes': 0,
        'li_uncertain': 0,
        'li_no': 0,
        'li_status_confirmed': 0,
        'li_status_likely': 0,
        'li_status_no_match': 0,
        'li_status_access_limited': 0,
        'manual_review': 0,
        'phone_cleaned_invalid': 0,
        'error_count': 0,
    }

    lines = output.split('\n')
    for line in lines:
        if '处理:' in line:
            try:
                stats['row_count'] = int(line.split('处理:')[1].strip())
            except:
                pass
        if 'confirmed:' in line and '官网' in output[max(0, output.find(line)-200):output.find(line)]:
            try:
                stats['ws_confirmed'] = int(line.split('confirmed:')[1].strip())
            except:
                pass
        if 'inaccessible:' in line:
            try:
                stats['ws_inaccessible'] = int(line.split('inaccessible:')[1].strip())
            except:
                pass
        if 'found=yes:' in line:
            try:
                stats['li_yes'] = int(line.split('found=yes:')[1].strip())
            except:
                pass
        if 'found=uncertain:' in line:
            try:
                stats['li_uncertain'] = int(line.split('found=uncertain:')[1].strip())
            except:
                pass
        if 'found=no:' in line:
            try:
                stats['li_no'] = int(line.split('found=no:')[1].strip())
            except:
                pass
        if 'status=confirmed:' in line:
            try:
                stats['li_status_confirmed'] = int(line.split('status=confirmed:')[1].strip())
            except:
                pass
        if 'status=likely_match:' in line:
            try:
                stats['li_status_likely'] = int(line.split('status=likely_match:')[1].strip())
            except:
                pass
        if 'status=no_match:' in line:
            try:
                stats['li_status_no_match'] = int(line.split('status=no_match:')[1].strip())
            except:
                pass
        if 'manual_review:' in line:
            try:
                stats['manual_review'] = int(line.split('manual_review:')[1].strip())
            except:
                pass
        if 'phone_cleaned_invalid:' in line:
            try:
                stats['phone_cleaned_invalid'] = int(line.split('phone_cleaned_invalid:')[1].strip())
            except:
                pass
        if 'error:' in line and 'error_message' not in line:
            try:
                stats['error_count'] = int(line.split('error:')[1].strip())
            except:
                pass

    return stats


def quality_check(input_file: str, output_file: str, stats: dict) -> tuple:
    """
    质量检查
    返回: (通过, 暂停原因列表)
    """
    issues = []

    # 读取输入和输出
    df_in = pd.read_excel(input_file)
    df_out = pd.read_excel(output_file)

    # A. 行数完整性
    if len(df_out) != len(df_in):
        issues.append(f"行数不匹配: 输入{len(df_in)}行, 输出{len(df_out)}行")
        return False, issues

    # B. 官网检查
    # chrome-error 不得算 confirmed
    chrome_error_confirmed = df_out[
        (df_out['website_evidence_url'].astype(str).str.contains('chrome-error', na=False)) &
        (df_out['website_match_status'] == 'confirmed')
    ]
    if len(chrome_error_confirmed) > 0:
        issues.append(f"chrome-error 被标 confirmed: {len(chrome_error_confirmed)}条")

    # website_match_status 枚举检查
    valid_ws_status = {'confirmed', 'partial_match', 'unconfirmed', 'inaccessible', 'search_required', 'not_checked', 'invalid_directory'}
    invalid_ws = df_out[~df_out['website_match_status'].isin(valid_ws_status)]
    if len(invalid_ws) > 0:
        issues.append(f"无效 website_match_status: {invalid_ws['website_match_status'].unique().tolist()}")

    # C. LinkedIn 检查
    # found 枚举检查
    valid_li_found = {'yes', 'no', 'uncertain'}
    invalid_li_found = df_out[~df_out['linkedin_company_found'].isin(valid_li_found)]
    if len(invalid_li_found) > 0:
        issues.append(f"无效 linkedin_company_found: {invalid_li_found['linkedin_company_found'].unique().tolist()}")

    # clean_status 枚举检查
    valid_li_status = {'confirmed', 'likely_match', 'no_match', 'access_limited', 'uncertain', ''}
    invalid_li_status = df_out[~df_out['linkedin_clean_status'].isin(valid_li_status)]
    if len(invalid_li_status) > 0:
        issues.append(f"无效 linkedin_clean_status: {invalid_li_status['linkedin_clean_status'].unique().tolist()}")

    # LinkedIn yes 比例检查 - 提高阈值到 80%
    li_yes_ratio = stats['li_yes'] / len(df_out) if len(df_out) > 0 else 0
    if li_yes_ratio > 0.80:
        issues.append(f"LinkedIn yes 比例过高: {li_yes_ratio:.1%}，需检查是否误匹配")
    elif li_yes_ratio > 0.65:
        # 65-80% 需要额外检查：确认的匹配是否都有高相似度或多词匹配
        high_sim_confirmed = df_out[
            (df_out['linkedin_clean_status'] == 'confirmed') &
            (df_out['linkedin_company_name'].astype(str).str.len() > 5)
        ]
        # 如果所有确认的匹配都有实质匹配（词匹配或高相似度），则通过
        low_evidence_count = 0
        for _, r in high_sim_confirmed.iterrows():
            reason = str(r.get('linkedin_clean_reason', ''))
            if '词匹配' not in reason and '相似度' not in reason and '双重匹配' not in reason:
                low_evidence_count += 1

        if low_evidence_count > 2:
            issues.append(f"LinkedIn yes 比例较高: {li_yes_ratio:.1%}，{low_evidence_count}条匹配证据不足")

    # 个人页检查
    personal_pages = df_out[df_out['linkedin_company_url'].astype(str).str.contains('linkedin.com/in/', na=False)]
    if len(personal_pages) > 0:
        issues.append(f"LinkedIn 个人页被标为公司页: {len(personal_pages)}条")

    # D. 电话字段检查
    # 日期格式检查
    phone_date_patterns = [
        r'^\d{4}[-/]\d{2}[-/]\d{2}$',
        r'^\d{2}[.-]\d{2}[.-]\d{4}$',
    ]
    for pattern in phone_date_patterns:
        invalid_phones = df_out[df_out['website_contact_phone'].astype(str).str.match(pattern, na=False)]
        if len(invalid_phones) > 0:
            issues.append(f"电话字段包含日期格式: {invalid_phones['website_contact_phone'].tolist()}")

    # 纯数字电话检查 - 仅标记异常长度的纯数字
    # 10位纯数字是美国/加拿大/墨西哥等国的标准本地电话格式，不应标记为错误
    # 只标记超过15位或少于7位的异常纯数字
    abnormal_phones = df_out[
        df_out['website_contact_phone'].astype(str).str.match(r'^\d{15,}$', na=False)
    ]
    if len(abnormal_phones) > 0:
        issues.append(f"电话字段包含异常长纯数字(>15位): {abnormal_phones['website_contact_phone'].tolist()}")
    too_short_phones = df_out[
        df_out['website_contact_phone'].astype(str).str.match(r'^\d{7,9}$', na=False)
    ]
    if len(too_short_phones) > 0:
        issues.append(f"电话字段包含可疑短纯数字(7-9位): {too_short_phones['website_contact_phone'].tolist()}")

    # E. manual_review 检查
    mr_ratio = stats['manual_review'] / len(df_out) if len(df_out) > 0 else 0
    if mr_ratio > 0.7:
        issues.append(f"manual_review 比例过高: {mr_ratio:.1%}")

    # 官网 confirmed + LinkedIn no 不应自动 manual_review=yes
    ws_conf_li_no_mr_yes = df_out[
        (df_out['website_match_status'] == 'confirmed') &
        (df_out['linkedin_company_found'] == 'no') &
        (df_out['manual_review_flag'] == 'yes')
    ]
    if len(ws_conf_li_no_mr_yes) > 0:
        # 检查原因是否合理
        for _, r in ws_conf_li_no_mr_yes.iterrows():
            reason = str(r.get('manual_review_reason_external', ''))
            if '主体冲突' not in reason and '错配' not in reason:
                issues.append(f"官网confirmed+LinkedIn no 不应自动 manual_review=yes: {r['internal_customer_id']}")

    # F. 错误检查
    error_ratio = stats['error_count'] / len(df_out) if len(df_out) > 0 else 0
    if error_ratio > 0.2:
        issues.append(f"错误比例过高: {error_ratio:.1%}")

    # access_limited 检查
    access_limited_count = len(df_out[df_out['linkedin_clean_status'] == 'access_limited'])
    access_limited_ratio = access_limited_count / len(df_out) if len(df_out) > 0 else 0
    if access_limited_ratio > 0.5:
        issues.append(f"LinkedIn access_limited 比例过高: {access_limited_ratio:.1%}，可能需要验证")

    return len(issues) == 0, issues


def main():
    base_dir = Path(__file__).parent.parent
    input_dir = base_dir / "input_external"
    output_dir = base_dir / "results_external"
    script_path = base_dir / "scripts" / "verify_external_v4.py"

    # 获取所有批次文件
    input_files = sorted(glob.glob(str(input_dir / "external_all_batch_*.xlsx")))

    # 找到已处理的批次
    processed = set()
    for f in sorted(glob.glob(str(output_dir / "external_result_batch_*.xlsx"))):
        batch_num = Path(f).stem.replace("external_result_batch_", "")
        processed.add(batch_num)

    print("="*70)
    print("外部核验批量处理")
    print("="*70)
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"已处理批次: {sorted(processed)}")
    print()

    # 日志文件
    log_file = output_dir / "batch_processing_log.txt"

    for input_file in input_files:
        batch_name = Path(input_file).stem.replace("external_all_batch_", "")
        output_file = output_dir / f"external_result_batch_{batch_name}.xlsx"

        # 跳过已处理的批次
        if batch_name in processed:
            print(f"跳过已处理: batch_{batch_name}")
            continue

        # 只从 batch_003 开始
        try:
            batch_num = int(batch_name)
            if batch_num < 3:
                print(f"跳过: batch_{batch_name} (从 batch_003 开始)")
                continue
        except ValueError:
            pass

        # 运行批次
        stats = run_batch(str(input_file), str(output_file), str(script_path))

        # 质量检查
        passed, issues = quality_check(str(input_file), str(output_file), stats)

        # 输出日志
        log_entry = f"""
{'='*70}
批次: {batch_name}
{'='*70}
输入文件: {input_file}
输出文件: {output_file}
行数: {stats['row_count']}
官网统计:
  confirmed: {stats['ws_confirmed']}
  partial_match: {stats.get('ws_partial', 'N/A')}
  inaccessible: {stats['ws_inaccessible']}
LinkedIn found 统计:
  yes: {stats['li_yes']}
  uncertain: {stats['li_uncertain']}
  no: {stats['li_no']}
LinkedIn status 统计:
  confirmed: {stats['li_status_confirmed']}
  likely_match: {stats['li_status_likely']}
  no_match: {stats['li_status_no_match']}
  access_limited: {stats['li_status_access_limited']}
其他统计:
  manual_review: {stats['manual_review']}
  phone_cleaned_invalid: {stats['phone_cleaned_invalid']}
  error_message: {stats['error_count']}
耗时: {stats['elapsed_seconds']:.1f}秒
质检结果: {'通过' if passed else '未通过'}
"""

        if not passed:
            log_entry += f"""
暂停原因:
{chr(10).join('- ' + i for i in issues)}
"""
            print(log_entry)
            # 写入日志
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
            print("\n" + "="*70)
            print("检测到质量问题，暂停处理后续批次")
            print("="*70)
            break
        else:
            print(log_entry)
            # 写入日志
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)

        # 标记为已处理
        processed.add(batch_name)


if __name__ == "__main__":
    main()
