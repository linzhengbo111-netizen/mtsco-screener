"""
external_check.py — 官网 + LinkedIn 核验脚本

基于 external_check_input.xlsx 的 External_Check_Template sheet，
核验 17 个客户的官网和 LinkedIn 公司页。
"""

from __future__ import annotations

import sys
import re
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime

# Force UTF-8 output on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from playwright.sync_api import sync_playwright, Page, TimeoutError as PwTimeout

# ── 配置 ──
CDP_PORT = 9222
OUTPUT_FILE = Path(__file__).parent.parent / "external_check_results_17.xlsx"
INPUT_FILE   = Path(__file__).parent.parent / "external_check_input.xlsx"
SAVE_INTERVAL = 5  # 每处理 N 个保存一次中间结果
LINKEDIN_DELAY = 3  # LinkedIn 请求间隔(秒)，避免限速

# ── 产品相关关键词 ──
INDUSTRIAL_KEYWORDS = [
    "stainless", "steel", "tube", "pipe", "metal", "fittings", "flange",
    "engineering", "manufactur", "industrial", "valve", "pump", "pressure",
    "hydraulic", "pneumatic", "boiler", "energy", "oil", "gas", "petroleum",
    "automotive", "motor", "vehicle", "machinery", "equipment", "tool",
    "alloy", "welding", "fabrication", "construction", "mining",
    "sanitary", "process", "automation", "control", "system",
    "stainless steel", "pipe fitting", "tube", "flange",
    "saniter", "fitting", "coupling", "bend", "elbow", "tee", "reducer",
]

# 非官网黑名单（黄页/B2B/目录/新闻）
NOT_OFFICIAL_SITES = [
    "alibaba.com", "made-in-china.com", "globalsources.com", "amazon.com",
    "ebay.com", "yellowpages", "yelp.com", "linkedin.com", "facebook.com",
    "twitter.com", "wikipedia.org", "google.com", "bing.com",
    "bloomberg.com", "reuters.com", "crunchbase.com",
    "kompass.com", "thomasnet.com", "europages", "globalimporter",
    "importgenius", "panjiva", "trademap", "volza", "importyeti",
    "zoominfo.com", "glassdoor.com", "indeed.com",
    "tendata", "tianyancha", "qcc.com", "aiqicha",
    "yell.com", "1688.com", "dhgate.com", "tradekey.com",
    "manta.com", "hoovers.com", "company", "directory",
]


def is_official_site(url: str) -> bool:
    """判断 URL 是否属于非官网类型。"""
    if not url:
        return False
    url_lower = url.lower()
    for kw in NOT_OFFICIAL_SITES:
        if kw in url_lower:
            return True
    return False


def check_product_relevance(page_text: str) -> str:
    """根据页面文本判断产品相关性。"""
    if not page_text:
        return "unknown"
    text_lower = page_text.lower()
    match_count = sum(1 for kw in INDUSTRIAL_KEYWORDS if kw.lower() in text_lower)
    if match_count >= 3:
        return "high"
    elif match_count >= 1:
        return "medium"
    else:
        return "low"


def check_business_status(page_text: str, page_url: str = "") -> str:
    """判断公司经营状态。"""
    if not page_text:
        return "unknown"
    text_lower = page_text.lower()
    inactive_signals = [
        "defunct", "bankrupt", "liquidated", "ceased operations",
        "no longer in business", "closed down", "shut down", "dissolved",
        "this domain is for sale", "this website has been",
        "site is undergoing maintenance", "parked", "suspended",
        "404 not found", "page not found", "server error",
        "this domain name is not for sale but it might",
    ]
    for sig in inactive_signals:
        if sig in text_lower:
            return "inactive"
    return "active"


@dataclass
class ExternalCheckResult:
    internal_customer_id: str = ""
    customer_name: str = ""
    country_region: str = ""
    website_input: str = ""
    email_domain: str = ""
    linkedin: str = ""
    final_tendata_status: str = ""
    final_matched_company_name: str = ""
    final_matched_country: str = ""
    final_import_active_status: str = ""
    final_latest_import_date: str = ""
    final_used_search_variant: str = ""
    final_evidence_excerpt: str = ""
    final_manual_review_reason: str = ""

    website_accessible: str = ""
    website_match_status: str = ""
    website_business_status: str = ""
    website_product_relevance: str = ""
    website_contact_found: str = ""
    website_evidence_url: str = ""
    website_evidence_summary: str = ""

    linkedin_company_found: str = ""
    linkedin_company_url: str = ""
    linkedin_employee_range: str = ""
    linkedin_country_match: str = ""
    linkedin_recent_activity: str = ""

    contact_still_at_company: str = ""
    external_check_confidence: int = 0
    external_check_summary: str = ""
    followup_priority_candidate: str = ""
    manual_review_flag: str = ""
    manual_review_reason: str = ""


