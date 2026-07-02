"""
retry_noresult.py — 对 no_result / unconfirmed / detail_page_failed 客户进行二次补搜

功能：
1. 读取已有结果 Excel，筛选出需要补搜的行
2. 为每个客户生成 search_variants（多版本搜索词）
3. 按顺序尝试搜索，命中则进入详情页抓取
4. 只补搜 no_result / unconfirmed / detail_page_failed，保留已有 confirmed/likely_match/conflict

用法：
    python scripts/retry_noresult.py --input <原结果.xlsx> [--output <新结果.xlsx>]
"""

from __future__ import annotations

import sys
import re
import argparse
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from extract_tendata_fields import (
    _clean_company_name, _extract_company_body, _normalize_company_name,
    _score_search_candidate, _normalize_country, _detect_conflict,
    determine_import_active, determine_action, compute_match_status,
    determine_action, build_import_summary, build_summary, build_evidence_excerpt,
    EnrichmentRow, _get_scraper, _close_scraper, _reset_browser_pages,
    _contains_business_note, _clean_chinese_notes,
)

# ── 土耳其语字符等价映射 ──
_TURKISH_MAP = str.maketrans({
    'Ş': 'S', 'ş': 's',
    'İ': 'I', 'ı': 'i',
    'Ü': 'U', 'ü': 'u',
    'Ö': 'O', 'ö': 'o',
    'Ç': 'C', 'ç': 'c',
    'Ğ': 'G', 'ğ': 'g',
})

# 土耳其缩写展开
_TR_ABBR_EXPAND = [
    (r"\bSAN\.?\b", "SANAYI"),
    (r"\bTIC\.?\b", "TICARET"),
    (r"\bEND\.?\b", "ENDUSTRI"),
    (r"\bIHT\.?\b", "IHTIYAC"),
    (r"\bITH\.?\b", "ITHALAT"),
    (r"\bIHR\.?\b", "IHRACAT"),
    (r"\bMUM\.?\b", "MUMESSILLIK"),
]

# 土耳其法律后缀展开/压缩
_TR_LEGAL_VARIANTS = [
    (r"\bLTD\.?\s*STI\b", "LIMITED SIRKETI"),
    (r"\bLTD\.?\s*ŞTI\b", "LIMITED SIRKETI"),
    (r"\bLTD\.?\b", "LIMITED"),
    (r"\bSTI\b", "SIRKETI"),
    (r"\bŞTI\b", "SIRKETI"),
    (r"\bA\.?S\.?\b", "ANONIM SIRKETI"),
    (r"\bA\.?Ş\.?\b", "ANONIM SIRKETI"),
]

# 欧洲常见后缀
_EU_SUFFIXES = ["GMBH", "SRL", "S\.?R\.?L\.?", "S\.?L\.?", "SL", "SARL", "SAS",
                "SP\.?\s*Z\s*O\.?O\.?", "LTD", "LIMITED", "BV", "NV"]


def _turkic_normalize(text: str) -> str:
    """土耳其语字符归一化（Ş→S, İ→I 等）。"""
    return text.translate(_TURKISH_MAP)


def _clean_turkish_legal(text: str) -> str:
    """去掉土耳其公司法律后缀（不展开，仅删除）。"""
    result = text
    for suffix in [
        r"\bSANAYI\s+VE\s+TICARET\s*$",
        r"\bSAN\.?\s*VE\s*TIC\.?\b",
        r"\bSAN\.?\s*VE\s*TICARET\s*$",
        r"\bSANAYI\s+VE\s+TIC\.?\b",
        r"\bLIMITED\s+SIRKETI\b",
        r"\bLTD\.?\s*STI\b",
        r"\bLTD\.?\s*ŞTI\b",
        r"\bANONIM\s+SIRKETI\b",
        r"\bA\.?S\.?\b",
        r"\bA\.?Ş\.?\b",
        r"\bLTD\.?\b",
        r"\bSTI\b",
        r"\bŞTI\b",
        r"\bENDUSTRI\b",
        r"\bIHTIYAC\b",
        r"\bITHALAT\b",
        r"\bIHRACAT\b",
        r"\bMUMESSILLIK\b",
        r"\bSAN\.?\b",
        r"\bTIC\.?\b",
        r"\bTICARET\b",
    ]:
        result = re.sub(suffix, "", result, flags=re.IGNORECASE)
    # 清理多余空格和标点
    result = re.sub(r"\s+", " ", result).strip(" .,;")
    return result


