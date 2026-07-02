"""
multi_source_scorer.py — 七维度打分引擎

总分 = 源一致性(0-15) + 产品匹配(0-25) + 采购意愿(0-25)
     + 公司可信度(0-15) + 联系方式(0-10) + 行业规模(0-5)
     + 风险扣分(-20 ~ 0)

最终 clamped to [0, 100], 映射到 A/B/C/D 四级
"""

from __future__ import annotations

import re
import sys
import yaml
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models import (
    LeadQualificationResult,
    GoogleSourceSignals,
    LinkedInSourceSignals,
    TendataSourceSignals,
)


# ============================================================================
# 配置加载
# ============================================================================

def _load_config():
    config_path = Path(__file__).parent / "config" / "scoring_weights.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}

CONFIG = _load_config()
SCORING = CONFIG.get("scoring", {})
TIERS = CONFIG.get("tiers", {})

# 默认分级
DEFAULT_TIERS = {
    "A": {"min_score": 75, "label": "优先跟进"},
    "B": {"min_score": 60, "label": "列入跟进"},
    "C": {"min_score": 40, "label": "人工复核"},
    "D": {"min_score": 0, "label": "暂不跟进"},
}


# ============================================================================
# 打分函数
# ============================================================================

def _score_source_agreement(google: GoogleSourceSignals,
                            linkedin: LinkedInSourceSignals,
                            tendata: TendataSourceSignals) -> tuple[int, str]:
    """源一致性 (0-15): 三源对公司名的确认程度"""
    sources_found = sum([
        google.company_found and google.error != "disabled",
        linkedin.company_page_found and linkedin.error != "disabled",
        tendata.found and tendata.error != "disabled",
    ])

    # 取已找到源的公司名做对比
    names = []
    if google.company_found and google.official_website:
        names.append("google")
    if linkedin.company_page_found and linkedin.company_name_on_li:
        names.append("linkedin")
    if tendata.found and tendata.matched_company_name:
        names.append("tendata")

    # 简单的源数量评分
    if sources_found >= 3:
        return (15, "三源均找到公司信息")
    elif sources_found == 2:
        return (10, "两源找到公司信息")
    elif sources_found == 1:
        return (5, "仅单源找到公司信息")
    else:
        return (0, "无源找到公司信息")


def _score_product_fit(google: GoogleSourceSignals,
                       linkedin: LinkedInSourceSignals,
                       tendata: TendataSourceSignals) -> tuple[int, str]:
    """产品匹配 (0-25): 公司业务是否匹配不锈钢管件"""
    score = 0
    reasons = []

    # Google: 网站含产品关键词
    product_kws = google.product_keywords_found or []
    high_match = [k for k in product_kws if any(
        h in k.lower() for h in ["pipe", "tube", "fitting", "flange", "steel"])]
    if high_match:
        score += 10
        reasons.append(f"网站含产品关键词: {', '.join(high_match[:3])}")
    elif product_kws:
        score += 5
        reasons.append(f"网站含相关关键词: {', '.join(product_kws[:3])}")

    # LinkedIn: 行业标签匹配
    li_tags = [t.lower() for t in linkedin.industry_tags]
    metal_tags = [t for t in li_tags if any(
        m in t for m in ["steel", "metal", "manufacturing", "fabrication"])]
    semi_tags = [t for t in li_tags if any(
        m in t for m in ["semiconductor", "wafer", "electronics", "equipment"])]

    if metal_tags:
        score += 5
        reasons.append(f"LinkedIn行业: {', '.join(metal_tags[:3])}")
    if semi_tags:
        score += 3
        reasons.append(f"半导体相关: {', '.join(semi_tags[:2])}")

    # Tendata: HS 编码匹配
    target_hs = ["730640", "730641", "730723", "730661", "730721", "730729"]
    hs_codes = [str(h) for h in tendata.related_hs_codes]
    matched_hs = [h for h in hs_codes if any(h.startswith(t) for t in target_hs)]
    if matched_hs:
        score += 7
        reasons.append(f"海关HS匹配: {', '.join(matched_hs[:3])}")
    elif tendata.found:
        score += 2
        reasons.append("海关有进口记录但HS未精确匹配")

    # 多源加成
    signals = 0
    if high_match or product_kws:
        signals += 1
    if metal_tags or semi_tags:
        signals += 1
    if matched_hs:
        signals += 1
    if signals >= 2:
        score += 3
        reasons.append("多源交叉确认产品匹配")

    score = min(score, 25)
    return (score, "; ".join(reasons) if reasons else "未找到明确产品匹配信号")