class ExternalChecker:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None

    def connect(self):
        """通过 CDP 连接浏览器，复用已有页面以共享 LinkedIn 登录态。"""
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        contexts = self.browser.contexts
        if contexts:
            self.context = contexts[0]
            pages = self.context.pages
            if pages:
                self.page = pages[0]
                print(f"  [浏览器] 复用页面: {self.page.url[:80]}")
            else:
                self.page = self.context.new_page()
        else:
            self.context = self.browser.new_context()
            self.page = self.context.new_page()

    def close(self):
        try:
            if self.page:
                self.page.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

    def navigate_safe(self, url: str, timeout: int = 15000):
        """安全导航，返回 (success, final_url, title, page_text)。"""
        try:
            resp = self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)  # 等 JS 渲染

            final_url = self.page.url
            title = self.page.title() or ""

            # 尝试检测 HTTP 错误状态
            status = resp.status if resp else 0
            if status and status >= 400:
                return False, final_url, title, f"HTTP {status}"

            # 提取页面文本
            try:
                page_text = self.page.inner_text("body", timeout=3000) or ""
            except Exception:
                page_text = ""

            # 检查是否有错误页面特征
            body_class = ""
            try:
                body_class = self.page.get_attribute("body", "class") or ""
            except Exception:
                pass

            error_texts = ["404", "502", "503", "500", "ERR_", "connection refused",
                          "dns error", "site can't be reached"]
            text_lower = (title + " " + page_text[:500]).lower()
            if any(e.lower() in text_lower for e in error_texts):
                return False, final_url, title, page_text[:2000]

            return True, final_url, title, page_text[:5000]

        except PwTimeout:
            try:
                return False, self.page.url, self.page.title() or "", "navigation timeout"
            except Exception:
                return False, url, "", "navigation timeout"
        except Exception as e:
            err = str(e).lower()
            if any(kw in err for kw in ["connection refused", "net::err", "dns", "ssl"]):
                return False, url, "", f"connection error: {str(e)[:100]}"
            return False, url, "", str(e)[:200]

    def check_website(self, row: dict) -> dict:
        """核验官网。"""
        result = {
            "website_accessible": "no",
            "website_match_status": "no_website",
            "website_business_status": "unknown",
            "website_product_relevance": "unknown",
            "website_contact_found": "no",
            "website_evidence_url": "",
            "website_evidence_summary": "",
        }

        website = str(row.get("website_input", "") or "").strip()
        customer_name = str(row.get("customer_name", "") or "").strip()
        country = str(row.get("country_region", "") or "").strip()

        if not website or website in ("http://", "https://", "http:/", "https:/"):
            result["website_match_status"] = "no_website"
            result["website_accessible"] = "no"
            print(f"      官网: website_input 为空")
            return result

        # 补充 URL scheme
        if not website.startswith(("http://", "https://")):
            website = "https://" + website

        print(f"      官网: 尝试打开 {website}")
        success, final_url, title, page_text = self.navigate_safe(website)

        if not success:
            result["website_accessible"] = "no"
            result["website_match_status"] = "inaccessible"
            err_summary = page_text[:200] if page_text else "页面无法加载"
            result["website_evidence_summary"] = f"官网无法访问: {err_summary}"
            print(f"      官网: 无法访问 — {err_summary[:100]}")
            return result

        # 判断是否为官网
        if is_official_site(final_url):
            result["website_accessible"] = "yes"
            result["website_match_status"] = "unconfirmed"
            result["website_evidence_url"] = final_url
            result["website_evidence_summary"] = f"打开的是非官网页面: {final_url}"
            print(f"      官网: 非官网 ({final_url})")
            return result

        # 判断公司主体是否匹配
        # 提取页面中的公司名称关键词
        page_text_full = page_text
        title_lower = title.lower()
        name_lower = customer_name.lower()

        # 清理公司名称用于匹配
        name_parts = [p for p in re.split(r'[\s,./\-()]+', name_lower) if len(p) > 2
                      and p not in ('inc', 'ltd', 'co', 'corp', 'gmbh', 'nv', 'pty', 'spa',
                                     'srl', 'bv', 'the', 'and', 'for', 'of', 'de', 'del',
                                     'di', 'el', 'la', 'le', 'en', 've', 'et', 'sa', 'srl',
                                     'san', 'tic', 'sanayi', 'ticaret', 'limited', 'sirketi')]

        match_count = sum(1 for p in name_parts if p in title_lower or p in page_text_full.lower())
        total_parts = max(len(name_parts), 1)
        match_ratio = match_count / total_parts

        if match_ratio >= 0.5 or name_lower.split()[0] in title_lower:
            result["website_match_status"] = "confirmed"
        elif match_ratio >= 0.3:
            result["website_match_status"] = "likely_match"
        else:
            result["website_match_status"] = "unconfirmed"

        result["website_accessible"] = "yes"
        result["website_evidence_url"] = final_url

        # 判断经营状态
        result["website_business_status"] = check_business_status(page_text_full, final_url)

        # 判断产品相关性
        result["website_product_relevance"] = check_product_relevance(page_text_full)

        # 查找联系信息
        contact_found = "no"
        contact_url = ""
        # 尝试访问 contact/about 页面
        try:
            base = re.match(r'(https?://[^/]+)', final_url)
            if base:
                contact_paths = ["/contact", "/contact-us", "/about", "/about-us"]
                for cp in contact_paths:
                    try:
                        c_success, c_url, c_title, c_text = self.navigate_safe(
                            f"{base.group(1)}{cp}", timeout=10000)
                        if c_success and c_text:
                            # 查找邮箱、电话、地址
                            emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', c_text)
                            phones = re.findall(r'[\+]?[\d\s\-\(\)]{7,}', c_text)
                            if emails or phones:
                                contact_found = "yes"
                                contact_url = c_url
                                result["website_evidence_summary"] = (
                                    f"官网可访问 ({final_url})，标题='{title[:80]}'。"
                                    f"在 {c_url} 找到联系信息: email={emails[:2]}, phone={phones[:1]}。"
                                )
                                break
                    except Exception:
                        continue
        except Exception:
            pass

        if contact_found == "no":
            # 在主页面查找联系信息
            emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', page_text_full)
            phones = re.findall(r'[\+]?[\d\s\-\(\)\.]{7,25}', page_text_full)
            if emails or phones:
                contact_found = "yes"
                result["website_evidence_summary"] = (
                    f"官网可访问 ({final_url})，标题='{title[:80]}'。"
                    f"在主页找到: email={emails[:2]}, phone={phones[:1]}。"
                )
            else:
                result["website_evidence_summary"] = (
                    f"官网可访问 ({final_url})，标题='{title[:80]}'。"
                )

        result["website_contact_found"] = contact_found

        print(f"      官网: match={result['website_match_status']}, "
              f"business={result['website_business_status']}, "
              f"product={result['website_product_relevance']}, "
              f"contact={contact_found}")

        return result

    def check_linkedin(self, row: dict) -> dict:
        """核验 LinkedIn 公司页。尝试直接访问公司页，不需要登录。"""
        result = {
            "linkedin_company_found": "no",
            "linkedin_company_url": "",
            "linkedin_employee_range": "",
            "linkedin_country_match": "unknown",
            "linkedin_recent_activity": "unknown",
        }

        customer_name = str(row.get("customer_name", "") or "").strip()
        country = str(row.get("country_region", "") or "").strip()

        if not customer_name:
            return result

        # 清理搜索词，生成 LinkedIn 公司 URL slug
        search_kw = re.sub(r'[\(\[].*?[\)\)]', '', customer_name).strip()
        search_kw = re.sub(r'\b(inc|ltd|corp|gmbh|nv|pty|spa|srl|bv|co\.?,?ltd|gesmbh)\b', '',
                           search_kw, flags=re.IGNORECASE).strip()
        search_kw = re.sub(r'[^a-z0-9\s-]', '', search_kw, flags=re.IGNORECASE).strip()
        search_kw = re.sub(r'\s+', '-', search_kw).strip().lower()

        if not search_kw:
            return result

        print(f"      LinkedIn: 尝试公司 '{customer_name}' ({country})")

        # 方法 1: 先尝试搜索页面找公司页
        search_url = f"https://www.linkedin.com/search/results/companies/?keywords={search_kw}"

        try:
            resp = self.page.goto(search_url, timeout=12000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            final_url = self.page.url

            # 检测登录墙
            if "linkedin.com/authwall" in final_url or "linkedin.com/uas/login" in final_url:
                print(f"      LinkedIn: 需要登录 (authwall)")
                return result

            page_text = ""
            try:
                page_text = self.page.inner_text("body", timeout=3000) or ""
            except Exception:
                pass

            # 查找公司页链接
            company_links = []
            try:
                links = self.page.query_selector_all('a[href*="linkedin.com/company/"]')
                for link in links:
                    href = (link.get_attribute("href") or "").split("?")[0].split("#")[0]
                    if href and "linkedin.com/company/" in href:
                        text = link.inner_text() or ""
                        company_links.append((href, text))
            except Exception:
                pass

            # 去重
            seen = set()
            unique_links = []
            for href, text in company_links:
                if href not in seen:
                    seen.add(href)
                    unique_links.append((href, text))

            best_link = ""
            best_text = ""
            name_lower = customer_name.lower()
            first_word = name_lower.split()[0]

            for href, text in unique_links[:5]:
                text_lower = text.lower()
                if first_word in text_lower and len(first_word) > 2:
                    best_link = href
                    best_text = text
                    break

            if not best_link and unique_links:
                best_link, best_text = unique_links[0]

            if best_link:
                result["linkedin_company_found"] = "yes"
                result["linkedin_company_url"] = best_link
                print(f"      LinkedIn: 找到公司页 {best_link}")

                # 进入公司页获取详情
                try:
                    resp2 = self.page.goto(best_link, timeout=12000, wait_until="domcontentloaded")
                    self.page.wait_for_timeout(2000)

                    # 检查是否被重定向到登录
                    final2 = self.page.url
                    if "linkedin.com/authwall" in final2 or "linkedin.com/uas/login" in final2:
                        print(f"      LinkedIn: 公司页需要登录")
                        return result

                    li_text = ""
                    try:
                        li_text = self.page.inner_text("body", timeout=3000) or ""
                    except Exception:
                        pass

                    # 提取员工规模
                    size_patterns = [
                        r'(\d[\d,]*[-–]\d[\d,]*|\d[\d,]*\+?)\s*employees',
                        r'(\d[\d,]*[-–]\d[\d,]*|\d[\d,]*\+?)\s*员工',
                        r'company size[:\s]+(\d[\d,]*[-–]\d[\d,]*)',
                    ]
                    for pat in size_patterns:
                        m = re.search(pat, li_text, re.IGNORECASE)
                        if m:
                            result["linkedin_employee_range"] = m.group(1)
                            break

                    # 国家匹配
                    country_lower = country.lower()
                    country_map = {
                        "波兰": ["poland"],
                        "印度": ["india"],
                        "美国": ["united states", "usa"],
                        "加拿大": ["canada"],
                        "澳大利亚": ["australia"],
                        "泰国": ["thailand"],
                        "斯里兰卡": ["sri lanka"],
                        "比利时": ["belgium"],
                        "土耳其": ["turkey", "türkiye"],
                        "德国": ["germany", "deutschland"],
                        "爱尔兰": ["ireland"],
                        "意大利": ["italy", "italia"],
                        "西班牙": ["spain", "españa"],
                        "柬埔寨": ["cambodia"],
                    }
                    expected = country_map.get(country_lower, [country_lower])
                    li_lower = li_text.lower()
                    for ec in expected:
                        if ec in li_lower:
                            result["linkedin_country_match"] = "yes"
                            break
                    else:
                        result["linkedin_country_match"] = "no"

                    # 活动检测
                    activity_signals = [
                        "posted", "shared", "published", "update",
                        "followers", "employees on linkedin",
                    ]
                    if any(s in li_lower for s in activity_signals):
                        result["linkedin_recent_activity"] = "yes"

                except PwTimeout:
                    pass
                except Exception as e:
                    print(f"      LinkedIn: 公司页加载失败: {str(e)[:50]}")
            else:
                print(f"      LinkedIn: 搜索结果中未找到公司页")

        except PwTimeout:
            print(f"      LinkedIn: 搜索超时")
        except Exception as e:
            err = str(e).lower()
            if "connection" in err or "closed" in err:
                print(f"      LinkedIn: 连接被拒 (可能被限速)")
            else:
                print(f"      LinkedIn: 失败: {str(e)[:80]}")

        return result


def compute_priority(result: ExternalCheckResult) -> str:
    """计算跟进优先级。"""
    tendata = result.final_tendata_status
    web_match = result.website_match_status
    web_access = result.website_accessible
    web_business = result.website_business_status
    web_relevance = result.website_product_relevance
    li_found = result.linkedin_company_found

    # A: 腾道 confirmed/likely_match + 官网或LinkedIn至少一个确认 + active + high/medium
    if tendata in ("confirmed", "likely_match"):
        web_ok = (web_match in ("confirmed", "likely_match") and
                  web_business == "active" and
                  web_relevance in ("high", "medium"))
        li_ok = (li_found == "yes" and
                 result.linkedin_country_match in ("yes", "unknown"))

        if web_ok or li_ok:
            return "A"
        else:
            return "B"

    # needs_manual_tendata_check: 详情页失败，需要人工腾道检查
    if tendata == "needs_manual_tendata_check":
        # 如果官网或LinkedIn有信号，标 B；否则标 C
        web_ok = (web_match in ("confirmed", "likely_match") and
                  web_business == "active")
        li_ok = (li_found == "yes")
        if web_ok or li_ok:
            return "B"
        else:
            return "C"

    # 冲突或其他
    if tendata == "conflict":
        return "manual_review"

    # D: 无信号
    return "D"


def main():
    print(f"[1/4] 读取输入文件...")
    df = pd.read_excel(INPUT_FILE, sheet_name="External_Check_Template")
    print(f"  共 {len(df)} 个客户")

    results = []

    # 加载中间结果（如果存在）
    intermediate_path = Path(__file__).parent.parent / "external_check_results_17_partial.xlsx"
    if intermediate_path.exists():
        print(f"  发现中间结果，加载中...")
        partial_df = pd.read_excel(intermediate_path)
        results = partial_df.to_dict("records")
        processed_ids = {r.get("internal_customer_id", "") for r in results}
        print(f"  已处理 {len(results)} 个")
        # 过滤掉已处理的
        rows_to_process = df[~df["internal_customer_id"].isin(processed_ids)]
    else:
        rows_to_process = df

    checker = ExternalChecker()

    print(f"[2/4] 连接浏览器...")
    checker.connect()

    try:
        for i, (_, row) in enumerate(rows_to_process.iterrows()):
            cid = str(row.get("internal_customer_id", ""))
            name = str(row.get("customer_name", ""))[:40]
            print(f"  [{len(results)+1}/{len(df)}] 核验: {cid} - {name}")

            er = ExternalCheckResult(
                internal_customer_id=cid,
                customer_name=str(row.get("customer_name", "")),
                country_region=str(row.get("country_region", "")),
                website_input=str(row.get("website_input", "")),
                email_domain=str(row.get("email_domain", "")),
                linkedin=str(row.get("linkedin", "")),
                final_tendata_status=str(row.get("final_tendata_status", "")),
                final_matched_company_name=str(row.get("final_matched_company_name", "")),
                final_matched_country=str(row.get("final_matched_country", "")),
                final_import_active_status=str(row.get("final_import_active_status", "")),
                final_latest_import_date=str(row.get("final_latest_import_date", "")),
                final_used_search_variant=str(row.get("final_used_search_variant", "")),
                final_evidence_excerpt=str(row.get("final_evidence_excerpt", "")),
                final_manual_review_reason=str(row.get("final_manual_review_reason", "")),
            )

            # 官网核验
            try:
                web_result = checker.check_website(row.to_dict())
                for k, v in web_result.items():
                    if hasattr(er, k):
                        setattr(er, k, v)
            except Exception as e:
                print(f"      官网: 异常 — {str(e)[:100]}")
                er.website_accessible = "no"
                er.website_match_status = "inaccessible"

            # LinkedIn 核验（加延迟避免限速）
            time.sleep(LINKEDIN_DELAY)
            try:
                li_result = checker.check_linkedin(row.to_dict())
                for k, v in li_result.items():
                    if hasattr(er, k):
                        setattr(er, k, v)
            except Exception as e:
                print(f"      LinkedIn: 异常 — {str(e)[:100]}")
                er.linkedin_company_found = "no"

            # 综合判断
            # contact_still_at_company: 基于多信号
            if er.website_match_status in ("confirmed", "likely_match") and er.website_business_status == "active":
                er.contact_still_at_company = "likely_yes"
            elif er.linkedin_company_found == "yes" and er.linkedin_recent_activity == "yes":
                er.contact_still_at_company = "likely_yes"
            elif er.website_match_status == "inaccessible" and er.linkedin_company_found == "no":
                er.contact_still_at_company = "unknown"
            else:
                er.contact_still_at_company = "uncertain"

            # 外部核验置信度
            confidence = 0
            summary_parts = []

            if er.website_match_status == "confirmed":
                confidence += 30
                summary_parts.append(f"官网确认 ({er.website_evidence_url})")
            elif er.website_match_status == "likely_match":
                confidence += 20
                summary_parts.append(f"官网疑似匹配 ({er.website_evidence_url})")

            if er.linkedin_company_found == "yes":
                confidence += 20
                summary_parts.append(f"LinkedIn公司页 ({er.linkedin_company_url})")
                if er.linkedin_country_match == "yes":
                    confidence += 10
                    summary_parts.append("LinkedIn国家匹配")
                elif er.linkedin_country_match == "no":
                    confidence -= 10
                    summary_parts.append("LinkedIn国家不匹配")
                if er.linkedin_recent_activity == "yes":
                    confidence += 10
                    summary_parts.append("LinkedIn有最近动态")

            if er.final_tendata_status == "confirmed":
                confidence += 20
                summary_parts.append(f"腾道已确认: {er.final_matched_company_name}")
            elif er.final_tendata_status == "likely_match":
                confidence += 10
                summary_parts.append(f"腾道疑似: {er.final_matched_company_name}")

            confidence = max(0, min(100, confidence))
            er.external_check_confidence = confidence
            er.external_check_summary = "; ".join(summary_parts) if summary_parts else "无有效证据"

            # 优先级
            er.followup_priority_candidate = compute_priority(er)

            # 人工复核标记
            if er.followup_priority_candidate == "manual_review":
                er.manual_review_flag = "yes"
                reason_parts = []
                if er.final_tendata_status == "conflict":
                    reason_parts.append("腾道结果冲突")
                if er.linkedin_country_match == "no":
                    reason_parts.append("LinkedIn国家不匹配")
                if er.website_match_status == "unconfirmed":
                    reason_parts.append("官网主体不确定")
                er.manual_review_reason = "; ".join(reason_parts) if reason_parts else "综合判断需人工复核"
            elif er.website_match_status == "inaccessible" and er.linkedin_company_found == "no":
                er.manual_review_flag = "yes"
                er.manual_review_reason = "官网不可访问且无LinkedIn公司页"
            else:
                er.manual_review_flag = "no"

            results.append(asdict(er))

            # 每处理 5 个保存中间结果
            if len(results) % SAVE_INTERVAL == 0:
                temp_df = pd.DataFrame(results)
                temp_df.to_excel(intermediate_path, index=False)
                print(f"  [中间结果] 已保存 {len(results)} 条")

    finally:
        checker.close()

    # [3/4] 导出最终结果
    print(f"[3/4] 导出最终结果...")
    final_df = pd.DataFrame(results)
    final_df.to_excel(OUTPUT_FILE, index=False)
    print(f"  结果已保存: {OUTPUT_FILE}")

    # [4/4] 清理中间结果 + 汇总
    if intermediate_path.exists():
        intermediate_path.unlink()
        print(f"  已清理中间结果")

    print(f"[4/4] 汇总统计...")
    print()

    # 优先级统计
    print("=== 优先级分布 ===")
    priority_counts = {}
    for r in results:
        p = r.get("followup_priority_candidate", "unknown")
        priority_counts[p] = priority_counts.get(p, 0) + 1
    for p in ["A", "B", "C", "D", "manual_review"]:
        c = priority_counts.get(p, 0)
        if c > 0:
            print(f"  {p}: {c} 条")

    print()

    # 需要人工复核的
    print("=== 需要人工复核的客户 ===")
    manual_rows = [r for r in results if r.get("manual_review_flag") == "yes"]
    for r in manual_rows:
        print(f"  {r['internal_customer_id']} | {r['customer_name'][:30]} | "
              f"tendata={r['final_tendata_status']} | "
              f"web={r['website_match_status']} | "
              f"li={r['linkedin_company_found']} | "
              f"reason={r['manual_review_reason'][:60]}")
    print(f"  共 {len(manual_rows)} 条")

    print()

    # 详细列表
    print("=== 全部 17 个客户核验结果 ===")
    for r in results:
        print(f"  {r['internal_customer_id']} | {r['customer_name'][:30]:30s} | "
              f"priority={r['followup_priority_candidate']} | "
              f"tendata={r['final_tendata_status']:25s} | "
              f"web={r['website_match_status']:15s} | "
              f"li={r['linkedin_company_found']:8s} | "
              f"conf={r['external_check_confidence']}")


if __name__ == "__main__":
    main()