def _expand_turkish_abbrevs(text: str) -> str:
    """展开土耳其语缩写（SAN.→SANAYI, TIC.→TICARET 等）。"""
    result = text
    for pat, repl in _TR_ABBR_EXPAND:
        result = re.sub(pat, repl, result, flags=re.IGNORECASE)
    return result


def _expand_turkish_legal(text: str) -> str:
    """展开土耳其法律后缀（LTD STI → LIMITED SIRKETI）。"""
    result = text
    for pat, repl in _TR_LEGAL_VARIANTS:
        result = re.sub(pat, repl, result, flags=re.IGNORECASE)
    return result


def _strip_legal_suffixes(text: str) -> str:
    """去掉通用法律后缀（保留主体名用于搜索）。"""
    result = text
    # 先去掉括号内容
    result = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", result).strip()
    # 去掉常见后缀
    for suffix in [
        r"\bLLC\b", r"\bLLP\b", r"\bLTD\b", r"\bLTD\.\b",
        r"\bINC\b", r"\bCORP\b", r"\bGMBH\b", r"\bBV\b", r"\bNV\b",
        r"\bSRL\b", r"\bS\.R\.L\.\b", r"\bS\.L\.\b", r"\bSARL\b",
        r"\bSAS\b", r"\bPTY\s*\(?\)?\s*LTD\b", r"\bPTE\b", r"\bSDN\s*BHD\b",
        r"\bCO\.?\s*,?\s*LTD\b",
        r"\bSPA\b", r"\bSP\.?\s*Z\s*O\.?O\.?\b",
    ]:
        result = re.sub(suffix, "", result, flags=re.IGNORECASE)
    # 去掉 PT. (印尼)
    result = re.sub(r"^PT\.?\s+", "", result).strip()
    # 去掉 ТОО / ООО 前缀
    result = re.sub(r"^(?:ТОО|ООО)\s+", "", result).strip()
    result = re.sub(r"\s+", " ", result).strip(" .,;")
    return result


def _truncate_to_core_words(text: str, max_words: int = 4) -> str:
    """保留前 N 个主体词（去掉法律词后截取）。"""
    words = text.split()
    legal_words = {"LLC", "LTD", "CORP", "INC", "GMBH", "SRL", "BV", "NV",
                   "SAS", "SARL", "CO", "PTY", "PTE", "SDN", "BHD",
                   "TIC", "TICARET", "SAN", "SANAYI", "VE", "TIC.",
                   "A.S.", "A.Ş.", "STI", "ŞTI", "END", "ENDUSTRI",
                   "LIMITED", "SIRKETI", "ANONIM"}
    core = [w for w in words if w.upper().rstrip(".,;") not in legal_words and len(w) > 1]
    return " ".join(core[:max_words])


def _remove_chinese_notes(text: str) -> str:
    """去掉中文业务备注。"""
    result = re.sub(r"[一-鿿].*", "", text).strip()
    # 去掉括号中的中文
    result = re.sub(r"[（(][^）)]*[一-鿿][^）)]*[）)]", "", result).strip()
    return result if result else text.strip()


def _turkic_variant(text: str) -> str:
    """生成土耳其语字符等价版本。"""
    return _turkic_normalize(text)


