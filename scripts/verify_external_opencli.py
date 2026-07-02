"""
verify_external_opencli.py — 使用 OpenCLI 核验官网/LinkedIn

通过 OpenCLI 浏览器命令进行官网和 LinkedIn 公司页核验。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class VerificationResult:
    """核验结果"""
    internal_customer_id: str = ""
    customer_name: str = ""
    country_region: str = ""

    # 官网核验结果
    website_accessible: str = ""
    website_match_status: str = ""
    website_business_status: str = ""
    website_product_relevance: str = ""
    website_contact_found: str = ""
    website_contact_email: str = ""
    website_contact_phone: str = ""
    website_evidence_url: str = ""
    website_evidence_summary: str = ""

    # LinkedIn核验结果
    linkedin_company_found: str = ""
    linkedin_company_url: str = ""
    linkedin_company_name: str = ""
    linkedin_employee_range: str = ""
    linkedin_country_match: str = ""
    linkedin_industry: str = ""
    linkedin_recent_activity: str = ""
    linkedin_clean_status: str = ""
    linkedin_clean_reason: str = ""

    # 综合判断
    external_check_confidence: str = ""
    external_check_summary: str = ""
    manual_review_flag: str = "no"
    manual_review_reason_external: str = ""
    external_recommended_action: str = ""

    # 错误信息
    error_message: str = ""


SESSION_NAME = "verify_batch"


def run_opencli(args: list, timeout: int = 30000) -> dict:
    """运行 OpenCLI 命令"""
    cmd = ["opencli"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout / 1000,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"raw": result.stdout, "success": True}
        else:
            return {"error": result.stderr or "empty output", "success": False, "stdout": result.stdout}
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


def clean_url(url: str) -> str:
    """清理URL"""
    if not url:
        return ""
    url = url.strip()
    if url in ("http://", "https://", "http:///", "https:///"):
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def extract_emails(text: str) -> list:
    """提取邮箱"""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)
    exclude = ['example.com', 'test.com', 'domain.com', 'sentry', 'wixpress']
    return [e for e in emails if not any(x in e.lower() for x in exclude)][:3]


def extract_phones(text: str) -> list:
    """提取电话"""
    pattern = r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}'
    phones = re.findall(pattern, text)
    return [p for p in phones if len(p) > 7][:3]


def is_directory_site(url: str) -> bool:
    """判断是否为目录站"""
    exclude_patterns = [
        "alibaba.com", "made-in-china.com", "globalsources.com",
        "yellowpages", "yelp.com", "bbb.org",
        "crunchbase.com", "bloomberg.com", "zoominfo.com",
        "linkedin.com", "facebook.com", "twitter.com",
        "importgenius.com", "panjiva.com", "searates.com",
    ]
    url_lower = url.lower()
    return any(p in url_lower for p in exclude_patterns)


def verify_website_opencli(url: str, customer_name: str, country: str, session: str) -> dict:
    """使用 OpenCLI 核验官网"""
    result = {
        "website_accessible": "no",
        "website_match_status": "not_checked",
        "website_business_status": "",
        "website_product_relevance": "",
        "website_contact_found": "no",
        "website_contact_email": "",
        "website_contact_phone": "",
        "website_evidence_url": "",
        "website_evidence_summary": "",
    }

    if not url:
        result["website_match_status"] = "search_required"
        result["website_evidence_summary"] = "无官网URL，需搜索"
        return result

    if is_directory_site(url):
        result["website_match_status"] = "invalid_directory"
        result["website_evidence_summary"] = "URL指向目录站/B2B平台"
        return result

    # 打开网页
    open_result = run_opencli(["browser", session, "open", url])
    if not open_result.get("url"):
        result["website_evidence_summary"] = f"无法打开网页: {open_result.get('error', 'unknown')}"
        return result

    time.sleep(2)

    # 提取内容
    extract_result = run_opencli(["browser", session, "extract"])
    content = extract_result.get("content", "")
    title = extract_result.get("title", "")
    final_url = extract_result.get("url", url)

    if not content:
        result["website_evidence_summary"] = "无法提取页面内容"
        return result

    result["website_accessible"] = "yes"
    result["website_evidence_url"] = final_url

    # 分析内容
    content_lower = content.lower()
    name_parts = customer_name.lower().split()

    # 检查公司名称匹配
    name_found = any(part in content_lower for part in name_parts if len(part) > 3)

    if name_found:
        result["website_match_status"] = "confirmed"
        result["website_evidence_summary"] = "网站包含公司名称"
    else:
        result["website_match_status"] = "unconfirmed"
        result["website_evidence_summary"] = "未找到明确公司名称匹配"

    # 检查业务相关性
    business_keywords = ["steel", "pipe", "tube", "metal", "industrial", "manufacturing",
                        "engineering", "boiler", "valve", "flange", "welding", "stainless"]
    found_keywords = [kw for kw in business_keywords if kw in content_lower]
    if found_keywords:
        result["website_business_status"] = "related_business"
        result["website_product_relevance"] = "relevant" if len(found_keywords) >= 2 else "possibly_related"

    # 提取联系方式
    emails = extract_emails(content)
    if emails:
        result["contact_found"] = "yes"
        result["website_contact_email"] = emails[0]

    phones = extract_phones(content)
    if phones:
        result["website_contact_phone"] = phones[0]

    return result


def verify_linkedin_opencli(customer_name: str, country: str) -> dict:
    """使用 OpenCLI 核验 LinkedIn 公司页"""
    result = {
        "linkedin_company_found": "",
        "linkedin_company_url": "",
        "linkedin_company_name": "",
        "linkedin_employee_range": "",
        "linkedin_country_match": "",
        "linkedin_industry": "",
        "linkedin_recent_activity": "",
        "linkedin_clean_status": "",
        "linkedin_clean_reason": "",
    }

    # 使用 Google 搜索 LinkedIn 公司页
    search_query = f"site:linkedin.com/company {customer_name}"
    google_url = f"https://www.google.com/search?q={customer_name.replace(' ', '+')}+linkedin+company"

    # 打开 Google 搜索
    open_result = run_opencli(["browser", SESSION_NAME, "open", google_url], timeout=20000)
    time.sleep(2)

    # 获取页面状态
    state_result = run_opencli(["browser", SESSION_NAME, "state"])
    state_json = state_result if isinstance(state_result, dict) else {}

    # 尝试提取 LinkedIn 链接
    find_result = run_opencli(["browser", SESSION_NAME, "find", "a[href*='linkedin.com/company']"])
    matches = find_result.get("entries", [])

    if not matches:
        result["linkedin_company_found"] = "not_found"
        result["linkedin_clean_reason"] = "搜索结果未找到LinkedIn公司页"
        return result

    # 获取第一个 LinkedIn 公司链接
    linkedin_url = None
    for match in matches[:5]:
        href = match.get("href", "")
        if "linkedin.com/company/" in href:
            linkedin_url = href.split("?")[0]  # 清理URL参数
            break

    if not linkedin_url:
        result["linkedin_company_found"] = "not_found"
        result["linkedin_clean_reason"] = "未找到有效的LinkedIn公司链接"
        return result

    # 访问 LinkedIn 公司页
    result["linkedin_company_url"] = linkedin_url

    open_result = run_opencli(["browser", SESSION_NAME, "open", linkedin_url], timeout=20000)
    time.sleep(3)

    # 提取内容
    extract_result = run_opencli(["browser", SESSION_NAME, "extract"])
    content = extract_result.get("content", "")
    title = extract_result.get("title", "")

    result["linkedin_company_found"] = "found"

    # 从标题提取公司名称
    if " | " in title:
        result["linkedin_company_name"] = title.split(" | ")[0]
    else:
        result["linkedin_company_name"] = title[:50]

    # 检查国家匹配
    content_lower = content.lower()
    country_lower = country.lower()

    if country_lower in content_lower or country in title:
        result["linkedin_country_match"] = "yes"
    else:
        result["linkedin_country_match"] = "unconfirmed"

    # 提取行业信息
    industries = ["steel", "oil", "gas", "energy", "manufacturing", "engineering",
                  "industrial", "metal", "mining", "construction"]
    for ind in industries:
        if ind in content_lower:
            result["linkedin_industry"] = ind.capitalize()
            break

    # 判断匹配度
    name_lower = result["linkedin_company_name"].lower()
    customer_name_lower = customer_name.lower()

    common_words = set(name_lower.split()) & set(customer_name_lower.split())
    if common_words:
        result["linkedin_clean_status"] = "likely_match"
    else:
        result["linkedin_clean_status"] = "uncertain"
        result["linkedin_clean_reason"] = "公司名称匹配度低"

    return result


def calculate_confidence(website_result: dict, linkedin_result: dict) -> dict:
    """计算综合置信度"""
    confidence = 0
    summary_parts = []

    if website_result["website_accessible"] == "yes":
        confidence += 20
        if website_result["website_match_status"] == "confirmed":
            confidence += 30
            summary_parts.append("官网确认")
        elif website_result["website_match_status"] == "unconfirmed":
            confidence += 10
            summary_parts.append("官网未确认")
        if website_result.get("contact_found") == "yes":
            confidence += 10
        if website_result["website_product_relevance"] == "relevant":
            confidence += 10

    if linkedin_result["linkedin_company_found"] == "found":
        confidence += 10
        if linkedin_result["linkedin_clean_status"] == "likely_match":
            confidence += 20
            summary_parts.append("LinkedIn匹配")
        if linkedin_result["linkedin_country_match"] == "yes":
            confidence += 10

    confidence = min(confidence, 100)

    manual_review = "no"
    review_reason = ""
    action = ""

    if confidence >= 70:
        action = "建议继续跟进"
    elif confidence >= 40:
        action = "待人工复核"
        manual_review = "yes"
        review_reason = "置信度中等，需人工核验"
    else:
        action = "暂不跟进"
        if website_result["website_accessible"] != "yes":
            manual_review = "yes"
            review_reason = "官网无法核验"

    return {
        "external_check_confidence": str(confidence),
        "external_check_summary": "; ".join(summary_parts) if summary_parts else "无有效核验信号",
        "manual_review_flag": manual_review,
        "manual_review_reason_external": review_reason,
        "external_recommended_action": action,
    }


def verify_customer(customer: pd.Series) -> VerificationResult:
    """核验单个客户"""
    result = VerificationResult(
        internal_customer_id=str(customer.get("internal_customer_id", "")),
        customer_name=str(customer.get("customer_name", "")),
        country_region=str(customer.get("country_region", "")),
    )

    try:
        # 官网核验
        url = clean_url(str(customer.get("website_input", "")))
        website_result = verify_website_opencli(url, result.customer_name, result.country_region)

        result.website_accessible = website_result["website_accessible"]
        result.website_match_status = website_result["website_match_status"]
        result.website_business_status = website_result.get("website_business_status", "")
        result.website_product_relevance = website_result.get("website_product_relevance", "")
        result.website_contact_found = website_result.get("contact_found", "no")
        result.website_contact_email = website_result.get("website_contact_email", "")
        result.website_contact_phone = website_result.get("website_contact_phone", "")
        result.website_evidence_url = website_result["website_evidence_url"]
        result.website_evidence_summary = website_result["website_evidence_summary"]

        # LinkedIn核验
        linkedin_result = verify_linkedin_opencli(result.customer_name, result.country_region)

        result.linkedin_company_found = linkedin_result["linkedin_company_found"]
        result.linkedin_company_url = linkedin_result["linkedin_company_url"]
        result.linkedin_company_name = linkedin_result["linkedin_company_name"]
        result.linkedin_employee_range = linkedin_result["linkedin_employee_range"]
        result.linkedin_country_match = linkedin_result["linkedin_country_match"]
        result.linkedin_industry = linkedin_result["linkedin_industry"]
        result.linkedin_recent_activity = linkedin_result["linkedin_recent_activity"]
        result.linkedin_clean_status = linkedin_result["linkedin_clean_status"]
        result.linkedin_clean_reason = linkedin_result["linkedin_clean_reason"]

        # 综合判断
        confidence_result = calculate_confidence(website_result, linkedin_result)

        result.external_check_confidence = confidence_result["external_check_confidence"]
        result.external_check_summary = confidence_result["external_check_summary"]
        result.manual_review_flag = confidence_result["manual_review_flag"]
        result.manual_review_reason_external = confidence_result["manual_review_reason_external"]
        result.external_recommended_action = confidence_result["external_recommended_action"]

    except Exception as e:
        result.error_message = str(e)[:200]

    return result


def save_results(results: list[VerificationResult], output_path: str):
    """保存结果"""
    df = pd.DataFrame([asdict(r) for r in results])
    df.to_excel(output_path, index=False)


def main():
    parser = argparse.ArgumentParser(description="OpenCLI 官网/LinkedIn核验")
    parser.add_argument("--input", required=True, help="输入批次文件")
    parser.add_argument("--output", required=True, help="输出结果文件")
    parser.add_argument("--save-interval", type=int, default=5, help="保存间隔")
    args = parser.parse_args()

    print("=" * 60)
    print("OpenCLI 官网/LinkedIn核验")
    print("=" * 60)

    # 读取输入
    print(f"\n读取输入: {args.input}")
    df = pd.read_excel(args.input)
    print(f"客户数: {len(df)}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []

    # 初始化浏览器会话
    print("\n初始化浏览器会话...")
    run_opencli(["browser", SESSION_NAME, "open", "about:blank"])

    try:
        for i, (_, customer) in enumerate(df.iterrows()):
            cid = customer.get("internal_customer_id", "")
            print(f"\n[{i+1}/{len(df)}] ID: {cid}")

            try:
                result = verify_customer(customer)
                results.append(result)

                print(f"  Website: {result.website_accessible} | LinkedIn: {result.linkedin_company_found}")
                print(f"  Confidence: {result.external_check_confidence}%")

            except Exception as e:
                print(f"  Error: {str(e)[:80]}")
                error_result = VerificationResult(
                    internal_customer_id=str(customer.get("internal_customer_id", "")),
                    customer_name=str(customer.get("customer_name", "")),
                    country_region=str(customer.get("country_region", "")),
                    error_message=str(e)[:200],
                )
                results.append(error_result)

            # 定期保存
            if len(results) % args.save_interval == 0:
                save_results(results, args.output)
                print(f"  Saved {len(results)} results")

    finally:
        # 关闭浏览器
        run_opencli(["browser", SESSION_NAME, "close"])

    # 最终保存
    print("\n保存最终结果...")
    save_results(results, args.output)

    # 统计
    print("\n" + "=" * 60)
    print("统计")
    print("=" * 60)
    print(f"处理客户数: {len(results)}")

    website_ok = sum(1 for r in results if r.website_accessible == "yes")
    linkedin_found = sum(1 for r in results if r.linkedin_company_found == "found")
    manual_review = sum(1 for r in results if r.manual_review_flag == "yes")

    print(f"官网可访问: {website_ok}")
    print(f"LinkedIn找到: {linkedin_found}")
    print(f"需人工复核: {manual_review}")


if __name__ == "__main__":
    main()
