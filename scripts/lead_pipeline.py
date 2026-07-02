"""
lead_pipeline.py — 多源B2B客户筛选流水线

编排 Google + LinkedIn + Tendata 三源收集 → 打分 → 排序

用法:
    from scripts.lead_pipeline import process_batch
    results = process_batch(customers, use_google=True, use_linkedin=True, use_tendata=True,
                           progress_callback=print)
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))

from models import (
    CustomerInput,
    LeadQualificationResult,
    GoogleSourceSignals,
    LinkedInSourceSignals,
    TendataSourceSignals,
)
from multi_source_scorer import score_company


# ============================================================================
# 流水线
# ============================================================================

def _dict_to_google_signals(d: dict) -> GoogleSourceSignals:
    return GoogleSourceSignals(
        company_found=d.get("company_found", False),
        official_website=d.get("official_website", ""),
        website_title=d.get("website_title", ""),
        website_match_confidence=d.get("website_match_confidence", 0.0),
        industry_keywords_found=d.get("industry_keywords_found", []),
        business_type=d.get("business_type", "unknown"),
        product_keywords_found=d.get("product_keywords_found", []),
        contact_email=d.get("contact_email", ""),
        contact_phone=d.get("contact_phone", ""),
        evidence_urls=d.get("evidence_urls", []),
        search_snippet=d.get("search_snippet", ""),
        company_status=d.get("company_status", "unknown"),
        confidence=d.get("confidence", 0),
        error=d.get("error", ""),
    )


def _dict_to_linkedin_signals(d: dict) -> LinkedInSourceSignals:
    return LinkedInSourceSignals(
        company_page_found=d.get("company_page_found", False),
        company_url=d.get("company_url", ""),
        company_name_on_li=d.get("company_name_on_li", ""),
        name_match_status=d.get("name_match_status", "no_match"),
        industry_tags=d.get("industry_tags", []),
        employee_count_range=d.get("employee_count_range", ""),
        employee_count_estimate=d.get("employee_count_estimate", 0),
        key_contacts=d.get("key_contacts", []),
        country_match=d.get("country_match", False),
        specialties=d.get("specialties", []),
        company_description=d.get("company_description", ""),
        founded_year=d.get("founded_year", ""),
        confidence=d.get("confidence", 0),
        error=d.get("error", ""),
    )


def _dict_to_tendata_signals(d: dict) -> TendataSourceSignals:
    return TendataSourceSignals(
        found=d.get("found", False),
        match_status=d.get("match_status", "no_result"),
        match_confidence=d.get("match_confidence", 0),
        matched_company_name=d.get("matched_company_name", ""),
        import_active=d.get("import_active", False),
        latest_import_date=d.get("latest_import_date", ""),
        total_shipments_12m=d.get("total_shipments_12m", 0),
        related_hs_codes=d.get("related_hs_codes", []),
        top_products=d.get("top_products", []),
        top_suppliers=d.get("top_suppliers", []),
        has_chinese_supplier=d.get("has_chinese_supplier", False),
        product_relevance_level=d.get("product_relevance_level", "unknown"),
        error=d.get("error", ""),
    )


def process_one(company_name: str, country: str = "",
                website: str = "", product_keywords: str = "",
                use_google: bool = True, use_linkedin: bool = True,
                use_tendata: bool = True,
                progress_callback: Callable = None) -> LeadQualificationResult:
    """处理单家公司"""

    def log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            print(f"  {msg}")

    result = LeadQualificationResult(
        customer_name=company_name,
        country_region=country,
    )

    t0 = time.time()

    # ── Google 验证 ──
    if not use_google:
        result.google.error = "disabled"
        log(f"[Google] 已跳过（用户禁用）")
    else:
        t_google = time.time()
        log(f"[Google] 搜索 {company_name}...")
        try:
            from google_verifier import GoogleVerifier
            gv = GoogleVerifier()
            try:
                g_signals = gv.verify(company_name, country, website)
                result.google = _dict_to_google_signals(g_signals)
                log(f"[Google] 完成 (置信度:{result.google.confidence}, 网站:{result.google.official_website[:60] if result.google.official_website else '无'})")
            finally:
                gv.close()
        except Exception as e:
            result.google.error = f"Google验证异常: {e}"
            log(f"[Google] 失败: {e}")
        result.google_elapsed = round(time.time() - t_google, 1)

    # ── LinkedIn 信息 ──
    if not use_linkedin:
        result.linkedin.error = "disabled"
        log(f"[LinkedIn] 已跳过（用户禁用）")
    else:
        t_li = time.time()
        log(f"[LinkedIn] 搜索 {company_name}...")
        try:
            from linkedin_enricher import LinkedInEnricher
            li = LinkedInEnricher()
            try:
                li_signals = li.enrich(company_name, country, website)
                result.linkedin = _dict_to_linkedin_signals(li_signals)
                log(f"[LinkedIn] 完成 (置信度:{result.linkedin.confidence}, 员工:{result.linkedin.employee_count_range or '未知'})")
            finally:
                li.close()
        except Exception as e:
            result.linkedin.error = f"LinkedIn异常: {e}"
            log(f"[LinkedIn] 失败: {e}")
        result.linkedin_elapsed = round(time.time() - t_li, 1)

    # ── Tendata 海关数据 ──
    if not use_tendata:
        result.tendata.error = "disabled"
        log(f"[海关] 已跳过（用户禁用）")
    else:
        t_td = time.time()
        log(f"[海关] 搜索 {company_name}...")
        try:
            from tendata_source import TendataSource
            td = TendataSource()
            td_signals = td.search(company_name, country, product_keywords)
            result.tendata = _dict_to_tendata_signals(td_signals)
            log(f"[海关] 完成 (状态:{result.tendata.match_status}, 进口:{result.tendata.total_shipments_12m}次)")
        except Exception as e:
            result.tendata.error = f"海关数据异常: {e}"
            log(f"[海关] 失败: {e}")
        result.tendata_elapsed = round(time.time() - t_td, 1)

    # ── 打分 ──
    result = score_company(result)
    result.elapsed_seconds = round(time.time() - t0, 1)

    log(f"  总分: {result.final_score} | 级别: {result.tier} | 耗时: {result.elapsed_seconds}s")

    return result


def process_batch(
    customers: list[CustomerInput],
    use_google: bool = True,
    use_linkedin: bool = True,
    use_tendata: bool = True,
    progress_callback: Callable = None,
) -> list[LeadQualificationResult]:
    """
    批量处理客户名单

    Args:
        customers: 客户列表
        use_google: 启用 Google 验证
        use_linkedin: 启用 LinkedIn 信息
        use_tendata: 启用海关数据
        progress_callback: 进度回调 (msg: str) -> None

    Returns:
        按 final_score 降序排列的结果列表
    """
    results = []
    total = len(customers)

    for i, c in enumerate(customers):
        progress_callback(f"[{i+1}/{total}] 开始处理: {c.customer_name}")

        try:
            result = process_one(
                company_name=c.customer_name,
                country=c.country_region,
                website=c.website,
                product_keywords=c.product_keywords,
                use_google=use_google,
                use_linkedin=use_linkedin,
                use_tendata=use_tendata,
                progress_callback=progress_callback,
            )
            result.rank = i + 1  # 初始排名，后面会重新排序
            results.append(result)
        except Exception as e:
            progress_callback(f"[{i+1}/{total}] ❌ 处理失败: {c.customer_name} — {e}")
            # 返回一个错误结果
            err_result = LeadQualificationResult(
                customer_name=c.customer_name,
                country_region=c.country_region,
                error=str(e),
            )
            results.append(err_result)

    # 按分数排序
    results.sort(key=lambda r: r.final_score, reverse=True)
    for i, r in enumerate(results):
        r.rank = i + 1

    return results


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    print("=== 单公司测试 ===\n")
    test_customer = CustomerInput(
        customer_name="TEXON CO LTD",
        country_region="韩国",
        website="texon.co.kr",
    )

    result = process_one(
        company_name=test_customer.customer_name,
        country=test_customer.country_region,
        website=test_customer.website,
        use_google=True,
        use_linkedin=True,
        use_tendata=False,  # Tendata 需要较长时间，测试时可关闭
    )

    print(f"\n=== 结果 ===")
    print(f"总分: {result.final_score} | 级别: {result.tier} ({result.tier_label})")
    print(f"Google: 置信度={result.google.confidence}, 网站={result.google.official_website}")
    print(f"LinkedIn: 置信度={result.linkedin.confidence}, 行业={result.linkedin.industry_tags}")
    print(f"产品匹配: {result.product_fit_score}/25")
    print(f"采购意愿: {result.purchase_intent_score}/25")
    print(f"风险: {result.risk_penalty} — {result.risk_notes}")