def generate_search_variants(
    customer_name: str,
    country_region: str = "",
    website: str = "",
    email_domain: str = "",
) -> list[str]:
    """为单个客户生成搜索词变体列表。

    变体按优先级排列：
    A. 原始清洗公司名
    B. 去掉业务备注
    C. 去掉法律后缀
    D. 展开常见缩写
    E. 压缩常见法律后缀
    F. 保留前 2-4 个主体词
    """
    variants = []
    seen = set()

    def add(v: str):
        v = v.strip()
        if not v or v in seen or len(v) < 2:
            return
        seen.add(v)
        variants.append(v)

    raw = customer_name.strip()

    # 0. 去掉中文备注后的版本
    no_notes = _remove_chinese_notes(raw)
    if no_notes != raw:
        add(no_notes)

    # A. 原始清洗（去掉业务备注 + 法律后缀）
    cleaned = _clean_company_name(raw)
    add(cleaned)

    # B. 去掉法律后缀后的主体名
    body = _strip_legal_suffixes(cleaned)
    add(body)

    # 土耳其专用
    if _is_turkey(country_region):
        # D. 展开土耳其缩写
        expanded = _expand_turkish_abbrevs(cleaned)
        add(expanded)

        # E. 展开土耳其法律后缀
        expanded_legal = _expand_turkish_legal(cleaned)
        add(expanded_legal)

        # 土耳其语字符等价版本
        for base in list(variants):
            tv = _turkic_variant(base)
            if tv != base:
                add(tv)

        # 仅去掉法律词但保留 SANAYI/TICARET 等展开形式
        turkish_body = _clean_turkish_legal(expanded_legal)
        add(turkish_body)

    # 俄罗斯/哈萨克斯坦
    if _is_russia_or_kaz(country_region):
        # 去掉 ТОО/ООО 后重试
        no_prefix = re.sub(r"^(?:ТОО|ООО)\s+", "", cleaned).strip()
        if no_prefix and no_prefix != cleaned:
            add(no_prefix)
            # 也生成 LLC 等价
            llc_variant = re.sub(r"^(?:ТОО|ООО)\s+", "LLC ", cleaned).strip()
            add(llc_variant)

    # F. 截断到前 3-4 个核心词（适用于长公司名）
    if len(cleaned.split()) > 4:
        add(_truncate_to_core_words(cleaned, 4))
        add(_truncate_to_core_words(cleaned, 3))
        add(_truncate_to_core_words(cleaned, 2))

    # 土耳其语长名截断
    if _is_turkey(country_region) and len(cleaned.split()) > 4:
        add(_truncate_to_core_words(expanded_legal if expanded_legal else cleaned, 4))

    # 最终去重但保持顺序
    unique_variants = []
    seen2 = set()
    for v in variants:
        v_lower = v.lower().strip()
        if v_lower not in seen2:
            seen2.add(v_lower)
            unique_variants.append(v)

    return unique_variants


def _is_turkey(country: str) -> bool:
    norm = _normalize_country(country)
    return norm in ("turkey", "tr", "土耳其")


def _is_russia_or_kaz(country: str) -> bool:
    norm = _normalize_country(country)
    return norm in ("russia", "ru", "俄罗斯", "kazakhstan", "kz", "哈萨克斯坦")


def _country_from_detail(detail) -> str:
    """从 detail 对象中提取国家。"""
    if hasattr(detail, 'country') and detail.country:
        return detail.country
    if hasattr(detail, 'location') and detail.location:
        return detail.location
    return ""


# ── 全局搜索结果缓存（供 get_and_clear 使用） ──
_hs_extra_results = []


def _search_and_select_v2(scraper, search_kw: str, customer_name: str,
                          country_region: str, score_threshold: int = 60):
    """执行搜索 + 提取候选 + 打分 + 选择最佳候选。

    复用已有 _search_and_select 逻辑。
    """
    from extract_tendata_fields import _search_and_select
    return _search_and_select(scraper, search_kw, customer_name, country_region, score_threshold)


