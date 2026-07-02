"""
public_info_query.py — 客户公开信息查询脚本（增强版）

功能：
1. 使用 opencli google search 搜索公司官网
2. 使用 opencli web read 读取官网内容，提取公司简介、产品、规模
3. 使用 opencli google search 搜索 LinkedIn 公司页面
4. 使用 opencli web read 读取 LinkedIn 公司页面
5. 如果官网和LinkedIn都没找到，查询可信第三方页面
6. 汇总公开信息，生成置信度评估

用法：
    # 测试模式：只查前 10 个客户
    python scripts/public_info_query.py --input input/opencli_public_info_test_10.xlsx --test

    # 完整模式：查询全部客户
    python scripts/public_info_query.py --input input/opencli_public_info_priority_200.xlsx
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, Page, Browser

# 确保控制台 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ============================================================================
# 全局浏览器连接（Playwright CDP）
# ============================================================================

_browser: Browser | None = None
_playwright_ctx = None
_search_page: Page | None = None


def init_browser(cdp_url: str = "http://localhost:9222"):
    """通过 CDP 连接已有 Chrome 实例。"""
    global _browser, _playwright_ctx, _search_page
    if _browser is not None:
        return
    pw = sync_playwright().start()
    _playwright_ctx = pw
    _browser = pw.chromium.connect_over_cdp(cdp_url)
    contexts = _browser.contexts
    if not contexts:
        raise RuntimeError("Chrome 没有可用的 browser context，请先启动 Chrome")
    # 用一个独立页面做搜索，避免反复创建/关闭
    _search_page = contexts[0].new_page()
    print(f"  [浏览器] 已连接 Chrome (contexts={len(contexts)})")


def close_browser():
    """关闭搜索页面（不关闭 Chrome 本身）。"""
    global _browser, _playwright_ctx, _search_page
    if _search_page and not _search_page.is_closed():
        try:
            _search_page.close()
        except Exception:
            pass
    _search_page = None
    # 不关闭 browser（它是 CDP 连接的，close 会断开 Chrome）
    _browser = None
    if _playwright_ctx:
        try:
            _playwright_ctx.stop()
        except Exception:
            pass
        _playwright_ctx = None


# ============================================================================
# 配置常量
# ============================================================================

# 明显非官网域名（排除）
EXCLUDED_DOMAINS = [
    # 社交平台
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "pinterest.com",
    "tiktok.com",
    # B2B 平台
    "alibaba.com",
    "made-in-china.com",
    "globalsources.com",
    "indiamart.com",
    "tradeindia.com",
    "ec21.com",
    "ecplaza.net",
    "dhgate.com",
    "globalspec.com",
    "thomasnet.com",
    # 企业目录/信息站
    "zaubacorp.com",
    "dnb.com",
    "zoominfo.com",
    "importgenius.com",
    "volza.com",
    "panjiva.com",
    "tendata.com",
    "companycheck.co.uk",
    "opencorporates.com",
    "beta.companieshouse.gov.uk",
    "crunchbase.com",
    "bloomberg.com",
    "pitchbook.com",
    # 搜索引擎
    "google.com",
    "bing.com",
    "yahoo.com",
    "baidu.com",
    # 维基百科
    "wikipedia.org",
    # 其他目录站
    "findthecompany.com",
    "bizapedia.com",
    "buzzfile.com",
    "corporationwiki.com",
    "databy.com",
    "bizstands.com",
    "orgboard.com",
    "falconebiz.com",
    "sgpgrid.com",
    "yellowpages.com",
    "yp.com",
    "yelp.com",
]

# 目录站特征关键词
DIRECTORY_KEYWORDS = [
    "directory",
    "listing",
    "company-profile",
    "business-directory",
    "company-details",
    "find-company",
    "company-info",
    "companies/",
    "yellowpages",
    "opencorporates",
    "companycheck",
]

# 可信第三方来源（用于补充信息）
TRUSTED_THIRD_PARTY = [
    "crunchbase.com",
    "bloomberg.com",
    "dnb.com",
    "kompass.com",
    "europages.com",
    "thomasnet.com",
]


# ============================================================================
# 工具函数
# ============================================================================

def _check_captcha(page: Page) -> bool:
    """检测当前页面是否为 Google/DuckDuckGo 的拦截页面。"""
    try:
        url = page.url
        if 'sorry' in url or 'captcha' in url.lower():
            return True
        blocked = page.evaluate('''() => {
            const body = document.body?.textContent || '';
            return body.includes('unusual traffic') || body.includes('not a robot');
        }''')
        return bool(blocked)
    except Exception:
        return False


def search_google(query: str, limit: int = 5) -> list:
    """使用 DuckDuckGo HTML 版搜索，返回 [{url, title}, ...]。
    DuckDuckGo HTML 版 (html.duckduckgo.com) 几乎不会触发验证码。"""
    page = _search_page
    if not page:
        print("    [ERROR] 浏览器未初始化")
        return []

    results = []
    try:
        encoded_q = query.replace(' ', '+')
        url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(1.5)

        results = page.evaluate('''(limit) => {
            const items = [];
            const seen = new Set();
            // DuckDuckGo HTML: 结果标题链接 class="result__a"
            const titleLinks = document.querySelectorAll('a.result__a');
            for (const a of titleLinks) {
                let href = a.getAttribute('href') || '';
                const text = (a.textContent || '').trim();
                if (!href || !text) continue;
                // 从 uddg= 参数提取真实 URL
                if (href.includes('uddg=')) {
                    try {
                        const m = href.match(/uddg=([^&]+)/);
                        if (m) href = decodeURIComponent(m[1]);
                    } catch(e) {}
                }
                if (!href.startsWith('http')) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                items.push({url: href, title: text.substring(0, 120)});
                if (items.length >= limit) break;
            }
            return items;
        }''', limit)

    except Exception as e:
        print(f"    [ERROR] 搜索异常: {str(e)[:60]}")

    return results


def read_webpage(url: str) -> dict | None:
    """使用 Playwright 读取网页内容，返回 {content, title}。"""
    page = _search_page
    if not page:
        return None

    try:
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(2)  # 等待动态内容

        title = page.title()
        content = page.evaluate('''() => {
            // 提取正文：优先 article / main，退回 body
            const main = document.querySelector('article') || document.querySelector('main') || document.body;
            if (!main) return '';
            // 移除 script / style / nav / footer
            const clone = main.cloneNode(true);
            for (const sel of ['script','style','nav','footer','header','aside','iframe','noscript']) {
                clone.querySelectorAll(sel).forEach(el => el.remove());
            }
            return (clone.textContent || '').replace(/\\s+/g, ' ').trim();
        }''')

        if content and len(content) > 30:
            return {"content": content[:8000], "title": title}
        return None

    except Exception as e:
        print(f"    [ERROR] 读取网页异常: {str(e)[:60]}")
        return None


def extract_domain_name(url: str) -> str:
    """从 URL 提取主域名。"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        domain = re.sub(r'^www\.', '', domain.lower())
        parts = domain.split('.')
        if len(parts) >= 2:
            return '.'.join(parts[-2:])
        return domain
    except:
        return ""


