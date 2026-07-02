"""
generate_priority_v2.py — 生成最终全量客户优先级排序表 v2

改进：
1. priority_level 只允许 A/B/C/D/Excluded
2. 新增 manual_review_flag 和 priority_display
3. 增加评分拆解列
4. 为每个客户生成 ranking_reason
5. 为每个客户生成 customer_analysis
6. 生成 next_action
7. 生成 risk_notes
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

import pandas as pd
import numpy as np

# Force UTF-8 output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


def calculate_tendata_score(row: pd.Series) -> Tuple[int, str]:
    """
    计算腾道采购活跃度分数（25分）
    返回: (分数, 原因)
    """
    match_status = str(row.get('tendata_match_status', '')).lower()
    import_active = str(row.get('import_active_status', '')).lower()
    analysis_status = str(row.get('analysis_data_status', '')).lower()
    total_shipment = row.get('total_shipment_count', 0) or 0
    latest_import = row.get('latest_import_date')

    # 冲突记录
    if match_status == 'conflict':
        return 0, "腾道主体冲突"

    # excluded_internal_record
    if match_status == 'excluded_internal_record':
        return 0, "内部排除记录"

    # confirmed/likely_match 且 import_active=active
    if match_status in ['confirmed', 'likely_match'] and import_active == 'active':
        if total_shipment >= 50:
            return 25, "腾道确认+活跃采购(高频)"
        elif total_shipment >= 20:
            return 22, "腾道确认+活跃采购(中频)"
        else:
            return 20, "腾道确认+活跃采购"

    # import_active=recent
    if import_active == 'recent':
        if total_shipment >= 10:
            return 18, "腾道确认+近期采购"
        else:
            return 14, "腾道确认+近期有采购"

    # 主体确认但无活跃采购
    if match_status in ['confirmed', 'likely_match']:
        if analysis_status == 'partial_import_signal':
            return 10, "腾道确认+部分进口信号"
        elif analysis_status == 'no_import_analysis_data':
            return 6, "腾道确认+无进口数据"
        else:
            return 12, "腾道确认"

    # candidate_found_not_entered / no_result / unknown
    if match_status in ['candidate_found_not_entered', 'no_result', 'unknown']:
        return 3, f"腾道信号弱({match_status})"

    return 3, "腾道无有效信号"


def calculate_product_score(row: pd.Series) -> Tuple[int, str]:
    """
    计算产品相关度分数（20分）
    """
    tendata_rel = str(row.get('product_relevance_level', '')).lower()
    website_rel = str(row.get('website_product_relevance', '')).lower()

    high_rel = ['high', '高度相关', 'highly_relevant']
    medium_rel = ['medium', '中等相关', 'moderately_relevant', 'partial']
    low_rel = ['low', '低相关', 'low_relevance']

    if tendata_rel in high_rel or website_rel in high_rel:
        return 18, "产品高度相关"

    if tendata_rel in medium_rel or website_rel in medium_rel:
        return 12, "产品中等相关"

    if tendata_rel in low_rel or website_rel in low_rel:
        return 5, "产品低相关"

    if pd.notna(row.get('product_keywords')) and str(row.get('product_keywords', '')).strip():
        return 8, "有产品关键词"

    return 3, "产品相关度未知"


def calculate_history_score(row: pd.Series) -> Tuple[int, str]:
    """
    计算历史客户价值分数（20分）
    """
    customer_level = str(row.get('customer_level', '')).lower()
    customer_status = str(row.get('customer_status', '')).lower()
    last_purchase = row.get('last_purchase_date')
    last_contact = row.get('last_contact_date')

    # 高价值客户
    if customer_level in ['a', '高', 'vip', 'key']:
        if pd.notna(last_purchase):
            return 18, "高价值客户+有采购"
        return 15, "高价值客户"

    # 已成交客户
    if customer_status in ['active', '活跃', '成交', '已成交']:
        if pd.notna(last_purchase):
            return 16, "已成交客户+有采购记录"
        return 12, "已成交客户"

    # 潜在客户
    if customer_status in ['potential', '潜在', 'prospect']:
        if pd.notna(last_contact):
            return 10, "潜在客户+有联系"
        return 7, "潜在客户"

    # 有采购记录
    if pd.notna(last_purchase):
        return 10, "有采购记录"

    # 有联系记录
    if pd.notna(last_contact):
        return 6, "有联系记录"

    return 3, "客户价值不明"


def calculate_business_status_score(row: pd.Series) -> Tuple[int, str]:
    """
    计算当前经营状态分数（15分）
    """
    ws_status = str(row.get('website_match_status', '')).lower()
    ws_business = str(row.get('website_business_status', '')).lower()
    li_status = str(row.get('linkedin_clean_status', '')).lower()

    # 官网 confirmed 且 business_status=active
    if ws_status == 'confirmed' and ws_business in ['active', '活跃', 'operating']:
        if li_status == 'confirmed':
            return 15, "官网+LinkedIn双确认+经营活跃"
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
        return 7, "官网部分匹配"

    # 官网 inaccessible 但 LinkedIn confirmed
    if ws_status == 'inaccessible' and li_status == 'confirmed':
        return 8, "官网不可访问+LinkedIn确认"

    # LinkedIn confirmed (单独)
    if li_status == 'confirmed':
        return 6, "LinkedIn确认"

    # LinkedIn likely_match
    if li_status == 'likely_match':
        return 4, "LinkedIn可能匹配"

    # 外部证据不足
    if ws_status in ['inaccessible', 'search_required', 'unconfirmed']:
        return 2, f"外部证据不足({ws_status})"

    return 3, "经营状态未知"


def calculate_contactability_score(row: pd.Series) -> Tuple[int, str]:
    """
    计算触达可行性分数（10分）
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


