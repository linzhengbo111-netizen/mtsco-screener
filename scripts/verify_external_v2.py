"""
verify_external_v2.py — 使用 OpenCLI 核验官网/LinkedIn (简化版)

直接使用 OpenCLI 命令行进行核验。
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

import pandas as pd


@dataclass
class VerificationResult:
    """核验结果"""
    internal_customer_id: str = ""
    customer_name: str = ""
    country_region: str = ""

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


# OpenCLI 完整路径 (Windows)
OPENCLI_PATH = r"C:\Users\Admin\AppData\Roaming\npm\opencli.cmd"


def run_opencli(cmd_parts: list, timeout: int = 30) -> dict:
    """运行 OpenCLI 命令并解析 JSON 输出"""
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
    valid_emails = [e for e in emails if not any(x in e.lower() for x in ['example', 'test', 'sentry'])]
    if valid_emails:
        result["contact"] = "yes"
        result["email"] = valid_emails[0]

    phones = re.findall(r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}', content)
    valid_phones = [p for p in phones if len(p) > 8]
    if valid_phones:
        result["phone"] = valid_phones[0]

    return result


def verify_linkedin(name: str, country: str, session: str) -> dict:
    """核验 LinkedIn 公司页"""
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

    # 搜索 LinkedIn
    search_url = f"https://www.google.com/search?q={name.replace(' ', '+')}+linkedin+company"

    open_res = run_opencli(["browser", session, "open", search_url], timeout=20)
    time.sleep(2)

    # 查找 LinkedIn 公司链接
    find_res = run_opencli(["browser", session, "find", "a[href*='linkedin.com/company']"])
    matches = find_res.get("entries", [])

    if not matches:
        result["found"] = "not_found"
        result["reason"] = "搜索无结果"
        return result

    # 获取第一个链接
    linkedin_url = None
    for m in matches[:5]:
        href = m.get("href", "")
        if "linkedin.com/company/" in href:
            linkedin_url = href.split("?")[0]
            break

    if not linkedin_url:
        result["found"] = "not_found"
        result["reason"] = "无有效链接"
        return result

    result["url"] = linkedin_url

    # 访问 LinkedIn 公司页
    open_res = run_opencli(["browser", session, "open", linkedin_url], timeout=20)
    time.sleep(3)

    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    title = ext_res.get("title", "")

    result["found"] = "found"

    # 从标题提取公司名
    if " | " in title:
        result["name"] = title.split(" | ")[0]
    else:
        result["name"] = title[:50]

    # 检查国家
    if country.lower() in content.lower() or country in title:
        result["country_match"] = "yes"
    else:
        result["country_match"] = "unconfirmed"

    # 提取行业
    industries = ["steel", "oil", "gas", "energy", "manufacturing", "engineering", "metal"]
    content_lower = content.lower()
    for ind in industries:
        if ind in content_lower:
            result["industry"] = ind.capitalize()
            break

    # 匹配状态
    name_lower = result["name"].lower()
    customer_lower = name.lower()
    common = set(name_lower.split()) & set(customer_lower.split())
    if common:
        result["status"] = "likely_match"
    else:
        result["status"] = "uncertain"
        result["reason"] = "名称匹配度低"

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
        if li["status"] == "likely_match":
            conf += 20
            parts.append("LinkedIn匹配")
        if li["country_match"] == "yes":
            conf += 10

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
        if ws["accessible"] != "yes":
            manual = "yes"
            reason = "官网无法核验"

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
    args = parser.parse_args()

    print("=" * 60)
    print("OpenCLI 官网/LinkedIn 核验")
    print("=" * 60)

    df = pd.read_excel(args.input)
    print(f"\n读取: {args.input} ({len(df)} 客户)")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    session = "verify_ext"

    # 初始化
    run_opencli(["browser", session, "open", "about:blank"])

    try:
        for i, row in df.iterrows():
            cid = str(row.get("internal_customer_id", ""))
            name = str(row.get("customer_name", ""))
            country = str(row.get("country_region", ""))
            url = str(row.get("website_input", ""))

            print(f"\n[{i+1}/{len(df)}] {cid}")

            result = VerificationResult(
                internal_customer_id=cid,
                customer_name=name,
                country_region=country,
            )

            try:
                # 官网核验
                ws = verify_website(url, name, session)
                result.website_accessible = ws["accessible"]
                result.website_match_status = ws["match"]
                result.website_business_status = ws["business"]
                result.website_product_relevance = ws["relevance"]
                result.website_contact_found = ws["contact"]
                result.website_contact_email = ws["email"]
                result.website_contact_phone = ws["phone"]
                result.website_evidence_url = ws["evidence_url"]
                result.website_evidence_summary = ws["summary"]

                # LinkedIn 核验
                li = verify_linkedin(name, country, session)
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

                print(f"  WS:{ws['accessible']} LI:{li['found']} Conf:{c['confidence']}%")

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
    print(f"LinkedIn找到: {sum(1 for r in results if r.linkedin_company_found == 'found')}")
    print(f"需人工复核: {sum(1 for r in results if r.manual_review_flag == 'yes')}")


if __name__ == "__main__":
    main()