def is_excluded_domain(url: str) -> bool:
    """判断 URL 是否在排除域名列表中。"""
    if not url:
        return True
    url_lower = url.lower()

    for excluded in EXCLUDED_DOMAINS:
        if excluded in url_lower:
            return True

    for keyword in DIRECTORY_KEYWORDS:
        if keyword in url_lower:
            return True

    return False


def is_trusted_third_party(url: str) -> bool:
    """判断 URL 是否是可信第三方来源。"""
    if not url:
        return False
    url_lower = url.lower()

    for trusted in TRUSTED_THIRD_PARTY:
        if trusted in url_lower:
            return True
    return False


def extract_company_keywords(company_name: str) -> list:
    """从公司名提取关键词。"""
    if not company_name:
        return []

    name = company_name.lower()
    for suffix in ['co., ltd', 'co.,ltd', 'co ltd', 'co. ltd', 'corp.', 'corporation',
                   'inc.', 'inc', 'llc', 'ltd', 'limited', 'pte ltd', 'pte. ltd',
                   'sdn bhd', 'sdn. bhd', 'gmbh', 's.a.', 's.a', 'sa', 'bv', 'b.v.',
                   'private limited', 'pvt ltd', 'pvt. ltd']:
        name = name.replace(suffix, '')

    words = re.findall(r'[a-z0-9]+', name)
    keywords = [w for w in words if len(w) >= 3]
    return keywords[:5]