def calculate_reactivation_score(row: pd.Series) -> Tuple[int, str]:
    """
    计算复购/唤醒机会分数（10分）
    """
    import_active = str(row.get('import_active_status', '')).lower()
    last_purchase = row.get('last_purchase_date')
    last_contact = row.get('last_contact_date')
    latest_import = row.get('latest_import_date')

    if import_active in ['active', 'recent']:
        if pd.notna(latest_import):
            return 9, "腾道显示活跃进口"
        return 8, "腾道显示近期进口"

    if pd.notna(last_contact) and not pd.notna(last_purchase):
        return 6, "最近有联系但无采购"

    if pd.notna(last_purchase):
        return 5, "近期有采购记录"

    return 2, "无明显复购信号"


def calculate_risk_penalty(row: pd.Series) -> Tuple[int, str]:
    """
    计算风险扣分（0 到 -20）
    """
    penalties = []
    total_penalty = 0

    match_status = str(row.get('tendata_match_status', '')).lower()
    ws_status = str(row.get('website_match_status', '')).lower()
    li_status = str(row.get('linkedin_clean_status', '')).lower()
    li_found = str(row.get('linkedin_company_found', '')).lower()

    # 腾道 likely_match 需确认主体
    if match_status == 'likely_match':
        penalties.append("腾道likely_match需确认主体")
        total_penalty -= 3

    # 腾道 conflict
    if match_status == 'conflict':
        penalties.append("腾道主体冲突")
        total_penalty -= 5

    # 官网不可访问
    if ws_status == 'inaccessible':
        penalties.append("官网不可访问")
        total_penalty -= 3

    # LinkedIn未确认
    if li_status in ['no_match', 'uncertain'] and li_found == 'yes':
        penalties.append("LinkedIn未确认")
        total_penalty -= 2

    # LinkedIn疑似错配
    li_reason = str(row.get('linkedin_clean_reason', ''))
    if '错配' in li_reason:
        penalties.append("LinkedIn疑似错配")
        total_penalty -= 3

    # 官网仅 partial_match
    if ws_status == 'partial_match':
        penalties.append("官网仅部分匹配")
        total_penalty -= 2

    # 产品相关度 unknown
    prod_rel = str(row.get('product_relevance_level', '')).lower()
    if prod_rel in ['unknown', '']:
        penalties.append("产品相关度未知")
        total_penalty -= 2

    # 客户名称含备注
    customer_name = str(row.get('customer_name', ''))
    if any(x in customer_name for x in ['(', '（', '-', '—', '备注', '待确认']):
        penalties.append("客户名称需清洗")
        total_penalty -= 1

    # 限制最大扣分
    total_penalty = max(total_penalty, -20)

    if not penalties:
        return 0, "无明显风险"

    return total_penalty, "; ".join(penalties[:3])


