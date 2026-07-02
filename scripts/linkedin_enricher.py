"""
linkedin_enricher.py — LinkedIn 公司信息提取

通过 Chrome CDP 搜索并提取 LinkedIn 公司页面信息。
复用 verify_external_v4.py 的搜索和匹配逻辑。

用法:
    from scripts.linkedin_enricher import LinkedInEnricher
    li = LinkedInEnricher()
    signals = li.enrich("TEXON CO LTD", "韩国")
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ============================================================================
# 工具函数
# ============================================================================

def _normalize_name(name: str) -> str:
    """标准化公司名用于对比"""
    suffixes = [
        r'\b(LTD|L\.L\.C\.?|LLC|INC\.?|CORP\.?|CORPORATION|GMBH|AG|S\.A\.|S\.R\.L\.|B\.V\.|N\.V\.|A\.S\.|PTE?|LIMITED|CO\.?)\b',
    ]
    result = name.strip().lower()
    for pat in suffixes:
        result = re.sub(pat, '', result, flags=re.IGNORECASE)
    return re.sub(r'[^a-z0-9]', '', result)


def _extract_slug(url: str) -> str:
    """从 LinkedIn URL 提取公司 slug"""
    if not url:
        return ""
    m = re.search(r'linkedin\.com/company/([^/?&]+)', url)
    return m.group(1) if m else ""


# ============================================================================
# LinkedInEnricher
# ============================================================================

class LinkedInEnricher:
    """LinkedIn 公司信息提取器（需要 Chrome CDP）"""

    def __init__(self, cdp_url: str = "http://localhost:9222"):
        self.cdp_url = cdp_url
        self._browser = None
        self._pw = None
        self._page = None

    def _connect(self):
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("需要 playwright")
        if self._browser is not None:
            return
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError("Chrome 无可用 context")
            pages = contexts[0].pages
            self._page = pages[0] if pages else contexts[0].new_page()
        except Exception as e:
            if self._pw:
                self._pw.stop()
                self._pw = None
            raise RuntimeError(f"CDP 连接失败: {e}")

    # ── 搜索 LinkedIn 公司页 ──

    def _search_linkedin_company(self, company_name: str, country: str = "") -> list[dict]:
        """搜索 LinkedIn 公司页面"""
        self._connect()
        query = f"{company_name} {country}"
        query = re.sub(r'\s+', ' ', query).strip()

        results = []
        try:
            # 用 Google 搜索 LinkedIn 公司页（比 DDG 结果更好）
            encoded = query.replace(' ', '+')
            self._page.goto(
                f"https://html.duckduckgo.com/html/?q=site:linkedin.com/company+{encoded}",
                timeout=20000, wait_until="domcontentloaded"
            )
            time.sleep(1.5)

            results = self._page.evaluate('''() => {
                const items = [];
                const seen = new Set();
                const links = document.querySelectorAll('a.result__a');
                for (const a of links) {
                    let href = a.getAttribute('href') || '';
                    const text = (a.textContent || '').trim();
                    if (!href || !text) continue;
                    if (href.includes('uddg=')) {
                        const m = href.match(/uddg=([^&]+)/);
                        if (m) href = decodeURIComponent(m[1]);
                    }
                    if (!href.includes('linkedin.com/company/')) continue;
                    if (seen.has(href)) continue;
                    seen.add(href);
                    items.push({url: href, title: text.substring(0, 150)});
                    if (items.length >= 5) break;
                }
                return items;
            }''')
        except Exception as e:
            print(f"    [LinkedIn] 搜索失败: {e}")

        return results

    # ── 读取 LinkedIn 公司页 ──

    def _read_linkedin_page(self, url: str) -> dict:
        """读取 LinkedIn 公司页面，提取结构化信息"""
        self._connect()
        result = {
            "found": False, "company_name": "", "industry_tags": [],
            "employee_count": "", "employee_range": "",
            "description": "", "specialties": [], "url": url,
        }
        try:
            self._page.goto(url, timeout=25000, wait_until="domcontentloaded")
            time.sleep(2)

            # 提取页面元数据和结构化数据
            page_data = self._page.evaluate('''() => {
                const data = {};
                // Title
                data.title = document.title || '';

                // Meta description
                const metaDesc = document.querySelector('meta[name="description"]');
                data.description = metaDesc ? metaDesc.getAttribute('content') : '';

                // og:description
                const ogDesc = document.querySelector('meta[property="og:description"]');
                data.og_description = ogDesc ? ogDesc.getAttribute('content') : '';

                // JSON-LD
                try {
                    const ld = document.querySelector('script[type="application/ld+json"]');
                    if (ld) data.jsonld = JSON.parse(ld.textContent);
                } catch(e) { data.jsonld = null; }

                // Page text (first 4000 chars of main content)
                const main = document.querySelector('main') || document.body;
                if (main) {
                    const clone = main.cloneNode(true);
                    for (const sel of ['script','style','nav','footer','header']) {
                        clone.querySelectorAll(sel).forEach(el => el.remove());
                    }
                    data.pageText = (clone.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 4000);
                } else {
                    data.pageText = '';
                }

                // Employee count: look for "X employees" or "X-XXX employees" patterns
                const bodyText = (document.body ? document.body.textContent || '' : '');
                const empMatch = bodyText.match(/(\\d{1,3}(?:,\\d{3})*)\\s*(?:-|to)\\s*(\\d{1,3}(?:,\\d{3})*)\\s*employees/i);
                const empMatch2 = bodyText.match(/(\\d{1,3}(?:,\\d{3})*)\\s*employees/i);
                data.empPatternRange = empMatch ? empMatch[0] : '';
                data.empPatternSingle = empMatch2 ? empMatch2[0] : '';

                return data;
            }''')

            if page_data.get("pageText"):
                result["found"] = True

            # 公司名（从标题提取）
            title = page_data.get("title", "")
            # LinkedIn 标题格式: "Company Name | LinkedIn"
            if "|" in title:
                result["company_name"] = title.split("|")[0].strip()
            elif "LinkedIn" in title:
                result["company_name"] = title.replace("LinkedIn", "").strip().rstrip("|").strip()

            # 描述
            desc = page_data.get("og_description") or page_data.get("description") or ""
            result["description"] = desc[:500]

            # 员工数
            emp_text = page_data.get("empPatternRange") or page_data.get("empPatternSingle") or ""
            if emp_text:
                result["employee_count"] = emp_text
                # 提取范围 "51-200" 或单值
                nums = re.findall(r'[\d,]+', emp_text)
                if len(nums) >= 2:
                    result["employee_range"] = f"{nums[0]}-{nums[-1]}"
                elif len(nums) == 1:
                    n = int(nums[0].replace(',', ''))
                    if n < 10:
                        result["employee_range"] = "1-10"
                    elif n < 50:
                        result["employee_range"] = "11-50"
                    elif n < 200:
                        result["employee_range"] = "51-200"
                    elif n < 1000:
                        result["employee_range"] = "201-1000"
                    else:
                        result["employee_range"] = "1001+"

            # 行业标签（从页面文本提取）
            page_text = (page_data.get("pageText") or "").lower()
            industry_keywords = [
                "steel", "metal", "manufacturing", "semiconductor",
                "construction", "engineering", "mechanical", "industrial",
                "machinery", "fabrication", "oil & gas", "energy",
                "chemical", "petrochemical", "piping", "plumbing",
                "automotive", "aerospace", "defense", "electronics",
                "cleanroom", "wafer", "equipment manufacturer",
                "process equipment", "plant engineering",
            ]
            result["industry_tags"] = [kw for kw in industry_keywords if kw in page_text][:5]

            # Specialties (from JSON-LD if available)
            jsonld = page_data.get("jsonld") or {}
            if isinstance(jsonld, dict):
                result["specialties"] = jsonld.get("knowsAbout", []) or []

        except Exception as e:
            print(f"    [LinkedIn] 读取页面失败 {url[:60]}: {e}")

        return result

    # ── 搜索关键联系人 ──

    def _search_key_contacts(self, company_name: str) -> list[dict]:
        """搜索公司关键联系人（采购/供应链相关）"""
        self._connect()
        contacts = []
        try:
            query = f"{company_name} procurement OR purchasing OR sourcing manager"
            encoded = query.replace(' ', '+')
            self._page.goto(
                f"https://html.duckduckgo.com/html/?q=site:linkedin.com/in+{encoded}",
                timeout=15000, wait_until="domcontentloaded"
            )
            time.sleep(1)

            results = self._page.evaluate('''() => {
                const items = [];
                const links = document.querySelectorAll('a.result__a');
                for (const a of links) {
                    let href = a.getAttribute('href') || '';
                    const text = (a.textContent || '').trim();
                    if (!href || !text) continue;
                    if (href.includes('uddg=')) {
                        const m = href.match(/uddg=([^&]+)/);
                        if (m) href = decodeURIComponent(m[1]);
                    }
                    if (!href.includes('linkedin.com/in/')) continue;
                    items.push({url: href, title: text});
                    if (items.length >= 3) break;
                }
                return items;
            }''')

            for r in (results or []):
                # 从标题提取姓名和职位
                title = r["title"]
                parts = title.split(" - ")
                name = parts[0].strip() if parts else ""
                position = parts[1].strip() if len(parts) > 1 else ""
                if "LinkedIn" in position:
                    position = ""
                if name and len(name) < 60:
                    contacts.append({"name": name, "title": position, "url": r["url"]})
        except Exception as e:
            print(f"    [LinkedIn] 联系人搜索失败: {e}")

        return contacts[:3]

    # ── 主入口 ──

    def enrich(self, company_name: str, country: str = "",
               website_hint: str = "") -> dict:
        """
        提取 LinkedIn 公司信息
        Returns: dict — 用于构建 LinkedInSourceSignals
        """
        result = {
            "company_page_found": False,
            "company_url": "",
            "company_name_on_li": "",
            "name_match_status": "no_match",
            "industry_tags": [],
            "employee_count_range": "",
            "employee_count_estimate": 0,
            "key_contacts": [],
            "country_match": False,
            "specialties": [],
            "company_description": "",
            "founded_year": "",
            "confidence": 0,
            "error": "",
        }

        # 1. 搜索 LinkedIn 公司页
        search_results = self._search_linkedin_company(company_name, country)

        if not search_results:
            result["error"] = "LinkedIn 未找到公司页面"
            return result

        # 2. 找最佳匹配
        norm_input = _normalize_name(company_name)
        best = search_results[0]
        best_slug_match = 0

        for r in search_results:
            slug = _extract_slug(r["url"])
            # slug 匹配公司名关键词
            slug_match = sum(1 for part in norm_input[:8] if part in slug.lower()) if norm_input else 0
            if slug_match > best_slug_match:
                best_slug_match = slug_match
                best = r

        result["company_url"] = best["url"]
        result["company_page_found"] = True

        # 3. 读 LinkedIn 公司页
        page_info = self._read_linkedin_page(best["url"])

        result["company_name_on_li"] = page_info.get("company_name", "")
        result["description"] = page_info.get("description", "")
        result["industry_tags"] = page_info.get("industry_tags", [])
        result["employee_count_range"] = page_info.get("employee_range", "")
        result["specialties"] = page_info.get("specialties", [])

        # 4. 公司名匹配度
        li_name_normalized = _normalize_name(page_info.get("company_name", ""))
        if li_name_normalized == norm_input:
            result["name_match_status"] = "confirmed"
            result["confidence"] = 85
        elif li_name_normalized and (li_name_normalized in norm_input or norm_input in li_name_normalized):
            result["name_match_status"] = "likely_match"
            result["confidence"] = 60
        elif li_name_normalized:
            from difflib import SequenceMatcher
            ratio = SequenceMatcher(None, norm_input, li_name_normalized).ratio()
            if ratio > 0.6:
                result["name_match_status"] = "likely_match"
                result["confidence"] = 50
            else:
                result["name_match_status"] = "no_match"
                result["confidence"] = 20
        else:
            result["confidence"] = 30  # 页面找到了但没提取到公司名

        # 5. 国家匹配
        country_lower = country.lower()
        page_text = (page_info.get("description", "") + " " + " ".join(page_info.get("industry_tags", []))).lower()
        result["country_match"] = country_lower in page_text

        # 6. 搜索关键联系人
        result["key_contacts"] = self._search_key_contacts(company_name)

        # 7. 计算最终置信度
        if result["key_contacts"]:
            result["confidence"] = min(result["confidence"] + 10, 100)
        if result["employee_count_range"]:
            result["confidence"] = min(result["confidence"] + 5, 100)

        return result

    def close(self):
        if self._browser:
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    li = LinkedInEnricher()
    try:
        print("LinkedIn 搜索: TEXON CO LTD (韩国)")
        s = li.enrich("TEXON CO LTD", "韩国")
        print(f"  页面: {s['company_url'][:80]}")
        print(f"  LI公司名: {s['company_name_on_li']}")
        print(f"  匹配状态: {s['name_match_status']}")
        print(f"  员工: {s['employee_count_range']}")
        print(f"  行业: {s['industry_tags']}")
        print(f"  联系人: {len(s['key_contacts'])}")
        print(f"  置信度: {s['confidence']}")
    finally:
        li.close()