def url_matches_company(url: str, company_name: str) -> bool:
    """判断 URL 域名是否匹配公司名关键词。"""
    if not url or not company_name:
        return False

    domain = extract_domain_name(url)
    keywords = extract_company_keywords(company_name)

    for kw in keywords:
        if kw in domain:
            return True
    return False


def extract_summary_from_content(content: str, company_name: str) -> str:
    """从网页内容提取公司简介。"""
    if not content:
        return ""

    # 清理内容
    content = content.replace('\n', ' ').replace('\r', ' ')
    content = re.sub(r'\s+', ' ', content)

    # 尝试提取包含公司名的段落
    sentences = content.split('.')
    relevant_sentences = []

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10:
            continue
        # 查找包含公司名或关键词的句子
        keywords = extract_company_keywords(company_name)
        for kw in keywords:
            if kw.lower() in sentence.lower():
                relevant_sentences.append(sentence)
                break

    if relevant_sentences:
        # 取前3个相关句子
        summary = '. '.join(relevant_sentences[:3])
        if len(summary) > 500:
            summary = summary[:500] + '...'
        return summary

    # 如果没有找到相关句子，尝试提取前200字符作为摘要
    if len(content) > 100:
        return content[:200] + '...'

    return content[:200]


def extract_products_from_content(content: str) -> str:
    """从网页内容提取产品/服务信息。"""
    if not content:
        return ""

    content = content.lower()

    # 常见产品关键词
    product_keywords = [
        'stainless steel', 'steel', 'pipe', 'tube', 'fitting', 'flange',
        'plate', 'sheet', 'bar', 'rod', 'wire', 'coil', 'valve',
        'product', 'service', 'manufacture', 'supply', 'export',
        'oil', 'gas', 'petrochemical', 'chemical', 'energy',
        'automotive', 'construction', 'industrial'
    ]

    found_products = []
    for kw in product_keywords:
        if kw in content:
            found_products.append(kw)

    if found_products:
        return ', '.join(found_products[:8])

    return ""