def determine_priority_level(score: int, row: pd.Series) -> Tuple[str, bool, str, str]:
    """
    确定优先级等级
    返回: (priority_level, manual_review_flag, review_type, review_reason_short)
    """
    match_status = str(row.get('tendata_match_status', '')).lower()
    customer_status = str(row.get('customer_status', '')).lower()
    ws_status = str(row.get('website_match_status', '')).lower()
    li_status = str(row.get('linkedin_clean_status', '')).lower()

    # Excluded
    if match_status == 'excluded_internal_record' or customer_status in ['internal', '内部', 'non-customer']:
        return 'Excluded', False, '', '内部/非客户记录'

    # 判断是否需要人工复核
    need_review = False
    review_type = ''
    review_reason = ''

    # 主体需确认
    if match_status == 'likely_match':
        need_review = True
        review_type = '主体确认'
        review_reason = '腾道likely_match需确认主体'

    # 主体冲突
    if match_status == 'conflict':
        need_review = True
        review_type = '主体冲突'
        review_reason = '腾道显示主体冲突'

    # LinkedIn错配
    li_reason = str(row.get('linkedin_clean_reason', ''))
    if '错配' in li_reason:
        need_review = True
        review_type = 'LinkedIn错配'
        review_reason = 'LinkedIn疑似错配'

    # 官网不可访问且LinkedIn未确认
    if ws_status == 'inaccessible' and li_status != 'confirmed':
        need_review = True
        review_type = '外部核验不足'
        review_reason = '官网不可访问且LinkedIn未确认'

    # 基于分数确定等级
    if score >= 75:
        level = 'A'
    elif score >= 60:
        level = 'B'
    elif score >= 40:
        level = 'C'
    else:
        level = 'D'

    return level, need_review, review_type, review_reason


def generate_ranking_reason(row: pd.Series, level: str, need_review: bool) -> str:
    """
    生成排名原因（一句话）
    """
    match_status = str(row.get('tendata_match_status', '')).lower()
    import_active = str(row.get('import_active_status', '')).lower()
    ws_status = str(row.get('website_match_status', '')).lower()
    li_status = str(row.get('linkedin_clean_status', '')).lower()
    prod_rel = str(row.get('product_relevance_level', '')).lower()
    customer_status = str(row.get('customer_status', '')).lower()
    last_purchase = row.get('last_purchase_date')

    reasons = []

    # 腾道情况
    if match_status == 'confirmed' and import_active in ['active', 'recent']:
        reasons.append("腾道确认且有活跃采购")
    elif match_status == 'confirmed':
        reasons.append("腾道确认主体")
    elif match_status == 'likely_match':
        reasons.append("腾道可能匹配(待确认)")
    elif match_status in ['no_result', 'unknown']:
        reasons.append("腾道无有效信号")

    # 产品相关度
    if prod_rel in ['high', '高度相关']:
        reasons.append("产品高度相关")
    elif prod_rel in ['medium', '中等相关']:
        reasons.append("产品中等相关")

    # 外部核验
    if ws_status == 'confirmed' and li_status == 'confirmed':
        reasons.append("官网和LinkedIn均确认")
    elif ws_status == 'confirmed':
        reasons.append("官网确认")
    elif li_status == 'confirmed':
        reasons.append("LinkedIn确认")
    elif ws_status == 'inaccessible':
        reasons.append("官网不可访问")

    # 历史客户
    if customer_status in ['active', '活跃', '成交', '已成交']:
        reasons.append("历史成交客户")
    elif pd.notna(last_purchase):
        reasons.append("有采购记录")

    # 生成结论
    if level == 'Excluded':
        return "内部费用/非客户记录，排除跟进。"

    if need_review:
        reasons.append(f"需人工复核，因此列为{level}-需复核")
    else:
        if level == 'A':
            reasons.append("综合条件优秀，列为A优先跟进")
        elif level == 'B':
            reasons.append("有一定价值，列为B建议跟进")
        elif level == 'C':
            reasons.append("价值一般，列为C观察客户")
        else:
            reasons.append("当前跟进价值低，列为D")

    return "，".join(reasons) + "。"