def _score_purchase_intent(google: GoogleSourceSignals,
                           linkedin: LinkedInSourceSignals,
                           tendata: TendataSourceSignals) -> tuple[int, str]:
    """采购意愿 (0-25)"""
    score = 0
    reasons = []

    shipments = tendata.total_shipments_12m
    if shipments >= 50:
        score += 18
        reasons.append(f"12月进口≥50次 ({shipments}次)")
    elif shipments >= 20:
        score += 13
        reasons.append(f"12月进口≥20次 ({shipments}次)")
    elif shipments >= 5:
        score += 8
        reasons.append(f"12月进口≥5次 ({shipments}次)")
    elif shipments > 0:
        score += 3
        reasons.append(f"有少量进口记录 ({shipments}次)")

    if tendata.has_chinese_supplier:
        score += 5
        reasons.append("有中国供应商")

    # 最近进口
    if tendata.latest_import_date:
        try:
            from datetime import datetime, timedelta
            date_str = str(tendata.latest_import_date)[:10]
            import_date = datetime.strptime(date_str, "%Y-%m-%d")
            if import_date > datetime.now() - timedelta(days=90):
                score += 2
                reasons.append(f"最近进口: {date_str} (3个月内)")
        except Exception:
            pass

    score = min(score, 25)
    return (score, "; ".join(reasons) if reasons else "无活跃进口信号")


def _score_legitimacy(google: GoogleSourceSignals,
                      linkedin: LinkedInSourceSignals,
                      tendata: TendataSourceSignals) -> tuple[int, str]:
    """公司可信度 (0-15)"""
    score = 0
    reasons = []

    if google.company_found and google.official_website:
        if google.website_match_confidence > 0.6:
            score += 8
            reasons.append(f"官网验证通过 ({google.official_website[:50]})")
        elif google.website_match_confidence > 0.3:
            score += 4
            reasons.append("官网部分匹配")

    if linkedin.company_page_found and linkedin.company_name_on_li:
        if linkedin.name_match_status in ("confirmed", "likely_match"):
            score += 5
            reasons.append("LinkedIn公司页已验证")
        else:
            score += 2
            reasons.append("LinkedIn公司页存在但未完全匹配")

    sources_confirming = sum([
        google.company_found,
        linkedin.company_page_found,
        tendata.found and tendata.match_status not in ("no_result", "conflict"),
    ])
    if sources_confirming >= 2:
        score += 2
        reasons.append("多源交叉验证确认")

    score = min(score, 15)
    return (score, "; ".join(reasons) if reasons else "公司可信度信号不足")


def _score_contacts(google: GoogleSourceSignals,
                    linkedin: LinkedInSourceSignals,
                    tendata: TendataSourceSignals) -> tuple[int, str]:
    """联系方式 (0-10)"""
    score = 0
    reasons = []

    if google.contact_email:
        score += 4
        reasons.append(f"邮箱: {google.contact_email}")
    if google.contact_phone:
        score += 3
        reasons.append(f"电话: {google.contact_phone}")
    if linkedin.key_contacts:
        score += 3
        titles = [c.get("title", "") for c in linkedin.key_contacts[:2]]
        reasons.append(f"LinkedIn联系人: {', '.join(titles) if titles else '找到'}")

    score = min(score, 10)
    return (score, "; ".join(reasons) if reasons else "未找到联系方式")


def _score_industry_scale(google: GoogleSourceSignals,
                          linkedin: LinkedInSourceSignals,
                          tendata: TendataSourceSignals) -> tuple[int, str]:
    """行业规模 (0-5)"""
    score = 0
    reasons = []

    emp_range = linkedin.employee_count_range or ""
    if emp_range:
        try:
            parts = emp_range.split("-")
            if len(parts) == 2:
                emp_low = int(parts[0].replace(",", ""))
                emp_high = int(parts[1].replace(",", ""))
                if emp_high >= 201:
                    score += 5
                    reasons.append(f"员工规模: {emp_range} (中大型企业)")
                elif emp_low >= 11:
                    score += 3
                    reasons.append(f"员工规模: {emp_range} (中小企业)")
                else:
                    score += 1
                    reasons.append(f"员工规模: {emp_range} (小型企业)")
        except ValueError:
            pass

    # 行业类型加分
    target_business = ["manufacturer", "manufacturer_distributor", "engineering_firm"]
    if google.business_type in target_business:
        if score < 4:
            score += 1
        reasons.append(f"业务类型: {google.business_type}")

    score = min(score, 5)
    return (score, "; ".join(reasons) if reasons else "规模/行业信息不足")


def _score_risk(google: GoogleSourceSignals,
                linkedin: LinkedInSourceSignals,
                tendata: TendataSourceSignals) -> tuple[int, str]:
    """风险扣分 (-20 ~ 0)"""
    score = 0
    risks = []

    # 网站不匹配/不可访问
    if google.company_found and google.confidence < 20:
        score -= 5
        risks.append("网站不可访问或内容不匹配")

    # LinkedIn 无公司页（仅当 LinkedIn 被使用时）
    if linkedin.error != "disabled" and not linkedin.company_page_found and not linkedin.error:
        score -= 3
        risks.append("LinkedIn无公司主页")

    # Tendata 无匹配（仅当 Tendata 被使用时）
    if tendata.error != "disabled" and tendata.match_status in ("no_result", "conflict"):
        score -= 5
        risks.append(f"海关数据匹配失败({tendata.match_status})")

    # 疑似聚合平台
    if google.business_type == "trader":
        domain = google.official_website.lower()
        aggregator_domains = ["alibaba", "made-in-china", "globalsources", "tradeindia", "indiamart"]
        if any(a in domain for a in aggregator_domains):
            score -= 5
            risks.append("疑似B2B聚合平台")

    # Google 置信度很低
    if google.company_found and google.confidence < 30:
        score -= 2
        risks.append("Google验证置信度低")

    score = max(score, -20)
    return (score, "; ".join(risks) if risks else "无风险信号")


