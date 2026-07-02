"""
verify_external_v3.py — 官网/LinkedIn核验 (改进版)

LinkedIn 核验逻辑改进：
1. 优先使用输入字段 linkedin
2. 从官网提取 LinkedIn 链接
3. 使用 LinkedIn 站内搜索
4. 遇到限制标记 uncertain
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import quote_plus
import urllib.parse

import pandas as pd


# OpenCLI 完整路径 (Windows)
OPENCLI_PATH = r"C:\Users\Admin\AppData\Roaming\npm\opencli.cmd"


@dataclass
class VerificationResult:
    """核验结果"""
    internal_customer_id: str = ""
    customer_name: str = ""
    country_region: str = ""
    matched_company_name: str = ""  # 腾道匹配的公司名

    website_accessible: str = ""
    website_match_status: str = ""
    website_business_status: str = ""
    website_product_relevance: str = ""
    website_contact_found: str = ""
    website_contact_email: str = ""
    website_contact_phone: str = ""
    website_evidence_url: str = ""
    website_evidence_summary: str = ""

    linkedin_company_found: str = ""
    linkedin_company_url: str = ""
    linkedin_company_name: str = ""
    linkedin_employee_range: str = ""
    linkedin_country_match: str = ""
    linkedin_industry: str = ""
    linkedin_recent_activity: str = ""
    linkedin_clean_status: str = ""
    linkedin_clean_reason: str = ""

    external_check_confidence: str = ""
    external_check_summary: str = ""
    manual_review_flag: str = "no"
    manual_review_reason_external: str = ""
    external_recommended_action: str = ""

    error_message: str = ""


def run_opencli(cmd_parts: list, timeout: int = 30) -> dict:
    """运行 OpenCLI 命令"""
    cmd = [OPENCLI_PATH] + cmd_parts
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace'
        )
        stdout = proc.stdout.strip()
        if stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return {"raw": stdout}
        return {"error": proc.stderr or "empty output"}
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}


def clean_url(url: str) -> str:
    """清理 URL"""
    if not url:
        return ""
    url = url.strip()
    if url in ("http://", "https://", "http:///", "https:///"):
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def is_valid_linkedin_company_url(url: str) -> bool:
    """判断是否为有效的 LinkedIn 公司页 URL"""
    if not url:
        return False
    url_lower = url.lower()
    # 必须是 company 页面
    if "linkedin.com/company/" not in url_lower:
        return False
    # 排除个人页、帖子页、搜索页
    exclude = ["/in/", "/posts/", "/search/", "/jobs/", "/school/"]
    for ex in exclude:
        if ex in url_lower:
            return False
    return True


def extract_linkedin_from_page(session: str) -> str:
    """从当前页面提取 LinkedIn 公司链接"""
    # 使用 --role link 而不是 CSS 选择器（因为 CSS 选择器中的 / 字符有问题）
    find_res = run_opencli(["browser", session, "find", "--role", "link", "--limit", "50"])
    matches = find_res.get("entries", [])

    for m in matches:
        href = m.get("attrs", {}).get("href", "")
        if is_valid_linkedin_company_url(href):
            # 清理 URL
            parsed = urllib.parse.urlparse(href)
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            return clean
    return ""


def verify_website(url: str, name: str, session: str) -> dict:
    """核验官网"""
    result = {
        "accessible": "no",
        "match": "not_checked",
        "business": "",
        "relevance": "",
        "contact": "no",
        "email": "",
        "phone": "",
        "evidence_url": "",
        "summary": "",
        "linkedin_link": "",  # 从官网提取的 LinkedIn 链接
    }

    url = clean_url(url)
    if not url:
        result["match"] = "search_required"
        result["summary"] = "无URL"
        return result

    # 排除目录站
    exclude = ["alibaba.com", "made-in-china.com", "linkedin.com", "facebook.com",
               "yellowpages", "crunchbase.com", "bloomberg.com"]
    if any(x in url.lower() for x in exclude):
        result["match"] = "invalid_directory"
        result["summary"] = "目录站/B2B平台"
        return result

    # 打开页面
    open_res = run_opencli(["browser", session, "open", url])
    if not open_res.get("url"):
        result["summary"] = f"打开失败: {open_res.get('error', '')[:50]}"
        return result

    time.sleep(2)

    # 提取内容
    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    title = ext_res.get("title", "")
    final_url = ext_res.get("url", url)

    if not content:
        result["summary"] = "无法提取内容"
        return result

    result["accessible"] = "yes"
    result["evidence_url"] = final_url

    # 检查名称匹配
    name_lower = name.lower()
    content_lower = content.lower()

    name_parts = [p for p in name_lower.split() if len(p) > 2]
    found_parts = sum(1 for p in name_parts if p in content_lower)

    if found_parts >= len(name_parts) * 0.5:
        result["match"] = "confirmed"
        result["summary"] = f"名称匹配({found_parts}/{len(name_parts)}词)"
    else:
        result["match"] = "unconfirmed"
        result["summary"] = f"名称未完全匹配({found_parts}/{len(name_parts)}词)"

    # 检查业务关键词
    keywords = ["steel", "pipe", "tube", "metal", "industrial", "manufacturing",
                "engineering", "boiler", "valve", "welding", "stainless"]
    found_kw = [k for k in keywords if k in content_lower]
    if found_kw:
        result["business"] = "related"
        result["relevance"] = "relevant" if len(found_kw) >= 2 else "possible"

    # 提取联系方式
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content)
    valid_emails = [e for e in emails if not any(x in e.lower() for x in ['example', 'test', 'sentry', 'wixpress'])]
    if valid_emails:
        result["contact"] = "yes"
        result["email"] = valid_emails[0]

    phones = re.findall(r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}', content)
    valid_phones = [p for p in phones if len(p) > 8]
    if valid_phones:
        result["phone"] = valid_phones[0]

    # 如果官网确认，尝试提取 LinkedIn 链接
    if result["match"] == "confirmed":
        li_link = extract_linkedin_from_page(session)
        if li_link:
            result["linkedin_link"] = li_link

    return result


def verify_linkedin_page(session: str, customer_name: str, matched_name: str, country: str) -> dict:
    """验证 LinkedIn 公司页"""
    result = {
        "found": "",
        "url": "",
        "name": "",
        "employees": "",
        "country_match": "",
        "industry": "",
        "activity": "",
        "status": "",
        "reason": "",
    }

    # 提取页面内容
    time.sleep(2)
    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    title = ext_res.get("title", "")
    current_url = ext_res.get("url", "")

    # 检查是否遇到限制
    limit_indicators = ["verification", "security verification", "captcha",
                        "sign in", "log in", "authwall", "challenge"]
    content_lower = content.lower()
    if any(ind in content_lower for ind in limit_indicators):
        result["found"] = "uncertain"
        result["status"] = "access_limited"
        result["reason"] = "LinkedIn 需要验证或登录"
        return result

    # 确认是公司页
    if "linkedin.com/company/" not in current_url.lower():
        result["found"] = "no"
        result["reason"] = "URL不是公司页"
        return result

    result["found"] = "found"
    result["url"] = current_url.split("?")[0]

    # 从标题提取公司名
    if " | " in title:
        result["name"] = title.split(" | ")[0].strip()
    else:
        result["name"] = title[:60].strip()

    # 检查国家匹配
    if country and country.lower() in content_lower:
        result["country_match"] = "yes"
    else:
        result["country_match"] = "unconfirmed"

    # 提取行业
    industries = ["steel", "oil", "gas", "energy", "manufacturing",
                  "engineering", "metal", "mining", "construction",
                  "industrial", "machinery", "trading"]
    for ind in industries:
        if ind in content_lower:
            result["industry"] = ind.capitalize()
            break

    # 判断公司名匹配度
    li_name_lower = result["name"].lower()
    customer_name_lower = customer_name.lower()
    matched_name_lower = matched_name.lower() if matched_name else ""

    # 提取关键词
    def get_key_parts(name):
        return set(p for p in name.split() if len(p) > 2 and p not in
                   ['the', 'and', 'inc', 'ltd', 'llc', 'co', 'corp', 'limited', 'company'])

    customer_parts = get_key_parts(customer_name_lower)
    matched_parts = get_key_parts(matched_name_lower)
    li_parts = get_key_parts(li_name_lower)

    # 检查匹配
    common_with_customer = customer_parts & li_parts
    common_with_matched = matched_parts & li_parts

    if common_with_customer or common_with_matched:
        result["status"] = "yes"
        result["reason"] = f"公司名匹配 ({len(common_with_customer or common_with_matched)} 词)"
    elif result["country_match"] == "yes" and result["industry"]:
        result["status"] = "likely_match"
        result["reason"] = "国家和行业匹配"
    else:
        result["status"] = "uncertain"
        result["reason"] = "公司名匹配度低"

    return result


def search_linkedin_internal(session: str, company_name: str, country: str) -> dict:
    """使用 LinkedIn 站内搜索"""
    result = {
        "found": "",
        "url": "",
        "name": "",
        "employees": "",
        "country_match": "",
        "industry": "",
        "activity": "",
        "status": "",
        "reason": "",
    }

    # 构建搜索 URL - 简化搜索词
    # 去除特殊字符，只用公司名的主要部分
    clean_name = re.sub(r'[^\w\s]', ' ', company_name)  # 去除特殊字符
    clean_name = ' '.join(clean_name.split())  # 合并多余空格

    # 取前几个单词作为搜索词（避免太长）
    name_words = clean_name.split()[:4]
    search_keyword = ' '.join(name_words)

    # 不加国家，避免搜索词太复杂
    search_url = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(search_keyword)}"

    # 打开搜索页
    print(f"    LI Search: {search_keyword[:30]}")
    open_res = run_opencli(["browser", session, "open", search_url], timeout=25)
    print(f"    Open result: url={open_res.get('url', 'N/A')[:50]}, error={open_res.get('error', 'N/A')[:30]}")
    if not open_res.get("url"):
        result["found"] = "error"
        result["reason"] = f"无法打开搜索页: {open_res.get('error', '')}"
        return result

    time.sleep(4)  # 增加等待时间

    # 检查是否遇到限制
    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    current_url = ext_res.get("url", "")
    content_lower = content.lower()

    # 检查限制信号
    limit_indicators = ["verification", "captcha", "security",
                        "sign in to see more", "authwall"]
    if any(ind in content_lower for ind in limit_indicators):
        result["found"] = "uncertain"
        result["status"] = "access_limited"
        result["reason"] = "LinkedIn 搜索需要验证"
        return result

    # 检查是否在搜索结果页
    if "search/results" not in current_url.lower():
        result["found"] = "uncertain"
        result["status"] = "access_limited"
        result["reason"] = "未到达搜索结果页"
        return result

    # 查找公司搜索结果链接 - 使用 --role link
    find_res = run_opencli(["browser", session, "find", "--role", "link", "--limit", "100"])
    matches = find_res.get("entries", [])
    print(f"    Find result: {len(matches)} links, error={find_res.get('error', 'N/A')[:30]}")

    if not matches:
        result["found"] = "not_found"
        result["reason"] = f"find 命令返回空: {find_res.get('error', 'unknown')}"
        return result

    # 过滤出公司链接
    company_links = []
    for m in matches:
        href = m.get("attrs", {}).get("href", "")
        if is_valid_linkedin_company_url(href):
            clean_url = href.split("?")[0]
            if clean_url not in company_links:
                company_links.append(clean_url)

    if not company_links:
        result["found"] = "not_found"
        result["reason"] = f"搜索结果中无公司页 (共{len(matches)}链接)"
        return result

    # 打开第一个公司页
    first_company = company_links[0]
    open_res = run_opencli(["browser", session, "open", first_company], timeout=25)

    # 验证公司页
    return verify_linkedin_page(session, company_name, "", country)


def verify_linkedin(
    session: str,
    customer_name: str,
    matched_name: str,
    country: str,
    input_linkedin: str,
    website_result: dict
) -> dict:
    """核验 LinkedIn 公司页（改进版）"""
    result = {
        "found": "",
        "url": "",
        "name": "",
        "employees": "",
        "country_match": "",
        "industry": "",
        "activity": "",
        "status": "",
        "reason": "",
    }

    # 1. 优先使用输入字段的 LinkedIn 链接
    if input_linkedin and is_valid_linkedin_company_url(input_linkedin):
        open_res = run_opencli(["browser", session, "open", input_linkedin], timeout=25)
        return verify_linkedin_page(session, customer_name, matched_name, country)

    # 2. 如果官网确认且有 LinkedIn 链接
    if website_result.get("match") == "confirmed" and website_result.get("linkedin_link"):
        li_link = website_result["linkedin_link"]
        open_res = run_opencli(["browser", session, "open", li_link], timeout=25)
        return verify_linkedin_page(session, customer_name, matched_name, country)

    # 3. 使用 LinkedIn 站内搜索
    # 先用原公司名搜索
    result = search_linkedin_internal(session, customer_name, country)

    # 如果没找到，尝试用 matched_name 搜索
    if result["found"] == "not_found" and matched_name and matched_name != customer_name:
        result = search_linkedin_internal(session, matched_name, country)

    return result


def calc_confidence(ws: dict, li: dict) -> dict:
    """计算置信度"""
    conf = 0
    parts = []

    if ws["accessible"] == "yes":
        conf += 20
        if ws["match"] == "confirmed":
            conf += 30
            parts.append("官网确认")
        elif ws["match"] == "unconfirmed":
            conf += 10
            parts.append("官网待确认")
        if ws["contact"] == "yes":
            conf += 10
        if ws["relevance"] == "relevant":
            conf += 10

    if li["found"] == "found":
        conf += 10
        if li["status"] == "yes":
            conf += 30
            parts.append("LinkedIn确认")
        elif li["status"] == "likely_match":
            conf += 20
            parts.append("LinkedIn可能匹配")
        if li["country_match"] == "yes":
            conf += 10
    elif li["found"] == "uncertain":
        parts.append("LinkedIn受限")

    conf = min(conf, 100)

    manual = "no"
    reason = ""
    action = "暂不跟进"

    if conf >= 70:
        action = "建议继续跟进"
    elif conf >= 40:
        action = "待人工复核"
        manual = "yes"
        reason = "置信度中等"
    else:
        if ws["accessible"] != "yes" and li["found"] != "found":
            manual = "yes"
            reason = "官网和LinkedIn均无法核验"
        elif li["status"] == "access_limited":
            manual = "yes"
            reason = "LinkedIn访问受限"

    return {
        "confidence": str(conf),
        "summary": "; ".join(parts) or "无有效信号",
        "manual_flag": manual,
        "manual_reason": reason,
        "action": action,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--save-interval", type=int, default=5)
    parser.add_argument("--linkedin-only", action="store_true", help="只重跑LinkedIn部分")
    args = parser.parse_args()

    print("=" * 60)
    print("OpenCLI 官网/LinkedIn 核验 (v3)")
    print("=" * 60)

    df = pd.read_excel(args.input)
    print(f"\n读取: {args.input} ({len(df)} 客户)")

    # 如果是只重跑 LinkedIn，读取之前的结果
    prev_results = {}
    if args.linkedin_only:
        prev_path = args.output.replace("_v2.xlsx", ".xlsx")
        if Path(prev_path).exists():
            prev_df = pd.read_excel(prev_path)
            for _, row in prev_df.iterrows():
                prev_results[row["internal_customer_id"]] = row.to_dict()
            print(f"读取之前结果: {prev_path}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    session = "verify_v3"

    # 初始化
    run_opencli(["browser", session, "open", "about:blank"])

    try:
        for i, row in df.iterrows():
            cid = str(row.get("internal_customer_id", ""))
            name = str(row.get("customer_name", ""))
            country = str(row.get("country_region", ""))
            url = str(row.get("website_input", ""))
            input_linkedin = str(row.get("linkedin", ""))
            matched_name = str(row.get("matched_company_name", ""))

            print(f"\n[{i+1}/{len(df)}] {cid}")

            result = VerificationResult(
                internal_customer_id=cid,
                customer_name=name,
                country_region=country,
                matched_company_name=matched_name,
            )

            try:
                # 如果只重跑 LinkedIn，使用之前的官网结果
                if args.linkedin_only and cid in prev_results:
                    prev = prev_results[cid]
                    result.website_accessible = prev.get("website_accessible", "")
                    result.website_match_status = prev.get("website_match_status", "")
                    result.website_business_status = prev.get("website_business_status", "")
                    result.website_product_relevance = prev.get("website_product_relevance", "")
                    result.website_contact_found = prev.get("website_contact_found", "no")
                    result.website_contact_email = prev.get("website_contact_email", "")
                    result.website_contact_phone = prev.get("website_contact_phone", "")
                    result.website_evidence_url = prev.get("website_evidence_url", "")
                    result.website_evidence_summary = prev.get("website_evidence_summary", "")

                    ws = {
                        "accessible": result.website_accessible,
                        "match": result.website_match_status,
                        "linkedin_link": "",
                        "relevance": result.website_product_relevance,
                        "contact": result.website_contact_found,
                    }
                else:
                    # 官网核验
                    ws = verify_website(url, name, session)
                    result.website_accessible = ws["accessible"]
                    result.website_match_status = ws["match"]
                    result.website_business_status = ws.get("business", "")
                    result.website_product_relevance = ws.get("relevance", "")
                    result.website_contact_found = ws.get("contact", "no")
                    result.website_contact_email = ws.get("email", "")
                    result.website_contact_phone = ws.get("phone", "")
                    result.website_evidence_url = ws["evidence_url"]
                    result.website_evidence_summary = ws["summary"]

                # LinkedIn 核验
                li = verify_linkedin(session, name, matched_name, country, input_linkedin, ws)
                result.linkedin_company_found = li["found"]
                result.linkedin_company_url = li["url"]
                result.linkedin_company_name = li["name"]
                result.linkedin_employee_range = li["employees"]
                result.linkedin_country_match = li["country_match"]
                result.linkedin_industry = li["industry"]
                result.linkedin_recent_activity = li["activity"]
                result.linkedin_clean_status = li["status"]
                result.linkedin_clean_reason = li["reason"]

                # 置信度
                c = calc_confidence(ws, li)
                result.external_check_confidence = c["confidence"]
                result.external_check_summary = c["summary"]
                result.manual_review_flag = c["manual_flag"]
                result.manual_review_reason_external = c["manual_reason"]
                result.external_recommended_action = c["action"]

                print(f"  WS:{ws['accessible'][:3]} LI:{li['found'][:8]} Status:{li['status']} Conf:{c['confidence']}%")

            except Exception as e:
                result.error_message = str(e)[:150]
                print(f"  Error: {str(e)[:60]}")

            results.append(result)

            # 定期保存
            if len(results) % args.save_interval == 0:
                pd.DataFrame([asdict(r) for r in results]).to_excel(args.output, index=False)
                print(f"  Saved {len(results)}")

    finally:
        run_opencli(["browser", session, "close"])

    # 最终保存
    pd.DataFrame([asdict(r) for r in results]).to_excel(args.output, index=False)

    # 统计
    print("\n" + "=" * 60)
    print("统计")
    print("=" * 60)
    print(f"处理: {len(results)}")
    print(f"官网可访问: {sum(1 for r in results if r.website_accessible == 'yes')}")
    print(f"官网确认: {sum(1 for r in results if r.website_match_status == 'confirmed')}")

    print("\nLinkedIn 统计:")
    li_yes = sum(1 for r in results if r.linkedin_clean_status == "yes")
    li_likely = sum(1 for r in results if r.linkedin_clean_status == "likely_match")
    li_uncertain = sum(1 for r in results if r.linkedin_clean_status == "uncertain")
    li_limited = sum(1 for r in results if r.linkedin_clean_status == "access_limited")
    li_not_found = sum(1 for r in results if r.linkedin_company_found == "not_found")

    print(f"  yes (确认): {li_yes}")
    print(f"  likely_match: {li_likely}")
    print(f"  uncertain: {li_uncertain}")
    print(f"  access_limited: {li_limited}")
    print(f"  not_found: {li_not_found}")

    print(f"\n需人工复核: {sum(1 for r in results if r.manual_review_flag == 'yes')}")


if __name__ == "__main__":
    main()