def generate_customer_analysis(row: pd.Series) -> str:
    """
    生成客户分析（2-4句）
    """
    parts = []

    # 1. 腾道情况
    match_status = str(row.get('tendata_match_status', '')).lower()
    import_active = str(row.get('import_active_status', '')).lower()
    prod_rel = str(row.get('product_relevance_level', '')).lower()
    total_shipment = row.get('total_shipment_count', 0) or 0
    latest_import = row.get('latest_import_date')
    china_signal = str(row.get('china_supplier_signal', '')).lower()

    tendata_part = ""
    if match_status == 'confirmed':
        tendata_part = "腾道已确认主体"
        if import_active == 'active':
            tendata_part += f"，显示活跃采购({total_shipment}船次)"
        elif import_active == 'recent':
            tendata_part += "，近期有采购记录"
    elif match_status == 'likely_match':
        tendata_part = "腾道显示可能匹配，主体待确认"
    elif match_status == 'conflict':
        tendata_part = "腾道显示主体冲突，需人工核查"
    elif match_status in ['no_result', 'unknown']:
        tendata_part = "腾道未找到有效采购信号"
    else:
        tendata_part = f"腾道状态: {match_status}"

    if prod_rel in ['high', '高度相关']:
        tendata_part += "，产品高度相关"
    elif prod_rel in ['medium', '中等相关']:
        tendata_part += "，产品中等相关"

    if china_signal in ['yes', '有', '强']:
        tendata_part += "，有中国供应商"
    elif china_signal in ['weak', '弱']:
        tendata_part += "，部分中国供应商"

    parts.append(tendata_part + "。")

    # 2. 官网情况
    ws_status = str(row.get('website_match_status', '')).lower()
    ws_business = str(row.get('website_business_status', '')).lower()
    ws_url = row.get('website_evidence_url', '')

    ws_part = ""
    if ws_status == 'confirmed':
        ws_part = "官网确认可访问"
        if ws_business in ['active', '活跃', 'operating']:
            ws_part += "且显示经营活跃"
    elif ws_status == 'partial_match':
        ws_part = "官网部分匹配"
    elif ws_status == 'inaccessible':
        ws_part = "官网不可访问"
    elif ws_status == 'search_required':
        ws_part = "需进一步搜索官网"
    else:
        ws_part = f"官网状态: {ws_status}"

    parts.append(ws_part + "。")

    # 3. LinkedIn情况
    li_found = str(row.get('linkedin_company_found', '')).lower()
    li_status = str(row.get('linkedin_clean_status', '')).lower()
    li_name = row.get('linkedin_company_name', '')

    li_part = ""
    if li_status == 'confirmed':
        li_part = f"LinkedIn确认找到公司页({li_name})"
    elif li_status == 'likely_match':
        li_part = "LinkedIn显示可能匹配"
    elif li_status == 'no_match':
        li_part = "LinkedIn未找到匹配公司"
    elif li_found == 'no':
        li_part = "LinkedIn未找到公司页"
    else:
        li_part = f"LinkedIn状态: {li_status}"

    parts.append(li_part + "。")

    # 4. 历史客户情况
    customer_status = str(row.get('customer_status', '')).lower()
    customer_level = str(row.get('customer_level', '')).lower()
    last_purchase = row.get('last_purchase_date')
    last_contact = row.get('last_contact_date')
    owner = row.get('owner', '')

    history_part = ""
    if customer_status in ['active', '活跃', '成交', '已成交']:
        history_part = "系统标记为已成交客户"
        if pd.notna(last_purchase):
            history_part += f"，最后采购: {str(last_purchase)[:10]}"
    elif customer_status in ['potential', '潜在', 'prospect']:
        history_part = "潜在客户"
        if pd.notna(last_contact):
            history_part += f"，最后联系: {str(last_contact)[:10]}"
    else:
        history_part = "客户状态: " + (customer_status if customer_status else "未知")

    if customer_level in ['a', '高', 'vip', 'key']:
        history_part += "(高等级客户)"

    parts.append(history_part + "。")

    return " ".join(parts)


