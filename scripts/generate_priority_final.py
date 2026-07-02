"""
generate_priority_final.py — 生成最终全量客户优先级排序表

输入：
1. external_all_customers_input.xlsx - 原始客户信息
2. tendata_all_merged.xlsx - 腾道结果
3. external_all_merged_cleaned.xlsx - 外部核验结果

输出：customer_priority_full_final.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

# Force UTF-8 output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def calculate_tendata_score(row: pd.Series) -> tuple:
    """
    计算腾道采购活跃度分数（25分）
    返回: (分数, 原因)
    """
    match_status = str(row.get('match_status', '')).lower()
    import_active = str(row.get('import_active_status', '')).lower()
    analysis_status = str(row.get('analysis_data_status', '')).lower()

    # 冲突或排除记录
    if match_status in ['conflict', 'excluded_internal_record']:
        return 0, f"腾道状态: {match_status}"

    # confirmed/likely_match 且 import_active=active
    if match_status in ['confirmed', 'likely_match'] and import_active == 'active':
        # 根据采购量细分
        total_shipment = row.get('total_shipment_count', 0) or 0
        if total_shipment >= 50:
            return 25, "腾道确认+活跃采购(高频)"
        elif total_shipment >= 20:
            return 22, "腾道确认+活跃采购(中频)"
        else:
            return 20, "腾道确认+活跃采购"

    # import_active=recent
    if import_active == 'recent':
        total_shipment = row.get('total_shipment_count', 0) or 0
        if total_shipment >= 10:
            return 18, "腾道确认+近期采购"
        else:
            return 12, "腾道确认+近期有采购"

    # 主体确认但 analysis_data_status 不理想
    if match_status in ['confirmed', 'likely_match']:
        if analysis_status == 'partial_import_signal':
            return 10, "腾道确认+部分进口信号"
        elif analysis_status == 'no_import_analysis_data':
            return 5, "腾道确认+无进口数据"
        else:
            return 15, "腾道确认"

    # candidate_found_not_entered / no_result / unknown
    if match_status in ['candidate_found_not_entered', 'no_result', 'unknown']:
        return 3, f"腾道状态: {match_status}"

    return 3, "腾道无强信号"


def calculate_product_score(row: pd.Series) -> tuple:
    """
    计算产品相关度分数（20分）
    返回: (分数, 原因)
    """
    tendata_rel = str(row.get('product_relevance_level', '')).lower()
    website_rel = str(row.get('website_product_relevance', '')).lower()

    # 腾道或官网高度相关
    high_rel = ['high', '高度相关', 'highly_relevant']
    if tendata_rel in high_rel or website_rel in high_rel:
        return 18, "产品高度相关"

    # 腾道或官网中等相关
    medium_rel = ['medium', '中等相关', 'moderately_relevant', 'partial']
    if tendata_rel in medium_rel or website_rel in medium_rel:
        return 12, "产品中等相关"

    # 腾道或官网低相关
    low_rel = ['low', '低相关', 'low_relevance']
    if tendata_rel in low_rel or website_rel in low_rel:
        return 5, "产品低相关"

    # 有产品关键词
    if pd.notna(row.get('product_keywords')) and str(row.get('product_keywords', '')).strip():
        return 8, "有产品关键词(相关度未知)"

    return 3, "产品相关度未知"


def calculate_customer_value_score(row: pd.Series) -> tuple:
    """
    计算历史客户价值分数（20分）
    返回: (分数, 原因)
    """
    customer_level = str(row.get('customer_level', '')).lower()
    customer_status = str(row.get('customer_status', '')).lower()
    last_purchase = row.get('last_purchase_date')

    # 客户等级高
    if customer_level in ['a', '高', 'vip', 'key']:
        if pd.notna(last_purchase):
            return 18, "高价值客户+有采购"
        return 15, "高价值客户"

    # 已成交客户
    if customer_status in ['active', '活跃', '成交', '已成交']:
        if pd.notna(last_purchase):
            return 16, "已成交客户+有采购记录"
        return 10, "已成交客户"

    # 潜在客户
    if customer_status in ['potential', '潜在', 'prospect']:
        return 8, "潜在客户"

    # 非客户/内部记录
    if customer_status in ['internal', '内部', 'non-customer']:
        return 0, "内部/非客户记录"

    # 默认
    if pd.notna(last_purchase):
        return 10, "有采购记录"

    return 5, "客户价值不明"


def calculate_business_status_score(row: pd.Series) -> tuple:
    """
    计算当前经营状态分数（15分）
    返回: (分数, 原因)
    """
    ws_status = str(row.get('website_match_status', '')).lower()
    ws_business = str(row.get('website_business_status', '')).lower()
    li_found = str(row.get('linkedin_company_found', '')).lower()
    li_status = str(row.get('linkedin_clean_status', '')).lower()

    # 官网 confirmed 且 business_status=active
    if ws_status == 'confirmed' and ws_business in ['active', '活跃', 'operating']:
        if li_status == 'confirmed':
            return 15, "官网确认+LinkedIn确认+经营活跃"
        return 13, "官网确认+经营活跃"

    # 官网 confirmed
    if ws_status == 'confirmed':
        if li_status == 'confirmed':
            return 12, "官网+LinkedIn双确认"
        return 10, "官网确认"

    # 官网 partial_match
    if ws_status == 'partial_match':
        if li_status == 'confirmed':
            return 10, "官网部分匹配+LinkedIn确认"
        return 8, "官网部分匹配"

    # 官网 inaccessible 但 LinkedIn confirmed
    if ws_status == 'inaccessible' and li_status == 'confirmed':
        return 8, "官网不可访问+LinkedIn确认"

    # LinkedIn confirmed (单独)
    if li_status == 'confirmed':
        return 7, "LinkedIn确认(官网未确认)"

    # LinkedIn likely_match
    if li_status == 'likely_match':
        return 5, "LinkedIn可能匹配"

    # 外部证据不足
    if ws_status in ['inaccessible', 'search_required', 'unconfirmed']:
        return 2, f"外部证据不足({ws_status})"

    return 3, "经营状态未知"


def calculate_contactability_score(row: pd.Series) -> tuple:
    """
    计算触达可行性分数（10分）
    返回: (分数, 原因)
    """
    has_email = pd.notna(row.get('email')) and str(row.get('email', '')).strip() != ''
    has_phone = pd.notna(row.get('phone')) and str(row.get('phone', '')).strip() != ''
    has_website = pd.notna(row.get('website_input')) and str(row.get('website_input', '')).strip() != ''
    has_linkedin = pd.notna(row.get('linkedin')) and str(row.get('linkedin', '')).strip() != ''

    ws_email = pd.notna(row.get('website_contact_email')) and str(row.get('website_contact_email', '')).strip() != ''
    ws_phone = pd.notna(row.get('website_contact_phone')) and str(row.get('website_contact_phone', '')).strip() != ''
    li_url = pd.notna(row.get('linkedin_company_url')) and str(row.get('linkedin_company_url', '')).strip() != ''

    contact_count = sum([has_email, has_phone, has_website, has_linkedin, ws_email, ws_phone, li_url])

    if contact_count >= 4:
        return 10, f"多渠道可触达({contact_count}项)"
    elif contact_count >= 2:
        return 7, f"部分渠道可触达({contact_count}项)"
    elif contact_count >= 1:
        return 4, f"有限触达渠道({contact_count}项)"
    else:
        return 1, "触达信息缺失"


def calculate_reengagement_score(row: pd.Series) -> tuple:
    """
    计算复购/唤醒机会分数（10分）
    返回: (分数, 原因)
    """
    import_active = str(row.get('import_active_status', '')).lower()
    last_purchase = row.get('last_purchase_date')
    last_contact = row.get('last_contact_date')
    latest_import = row.get('latest_import_date')

    # 长期未采购但腾道显示仍有相关进口
    if import_active in ['active', 'recent']:
        if pd.notna(latest_import):
            return 9, "腾道显示活跃进口"
        return 8, "腾道显示近期进口"

    # 最近联系过但未采购
    if pd.notna(last_contact) and not pd.notna(last_purchase):
        return 6, "最近有联系但无采购"

    # 近期已采购
    if pd.notna(last_purchase):
        return 5, "近期有采购记录"

    # 无明显机会
    return 2, "无明显复购信号"


def determine_priority_level(score: int, row: pd.Series) -> str:
    """
    确定优先级等级
    """
    # 检查特殊状态
    match_status = str(row.get('match_status', '')).lower()
    customer_status = str(row.get('customer_status', '')).lower()
    manual_flag = str(row.get('manual_review_flag', '')).lower()

    # Excluded
    if match_status == 'excluded_internal_record' or customer_status in ['internal', '内部', 'non-customer']:
        return 'Excluded'

    # Manual Review
    if manual_flag == 'yes':
        return 'Manual_Review'

    # 冲突
    if match_status == 'conflict':
        return 'Manual_Review'

    # 基于分数
    if score >= 75:
        return 'A'
    elif score >= 60:
        return 'B'
    elif score >= 40:
        return 'C'
    else:
        return 'D'


def calculate_priority(row: pd.Series) -> dict:
    """
    计算客户优先级
    """
    # 计算各维度分数
    tendata_score, tendata_reason = calculate_tendata_score(row)
    product_score, product_reason = calculate_product_score(row)
    value_score, value_reason = calculate_customer_value_score(row)
    business_score, business_reason = calculate_business_status_score(row)
    contact_score, contact_reason = calculate_contactability_score(row)
    reengage_score, reengage_reason = calculate_reengagement_score(row)

    total_score = tendata_score + product_score + value_score + business_score + contact_score + reengage_score

    # 确定优先级
    priority_level = determine_priority_level(total_score, row)

    # 生成证据摘要
    evidence_parts = []
    if tendata_reason:
        evidence_parts.append(f"腾道: {tendata_reason}")
    if business_reason:
        evidence_parts.append(f"外部: {business_reason}")
    if value_reason and value_score > 0:
        evidence_parts.append(f"客户: {value_reason}")

    evidence_summary = "; ".join(evidence_parts[:3])

    # 生成推荐行动
    if priority_level == 'A':
        action = "优先跟进，建议电话+邮件触达"
    elif priority_level == 'B':
        action = "建议跟进，可先邮件了解需求"
    elif priority_level == 'C':
        action = "观察或补充核验，等待更多信号"
    elif priority_level == 'D':
        action = "暂不优先，定期检查"
    elif priority_level == 'Manual_Review':
        action = "需人工复核确认主体/匹配"
    else:
        action = "非目标客户，排除跟进"

    return {
        'priority_score': total_score,
        'priority_level': priority_level,
        'tendata_score': tendata_score,
        'product_score': product_score,
        'value_score': value_score,
        'business_score': business_score,
        'contact_score': contact_score,
        'reengage_score': reengage_score,
        'evidence_summary': evidence_summary,
        'calculated_recommended_action': action,
        'tendata_score_reason': tendata_reason,
        'product_score_reason': product_reason,
        'value_score_reason': value_reason,
        'business_score_reason': business_reason,
        'contact_score_reason': contact_reason,
        'reengage_score_reason': reengage_reason,
    }


def main():
    base_dir = Path(__file__).parent.parent

    print("="*70)
    print("生成最终客户优先级排序表")
    print("="*70)

    # 1. 读取输入文件
    print("\n1. 读取输入文件...")

    # 原始客户信息
    customers = pd.read_excel(base_dir / "external_all_customers_input.xlsx")
    print(f"   原始客户信息: {len(customers)} 行, {len(customers.columns)} 列")

    # 腾道结果
    tendata = pd.read_excel(base_dir / "tendata_all_merged.xlsx")
    print(f"   腾道结果: {len(tendata)} 行, {len(tendata.columns)} 列")

    # 外部核验结果
    xlsx = pd.ExcelFile(base_dir / "results_external" / "external_all_merged_cleaned.xlsx")
    external = pd.read_excel(xlsx, sheet_name='ALL_外部核验合并结果')
    print(f"   外部核验结果: {len(external)} 行, {len(external.columns)} 列")

    # 2. 合并数据
    print("\n2. 合并数据...")

    # 以 internal_customer_id 为主键
    # 从原始客户信息开始，删除会被外部核验覆盖的空列
    merged = customers.copy()

    # 删除原始数据中会被覆盖的空列
    cols_to_drop = [
        'website_match_status', 'website_business_status', 'website_product_relevance',
        'website_contact_found', 'website_contact_email', 'website_contact_phone',
        'website_evidence_url', 'website_evidence_summary', 'linkedin_company_found',
        'linkedin_company_url', 'linkedin_company_name', 'linkedin_clean_status',
        'linkedin_clean_reason', 'external_check_confidence', 'external_recommended_action',
        'manual_review_reason_external', 'external_check_summary'
    ]
    for col in cols_to_drop:
        if col in merged.columns:
            merged = merged.drop(columns=[col])

    # 合并腾道结果（选择关键列）
    tendata_cols = [
        'internal_customer_id', 'match_status', 'match_confidence', 'candidate_score',
        'latest_import_date', 'import_active_status', 'total_shipment_count',
        'supplier_count', 'china_supplier_signal', 'top_import_products',
        'product_relevance_level', 'import_frequency_level',
        'evidence_excerpt', 'analysis_data_status',
        'manual_review_flag', 'manual_review_reason'
    ]
    tendata_merge = tendata[[c for c in tendata_cols if c in tendata.columns]].copy()
    tendata_merge = tendata_merge.rename(columns={
        'match_status': 'tendata_match_status',
        'match_confidence': 'tendata_match_confidence',
        'manual_review_flag': 'tendata_manual_review_flag',
        'manual_review_reason': 'tendata_manual_review_reason',
    })

    merged = merged.merge(tendata_merge, on='internal_customer_id', how='left', suffixes=('', '_tendata'))
    print(f"   合并腾道后: {len(merged)} 行")

    # 合并外部核验结果（选择关键列）
    external_cols = [
        'internal_customer_id', 'website_match_status', 'website_business_status',
        'website_product_relevance', 'website_contact_found', 'website_contact_email',
        'website_contact_phone', 'website_evidence_url', 'website_evidence_summary',
        'linkedin_company_found', 'linkedin_company_url', 'linkedin_company_name',
        'linkedin_clean_status', 'linkedin_clean_reason', 'external_check_confidence',
        'manual_review_flag', 'manual_review_reason_external', 'external_recommended_action'
    ]
    external_merge = external[[c for c in external_cols if c in external.columns]].copy()
    external_merge = external_merge.rename(columns={
        'manual_review_flag': 'external_manual_review_flag',
    })

    merged = merged.merge(external_merge, on='internal_customer_id', how='left', suffixes=('', '_external'))
    print(f"   合并外部核验后: {len(merged)} 行")

    # 用外部核验结果覆盖原始空列
    for col in ['website_match_status', 'website_business_status', 'website_product_relevance',
                'website_contact_found', 'website_contact_email', 'website_contact_phone',
                'website_evidence_url', 'website_evidence_summary', 'linkedin_company_found',
                'linkedin_company_url', 'linkedin_company_name', 'linkedin_clean_status',
                'linkedin_clean_reason']:
        if col in merged.columns and f'{col}_external' in merged.columns:
            # 用外部核验结果填充
            merged[col] = merged[f'{col}_external'].combine_first(merged[col])
            merged = merged.drop(columns=[f'{col}_external'])

    # 3. 计算优先级
    print("\n3. 计算优先级...")

    results = []
    for idx, row in merged.iterrows():
        priority = calculate_priority(row)
        results.append(priority)

    priority_df = pd.DataFrame(results)
    merged = pd.concat([merged, priority_df], axis=1)

    # 4. 排序
    print("\n4. 排序...")
    merged = merged.sort_values(['priority_level', 'priority_score'], ascending=[True, False])

    # 添加排名
    merged['priority_rank'] = range(1, len(merged) + 1)

    # 5. 生成各分类sheet
    print("\n5. 生成分类sheet...")

    # 选择输出列
    output_cols = [
        'priority_rank', 'priority_level', 'priority_score', 'internal_customer_id',
        'customer_name', 'country_region', 'customer_status', 'customer_level',
        'last_purchase_date', 'last_contact_date', 'owner',
        'tendata_match_status', 'matched_company_name', 'latest_import_date',
        'import_active_status', 'total_shipment_count', 'product_relevance_level',
        'china_supplier_signal',
        'website_match_status', 'website_business_status', 'website_product_relevance',
        'website_evidence_url', 'website_evidence_summary',
        'linkedin_company_found', 'linkedin_company_name', 'linkedin_company_url',
        'linkedin_clean_status', 'linkedin_clean_reason',
        'contactability_score', 'final_recommended_action', 'evidence_summary',
        'manual_review_flag', 'followup_reason'
    ]

    # 重命名列
    if 'contact_score' in merged.columns:
        merged = merged.rename(columns={'contact_score': 'contactability_score'})
    if 'recommended_action' in merged.columns:
        # 删除原始的 recommended_action，使用计算的 final_recommended_action
        merged = merged.drop(columns=['recommended_action'])
    merged = merged.rename(columns={'calculated_recommended_action': 'final_recommended_action'})
    if 'manual_review_reason_external' in merged.columns:
        merged = merged.rename(columns={'manual_review_reason_external': 'followup_reason'})

    # 过滤存在的列
    output_cols = [c for c in output_cols if c in merged.columns]

    # 全量
    all_data = merged[output_cols].copy()

    # 各分类
    a_data = merged[merged['priority_level'] == 'A'][output_cols].copy()
    b_data = merged[merged['priority_level'] == 'B'][output_cols].copy()
    c_data = merged[merged['priority_level'] == 'C'][output_cols].copy()
    d_data = merged[merged['priority_level'] == 'D'][output_cols].copy()
    manual_data = merged[merged['priority_level'] == 'Manual_Review'][output_cols].copy()
    excluded_data = merged[merged['priority_level'] == 'Excluded'][output_cols].copy()

    print(f"   A_优先跟进: {len(a_data)} 行")
    print(f"   B_建议跟进: {len(b_data)} 行")
    print(f"   C_观察补充: {len(c_data)} 行")
    print(f"   D_暂不优先: {len(d_data)} 行")
    print(f"   Manual_Review: {len(manual_data)} 行")
    print(f"   Excluded: {len(excluded_data)} 行")

    # 6. 生成Summary
    print("\n6. 生成Summary...")

    summary_data = []

    # 总客户数
    summary_data.append({'统计项': '总客户数', '数值': len(merged), '占比': '100.0%'})

    # 各优先级数量
    level_counts = merged['priority_level'].value_counts()
    for level in ['A', 'B', 'C', 'D', 'Manual_Review', 'Excluded']:
        count = level_counts.get(level, 0)
        summary_data.append({
            '统计项': f'{level}级客户',
            '数值': count,
            '占比': f'{count/len(merged):.1%}'
        })

    summary_data.append({'统计项': '', '数值': '', '占比': ''})

    # 腾道强信号
    tendata_strong = len(merged[merged['tendata_match_status'].isin(['confirmed', 'likely_match'])])
    summary_data.append({'统计项': '腾道强信号客户', '数值': tendata_strong, '占比': f'{tendata_strong/len(merged):.1%}'})

    # 官网confirmed
    ws_confirmed = len(merged[merged['website_match_status'] == 'confirmed'])
    summary_data.append({'统计项': '官网confirmed客户', '数值': ws_confirmed, '占比': f'{ws_confirmed/len(merged):.1%}'})

    # LinkedIn confirmed
    li_confirmed = len(merged[merged['linkedin_clean_status'] == 'confirmed'])
    summary_data.append({'统计项': 'LinkedIn confirmed客户', '数值': li_confirmed, '占比': f'{li_confirmed/len(merged):.1%}'})

    # 三源均有信号
    three_source = len(merged[
        (merged['tendata_match_status'].isin(['confirmed', 'likely_match'])) &
        (merged['website_match_status'] == 'confirmed') &
        (merged['linkedin_clean_status'] == 'confirmed')
    ])
    summary_data.append({'统计项': '三源均有信号客户', '数值': three_source, '占比': f'{three_source/len(merged):.1%}'})

    # 需人工复核
    manual_count = len(merged[merged['manual_review_flag'] == 'yes']) + len(merged[merged['external_manual_review_flag'] == 'yes'])
    summary_data.append({'统计项': '需人工复核客户', '数值': manual_count, '占比': f'{manual_count/len(merged):.1%}'})

    summary_df = pd.DataFrame(summary_data)

    # 按负责人统计
    owner_stats = merged.groupby('owner').agg({
        'internal_customer_id': 'count',
        'priority_score': 'mean'
    }).round(1).reset_index()
    owner_stats.columns = ['负责人', '客户数', '平均分']
    owner_stats = owner_stats.sort_values('客户数', ascending=False)

    # 按国家统计
    country_stats = merged.groupby('country_region').agg({
        'internal_customer_id': 'count',
        'priority_score': 'mean'
    }).round(1).reset_index()
    country_stats.columns = ['国家', '客户数', '平均分']
    country_stats = country_stats.sort_values('客户数', ascending=False)

    # 7. 输出到Excel
    output_file = base_dir / "customer_priority_full_final.xlsx"
    print(f"\n7. 输出到: {output_file}")

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        all_data.to_excel(writer, sheet_name='全量优先级排序', index=False)
        a_data.to_excel(writer, sheet_name='A_优先跟进', index=False)
        b_data.to_excel(writer, sheet_name='B_建议跟进', index=False)
        c_data.to_excel(writer, sheet_name='C_观察补充', index=False)
        d_data.to_excel(writer, sheet_name='D_暂不优先', index=False)
        manual_data.to_excel(writer, sheet_name='Manual_Review', index=False)
        excluded_data.to_excel(writer, sheet_name='Excluded', index=False)
        summary_df.to_excel(writer, sheet_name='Summary', index=False)

        # 追加负责人和国家统计到Summary
        startrow = len(summary_df) + 3
        owner_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按负责人统计:')

        startrow = startrow + len(owner_stats) + 3
        country_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按国家统计:')

    print("\n完成!")
    print(f"输出文件: {output_file}")


if __name__ == "__main__":
    main()