def retry_one_customer(
    scraper,
    customer_name: str,
    country_region: str = "",
    website: str = "",
    email_domain: str = "",
    product_keywords: str = "",
    internal_customer_id: str = "",
    original_match_status: str = "",
    batch_id: str = "",
) -> EnrichmentRow:
    """对单个 no_result/unconfirmed 客户进行多搜索词补搜。"""

    variants = generate_search_variants(customer_name, country_region, website, email_domain)

    result = EnrichmentRow(
        customer_name=customer_name,
        country_region=country_region,
        website_input=website,
        email_domain=email_domain,
        product_keywords=product_keywords,
        internal_customer_id=internal_customer_id,
        source_capture_time=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        source_search_keyword="",  # 会被 used_variant 覆盖
        run_batch_id=batch_id,
    )

    if not variants:
        result.match_status = "no_result"
        result.manual_review_reason = "无法生成搜索变体"
        return result

    print(f"    搜索变体 ({len(variants)} 个):")
    for vi, v in enumerate(variants[:8]):
        print(f"      [{vi+1}] '{v}'")
    if len(variants) > 8:
        print(f"      ... 还有 {len(variants)-8} 个")

    top1 = None
    candidates = None
    used_variant = ""
    best_variant_score = -1

    for vi, variant in enumerate(variants):
        normalized_kw = _normalize_company_name(variant)
        print(f"    尝试 [{vi+1}/{len(variants)}]: '{variant}' → '{normalized_kw}'")

        if not scraper.search_company(normalized_kw):
            print(f"      → 搜索失败")
            continue

        cands = scraper.extract_search_results()
        if not cands:
            print(f"      → 无搜索结果")
            continue

        # 打分选最优
        scored = []
        for c in cands:
            s, d = _score_search_candidate(c, customer_name, country_region)
            scored.append((s, d, c))
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score = scored[0][0]
        print(f"      → 找到 {len(cands)} 个候选, 最高分={best_score}")
        for s, d, c in scored[:3]:
            cc = getattr(c, 'country', '') or ''
            print(f"        分数={s}  候选='{c.company_name[:50]}' 地区='{cc[:30]}'")

        if best_score >= 60 and best_score > best_variant_score:
            top1 = scored[0][2]
            candidates = cands
            used_variant = variant
            best_variant_score = best_score
            print(f"      → ✓ 当前最佳变体: '{variant}' (分数={best_score})")

    if not top1 or best_variant_score < 60:
        # 所有变体都没找到高分候选
        result.match_status = "no_result"
        result.match_confidence = best_variant_score if best_variant_score >= 0 else 0
        result.source_search_keyword = variants[0] if variants else customer_name
        result.manual_review_flag = "yes"
        result.manual_review_reason = f"所有搜索变体无高分候选（最高分={best_variant_score}）"
        result.recommended_action = "转官网/LinkedIn核验"
        print(f"    → 所有变体无结果")
        return result

    print(f"    使用变体: '{used_variant}' (分数={best_variant_score})")
    result.source_search_keyword = used_variant

    # 进入详情页
    from extract_tendata_fields import _name_similarity

    if not scraper.go_to_detail(top1):
        result.matched_company_name = top1.company_name
        result.match_status = "detail_page_failed"
        result.match_confidence = max(int(_name_similarity(customer_name, top1.company_name) * 100), 30)
        result.source_page_title = "贸易数据搜索结果页"
        result.manual_review_flag = "yes"
        result.manual_review_reason = "详情页进入失败"
        result.recommended_action = "转官网/LinkedIn核验"
        print(f"    → 详情页进入失败")
        return result

    # 提取详情
    from extract_tendata_fields import CompanyDetail
    import time
    t_detail = time.monotonic()
    detail = scraper.extract_company_detail()
    detail_sec = round(time.monotonic() - t_detail, 2)

    result.matched_company_name = detail.standard_name or top1.company_name
    result.website_result = detail.website
    result.company_status = detail.company_status
    result.contact_name = detail.contact_name
    result.phone = detail.phone
    result.email = detail.email
    result.address = detail.address
    result.location = detail.location
    result.whatsapp = detail.whatsapp
    result.linkedin = detail.linkedin

    # 产品页
    t_prod = time.monotonic()
    if scraper.go_to_product_info_tab():
        products = scraper.extract_top_products(max_items=3)
        if products:
            import json
            result.top_products_json = json.dumps(products, ensure_ascii=False)
    prod_sec = round(time.monotonic() - t_prod, 2)

    # 进口分析
    t_imp = time.monotonic()
    from extract_tendata_fields import ImportAnalysis
    imp = scraper.go_to_import_analysis()
    if imp.analysis_entry_status == "entered_confirmed":
        target_hs = []
        if product_keywords:
            target_hs = [re.sub(r"[^\d]", "", product_keywords)]
        imp = scraper.extract_import_analysis(imp, target_hs_codes=target_hs)
    imp_sec = round(time.monotonic() - t_imp, 2)

    result.analysis_entry_status = imp.analysis_entry_status
    result.analysis_data_status = imp.analysis_data_status
    result.latest_import_date = imp.latest_import_date
    result.import_active_status = determine_import_active(imp.latest_import_date)
    result.import_activity_summary = build_import_summary(imp)
    result.target_hs_amount_json = imp.target_hs_amount_json
    result.top_suppliers_json = imp.top_suppliers_json

    page_country = _country_from_detail(detail)

    # 冲突检测
    from extract_tendata_fields import _detect_conflict
    is_conflict, conflict_reasons = _detect_conflict(
        input_country=country_region,
        page_country=page_country,
        input_website=website,
        page_website=detail.website,
        input_email_domain=email_domain,
        page_email=detail.email,
        top_products_json=result.top_products_json,
    )

    if is_conflict:
        result.raw_candidate_latest_import_date = imp.latest_import_date or ""
        result.match_status = "conflict"
        result.match_confidence = max(result.match_confidence, 10)
        result.manual_review_flag = "yes"
        reason_str = "; ".join(conflict_reasons)
        if result.manual_review_reason:
            result.manual_review_reason += "; " + reason_str
        else:
            result.manual_review_reason = reason_str
        result.latest_import_date = ""
        result.import_active_status = "invalid_for_target"
        result.recommended_action = "待人工复核"
        print(f"    → conflict: {reason_str}")
        return result

    # 计算匹配状态
    status, conf = compute_match_status(
        input_name=customer_name,
        matched_name=detail.standard_name or top1.company_name,
        input_country=country_region,
        page_country=page_country,
        input_website=website,
        page_website=detail.website,
        has_country=bool(country_region),
    )
    result.match_status = status
    result.match_confidence = conf

    # 国家不匹配安全规则
    if country_region and page_country:
        norm_input = _normalize_country(country_region)
        norm_page = _normalize_country(page_country)
        if norm_input and norm_page and norm_input != norm_page:
            result.manual_review_flag = "yes"
            reason = f"国家不匹配：输入={country_region}，搜索结果={page_country}"
            if result.manual_review_reason:
                result.manual_review_reason += "; " + reason
            else:
                result.manual_review_reason = reason
            if result.match_status == "confirmed":
                result.match_status = "unconfirmed"
                result.match_confidence = min(result.match_confidence, 59)
            elif result.match_status == "likely_match":
                result.match_confidence = min(result.match_confidence, 59)

    result.business_summary = build_summary(detail, imp)
    result.evidence_excerpt = build_evidence_excerpt(detail, imp)
    result.recommended_action = determine_action(result.match_status, result.import_active_status)

    print(f"    → {result.match_status} (conf={result.match_confidence}, variant='{used_variant}')")
    return result


