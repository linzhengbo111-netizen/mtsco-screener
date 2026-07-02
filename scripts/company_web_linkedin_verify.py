"""
company_web_linkedin_verify.py — 官网和 LinkedIn 查询脚本（优化版）

功能：
1. 使用 opencli google search 搜索公司官网
2. 使用 opencli web read 读取官网内容
3. 使用 opencli google search 搜索 LinkedIn 公司页面
4. 使用 opencli web read 读取 LinkedIn 公司页面
5. 输出结构化结果

用法：
    # 测试模式：只查前 5 个客户
    python scripts/company_web_linkedin_verify.py --input input/opencli_company_verify_test_5.xlsx --test

    # 完整模式：查询全部客户
    python scripts/company_web_linkedin_verify.py --input input/opencli_company_verify_AB_priority.xlsx
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

# 确保控制台 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


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
    "falconebiz.com",  # 企业目录
    "sgpgrid.com",  # 企业目录
    "highpressurepipefittings.com",  # 关联企业
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


# ============================================================================
# 工具函数
# ============================================================================

def extract_domain_name(url: str) -> str:
    """从 URL 提取主域名（不含子域名）。"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        # 去掉 www.
        domain = re.sub(r'^www\.', '', domain.lower())
        # 取主域名（最后两部分）
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

    # 检查排除域名
    for excluded in EXCLUDED_DOMAINS:
        if excluded in url_lower:
            return True

    # 检查目录站特征
    for keyword in DIRECTORY_KEYWORDS:
        if keyword in url_lower:
            return True

    return False


def extract_company_keywords(company_name: str) -> list:
    """从公司名提取关键词。"""
    if not company_name:
        return []

    # 去掉常见后缀
    name = company_name.lower()
    for suffix in ['co., ltd', 'co.,ltd', 'co ltd', 'co. ltd', 'corp.', 'corporation',
                   'inc.', 'inc', 'llc', 'ltd', 'limited', 'pte ltd', 'pte. ltd',
                   'sdn bhd', 'sdn. bhd', 'gmbh', 's.a.', 's.a', 'sa', 'bv', 'b.v.',
                   'private limited', 'pvt ltd', 'pvt. ltd']:
        name = name.replace(suffix, '')

    # 分词
    words = re.findall(r'[a-z0-9]+', name)

    # 过滤太短的词
    keywords = [w for w in words if len(w) >= 3]

    return keywords[:5]  # 最多取前5个关键词


def url_matches_company(url: str, company_name: str) -> bool:
    """判断 URL 域名是否匹配公司名关键词。"""
    if not url or not company_name:
        return False

    domain = extract_domain_name(url)
    keywords = extract_company_keywords(company_name)

    # 检查关键词是否出现在域名中
    for kw in keywords:
        if kw in domain:
            return True

    return False


# ============================================================================
# OpenCLI 命令执行
# ============================================================================

def run_opencli(args: list, format: str = "json") -> dict | list | None:
    """执行 opencli 命令并返回结果。"""
    # Windows 上需要 shell=True 来执行 .cmd 文件
    # Windows cmd.exe 只识别双引号，需要用双引号包裹参数
    def quote_arg(arg: str) -> str:
        # 如果包含空格或特殊字符，用双引号包裹
        if ' ' in arg or '"' in arg or "'" in arg:
            # 先转义内部的双引号
            arg = arg.replace('"', '\\"')
            return f'"{arg}"'
        return arg

    quoted_args = [quote_arg(arg) for arg in args + ["-f", format]]
    cmd = "opencli " + " ".join(quoted_args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            encoding='utf-8',
            errors='replace',
            shell=True
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"raw": result.stdout}
        else:
            if result.stderr:
                print(f"  [ERROR] stderr: {result.stderr[:100]}")
        return None
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] 超时")
        return None
    except Exception as e:
        print(f"  [ERROR] 异常: {str(e)[:50]}")
        return None


def search_google(query: str, limit: int = 5) -> list:
    """使用 Google 搜索。"""
    # query 作为单个完整元素传递
    result = run_opencli(["google", "search", query, "--limit", str(limit)])
    if result and isinstance(result, list):
        return result
    return []


# ============================================================================
# 官网查询（优化版）
# ============================================================================