def extract_employee_size(content: str) -> str:
    """从内容提取员工规模。"""
    if not content:
        return ""

    content = content.lower()

    # 常见员工规模模式
    patterns = [
        r'(\d+[-+]?\s*(?:employees|staff|workers|people))',
        r'((?:small|medium|large)\s*(?:business|company|enterprise))',
        r'(\d+\s*[-]\s*\d+\s*(?:employees|staff))',
        r'(team\s*of\s*\d+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(1).strip()

    return ""


# ============================================================================
# 查询函数
# ============================================================================

def query_official_website(company_name: str, country: str = "") -> dict:
    """查询公司官网并读取内容。"""
    result = {
        "official_website_url": "",
        "website_company_name": "",
        "website_country": "",
        "website_products_or_services": "",
        "website_summary": "",
        "website_found": False,
    }

    # 构建查询
    if country:
        country_clean = str(country).split('\n')[0].strip()
        query = f'{company_name} {country_clean} official website'
    else:
        query = f'{company_name} official website'

    print(f'      查询官网: {query[:50]}...')
    search_results = search_google(query, limit=10)

    if not search_results:
        return result

    # 筛选官网候选
    candidates = []
    for r in search_results:
        url = r.get("url", "")
        title = r.get("title", "")

        if not url or is_excluded_domain(url):
            continue

        # 计算匹配度
        score = 0
        if url_matches_company(url, company_name):
            score += 50
        if company_name.lower()[:20] in title.lower():
            score += 30

        candidates.append({"url": url, "title": title, "score": score})

    # 按分数排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 尝试读取最佳候选
    if candidates:
        best = candidates[0]
        url = best["url"]

        print(f'      读取官网: {url[:60]}...')
        webpage = read_webpage(url)

        if webpage:
            content = webpage.get("content", "") or webpage.get("text", "") or webpage.get("raw", "")

            result["official_website_url"] = url
            result["website_found"] = True
            result["website_summary"] = extract_summary_from_content(content, company_name)
            result["website_products_or_services"] = extract_products_from_content(content)

            # 尝试从内容提取公司名和国家
            title = webpage.get("title", "")
            if title:
                result["website_company_name"] = title[:100]

    return result


def query_linkedin(company_name: str) -> dict:
    """查询 LinkedIn 公司页面。"""
    result = {
        "linkedin_company_url": "",
        "linkedin_company_name": "",
        "linkedin_country": "",
        "linkedin_industry": "",
        "linkedin_employee_size": "",
        "linkedin_summary": "",
        "linkedin_found": False,
    }

    query = f'{company_name} LinkedIn company'
    print(f'      查询LinkedIn: {query[:50]}...')
    search_results = search_google(query, limit=5)

    if not search_results:
        return result

    # 找 LinkedIn 公司页面
    for r in search_results:
        url = r.get("url", "")
        if "linkedin.com/company" in url.lower():
            result["linkedin_company_url"] = url
            result["linkedin_found"] = True

            # 尝试读取 LinkedIn 页面（但通常会被拦截）
            print(f'      读取LinkedIn: {url[:60]}...')
            webpage = read_webpage(url)

            if webpage:
                content = webpage.get("content", "") or webpage.get("text", "")
                if content:
                    result["linkedin_summary"] = content[:300]
                    result["linkedin_employee_size"] = extract_employee_size(content)

            break

    return result


def query_third_party(company_name: str, country: str = "") -> dict:
    """查询可信第三方页面。"""
    result = {
        "third_party_source_url": "",
        "third_party_source_type": "",
        "third_party_company_summary": "",
        "third_party_found": False,
    }

    # 尝试 Crunchbase 或 Kompass
    query = f'{company_name} company profile'
    print(f'      查询第三方: {query[:50]}...')
    search_results = search_google(query, limit=5)

    if not search_results:
        return result

    # 找可信第三方页面
    for r in search_results:
        url = r.get("url", "")
        if is_trusted_third_party(url):
            result["third_party_source_url"] = url
            result["third_party_found"] = True

            # 识别来源类型
            for trusted in TRUSTED_THIRD_PARTY:
                if trusted in url.lower():
                    result["third_party_source_type"] = trusted.replace('.com', '')
                    break

            # 尝试读取
            print(f'      读取第三方: {url[:60]}...')
            webpage = read_webpage(url)

            if webpage:
                content = webpage.get("content", "") or webpage.get("text", "")
                if content:
                    result["third_party_company_summary"] = extract_summary_from_content(content, company_name)

            break

    return result


# ============================================================================
# 主处理函数
# ============================================================================

def process_company(row: pd.Series, index: int, total: int) -> dict:
    """处理单个公司的公开信息查询。"""
    company_name = str(row.get("customer_name", "")).strip()
    internal_id = str(row.get("internal_customer_id", ""))
    country = str(row.get("country_region", "")).split('\n')[0].strip() if row.get("country_region") else ""

    print(f"\n[{index + 1}/{total}] {company_name[:40]}")

    result = {
        "internal_customer_id": internal_id,
        "customer_name": company_name,
        "country_region": country,
        "public_info_query_status": "查询失败",
        "public_info_sources": "未找到",
        "official_website_url": "",
        "website_company_name": "",
        "website_country": "",
        "website_products_or_services": "",
        "website_summary": "",
        "linkedin_company_url": "",
        "linkedin_company_name": "",
        "linkedin_country": "",
        "linkedin_industry": "",
        "linkedin_employee_size": "",
        "linkedin_summary": "",
        "third_party_source_url": "",
        "third_party_source_type": "",
        "third_party_company_summary": "",
        "public_company_summary": "公开信息不足，需业务跟进时确认。",
        "public_main_business": "待确认",
        "public_products_or_services": "待确认",
        "public_company_scale": "待确认",
        "public_info_confidence": "无",
        "public_info_note": "",
    }

    # Step 1: 查询官网
    print(f"  [1/3] 查询官网...")
    website_result = query_official_website(company_name, country)
    result["official_website_url"] = website_result.get("official_website_url", "")
    result["website_summary"] = website_result.get("website_summary", "")
    result["website_products_or_services"] = website_result.get("website_products_or_services", "")
    website_found = website_result.get("website_found", False)

    # Step 2: 查询 LinkedIn
    print(f"  [2/3] 查询 LinkedIn...")
    linkedin_result = query_linkedin(company_name)
    result["linkedin_company_url"] = linkedin_result.get("linkedin_company_url", "")
    result["linkedin_employee_size"] = linkedin_result.get("linkedin_employee_size", "")
    result["linkedin_summary"] = linkedin_result.get("linkedin_summary", "")
    linkedin_found = linkedin_result.get("linkedin_found", False)

    # Step 3: 如果官网和LinkedIn都没找到，查询第三方
    third_party_found = False
    if not website_found and not linkedin_found:
        print(f"  [3/3] 查询第三方...")
        third_party_result = query_third_party(company_name, country)
        result["third_party_source_url"] = third_party_result.get("third_party_source_url", "")
        result["third_party_source_type"] = third_party_result.get("third_party_source_type", "")
        result["third_party_company_summary"] = third_party_result.get("third_party_company_summary", "")
        third_party_found = third_party_result.get("third_party_found", False)

    # 汇总公开信息
    sources = []
    if website_found:
        sources.append("官网")
        result["public_company_summary"] = result["website_summary"][:300] if result["website_summary"] else "官网已找到，但内容提取有限。"
        result["public_products_or_services"] = result["website_products_or_services"] if result["website_products_or_services"] else "待确认"
    if linkedin_found:
        sources.append("LinkedIn")
        if not website_found:
            result["public_company_summary"] = result["linkedin_summary"][:300] if result["linkedin_summary"] else "LinkedIn已找到，但内容提取有限。"
        if result["linkedin_employee_size"]:
            result["public_company_scale"] = result["linkedin_employee_size"]
    if third_party_found:
        sources.append(f"第三方({result['third_party_source_type']})")
        if not website_found and not linkedin_found:
            result["public_company_summary"] = result["third_party_company_summary"][:300] if result["third_party_company_summary"] else "第三方页面已找到，但内容提取有限。"

    # 设置状态和置信度
    if sources:
        result["public_info_sources"] = "+".join(sources)
        result["public_info_query_status"] = "已查-有公开信息" if len(sources) >= 2 else "已查-信息有限"

        # 置信度判断
        if website_found and url_matches_company(result["official_website_url"], company_name):
            result["public_info_confidence"] = "高"
        elif website_found or linkedin_found:
            result["public_info_confidence"] = "中"
        else:
            result["public_info_confidence"] = "低"

        result["public_info_note"] = f"已查询来源: {result['public_info_sources']}"
    else:
        result["public_info_query_status"] = "已查-未找到"
        result["public_info_sources"] = "未找到"
        result["public_info_confidence"] = "无"
        result["public_info_note"] = "官网/LinkedIn/第三方均未找到有效信息"

    print(f"  → 状态: {result['public_info_query_status']} | 来源: {result['public_info_sources']} | 置信度: {result['public_info_confidence']}")

    return result


def main():
    parser = argparse.ArgumentParser(description="客户公开信息查询（增强版）")
    parser.add_argument("--input", required=True, help="输入文件路径")
    parser.add_argument("--output", default=None, help="输出文件路径")
    parser.add_argument("--test", action="store_true", help="测试模式")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量")
    parser.add_argument("--cdp", default="http://localhost:9222", help="Chrome CDP 地址")
    parser.add_argument("--resume", action="store_true", help="续跑模式：跳过已成功查询的客户，重查失败项")
    parser.add_argument("--delay", type=int, default=6, help="每次查询间隔秒数（默认 6）")
    args = parser.parse_args()

    print("=" * 70)
    print("客户公开信息查询（增强版 — Playwright CDP）")
    print("=" * 70)

    # 读取输入
    print(f"\n[1/4] 读取输入文件: {args.input}")
    df = pd.read_excel(args.input)
    print(f"  总行数: {len(df)}")

    if args.test:
        df = df.head(10)
        print(f"  测试模式: 只处理前 10 条")
    elif args.limit > 0:
        df = df.head(args.limit)
        print(f"  限制数量: 只处理前 {args.limit} 条")

    # 初始化浏览器
    print(f"\n[2/4] 连接 Chrome...")
    init_browser(args.cdp)

    # 断点续跑：加载已有结果，跳过已成功的
    output_path = args.output or "output/public_info_query_result.xlsx"
    existing_results = {}
    if args.resume and Path(output_path).exists():
        try:
            prev_df = pd.read_excel(output_path)
            for _, row in prev_df.iterrows():
                cid = str(row.get("internal_customer_id", ""))
                status = str(row.get("public_info_query_status", ""))
                if status != "已查-未找到" and cid:
                    existing_results[cid] = row.to_dict()
            print(f"  [续跑] 加载 {len(existing_results)} 条已有成功结果，跳过这些客户")
        except Exception as e:
            print(f"  [续跑] 加载已有结果失败: {e}")

    # 处理
    print(f"\n[3/4] 开始查询...")
    start_time = time.time()
    results = list(existing_results.values())  # 保留已有成功结果
    try:
        for i, (_, row) in enumerate(df.iterrows()):
            cid = str(row.get("internal_customer_id", ""))

            # 跳过已成功查询的
            if cid in existing_results:
                continue

            company_name = str(row.get("customer_name", "")).strip()
            print(f"\n  [{len(results)+1}/{len(df)}] {company_name[:40]}")

            result = process_company(row, i, len(df))
            results.append(result)

            # 每 10 条保存一次中间结果（防止中断丢失）
            if len(results) % 10 == 0:
                pd.DataFrame(results).to_excel(output_path, index=False)
                sys.stdout.flush()
                print(f"  [中间保存] 已保存 {len(results)} 条到 {output_path}")
                sys.stdout.flush()

            time.sleep(args.delay)  # 避免请求过快
    except KeyboardInterrupt:
        print("\n  [中断] 用户中断，保存已查询结果...")
    finally:
        close_browser()

    elapsed = time.time() - start_time

    # 导出
    output_path = args.output or "output/public_info_query_result.xlsx"
    print(f"\n[4/4] 导出结果: {output_path}")
    output_df = pd.DataFrame(results)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(output_path, index=False)

    # 统计
    print("\n" + "=" * 70)
    print("查询完成")
    print("=" * 70)

    print(f"\n【统计信息】")
    print(f"  处理数量: {len(results)}")

    # 各状态统计
    status_counts = {}
    for r in results:
        status = r.get("public_info_query_status", "未知")
        status_counts[status] = status_counts.get(status, 0) + 1

    print(f"\n【查询状态分布】")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")

    # 来源统计
    website_found = sum(1 for r in results if r.get("official_website_url"))
    linkedin_found = sum(1 for r in results if r.get("linkedin_company_url"))
    third_party_found = sum(1 for r in results if r.get("third_party_source_url"))

    print(f"\n【来源统计】")
    print(f"  官网找到: {website_found}/{len(results)}")
    print(f"  LinkedIn找到: {linkedin_found}/{len(results)}")
    print(f"  第三方找到: {third_party_found}/{len(results)}")

    # 置信度统计
    confidence_counts = {}
    for r in results:
        conf = r.get("public_info_confidence", "无")
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

    print(f"\n【置信度分布】")
    for conf in ["高", "中", "低", "无"]:
        print(f"  {conf}: {confidence_counts.get(conf, 0)}")

    print(f"\n【耗时统计】")
    print(f"  平均耗时: {elapsed/len(results):.1f} 秒/客户")
    print(f"  总耗时: {elapsed:.1f} 秒")

    print(f"\n【输出文件】")
    print(f"  {output_path}")

    # 检查编造风险
    print(f"\n【编造风险检查】")
    insufficient = sum(1 for r in results if r.get("public_company_summary") == "公开信息不足，需业务跟进时确认。")
    if insufficient > 0:
        print(f"  ✓ {insufficient} 条客户标注为「公开信息不足」，未强行编造")
    confirmed = sum(1 for r in results if r.get("public_main_business") == "待确认")
    print(f"  ✓ {confirmed} 条客户主营业务标注为「待确认」，未强行编造")


if __name__ == "__main__":
    main()