def main():
    parser = argparse.ArgumentParser(description="对 no_result / unconfirmed 客户进行二次补搜")
    parser.add_argument("--input", required=True, help="原结果 Excel 文件路径")
    parser.add_argument("--output", default=None, help="输出 Excel 文件路径")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    args = parser.parse_args()

    input_path = args.input
    if not Path(input_path).exists():
        print(f"[ERROR] 输入文件不存在: {input_path}")
        sys.exit(1)

    # 读取原结果
    print(f"[1/5] 读取原结果: {input_path}")
    results_df = pd.read_excel(input_path)
    print(f"  总行数: {len(results_df)}")

    # 筛选需要补搜的行
    target_statuses = ["no_result", "unconfirmed", "detail_page_failed"]
    mask = results_df["match_status"].isin(target_statuses)
    retry_rows = results_df[mask].reset_index(drop=True)
    keep_rows = results_df[~mask].reset_index(drop=True)

    print(f"  保留 (confirmed/likely_match/conflict): {len(keep_rows)} 条")
    print(f"  需要补搜 (no_result/unconfirmed/detail_page_failed): {len(retry_rows)} 条")

    if len(retry_rows) == 0:
        print("  无需补搜的行")
        sys.exit(0)

    # 初始化浏览器（先自检，再连接，顺序与 run_batch.py 一致）
    from extract_tendata_fields import self_check_before_batch

    print(f"[2/5] 环境自检...")
    check = self_check_before_batch(headless=args.headless)
    for msg in check["messages"]:
        print(f"  {msg}")
    if not check["ok"]:
        print("[ERROR] 自检未通过")
        sys.exit(1)

    print(f"[2/5] 启动浏览器...")
    scraper = _get_scraper(headless=args.headless)
    _reset_browser_pages(scraper)

    batch_id = f"RETRY-{__import__('uuid').uuid4().hex[:8].upper()}"

    # 逐条补搜
    print(f"[3/5] 开始补搜 {len(retry_rows)} 条...")
    new_results = []
    from datetime import datetime

    for i, row in retry_rows.iterrows():
        customer_name = str(row.get("customer_name", "")).strip()
        if not customer_name:
            print(f"  [{i+1}/{len(retry_rows)}] customer_name 为空，跳过")
            continue

        country = str(row.get("country_region", ""))
        website = str(row.get("website_input", "") or row.get("website", ""))
        email_domain = str(row.get("email_domain", "") or "")
        product_keywords = str(row.get("product_keywords", "") or "")
        internal_id = str(row.get("internal_customer_id", "") or "")
        original_status = str(row.get("match_status", ""))

        print(f"  [{i+1}/{len(retry_rows)}] 补搜: {customer_name[:50]} (国家={country}, 原状态={original_status})")

        try:
            er = retry_one_customer(
                scraper=scraper,
                customer_name=customer_name,
                country_region=country,
                website=website,
                email_domain=email_domain,
                product_keywords=product_keywords,
                internal_customer_id=internal_id,
                original_match_status=original_status,
                batch_id=batch_id,
            )

            # 附加搜索变体信息用于输出
            er_dict = asdict(er)
            er_dict["original_match_status"] = original_status

            # 重新生成变体列表写入结果
            variants = generate_search_variants(customer_name, country, website, email_domain)
            import json
            er_dict["search_variants"] = json.dumps(variants, ensure_ascii=False)

            new_results.append(er_dict)
        except Exception as e:
            print(f"    → 异常: {e}")
            new_results.append({
                "customer_name": customer_name,
                "country_region": country,
                "website_input": website,
                "email_domain": email_domain,
                "product_keywords": product_keywords,
                "internal_customer_id": internal_id,
                "original_match_status": original_status,
                "match_status": "detail_page_failed",
                "match_confidence": 0,
                "manual_review_flag": "yes",
                "manual_review_reason": f"补搜异常: {str(e)[:200]}",
                "recommended_action": "转官网/LinkedIn核验",
                "search_variants": "[]",
                "used_search_variant": "",
            })

    # 关闭浏览器
    print(f"[4/5] 关闭浏览器...")
    _close_scraper()

    # 合并结果
    print(f"[5/5] 合并结果并导出...")

    # 定义输出列
    output_columns = [
        "internal_customer_id", "customer_name", "country_region",
        "website_input", "email_domain", "product_keywords",
        "original_match_status",
        "source_search_keyword", "search_variants", "used_search_variant",
        "matched_company_name", "match_status", "match_confidence",
        "website_result", "company_status", "contact_name", "phone", "email",
        "address", "location", "whatsapp", "linkedin",
        "import_active_status", "latest_import_date", "raw_candidate_latest_import_date",
        "import_activity_summary", "business_summary", "evidence_excerpt",
        "source_page_title", "analysis_entry_status", "analysis_data_status",
        "top_products_json", "target_hs_amount_json", "top_suppliers_json",
        "hs_product", "total_import_volume",
        "recommended_action",
        "source_system", "source_capture_time", "source_candidate_rank",
        "source_page_url", "manual_review_flag", "manual_review_reason",
        "run_batch_id",
    ]

    # 新建补搜结果 DataFrame
    retry_df = pd.DataFrame(new_results, columns=output_columns)

    # 合并保留的行（重新映射列名以对齐）
    if len(keep_rows) > 0:
        keep_mapped = pd.DataFrame()
        for col in output_columns:
            if col in keep_rows.columns:
                keep_mapped[col] = keep_rows[col]
            else:
                keep_mapped[col] = ""
            # search_variants 和 used_search_variant 对已确认行设为空
            if col in ("search_variants", "used_search_variant", "original_match_status"):
                if col == "original_match_status":
                    keep_mapped[col] = keep_rows.get("match_status", "")
                else:
                    keep_mapped[col] = ""

        final_df = pd.concat([keep_mapped, retry_df], ignore_index=True)
    else:
        final_df = retry_df

    # 输出路径
    output_path = args.output
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"tendata_retry_noresult_unconfirmed_{ts}.xlsx"

    final_df.to_excel(output_path, index=False)
    print(f"  结果已保存: {output_path}")

    # 统计
    status_counts = {}
    for _, r in final_df.iterrows():
        s = str(r.get("match_status", "unknown"))
        status_counts[s] = status_counts.get(s, 0) + 1
    print("  最终状态统计:")
    for s, c in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"    {s}: {c} 条")


if __name__ == "__main__":
    main()