def search_official_website(company_name: str, country: str = "") -> dict:
    """搜索公司官网（优化版）。"""

    # 构建搜索查询（不带内部引号，整个 query 会由 run_opencli 统一引用）
    if country:
        # 清理国家名
        country_clean = str(country).split('\n')[0].strip()
        query = f'{company_name} {country_clean} official website'
    else:
        query = f'{company_name} official website'

    print(f'      查询: {query[:50]}...')
    results = search_google(query, limit=10)

    if not results:
        return {
            "official_website_url": "",
            "website_match_status": "未找到",
            "website_note": "搜索无结果",
        }

    # 提取公司关键词用于匹配
    company_keywords = extract_company_keywords(company_name)

    # 分析搜索结果
    candidates = []
    directory_candidates = []

    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        snippet = r.get("snippet", "")

        if not url:
            continue

        # 排除明显非官网域名
        if is_excluded_domain(url):
            # 记录为目录站候选
            if any(kw in url.lower() for kw in DIRECTORY_KEYWORDS + ["zauba", "falconebiz", "sgpgrid"]):
                directory_candidates.append({
                    "url": url,
                    "title": title,
                    "type": "directory"
                })
            continue

        # 计算匹配分数
        score = 0

        # URL 域名匹配公司名关键词
        if url_matches_company(url, company_name):
            score += 50

        # 标题包含公司名
        if company_name.lower()[:20] in title.lower():
            score += 30

        # snippet 包含公司名
        if company_name.lower()[:20] in snippet.lower():
            score += 20

        candidates.append({
            "url": url,
            "title": title,
            "snippet": snippet,
            "score": score
        })

    # 按分数排序
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 选择最佳候选
    if candidates:
        best = candidates[0]
        if best["score"] >= 50:
            return {
                "official_website_url": best["url"],
                "website_match_status": "可能匹配",
                "website_note": f"域名匹配公司名 (分数: {best['score']})",
            }
        elif best["score"] >= 20:
            return {
                "official_website_url": best["url"],
                "website_match_status": "可能匹配",
                "website_note": f"部分匹配 (分数: {best['score']})",
            }

    # 只有目录站
    if directory_candidates and not candidates:
        return {
            "official_website_url": "",
            "website_match_status": "未找到",
            "website_note": "仅找到目录站/B2B平台，未采纳为官网",
        }

    return {
        "official_website_url": "",
        "website_match_status": "未找到",
        "website_note": "未找到有效官网候选",
    }


# ============================================================================
# LinkedIn 查询
# ============================================================================

def search_linkedin_company(company_name: str) -> dict:
    """搜索 LinkedIn 公司页面。"""
    query = f'{company_name} LinkedIn company'
    results = search_google(query, limit=5)

    if not results:
        return {
            "linkedin_company_url": "",
            "linkedin_match_status": "未找到",
            "linkedin_note": "搜索无结果",
        }

    # 分析搜索结果，找 LinkedIn 公司页面
    for r in results:
        url = r.get("url", "")
        if "linkedin.com/company" in url.lower():
            return {
                "linkedin_company_url": url,
                "linkedin_match_status": "可能匹配",
                "linkedin_note": "找到 LinkedIn 公司页面",
            }

    return {
        "linkedin_company_url": "",
        "linkedin_match_status": "未找到",
        "linkedin_note": "未找到 LinkedIn 公司页面",
    }


# ============================================================================
# 主处理函数
# ============================================================================