def generate_next_action(level: str, need_review: bool, row: pd.Series) -> str:
    """
    生成下一步行动
    """
    owner = row.get('owner', '')
    owner_str = f"由{owner}" if pd.notna(owner) and owner else "由负责人"

    if level == 'Excluded':
        return "非客户记录，不纳入跟进。"

    if level == 'A':
        if need_review:
            return f"先人工确认主体/官网/LinkedIn，再{owner_str}电话+邮件优先触达。"
        return f"优先{owner_str}电话+邮件触达，确认近期采购计划和在供供应商情况。"

    if level == 'B':
        if need_review:
            return f"先补充核验主体信息，再{owner_str}邮件触达询问需求。"
        return f"{owner_str}邮件触达，询问当前项目和采购需求。"

    if level == 'C':
        return f"补充官网/联系人信息，暂作观察或低频触达。"

    # D
    return f"暂不主动投入销售资源，后续定期复查。"


def generate_priority_display(level: str, need_review: bool) -> str:
    """
    生成 priority_display
    """
    if level == 'Excluded':
        return '排除'

    if need_review:
        return f'{level}-需复核'

    return level


def main():
    base_dir = Path(__file__).parent.parent

    print("="*70)
    print("生成最终客户优先级排序表 v2")
    print("="*70)

    # 1. 读取输入文件
    print("\n1. 读取输入文件...")

    # 原始客户信息
    customers = pd.read_excel(base_dir / "external_all_customers_input.xlsx")
    print(f"   原始客户信息: {len(customers)} 行")

    # 腾道结果
    tendata = pd.read_excel(base_dir / "tendata_all_merged.xlsx")
    print(f"   腾道结果: {len(tendata)} 行")

    # 外部核验结果
    xlsx = pd.ExcelFile(base_dir / "results_external" / "external_all_merged_cleaned.xlsx")
    external = pd.read_excel(xlsx, sheet_name='ALL_外部核验合并结果')
    print(f"   外部核验结果: {len(external)} 行")

    # 2. 合并数据
    print("\n2. 合并数据...")

    merged = customers.copy()

    # 删除会被腾道覆盖的列（保留原始客户信息）
    tendata_overlap_cols = [
        'match_status', 'matched_company_name', 'matched_country', 'match_confidence',
        'analysis_data_status', 'latest_import_date', 'last_12m_import_count',
        'last_24m_import_count', 'last_36m_import_count', 'total_shipment_count',
        'supplier_count', 'top_suppliers', 'china_supplier_signal', 'top_import_products',
        'related_hs_codes', 'product_relevance_level', 'product_relevance_score',
        'import_active_status', 'import_frequency_level', 'candidate_summary_json',
        'manual_review_flag', 'manual_review_reason', 'recommended_action', 'evidence_excerpt'
    ]
    for col in tendata_overlap_cols:
        if col in merged.columns:
            merged = merged.drop(columns=[col])

    # 删除会被外部核验覆盖的列
    external_overlap_cols = [
        'website_match_status', 'website_business_status', 'website_product_relevance',
        'website_contact_found', 'website_contact_email', 'website_contact_phone',
        'website_evidence_url', 'website_evidence_summary', 'linkedin_company_found',
        'linkedin_company_url', 'linkedin_company_name', 'linkedin_clean_status',
        'linkedin_clean_reason', 'external_check_confidence', 'external_recommended_action',
        'manual_review_reason_external', 'external_check_summary', 'website_accessible'
    ]
    for col in external_overlap_cols:
        if col in merged.columns:
            merged = merged.drop(columns=[col])

    # 合并腾道结果
    tendata_cols = [
        'internal_customer_id', 'match_status', 'match_confidence', 'candidate_score',
        'latest_import_date', 'import_active_status', 'total_shipment_count',
        'supplier_count', 'china_supplier_signal', 'top_import_products',
        'product_relevance_level', 'import_frequency_level',
        'evidence_excerpt', 'analysis_data_status'
    ]
    tendata_merge = tendata[[c for c in tendata_cols if c in tendata.columns]].copy()
    tendata_merge = tendata_merge.rename(columns={
        'match_status': 'tendata_match_status',
        'match_confidence': 'tendata_match_confidence',
    })
    merged = merged.merge(tendata_merge, on='internal_customer_id', how='left')
    print(f"   合并腾道后: {len(merged)} 行")

    # 合并外部核验结果
    external_cols = [
        'internal_customer_id', 'website_match_status', 'website_business_status',
        'website_product_relevance', 'website_contact_found', 'website_contact_email',
        'website_contact_phone', 'website_evidence_url', 'website_evidence_summary',
        'linkedin_company_found', 'linkedin_company_url', 'linkedin_company_name',
        'linkedin_clean_status', 'linkedin_clean_reason', 'external_check_confidence'
    ]
    external_merge = external[[c for c in external_cols if c in external.columns]].copy()
    merged = merged.merge(external_merge, on='internal_customer_id', how='left', suffixes=('', '_external'))
    print(f"   合并外部核验后: {len(merged)} 行")

    # 3. 计算评分和优先级
    print("\n3. 计算评分和优先级...")

    results = []
    for idx, row in merged.iterrows():
        # 计算各维度分数
        tendata_score, tendata_reason = calculate_tendata_score(row)
        product_score, product_reason = calculate_product_score(row)
        history_score, history_reason = calculate_history_score(row)
        business_score, business_reason = calculate_business_status_score(row)
        contact_score, contact_reason = calculate_contactability_score(row)
        reactivation_score, reactivation_reason = calculate_reactivation_score(row)
        risk_penalty, risk_notes = calculate_risk_penalty(row)

        # 计算总分
        base_score = tendata_score + product_score + history_score + business_score + contact_score + reactivation_score
        final_score = max(0, min(100, base_score + risk_penalty))

        # 确定优先级
        level, need_review, review_type, review_reason = determine_priority_level(final_score, row)

        # 生成分析字段
        priority_display = generate_priority_display(level, need_review)
        ranking_reason = generate_ranking_reason(row, level, need_review)
        customer_analysis = generate_customer_analysis(row)
        next_action = generate_next_action(level, need_review, row)

        results.append({
            'tendata_score': tendata_score,
            'product_score': product_score,
            'history_score': history_score,
            'business_status_score': business_score,
            'contactability_score': contact_score,
            'reactivation_score': reactivation_score,
            'risk_penalty': risk_penalty,
            'final_score_before_review': base_score,
            'priority_score': final_score,
            'priority_level': level,
            'priority_display': priority_display,
            'manual_review_flag': 'yes' if need_review else 'no',
            'review_type': review_type,
            'review_reason_short': review_reason,
            'ranking_reason': ranking_reason,
            'customer_analysis': customer_analysis,
            'next_action': next_action,
            'risk_notes': risk_notes,
        })

    results_df = pd.DataFrame(results)
    merged = merged.reset_index(drop=True)
    merged = pd.concat([merged, results_df], axis=1)

    # 4. 排序
    print("\n4. 排序...")
    merged = merged.sort_values(['priority_level', 'priority_score'], ascending=[True, False])
    merged = merged.reset_index(drop=True)
    merged['priority_rank'] = range(1, len(merged) + 1)

    # 5. 生成各分类sheet
    print("\n5. 生成分类sheet...")

    # 选择输出列并重命名为中文
    output_cols_mapping = {
        'priority_rank': '排名',
        'priority_level': '优先级',
        'priority_display': '优先级显示',
        'priority_score': '总分',
        'internal_customer_id': '客户ID',
        'customer_name': '客户名称',
        'country_region': '国家/地区',
        'customer_status': '客户状态',
        'customer_level': '客户等级',
        'last_purchase_date': '最近采购日期',
        'last_contact_date': '最近联系日期',
        'owner': '负责人',
        'tendata_score': '腾道得分',
        'product_score': '产品得分',
        'history_score': '历史得分',
        'business_status_score': '经营状态得分',
        'contactability_score': '触达得分',
        'reactivation_score': '复购得分',
        'risk_penalty': '风险扣分',
        'tendata_match_status': '腾道匹配状态',
        'matched_company_name': '匹配公司名',
        'latest_import_date': '最近进口日期',
        'import_active_status': '进口活跃状态',
        'total_shipment_count': '总船次',
        'product_relevance_level': '产品相关度',
        'china_supplier_signal': '中国供应商信号',
        'website_match_status': '官网匹配状态',
        'website_evidence_url': '官网证据URL',
        'website_evidence_summary': '官网证据摘要',
        'linkedin_company_found': 'LinkedIn找到',
        'linkedin_company_name': 'LinkedIn公司名',
        'linkedin_company_url': 'LinkedIn URL',
        'linkedin_clean_status': 'LinkedIn状态',
        'linkedin_clean_reason': 'LinkedIn原因',
        'ranking_reason': '排名原因',
        'customer_analysis': '客户分析',
        'next_action': '下一步行动',
        'risk_notes': '风险说明',
        'manual_review_flag': '需人工复核',
        'review_type': '复核类型',
        'review_reason_short': '复核原因'
    }
    output_cols = [c for c in output_cols_mapping.keys() if c in merged.columns]

    # 重命名列为中文
    merged = merged.rename(columns=output_cols_mapping)

    # 中文列名列表
    chinese_cols = [output_cols_mapping[c] for c in output_cols]

    # 全量
    all_data = merged[chinese_cols].copy()

    # 各分类 (A/B/C/D) - 使用中文列名
    a_data = merged[merged['优先级'] == 'A'][chinese_cols].copy()
    b_data = merged[merged['优先级'] == 'B'][chinese_cols].copy()
    c_data = merged[merged['优先级'] == 'C'][chinese_cols].copy()
    d_data = merged[merged['优先级'] == 'D'][chinese_cols].copy()

    # Manual_Review (需人工复核=yes)
    manual_data = merged[merged['需人工复核'] == 'yes'][chinese_cols].copy()

    # Excluded
    excluded_data = merged[merged['优先级'] == 'Excluded'][chinese_cols].copy()

    print(f"   A_优先跟进: {len(a_data)} 行")
    print(f"   B_建议跟进: {len(b_data)} 行")
    print(f"   C_观察补充: {len(c_data)} 行")
    print(f"   D_暂不优先: {len(d_data)} 行")
    print(f"   Manual_Review: {len(manual_data)} 行")
    print(f"   Excluded: {len(excluded_data)} 行")

    # 6. 生成Summary
    print("\n6. 生成Summary...")

    summary_data = []
    total = len(merged)

    summary_data.append({'统计项': '总客户数', '数值': total, '占比': '100.0%'})
    summary_data.append({'统计项': '', '数值': '', '占比': ''})

    # 各优先级数量
    summary_data.append({'统计项': '--- 优先级分布 ---', '数值': '', '占比': ''})
    for level in ['A', 'B', 'C', 'D', 'Excluded']:
        count = len(merged[merged['优先级'] == level])
        need_review_count = len(merged[(merged['优先级'] == level) & (merged['需人工复核'] == 'yes')])
        summary_data.append({
            '统计项': f'{level}级客户',
            '数值': count,
            '占比': f'{count/total:.1%}'
        })
        if need_review_count > 0 and level != 'Excluded':
            summary_data.append({
                '统计项': f'  其中{level}-需复核',
                '数值': need_review_count,
                '占比': f'{need_review_count/total:.1%}'
            })

    summary_data.append({'统计项': '', '数值': '', '占比': ''})
    summary_data.append({'统计项': '--- 核验状态 ---', '数值': '', '占比': ''})

    # 腾道状态
    for status in ['confirmed', 'likely_match', 'conflict', 'no_result', 'unknown']:
        count = len(merged[merged['腾道匹配状态'] == status])
        if count > 0:
            summary_data.append({'统计项': f'腾道{status}', '数值': count, '占比': f'{count/total:.1%}'})

    # 官网状态
    for status in ['confirmed', 'partial_match', 'inaccessible', 'search_required']:
        count = len(merged[merged['官网匹配状态'] == status])
        if count > 0:
            summary_data.append({'统计项': f'官网{status}', '数值': count, '占比': f'{count/total:.1%}'})

    # LinkedIn状态
    for status in ['confirmed', 'likely_match', 'no_match', 'uncertain']:
        count = len(merged[merged['LinkedIn状态'] == status])
        if count > 0:
            summary_data.append({'统计项': f'LinkedIn {status}', '数值': count, '占比': f'{count/total:.1%}'})

    summary_df = pd.DataFrame(summary_data)

    # 按负责人统计
    owner_stats = merged.groupby('负责人').agg({
        '客户ID': 'count',
        '总分': 'mean'
    }).round(1).reset_index()
    owner_stats.columns = ['负责人', '客户数', '平均分']
    owner_stats = owner_stats.sort_values('客户数', ascending=False)

    # 按国家统计
    country_stats = merged.groupby('国家/地区').agg({
        '客户ID': 'count',
        '总分': 'mean'
    }).round(1).reset_index()
    country_stats.columns = ['国家', '客户数', '平均分']
    country_stats = country_stats.sort_values('客户数', ascending=False)

    # 按负责人统计A/B/C/D
    owner_level_stats = merged.pivot_table(
        index='负责人',
        columns='优先级',
        values='客户ID',
        aggfunc='count',
        fill_value=0
    ).reset_index()

    # 按国家统计A/B/C/D
    country_level_stats = merged.pivot_table(
        index='国家/地区',
        columns='优先级',
        values='客户ID',
        aggfunc='count',
        fill_value=0
    ).reset_index()

    # 7. 输出到Excel
    output_file = base_dir / "customer_priority_full_final_v2.xlsx"
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

        # 追加统计表
        startrow = len(summary_df) + 3
        owner_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按负责人统计:')

        startrow = startrow + len(owner_stats) + 3
        country_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按国家统计:')

        startrow = startrow + len(country_stats) + 3
        owner_level_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按负责人统计A/B/C/D:')

        startrow = startrow + len(owner_level_stats) + 3
        country_level_stats.to_excel(writer, sheet_name='Summary', index=False, startrow=startrow)
        writer.sheets['Summary'].cell(row=startrow, column=1, value='按国家统计A/B/C/D:')

    print("\n完成!")
    print(f"输出文件: {output_file}")

    # 8. 质量检查
    print("\n8. 质量检查...")

    # 检查总行数
    assert len(all_data) == 456, f"总行数不正确: {len(all_data)}"

    # 检查 优先级 不包含 Manual_Review
    invalid_levels = all_data[~all_data['优先级'].isin(['A', 'B', 'C', 'D', 'Excluded'])]
    assert len(invalid_levels) == 0, f"发现无效优先级: {invalid_levels['优先级'].unique()}"

    # 检查必填字段
    for col in ['排名原因', '客户分析', '下一步行动', '风险说明']:
        empty_count = all_data[all_data[col].isna() | (all_data[col] == '')].shape[0]
        assert empty_count == 0, f"{col} 有 {empty_count} 条空值"

    print("   所有质量检查通过")


if __name__ == "__main__":
    main()
