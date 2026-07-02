"""
google_verifier.py — Google/DuckDuckGo 公司验证

通过 Chrome CDP + Playwright 搜索公司并验证官网。
复用 public_info_query.py 已验证的 DDG HTML 搜索 + 网页读取逻辑。

用法:
    from scripts.google_verifier import GoogleVerifier
    v = GoogleVerifier()
    signals = v.verify("TEXON CO LTD", "韩国")
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ============================================================================
# 配置
# ============================================================================

EXCLUDED_DOMAINS = [
    "alibaba.com", "made-in-china.com", "globalsources.com",
    "exportersindia.com", "tradeindia.com", "indiamart.com",
    "kompass.com", "yellowpages.com", "yelp.com", "yell.com",
    "facebook.com", "instagram.com", "twitter.com", "youtube.com",
    "linkedin.com", "wikipedia.org",
    "tendata.cn", "tendata.com", "panjiva.com", "importgenius.com",
]

PRODUCT_KEYWORDS_HIGH = [
    "stainless steel pipe", "stainless steel tube", "stainless pipe",
    "steel pipe", "steel tube", "welded pipe", "seamless pipe",
    "pipe fitting", "tube fitting", "flange", "butt weld",
    "cold drawn pipe", "cold rolled tube", "stainless steel fitting",
    "square tube", "rectangular pipe", "sanitary tube", "industrial pipe",
    "process piping", "cleanroom pipe", "ultra high purity",
    "semiconductor equipment", "wafer fab", "foundry",
]

PRODUCT_KEYWORDS_MEDIUM = [
    "metal fabrication", "steel fabrication", "metal manufacturing",
    "steel structure", "metal component", "precision engineering",
    "machinery", "industrial equipment", "plant equipment",
    "manufacturing", "construction", "engineering",
]


# ============================================================================
# 工具函数
# ============================================================================

def _clean_company_name(name: str) -> str:
    suffixes = [
        r'\b(LTD|L\.L\.C\.?|LLC|INC\.?|CORP\.?|CORPORATION)\b',
        r'\b(CO\.,?\s*LTD|CO\.?)\b',
        r'\b(GMBH|AG|S\.A\.|S\.R\.L\.|B\.V\.|N\.V\.|A\.S\.)\b',
        r'\b(PVT|PTE?)\b', r'\bLIMITED\b',
    ]
    result = name.strip()
    for pat in suffixes:
        result = re.sub(pat, '', result, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', result).strip().strip(',').strip()


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc or url
        return re.sub(r'^www\.', '', domain.lower())
    except Exception:
        return ""


def _find_emails(text: str) -> list[str]:
    if not text:
        return []
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return [e for e in re.findall(pattern, text)
            if not e.endswith(('.png', '.jpg', '.svg', '.webp'))]


def _find_phones(text: str) -> list[str]:
    if not text:
        return []
    phones = []
    for pat in [r'\+?\d{1,4}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}',
                r'\(\d{2,4}\)\s?\d{3,4}[\s-]?\d{3,4}']:
        phones.extend(re.findall(pat, text))
    return [p.strip() for p in phones if len(re.sub(r'\D', '', p)) >= 7]


# ============================================================================
# GoogleVerifier
# ============================================================================

@dataclass
class SearchResult:
    url: str
    title: str


class GoogleVerifier:
    """Google/DuckDuckGo 公司验证器（需要 Chrome CDP 已启动）"""

    def __init__(self, cdp_url: str = "http://localhost:9222"):
        self.cdp_url = cdp_url
        self._browser = None
        self._pw = None
        self._page = None

    def _connect(self):
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("需要 playwright: pip3 install playwright")
        if self._browser is not None:
            return
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError("Chrome 无可用 context")
            # 复用已有页面或创建新页面
            pages = contexts[0].pages
            if pages:
                self._page = pages[0]
            else:
                self._page = contexts[0].new_page()
        except Exception as e:
            if self._pw:
                self._pw.stop()
                self._pw = None
            raise RuntimeError(f"CDP 连接失败: {e}")

    # ── 搜索 ──

    def search(self, company_name: str, country: str = "", limit: int = 8) -> list[SearchResult]:
        """DuckDuckGo HTML 版搜索（CDP浏览器方式，已验证可用）"""
        self._connect()

        query = f"{company_name} {country} official website company"
        query = re.sub(r'\s+', ' ', query).strip()

        results: list[SearchResult] = []
        try:
            encoded = query.replace(' ', '+')
            self._page.goto(
                f"https://html.duckduckgo.com/html/?q={encoded}",
                timeout=25000, wait_until="domcontentloaded"
            )
            time.sleep(1)

            ddg_results = self._page.evaluate('''(limit) => {
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
                    if (!href.startsWith('http')) continue;
                    if (seen.has(href)) continue;
                    seen.add(href);
                    items.push({url: href, title: text.substring(0, 120)});
                    if (items.length >= limit) break;
                }
                return items;
            }''', limit)

            for r in ddg_results or []:
                domain = _extract_domain(r["url"])
                if any(ex in domain for ex in EXCLUDED_DOMAINS):
                    continue
                results.append(SearchResult(url=r["url"], title=r["title"]))
        except Exception as e:
            print(f"    [Google] 搜索失败: {e}")

        return results[:limit]

    # ── 读取网页 ──

    def _read_page(self, url: str) -> dict:
        """用 Playwright 读取网页内容"""
        self._connect()
        result = {"found": False, "title": "", "content": ""}
        try:
            self._page.goto(url, timeout=20000, wait_until="domcontentloaded")
            time.sleep(1.5)
            result["title"] = self._page.title() or ""
            result["content"] = self._page.evaluate('''() => {
                const main = document.querySelector('article') || document.querySelector('main') || document.body;
                if (!main) return '';
                const clone = main.cloneNode(true);
                for (const sel of ['script','style','nav','footer','header','aside','iframe','noscript','svg']) {
                    clone.querySelectorAll(sel).forEach(el => el.remove());
                }
                const text = (clone.textContent || '').replace(/\\s+/g, ' ').trim();
                return text.substring(0, 6000);
            }''')
            if result["content"] and len(result["content"]) > 100:
                result["found"] = True
        except Exception as e:
            print(f"    [Google] 读取页面失败 {url[:60]}: {e}")
        return result

    # ── 选择最佳网站 ──

    def _pick_best_url(self, search_results: list[SearchResult], company_name: str,
                       website_hint: str = "") -> str:
        """从搜索结果中选最佳候选网站"""
        if not search_results:
            return website_hint if website_hint else ""

        if website_hint:
            hint_domain = _extract_domain(website_hint)
            for r in search_results:
                if hint_domain in _extract_domain(r.url):
                    return r.url

        clean = _clean_company_name(company_name).lower()
        name_parts = [p for p in clean.split() if len(p) >= 3]

        for r in search_results:
            domain = _extract_domain(r.url)
            title_lower = r.title.lower()
            for part in name_parts[:2]:
                if part in domain or part in title_lower:
                    return r.url

        return search_results[0].url

    # ── 行业分类 ──

    def _classify_business(self, text: str) -> str:
        text_lower = (text or "").lower()[:4000]

        manufacturer = sum(1 for s in [
            "manufacturer", "factory", "production", "fabrication",
            "manufacturing", "workshop", "cnc machine", "made in", "produce"
        ] if s in text_lower)
        distributor = sum(1 for s in [
            "distributor", "distribution", "stockist", "supplier",
            "wholesale", "supply chain", "importer", "exporter"
        ] if s in text_lower)
        engineering = sum(1 for s in [
            "engineering", "contractor", "design", "installation",
            "commissioning", "project management"
        ] if s in text_lower)

        if manufacturer >= 2 and distributor >= 2:
            return "manufacturer_distributor"
        if manufacturer >= 2:
            return "manufacturer"
        if engineering >= 2:
            return "engineering_firm"
        if distributor >= 2:
            return "distributor"
        if any(s in text_lower for s in ["trading company", "import export", "trading co"]):
            return "trader"
        return "unknown"

    # ── 产品关键词匹配 ──

    def _match_products(self, text: str) -> list[str]:
        text_lower = (text or "").lower()
        found = []
        for kw in PRODUCT_KEYWORDS_HIGH:
            if kw.lower() in text_lower:
                found.append(kw)
        if not found:
            for kw in PRODUCT_KEYWORDS_MEDIUM:
                if kw.lower() in text_lower:
                    found.append(kw)
        return found[:8]

    # ── 主入口 ──

    def verify(self, company_name: str, country: str = "",
               website_hint: str = "") -> dict:
        """
        验证一家公司
        Returns: dict — 用于构建 GoogleSourceSignals
        """
        result = {
            "company_found": False,
            "official_website": "",
            "website_title": "",
            "website_match_confidence": 0.0,
            "industry_keywords_found": [],
            "business_type": "unknown",
            "product_keywords_found": [],
            "contact_email": "",
            "contact_phone": "",
            "evidence_urls": [],
            "search_snippet": "",
            "company_status": "unknown",
            "confidence": 0,
            "error": "",
        }

        # 1. 搜索
        search_results = self.search(company_name, country)
        result["evidence_urls"] = [r.url for r in search_results[:3]]
        result["search_snippet"] = search_results[0].title[:200] if search_results else ""

        if not search_results:
            result["error"] = "未找到搜索结果"
            return result

        result["company_found"] = True

        # 2. 选择最佳网站
        best_url = self._pick_best_url(search_results, company_name, website_hint)
        result["official_website"] = best_url

        if not best_url:
            result["error"] = "未找到合适的官方网站"
            result["confidence"] = 10
            return result

        # 3. 读取网站
        page_info = self._read_page(best_url)
        if not page_info["found"]:
            result["error"] = "网站无法访问或内容过少"
            result["confidence"] = 15
            return result

        result["website_title"] = page_info["title"]
        content = page_info["content"]

        # 4. 网站内容与公司名的匹配度
        clean = _clean_company_name(company_name)
        name_in_title = clean.lower() in page_info["title"].lower()
        name_in_content = clean.lower() in content.lower()
        if name_in_title:
            result["website_match_confidence"] = 0.9
        elif name_in_content:
            result["website_match_confidence"] = 0.6
        else:
            result["website_match_confidence"] = 0.3

        # 5. 行业关键词 + 产品匹配
        result["industry_keywords_found"] = self._match_products(content)
        result["product_keywords_found"] = [k for k in result["industry_keywords_found"]
                                            if any(h in k for h in ["pipe", "tube", "fitting", "flange", "steel"])]

        # 6. 业务类型
        result["business_type"] = self._classify_business(content)

        # 7. 联系方式
        emails = _find_emails(content)
        phones = _find_phones(content)
        if emails:
            result["contact_email"] = emails[0]
        if phones:
            result["contact_phone"] = phones[0]

        # 8. 置信度 (0-100)
        score = 20  # 有搜索结果
        if page_info["found"]:
            score += 25
        if result["website_match_confidence"] > 0.6:
            score += 20
        if result["product_keywords_found"]:
            score += 15
        if result["contact_email"] or result["contact_phone"]:
            score += 10
        if result["business_type"] != "unknown":
            score += 10
        result["confidence"] = min(score, 100)
        result["company_status"] = "active" if page_info["found"] else "unknown"

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
        self._page = None


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    v = GoogleVerifier()
    try:
        print("验证: LAM RESEARCH MANUFACTURING KOREA LLC (韩国)")
        s = v.verify("LAM RESEARCH MANUFACTURING KOREA LLC", "韩国")
        print(f"  网站: {s['official_website']}")
        print(f"  业务类型: {s['business_type']}")
        print(f"  产品关键词: {s['product_keywords_found'][:5]}")
        print(f"  置信度: {s['confidence']}")
        print(f"  邮箱: {s['contact_email']}")
        print(f"  电话: {s['contact_phone']}")
    finally:
        v.close()