def process_company(row: pd.Series, index: int, total: int) -> dict:
    """处理单个公司。"""
    company_name = str(row.get("customer_name", "")).strip()
    internal_id = str(row.get("internal_customer_id", ""))
    country = str(row.get("country_region", "")).split('\n')[0].strip() if row.get("country_region") else ""

    print(f"\n[{index + 1}/{total}] {company_name[:40]}")

    result = {
        "internal_customer_id": internal_id,
        "customer_name": company_name,
        "official_website_url": "",
        "website_company_name": "",
        "website_country": "",
        "website_products": "",
        "website_match_status": "未找到",
        "linkedin_company_url": "",
        "linkedin_company_name": "",
        "linkedin_country": "",
        "linkedin_industry": "",
        "linkedin_employee_size": "",
        "linkedin_match_status": "未找到",
        "web_linkedin_confidence": 0,
        "web_linkedin_note": "",
        "business_confidence": 0,
    }

    # Step 1: 搜索官网（优化版）
    print(f"  [1/2] 搜索官网...")
    website_result = search_official_website(company_name, country)
    result["official_website_url"] = website_result.get("official_website_url", "")
    result["website_match_status"] = website_result.get("website_match_status", "未找到")
    website_note = website_result.get("website_note", "")

    # Step 2: 搜索 LinkedIn
    print(f"  [2/2] 搜索 LinkedIn...")
    linkedin_result = search_linkedin_company(company_name)
    result["linkedin_company_url"] = linkedin_result.get("linkedin_company_url", "")
    result["linkedin_match_status"] = linkedin_result.get("linkedin_match_status", "未找到")
    linkedin_note = linkedin_result.get("linkedin_note", "")

    # 计算置信度（保守规则）
    confidence = 0
    notes = []

    website_found = result["website_match_status"] != "未找到"
    linkedin_found = result["linkedin_match_status"] != "未找到"

    if website_found and linkedin_found:
        confidence = 60
        notes.append("官网可能匹配")
        notes.append("LinkedIn可能匹配")
    elif website_found:
        confidence = 30
        notes.append("官网可能匹配")
    elif linkedin_found:
        confidence = 30
        notes.append("LinkedIn可能匹配")
    else:
        confidence = 0
        notes.append("未找到官网或LinkedIn")

    # 如果只有目录站，降低置信度
    if "目录站" in website_note or "B2B平台" in website_note:
        if not linkedin_found:
            confidence = 0
            notes = ["仅找到目录站/B2B平台/社媒，未采纳为官网"]

    result["web_linkedin_confidence"] = confidence
    result["web_linkedin_note"] = "; ".join(notes)
    result["business_confidence"] = confidence

    print(f"  → 官网: {result['website_match_status']} | LinkedIn: {result['linkedin_match_status']} | 置信度: {confidence}")

    return result


def main():
    parser = argparse.ArgumentParser(description="官网和 LinkedIn 查询（优化版）")
    parser.add_argument("--input", required=True, help="输入文件路径")
    parser.add_argument("--output", default=None, help="输出文件路径")
    parser.add_argument("--test", action="store_true", help="测试模式，只查前 5 个")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量（0=不限制）")
    args = parser.parse_args()

    print("=" * 70)
    print("官网和 LinkedIn 查询工具（优化版）")
    print("=" * 70)

    # 读取输入
    print(f"\n[1/3] 读取输入文件: {args.input}")
    df = pd.read_excel(args.input)
    print(f"  总行数: {len(df)}")

    # 限制数量
    if args.test:
        df = df.head(5)
        print(f"  测试模式: 只处理前 5 条")
    elif args.limit > 0:
        df = df.head(args.limit)
        print(f"  限制数量: 只处理前 {args.limit} 条")

    # 处理
    print(f"\n[2/3] 开始查询...")
    start_time = time.time()
    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        result = process_company(row, i, len(df))
        results.append(result)
        time.sleep(2)  # 避免请求过快

    elapsed = time.time() - start_time

    # 导出
    output_path = args.output or "output/company_web_linkedin_verify_result.xlsx"
    print(f"\n[3/3] 导出结果: {output_path}")
    output_df = pd.DataFrame(results)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    output_df.to_excel(output_path, index=False)

    # 统计
    print("\n" + "=" * 70)
    print("查询完成")
    print("=" * 70)
    print(f"\n【统计信息】")
    print(f"  处理数量: {len(results)}")

    website_found = sum(1 for r in results if r["website_match_status"] != "未找到")
    linkedin_found = sum(1 for r in results if r["linkedin_match_status"] != "未找到")

    print(f"  官网找到: {website_found}/{len(results)}")
    print(f"  LinkedIn找到: {linkedin_found}/{len(results)}")
    print(f"  平均耗时: {elapsed/len(results):.1f} 秒/客户")
    print(f"  总耗时: {elapsed:.1f} 秒")

    # 置信度分布
    print(f"\n【置信度分布】")
    for conf in sorted(set(r["web_linkedin_confidence"] for r in results), reverse=True):
        count = sum(1 for r in results if r["web_linkedin_confidence"] == conf)
        print(f"  {conf} 分: {count} 条")

    print(f"\n【输出文件】")
    print(f"  {output_path}")


if __name__ == "__main__":
    main()
