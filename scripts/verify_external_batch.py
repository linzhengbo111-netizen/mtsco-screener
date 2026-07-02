"""
verify_external_batch.py — 官网/LinkedIn核验脚本

使用 Playwright 自动化浏览器核验客户官网和 LinkedIn 公司页。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlparse

import pandas as pd
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext


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


def clean_url(url: str) -> str:
    """清理URL，移除无效前缀"""
    if not url:
        return ""
    url = url.strip()
    # 移除 http:// 空链接
    if url in ("http://", "https://", "http:///", "https:///"):
        return ""
    # 确保有协议
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def is_valid_company_website(url: str, page_content: str, customer_name: str) -> dict:
    """判断是否为有效公司官网"""
    result = {
        "is_valid": False,
        "match_status": "unconfirmed",
        "business_status": "",
        "product_relevance": "",
        "contact_found": "no",
        "contact_email": "",
        "contact_phone": "",
        "evidence_summary": "",
    }

    url_lower = url.lower()
    content_lower = page_content.lower()

    # 排除目录站、B2B平台等
    exclude_patterns = [
        "alibaba.com", "made-in-china.com", "globalsources.com",
        "yellowpages", "yelp.com", "bbb.org",
        "crunchbase.com", "bloomberg.com", "zoominfo.com",
        "linkedin.com", "facebook.com", "twitter.com",
        "importgenius.com", "panjiva.com", "searates.com",
        "tradekey.com", "ec21.com", "ecplaza.net",
        "wedos.com", "godaddy.com", "wix.com",
    ]

    for pattern in exclude_patterns:
        if pattern in url_lower:
            result["match_status"] = "invalid_directory"
            result["evidence_summary"] = f"URL指向目录站/B2B平台: {pattern}"
            return result

    # 检查公司名称匹配
    name_parts = customer_name.lower().split()
    name_found = False
    for part in name_parts:
        if len(part) > 3 and part in content_lower:
            name_found = True
            break

    if name_found:
        result["is_valid"] = True
        result["match_status"] = "confirmed"
        result["evidence_summary"] = f"网站内容包含公司名称关键词"
    else:
        result["match_status"] = "unconfirmed"
        result["evidence_summary"] = "网站未找到明确公司名称匹配"

    # 提取联系方式
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(email_pattern, page_content)
    if emails:
        # 过滤常见无效邮箱
        valid_emails = [e for e in emails if not any(x in e.lower() for x in ['example.com', 'test.com', 'domain.com', 'email.com'])]
        if valid_emails:
            result["contact_found"] = "yes"
            result["contact_email"] = valid_emails[0]

    phone_pattern = r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}'
    phones = re.findall(phone_pattern, page_content)
    if phones and len(phones[0]) > 7:
        result["contact_phone"] = phones[0]

    # 检查业务相关性关键词
    business_keywords = ["steel", "pipe", "tube", "metal", "industrial", "manufacturing",
                        "engineering", "boiler", "valve", "flange", "welding",
                        "stainless", "carbon", "alloy", "piping", "supply"]
    found_keywords = [kw for kw in business_keywords if kw in content_lower]
    if found_keywords:
        result["business_status"] = "related_business"
        result["product_relevance"] = "relevant" if len(found_keywords) >= 2 else "possibly_related"

    return result


def verify_website(page: Page, customer: pd.Series) -> dict:
    """核验官网"""
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

    # 获取URL
    url = clean_url(str(customer.get("website_input", "")))

    if not url:
        # 尝试搜索官网
        customer_name = customer.get("customer_name", "")
        country = customer.get("country_region", "")
        search_query = f"{customer_name} {country} official website"
        result["website_evidence_summary"] = "需搜索官网"
        result["website_match_status"] = "search_required"
        return result

    try:
        # 访问网站
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

        # 获取页面内容
        page_content = page.content()
        title = page.title()

        result["website_accessible"] = "yes"
        result["website_evidence_url"] = url

        # 分析网站
        customer_name = customer.get("customer_name", "")
        analysis = is_valid_company_website(url, page_content, customer_name)

        result["website_match_status"] = analysis["match_status"]
        result["website_business_status"] = analysis["business_status"]
        result["website_product_relevance"] = analysis["product_relevance"]
        result["website_contact_found"] = analysis["contact_found"]
        result["website_contact_email"] = analysis["contact_email"]
        result["website_contact_phone"] = analysis["contact_phone"]
        result["website_evidence_summary"] = analysis["evidence_summary"]

    except Exception as e:
        result["website_accessible"] = "error"
        result["website_evidence_summary"] = f"访问失败: {str(e)[:100]}"

    return result


def verify_linkedin(page: Page, customer: pd.Series) -> dict:
    """核验LinkedIn公司页"""
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

    customer_name = customer.get("customer_name", "")
    country = customer.get("country_region", "")

    try:
        # 搜索LinkedIn公司页
        search_query = f"{customer_name} LinkedIn company"
        search_url = f"https://www.google.com/search?q={quote_plus(search_query)}"

        page.goto(search_url, timeout=30000)
        time.sleep(2)

        # 查找LinkedIn公司链接
        links = page.query_selector_all("a[href*='linkedin.com/company/']")
        if not links:
            # 尝试其他选择器
            links = page.query_selector_all("a")

        linkedin_url = None
        for link in links[:10]:
            href = link.get_attribute("href") or ""
            if "linkedin.com/company/" in href:
                linkedin_url = href
                break

        if not linkedin_url:
            result["linkedin_company_found"] = "not_found"
            result["linkedin_clean_reason"] = "搜索结果未找到LinkedIn公司页"
            return result

        # 访问LinkedIn公司页
        result["linkedin_company_url"] = linkedin_url
        page.goto(linkedin_url, timeout=30000)
        time.sleep(3)

        # 提取公司信息
        try:
            # 公司名称
            name_el = page.query_selector("h1")
            if name_el:
                result["linkedin_company_name"] = name_el.inner_text().strip()
        except:
            pass

        try:
            # 员工规模
            employee_el = page.query_selector("[class*='employee'], [class*='staff']")
            if employee_el:
                result["linkedin_employee_range"] = employee_el.inner_text().strip()
        except:
            pass

        try:
            # 行业
            industry_el = page.query_selector("[class*='industry']")
            if industry_el:
                result["linkedin_industry"] = industry_el.inner_text().strip()
        except:
            pass

        result["linkedin_company_found"] = "found"

        # 判断匹配度
        page_content = page.content().lower()
        country_lower = country.lower()

        if country_lower in page_content:
            result["linkedin_country_match"] = "yes"
        else:
            result["linkedin_country_match"] = "unconfirmed"

        # 判断是否干净
        if result["linkedin_company_name"]:
            name_lower = result["linkedin_company_name"].lower()
            customer_name_lower = customer_name.lower()

            # 检查名称相似度
            common_words = set(name_lower.split()) & set(customer_name_lower.split())
            if common_words:
                result["linkedin_clean_status"] = "likely_match"
            else:
                result["linkedin_clean_status"] = "uncertain"
                result["linkedin_clean_reason"] = "公司名称匹配度低"
        else:
            result["linkedin_clean_status"] = "uncertain"
            result["linkedin_clean_reason"] = "无法提取公司信息"

    except Exception as e:
        result["linkedin_company_found"] = "error"
        result["linkedin_clean_reason"] = f"访问失败: {str(e)[:100]}"

    return result


def calculate_confidence(website_result: dict, linkedin_result: dict, customer: pd.Series) -> dict:
    """计算综合置信度"""
    confidence = 0
    summary_parts = []

    # 官网置信度
    if website_result["website_accessible"] == "yes":
        confidence += 20
        if website_result["website_match_status"] == "confirmed":
            confidence += 30
            summary_parts.append("官网确认")
        elif website_result["website_match_status"] == "unconfirmed":
            confidence += 10
            summary_parts.append("官网未确认")
        if website_result["website_contact_found"] == "yes":
            confidence += 10
        if website_result["website_product_relevance"] == "relevant":
            confidence += 10
            summary_parts.append("产品相关")

    # LinkedIn置信度
    if linkedin_result["linkedin_company_found"] == "found":
        confidence += 10
        if linkedin_result["linkedin_clean_status"] == "likely_match":
            confidence += 20
            summary_parts.append("LinkedIn匹配")
        elif linkedin_result["linkedin_clean_status"] == "uncertain":
            confidence += 5
            summary_parts.append("LinkedIn待确认")
        if linkedin_result["linkedin_country_match"] == "yes":
            confidence += 10

    # 综合判断
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
        if website_result["website_accessible"] != "yes" and linkedin_result["linkedin_company_found"] != "found":
            manual_review = "yes"
            review_reason = "官网和LinkedIn均无法核验"

    return {
        "external_check_confidence": str(confidence),
        "external_check_summary": "; ".join(summary_parts) if summary_parts else "无有效核验信号",
        "manual_review_flag": manual_review,
        "manual_review_reason_external": review_reason,
        "external_recommended_action": action,
    }


def verify_customer(page: Page, customer: pd.Series) -> VerificationResult:
    """核验单个客户"""
    result = VerificationResult(
        internal_customer_id=str(customer.get("internal_customer_id", "")),
        customer_name=str(customer.get("customer_name", "")),
        country_region=str(customer.get("country_region", "")),
    )

    try:
        # 官网核验
        website_result = verify_website(page, customer)

        result.website_accessible = website_result["website_accessible"]
        result.website_match_status = website_result["website_match_status"]
        result.website_business_status = website_result["website_business_status"]
        result.website_product_relevance = website_result["website_product_relevance"]
        result.website_contact_found = website_result["website_contact_found"]
        result.website_contact_email = website_result["website_contact_email"]
        result.website_contact_phone = website_result["website_contact_phone"]
        result.website_evidence_url = website_result["website_evidence_url"]
        result.website_evidence_summary = website_result["website_evidence_summary"]

        # LinkedIn核验
        linkedin_result = verify_linkedin(page, customer)

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
        confidence_result = calculate_confidence(website_result, linkedin_result, customer)

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
    print(f"  已保存 {len(results)} 条结果到 {output_path}")


def main():
    parser = argparse.ArgumentParser(description="官网/LinkedIn核验")
    parser.add_argument("--input", required=True, help="输入批次文件")
    parser.add_argument("--output", required=True, help="输出结果文件")
    parser.add_argument("--headless", action="store_true", default=True, help="无头模式")
    parser.add_argument("--save-interval", type=int, default=5, help="保存间隔（每N个客户）")
    args = parser.parse_args()

    print("=" * 60)
    print("官网/LinkedIn核验")
    print("=" * 60)

    # 读取输入
    print(f"\n读取输入: {args.input}")
    df = pd.read_excel(args.input)
    print(f"  客户数: {len(df)}")

    # 确保输出目录存在
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []

    # 启动浏览器
    print("\n启动浏览器...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 逐个核验
        for i, (_, customer) in enumerate(df.iterrows()):
            try:
                name_display = str(customer['customer_name'])[:40]
            except:
                name_display = str(customer.get('internal_customer_id', 'Unknown'))
            print(f"\n[{i+1}/{len(df)}] ID: {customer.get('internal_customer_id', '')}")

            try:
                result = verify_customer(page, customer)
                results.append(result)

                print(f"  Website: {result.website_accessible} | LinkedIn: {result.linkedin_company_found}")
                print(f"  Confidence: {result.external_check_confidence}%")

            except KeyboardInterrupt:
                print("\n用户中断")
                break
            except Exception as e:
                print(f"  错误: {str(e)[:100]}")
                # 记录失败
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

        # 关闭浏览器
        browser.close()

    # 最终保存
    print("\n保存最终结果...")
    save_results(results, args.output)

    # 统计
    print("\n" + "=" * 60)
    print("统计")
    print("=" * 60)
    print(f"处理客户数: {len(results)}")

    website_accessible = sum(1 for r in results if r.website_accessible == "yes")
    linkedin_found = sum(1 for r in results if r.linkedin_company_found == "found")
    manual_review = sum(1 for r in results if r.manual_review_flag == "yes")

    print(f"官网可访问: {website_accessible}")
    print(f"LinkedIn找到: {linkedin_found}")
    print(f"需人工复核: {manual_review}")


if __name__ == "__main__":
    main()