# ============================================================================
# 主入口
# ============================================================================

def compute_tier(final_score: int) -> tuple[str, str]:
    """分数 → (tier, label)"""
    for tier in ["A", "B", "C", "D"]:
        cfg = TIERS.get(tier, DEFAULT_TIERS.get(tier, {}))
        if final_score >= cfg.get("min_score", 0):
            return (tier, cfg.get("label", "未知"))
    return ("D", "暂不跟进")


def score_company(result: LeadQualificationResult) -> LeadQualificationResult:
    """
    对一家公司进行七维度打分
    修改 result 的评分字段并返回
    """
    google = result.google
    linkedin = result.linkedin
    tendata = result.tendata

    # 1. 源一致性
    result.source_agreement_score, _ = _score_source_agreement(google, linkedin, tendata)

    # 2. 产品匹配
    result.product_fit_score, result.product_fit_analysis = \
        _score_product_fit(google, linkedin, tendata)

    # 3. 采购意愿
    result.purchase_intent_score, result.purchase_signals_analysis = \
        _score_purchase_intent(google, linkedin, tendata)

    # 4. 公司可信度
    result.legitimacy_score, _ = _score_legitimacy(google, linkedin, tendata)

    # 5. 联系方式
    result.contact_score, _ = _score_contacts(google, linkedin, tendata)

    # 6. 行业规模
    result.industry_scale_score, _ = _score_industry_scale(google, linkedin, tendata)

    # 7. 风险扣分
    result.risk_penalty, result.risk_notes = _score_risk(google, linkedin, tendata)

    # 总分
    result.final_score = result.source_agreement_score + result.product_fit_score \
        + result.purchase_intent_score + result.legitimacy_score \
        + result.contact_score + result.industry_scale_score \
        + result.risk_penalty
    result.final_score = max(result.final_score, 0)

    # 分级
    result.tier, result.tier_label = compute_tier(result.final_score)
    result.recommended_action = result.tier_label

    return result


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    # 模拟测试
    result = LeadQualificationResult(
        customer_name="TEST SEMICONDUCTOR MFG CO",
        country_region="韩国",
        google=GoogleSourceSignals(
            company_found=True,
            official_website="www.testfab.co.kr",
            website_match_confidence=0.85,
            product_keywords_found=["stainless steel pipe", "cleanroom pipe", "semiconductor equipment"],
            business_type="manufacturer",
            contact_email="procurement@testfab.co.kr",
            contact_phone="+82 31-123-4567",
            confidence=85,
        ),
        linkedin=LinkedInSourceSignals(
            company_page_found=True,
            company_name_on_li="Test Semiconductor MFG Co",
            name_match_status="confirmed",
            industry_tags=["Semiconductor Manufacturing", "Industrial Equipment", "Steel"],
            employee_count_range="201-1000",
            key_contacts=[{"name": "John Kim", "title": "Procurement Manager", "url": "..."}],
            confidence=85,
        ),
        tendata=TendataSourceSignals(
            found=True,
            match_status="confirmed",
            match_confidence=90,
            matched_company_name="Test Semiconductor MFG Co",
            import_active=True,
            latest_import_date="2026-05-15",
            total_shipments_12m=120,
            related_hs_codes=["730640", "730641", "730723"],
            top_products=[{"product_name": "STAINLESS STEEL PIPE"}, {"product_name": "STEEL FITTINGS"}],
            has_chinese_supplier=True,
            product_relevance_level="high",
        ),
    )

    scored = score_company(result)
    print(f"公司: {scored.customer_name}")
    print(f"总分: {scored.final_score} | 级别: {scored.tier} ({scored.tier_label})")
    print(f"  源一致性: {scored.source_agreement_score}/15")
    print(f"  产品匹配: {scored.product_fit_score}/25 → {scored.product_fit_analysis}")
    print(f"  采购意愿: {scored.purchase_intent_score}/25 → {scored.purchase_signals_analysis}")
    print(f"  可信度: {scored.legitimacy_score}/15")
    print(f"  联系方式: {scored.contact_score}/10")
    print(f"  行业规模: {scored.industry_scale_score}/5")
    print(f"  风险扣分: {scored.risk_penalty}/-20 → {scored.risk_notes}")
