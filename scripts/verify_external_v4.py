"""
verify_external_v4.py — 官网/LinkedIn核验 (v4 修正版)

修正内容：
1. LinkedIn误匹配修正 - 更严格的确认规则
2. 字段枚举统一 - linkedin_company_found: yes/no/uncertain
3. chrome-error检测 - 不算confirmed
4. UTF-8编码修复 - 处理土耳其/俄文/中文等字符
5. 降低无效manual_review - 更合理规则
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
from difflib import SequenceMatcher

import pandas as pd

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

OPENCLI_PATH = r"C:\Users\Admin\AppData\Roaming\npm\opencli.cmd"


@dataclass
class VerificationResult:
    """核验结果"""
    internal_customer_id: str = ""
    customer_name: str = ""
    country_region: str = ""
    matched_company_name: str = ""

    website_accessible: str = "no"
    website_match_status: str = "not_checked"
    website_business_status: str = ""
    website_product_relevance: str = ""
    website_contact_found: str = "no"
    website_contact_email: str = ""
    website_contact_phone: str = ""
    website_evidence_url: str = ""
    website_evidence_summary: str = ""
    phone_cleaned_invalid: str = ""  # 记录被清洗掉的无效电话

    linkedin_company_found: str = "no"  # yes / no / uncertain
    linkedin_company_url: str = ""
    linkedin_company_name: str = ""
    linkedin_employee_range: str = ""
    linkedin_country_match: str = ""
    linkedin_industry: str = ""
    linkedin_recent_activity: str = ""
    linkedin_clean_status: str = ""  # confirmed / likely_match / no_match / access_limited / uncertain
    linkedin_clean_reason: str = ""

    external_check_confidence: str = "0"
    external_check_summary: str = ""
    manual_review_flag: str = "no"
    manual_review_reason_external: str = ""
    external_recommended_action: str = "暂不跟进"

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


def is_error_url(url: str) -> bool:
    """判断是否为错误URL"""
    if not url:
        return True
    error_prefixes = [
        "chrome-error://",
        "about:blank",
        "about:error",
        "data:text/html",
    ]
    return any(url.lower().startswith(p) for p in error_prefixes)


def is_valid_linkedin_company_url(url: str) -> bool:
    """判断是否为有效的 LinkedIn 公司页 URL"""
    if not url:
        return False
    url_lower = url.lower()
    if "linkedin.com/company/" not in url_lower:
        return False
    # 排除个人页、帖子页、搜索页
    exclude = ["/in/", "/posts/", "/search/", "/jobs/", "/school/"]
    return not any(ex in url_lower for ex in exclude)


def normalize_company_name(name: str) -> str:
    """标准化公司名用于比较"""
    if not name:
        return ""
    # 转小写
    name = name.lower()
    # 移除常见后缀
    suffixes = [
        'ltd', 'limited', 'llc', 'inc', 'inc.', 'corp', 'corp.',
        'gmbh', 'srl', 'spa', 'co', 'co.', 'company',
        'pte', 'sdn bhd', 's.a.', 's.a.u.', 'as', 'oy', 'ab',
        'private limited', 'pvt ltd', 'plc',
    ]
    for suffix in suffixes:
        if name.endswith(' ' + suffix):
            name = name[:-len(suffix)-1]
    # 移除标点和多余空格
    name = re.sub(r'[^\w\s]', ' ', name)
    name = ' '.join(name.split())
    return name.strip()


def get_company_key_words(name: str) -> set:
    """提取公司名关键词（排除通用词）"""
    if not name:
        return set()
    # 通用词列表（不计入匹配）
    generic_words = {
        'the', 'and', 'of', 'for', 'in', 'on', 'at', 'to', 'by',
        'industrial', 'international', 'group', 'company', 'companies',
        'steel', 'metal', 'pipe', 'tube', 'piping', 'tubes', 'pipes',
        'trading', 'manufacturing', 'engineering', 'industries', 'industry',
        'limited', 'ltd', 'llc', 'inc', 'corp', 'co', 'gmbh', 'srl',
        'services', 'solutions', 'products', 'systems', 'technology',
        'global', 'world', 'europe', 'asia', 'america', 'africa',
        'energy', 'power', 'oil', 'gas', 'supply', 'supplies',
    }
    name = normalize_company_name(name)
    words = set(name.split())
    # 排除通用词和短词
    return {w for w in words if w not in generic_words and len(w) > 2}


def calculate_name_similarity(name1: str, name2: str) -> float:
    """计算两个公司名的相似度"""
    n1 = normalize_company_name(name1)
    n2 = normalize_company_name(name2)
    if not n1 or not n2:
        return 0.0
    return SequenceMatcher(None, n1, n2).ratio()


def extract_linkedin_slug(url: str) -> str:
    """从 LinkedIn URL 提取 slug"""
    if not url:
        return ""
    # https://www.linkedin.com/company/tubacex/ -> tubacex
    match = re.search(r'linkedin\.com/company/([^/]+)', url.lower())
    if match:
        slug = match.group(1).replace('-', ' ')
        # 如果 slug 包含数字，尝试分离字母部分
        # 例如 "technonet573" -> "technonet"
        parts = re.findall(r'[a-z]+', slug)
        if parts:
            return ' '.join(parts)
        return slug
    return ""


def clean_linkedin_company_name(title: str) -> str:
    """清洗 LinkedIn 公司名"""
    if not title:
        return ""
    name = title.strip()
    # 去掉前缀如 "(18) " 或 "(数字) "
    name = re.sub(r'^\(\d+\)\s*', '', name)
    # 去掉后缀如 ": 关于" / ": 简介" / " | LinkedIn"
    name = re.sub(r'\s*[:|].*$', '', name)
    # 去掉多余空格
    name = ' '.join(name.split())
    return name.strip()


def is_valid_phone(phone: str, context: str = "") -> bool:
    """验证电话号码是否有效"""
    if not phone:
        return False
    phone = phone.strip()

    # 排除太短的（<7字符）
    if len(phone) < 7:
        return False

    # 排除日期格式
    # yyyy-mm-dd, yyyy/mm/dd, dd.mm.yyyy, dd-mm-yyyy, yyyy-mm-dd-hhmm
    date_patterns = [
        r'^\d{4}[-/]\d{2}[-/]\d{2}',  # yyyy-mm-dd 开头
        r'^\d{2}[.-]\d{2}[.-]\d{4}$',  # dd.mm.yyyy, dd-mm-yyyy
        r'^\d{2}[.-]\d{2}[.-]\d{2}$',  # dd.mm.yy
    ]
    for pattern in date_patterns:
        if re.match(pattern, phone):
            return False

    # 排除年份范围格式 yyyy-yyyy
    if re.match(r'^\d{4}-\d{4}$', phone):
        return False

    # 排除疑似注册号/ID格式 (数字-数字-数字 或 数字-数字)
    if re.match(r'^\d+-\d+-\d+$', phone) or re.match(r'^\d{5,}-\d{3,}$', phone):
        return False

    # 排除以横线结尾的不完整号码
    if phone.endswith('-'):
        return False

    # 排除纯数字（除非明确标注为电话）
    if phone.isdigit():
        # 纯数字一律排除，除非页面文本明确标注为电话
        context_lower = context.lower() if context else ""
        phone_indicators = ['tel:', 'tel =', 'phone:', 'phone =', '电话:', 'gsm:', 'whatsapp:', 'mobile:', 'fax:', '传真:']
        has_phone_label = any(ind in context_lower for ind in phone_indicators)

        # 即使有标注，也要检查长度是否合理（7-15位）
        if has_phone_label and 7 <= len(phone) <= 15:
            return True
        return False

    # 有效电话特征
    has_plus = '+' in phone
    has_paren = '(' in phone or ')' in phone
    has_dash = '-' in phone
    has_space = ' ' in phone

    # 包含 + 号优先保留
    if has_plus:
        return True

    # 包含括号优先保留
    if has_paren:
        return True

    # 检查是否有国家区号格式 (00xx 开头)
    if phone.startswith('00') and len(phone) >= 10:
        return True

    # 合理长度且有合理的分隔符组合
    is_reasonable_length = 8 <= len(phone) <= 20
    if is_reasonable_length:
        # 必须有分隔符
        if has_dash or has_space:
            # 检查是否像真实电话（分隔后的段长度合理）
            parts = re.split(r'[-\s]', phone)
            if all(2 <= len(p) <= 6 for p in parts if p.isdigit()):
                return True

    return False


def extract_phones_with_context(content: str) -> tuple:
    """提取电话号码，返回 (有效电话列表, 无效电话列表)"""
    # 常见电话模式
    phone_patterns = [
        r'\+?\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{1,4}[\s\-]?\d{1,4}[\s\-]?\d{0,4}',  # 国际格式
        r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}',  # 通用格式
    ]

    all_matches = []
    for pattern in phone_patterns:
        all_matches.extend(re.findall(pattern, content))

    # 去重并清理
    unique_phones = []
    seen = set()
    for p in all_matches:
        p = p.strip()
        if p and len(p) >= 7 and p not in seen:
            seen.add(p)
            unique_phones.append(p)

    valid_phones = []
    invalid_phones = []

    for phone in unique_phones:
        # 获取电话周围的上下文（前后100字符）
        idx = content.find(phone)
        if idx >= 0:
            start = max(0, idx - 100)
            end = min(len(content), idx + len(phone) + 100)
            context = content[start:end]
        else:
            context = ""

        if is_valid_phone(phone, context):
            valid_phones.append(phone)
        else:
            invalid_phones.append(phone)

    return valid_phones, invalid_phones


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
        "invalid_phones": "",
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
    final_url = open_res.get("url", "")

    # 检查是否为错误页面
    if is_error_url(final_url):
        result["match"] = "inaccessible"
        result["summary"] = "官网无法访问或页面错误"
        result["evidence_url"] = final_url
        return result

    if not final_url:
        result["summary"] = f"打开失败"
        return result

    time.sleep(2)

    # 提取内容
    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    title = ext_res.get("title", "")
    evidence_url = ext_res.get("url", final_url)

    # 再次检查错误URL
    if is_error_url(evidence_url):
        result["match"] = "inaccessible"
        result["summary"] = "官网无法访问或页面错误"
        result["evidence_url"] = evidence_url
        return result

    if not content:
        result["summary"] = "无法提取内容"
        result["evidence_url"] = evidence_url
        return result

    result["accessible"] = "yes"
    result["evidence_url"] = evidence_url

    # 检查名称匹配
    name_lower = name.lower()
    content_lower = content.lower()

    key_words = get_company_key_words(name)
    if not key_words:
        key_words = set(name_lower.split()[:3])

    found_words = {w for w in key_words if w in content_lower}

    if len(found_words) >= len(key_words) * 0.6 and len(found_words) >= 2:
        result["match"] = "confirmed"
        result["summary"] = f"名称匹配({len(found_words)}/{len(key_words)}词)"
    elif len(found_words) >= 1:
        result["match"] = "partial_match"
        result["summary"] = f"部分匹配({len(found_words)}/{len(key_words)}词)"
    else:
        result["match"] = "unconfirmed"
        result["summary"] = f"未匹配({len(found_words)}/{len(key_words)}词)"

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

    # 提取电话 - 使用更严格的验证
    valid_phones, invalid_phones = extract_phones_with_context(content)

    if valid_phones:
        result["phone"] = valid_phones[0]
    if invalid_phones:
        result["invalid_phones"] = "; ".join(invalid_phones[:5])  # 记录无效电话

    return result


def verify_linkedin_page(session: str, customer_name: str, matched_name: str,
                         country: str, li_url: str, ws_result: dict = None) -> dict:
    """验证 LinkedIn 公司页"""
    result = {
        "found": "no",
        "url": "",
        "name": "",
        "employees": "",
        "country_match": "",
        "industry": "",
        "activity": "",
        "status": "",
        "reason": "",
    }

    # 打开 LinkedIn 公司页
    open_res = run_opencli(["browser", session, "open", li_url], timeout=25)
    final_url = open_res.get("url", "")

    # 检查是否成功打开
    if not final_url or "linkedin.com/company/" not in final_url.lower():
        result["found"] = "no"
        result["status"] = "no_match"
        result["reason"] = "无法打开LinkedIn公司页"
        return result

    time.sleep(3)

    # 提取页面内容
    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    title = ext_res.get("title", "")
    page_url = ext_res.get("url", "")

    # 检查是否遇到限制
    content_lower = content.lower()
    limit_indicators = ["verification", "captcha", "security verification",
                        "sign in to continue", "authwall"]
    if any(ind in content_lower for ind in limit_indicators):
        result["found"] = "uncertain"
        result["status"] = "access_limited"
        result["reason"] = "LinkedIn需要验证"
        result["url"] = page_url
        return result

    # 确认是公司页
    if "linkedin.com/company/" not in page_url.lower():
        result["found"] = "no"
        result["status"] = "no_match"
        result["reason"] = "URL不是公司页"
        return result

    result["found"] = "yes"
    result["url"] = page_url.split("?")[0]

    # 从标题提取并清洗公司名
    raw_title = title
    result["name"] = clean_linkedin_company_name(title)

    li_company_name = result["name"]

    # 检查国家匹配 - 修正：没有国家信息时写 unknown
    country_found = False
    if country:
        # 检查页面内容是否明确包含国家信息
        country_lower = country.lower()
        if country_lower in content_lower:
            result["country_match"] = "yes"
            country_found = True
        else:
            # 检查常见的国家标识字段
            country_patterns = [
                r'location[:\s]+' + re.escape(country),
                r'headquarters[:\s]+' + re.escape(country),
                r'based in ' + re.escape(country),
            ]
            for pattern in country_patterns:
                if re.search(pattern, content_lower):
                    result["country_match"] = "yes"
                    country_found = True
                    break
    if not country_found:
        result["country_match"] = "unknown"

    # 提取行业
    industries = ["steel", "oil", "gas", "energy", "manufacturing",
                  "engineering", "metal", "mining", "construction"]
    for ind in industries:
        if ind in content_lower:
            result["industry"] = ind.capitalize()
            break

    # ===== 核心匹配判断 =====
    # 重要：优先以 customer_name 为准，matched_name 仅作辅助参考

    # 计算关键词匹配
    customer_keys = get_company_key_words(customer_name)
    matched_keys = get_company_key_words(matched_name) if matched_name else set()
    li_keys = get_company_key_words(li_company_name)

    # 与 customer_name 匹配（主要依据）
    common_with_customer = customer_keys & li_keys
    # 与 matched_name 匹配（辅助参考）
    common_with_matched = matched_keys & li_keys

    # 关键：customer_name 必须有匹配，matched_name 不能单独作为确认依据
    customer_match_count = len(common_with_customer)
    matched_match_count = len(common_with_matched)

    # 计算相似度
    sim_customer = calculate_name_similarity(customer_name, li_company_name)
    sim_matched = calculate_name_similarity(matched_name, li_company_name) if matched_name else 0

    # 检查 slug 匹配
    slug = extract_linkedin_slug(page_url)
    slug_words = set(slug.split()) if slug else set()
    slug_match_customer = customer_keys & slug_words
    slug_match_matched = matched_keys & slug_words

    # ===== 确认规则 =====
    # 规则修改：customer_name 必须有实质性匹配才能确认
    # 且排除集团/泛名称/只匹配一个短词的情况

    # 通用词列表（用于过滤）
    generic_words = {
        'the', 'and', 'of', 'for', 'in', 'on', 'at', 'to', 'by',
        'industrial', 'international', 'group', 'company', 'companies',
        'steel', 'metal', 'pipe', 'tube', 'piping', 'tubes', 'pipes',
        'trading', 'manufacturing', 'engineering', 'industries', 'industry',
        'limited', 'ltd', 'llc', 'inc', 'corp', 'co', 'gmbh', 'srl',
        'services', 'solutions', 'products', 'systems', 'technology',
        'global', 'world', 'europe', 'asia', 'america', 'africa',
        'energy', 'power', 'oil', 'gas', 'supply', 'supplies',
    }

    confirmed = False
    reasons = []

    # 检查 LinkedIn 公司名是否为集团/泛名称
    li_name_lower = li_company_name.lower()
    is_group_name = any(word in li_name_lower for word in ['group', 'groupe', 'holding', 'international'])
    # 检查客户名是否包含对应的集团主体词
    customer_norm_lower = normalize_company_name(customer_name)
    customer_has_group = any(word in customer_norm_lower for word in ['group', 'groupe', 'holding'])

    # 如果 LinkedIn 是集团名但客户名不是对应集团，默认降级
    group_penalty = False
    if is_group_name and not customer_has_group:
        group_penalty = True

    # 检查是否为单短词公司名（核心关键词<=1个且长度<=5）
    customer_key_word_count = len(customer_keys)
    is_single_short_word_company = customer_key_word_count <= 1 and len(customer_norm_lower) <= 12

    # 检查是否只匹配一个短词（<=5字符）
    short_word_match = False
    if customer_match_count == 1:
        matched_word = list(common_with_customer)[0] if common_with_customer else ""
        if len(matched_word) <= 5:
            short_word_match = True

    # 收集辅助证据
    auxiliary_evidence = []
    if result["country_match"] == "yes":
        auxiliary_evidence.append("国家一致")
    if result["industry"]:
        auxiliary_evidence.append(f"行业:{result['industry']}")
    if matched_match_count >= 1:
        auxiliary_evidence.append("matched_name支持")
    # 检查官网一致性（如果有）
    if ws_result and ws_result.get("match") in ("confirmed", "partial_match"):
        auxiliary_evidence.append("官网支持")

    # 规则1: customer_name 有 2个及以上核心词匹配
    if customer_match_count >= 2:
        if group_penalty:
            # 集团名降级为 uncertain
            reasons.append(f"客户名{customer_match_count}词匹配但LinkedIn为集团名")
        else:
            confirmed = True
            reasons.append(f"客户名{customer_match_count}词匹配")

    # 规则2: customer_name 相似度 >= 0.80
    if sim_customer >= 0.80 and not group_penalty:
        # 单短词公司需要辅助证据
        if is_single_short_word_company:
            if len(auxiliary_evidence) >= 1:
                confirmed = True
                reasons.append(f"客户名相似度{sim_customer:.0%}+" + "+".join(auxiliary_evidence[:2]))
            else:
                reasons.append(f"单短词公司相似度高但无辅助证据")
        else:
            confirmed = True
            reasons.append(f"客户名相似度{sim_customer:.0%}")

    # 规则3: slug 与 customer_name 高度匹配（>=2词）
    if slug_match_customer and len(slug_match_customer) >= 2:
        if group_penalty:
            reasons.append("slug匹配客户名但LinkedIn为集团名")
        else:
            confirmed = True
            reasons.append("slug匹配客户名")

    # 规则3.5: slug 包含客户主要标识词 - 收紧规则
    if not confirmed and slug_match_customer and len(slug_match_customer) >= 1 and not group_penalty:
        # 检查 slug 匹配词是否是公司名的核心标识（出现在公司名前部）
        customer_word_list = customer_norm_lower.split()
        # 获取匹配的 slug 词
        matched_slug_word = list(slug_match_customer)[0] if slug_match_customer else ""

        if matched_slug_word and matched_slug_word in customer_word_list[:3]:
            # 检查 LinkedIn 公司名是否包含客户名的其他关键词
            li_name_words = set(li_name_lower.split())
            other_customer_words = set(customer_word_list) - {matched_slug_word}
            # 过滤通用词
            other_customer_words = {w for w in other_customer_words if w not in generic_words and len(w) > 2}
            other_words_in_li = other_customer_words & li_name_words

            # 单短词公司需要至少一项辅助证据
            if is_single_short_word_company or short_word_match:
                if len(auxiliary_evidence) >= 1:
                    confirmed = True
                    reasons.append(f"slug匹配公司标识+" + "+".join(auxiliary_evidence[:2]))
                else:
                    reasons.append(f"单短词slug匹配但无辅助证据")
            elif len(matched_slug_word) > 4:
                # 非短词：需要 LinkedIn 公司名也包含客户名的其他关键词，或者有辅助证据
                if len(other_words_in_li) >= 1 or len(auxiliary_evidence) >= 1:
                    confirmed = True
                    reasons.append("slug匹配公司标识+" + ("其他词匹配" if other_words_in_li else "+".join(auxiliary_evidence[:1])))
                else:
                    reasons.append(f"slug匹配单词'{matched_slug_word}'但LinkedIn公司名无其他匹配")

    # 规则4: customer_name 有1个匹配 + matched_name 也匹配（双重验证）
    # 但需要至少2个不同的关键词（合并去重后）
    combined_keywords = common_with_customer | common_with_matched
    if customer_match_count >= 1 and matched_match_count >= 1 and len(combined_keywords) >= 2:
        if group_penalty:
            reasons.append("双重匹配但LinkedIn为集团名")
        else:
            confirmed = True
            reasons.append("双重匹配(" + str(len(combined_keywords)) + "词)")

    # 规则5: customer_name 有1个匹配 + 国家/行业匹配
    if customer_match_count >= 1 and not short_word_match:
        extra_match = 0
        if result["country_match"] == "yes":
            extra_match += 1
        if result["industry"]:
            extra_match += 1
        if extra_match >= 1:
            if group_penalty:
                reasons.append("客户名+国家/行业匹配但LinkedIn为集团名")
            else:
                confirmed = True
                reasons.append("客户名+国家/行业匹配")

    # 用 matched_name 作为辅助指标（仅在 customer_name 也匹配时加分）
    if confirmed and matched_match_count >= 2 and sim_matched >= 0.7:
        reasons.append("匹配名验证通过")

    # ===== 排除规则 =====

    # 检查是否只匹配通用词
    if customer_match_count == 1:
        single_match = list(common_with_customer)
        if single_match:
            # 检查是否为通用词
            generic = {'industrial', 'international', 'group', 'steel', 'metal',
                      'pipe', 'tube', 'piping', 'trading', 'manufacturing',
                      'engineering', 'services', 'solutions', 'global'}
            if single_match[0] in generic:
                confirmed = False
                reasons = ["仅匹配通用词:" + single_match[0]]

    # 判断最终状态（以 customer_name 匹配为准）
    if confirmed:
        result["status"] = "confirmed"
        result["reason"] = "; ".join(reasons)
    elif group_penalty and (customer_match_count >= 1 or sim_customer >= 0.5):
        # 集团名降级为 uncertain
        result["status"] = "uncertain"
        result["reason"] = "; ".join(reasons) if reasons else "LinkedIn为集团名需确认"
        result["found"] = "uncertain"
    elif is_single_short_word_company and (customer_match_count == 1 or slug_match_customer):
        # 单短词公司只靠单词/slug匹配但没有辅助证据
        if len(auxiliary_evidence) == 0:
            result["status"] = "uncertain"
            result["reason"] = f"单短词公司匹配但无辅助证据({'; '.join(reasons) if reasons else 'slug/单词匹配'})"
            result["found"] = "uncertain"
        elif customer_match_count >= 1 and sim_customer >= 0.4:
            result["status"] = "likely_match"
            result["reason"] = f"部分匹配+{'; '.join(auxiliary_evidence[:2])}"
            result["found"] = "uncertain"
        else:
            result["status"] = "no_match"
            result["reason"] = f"单短词公司匹配不足"
            result["found"] = "no"
    elif short_word_match and customer_match_count == 1:
        # 只匹配一个短词，降级为 uncertain
        result["status"] = "uncertain"
        result["reason"] = f"仅匹配短词({list(common_with_customer)[0]})需确认"
        result["found"] = "uncertain"
    elif customer_match_count >= 1 and sim_customer >= 0.4:
        # customer_name 有匹配且相似度达标
        result["status"] = "likely_match"
        result["reason"] = f"部分匹配({customer_match_count}词, 相似度{sim_customer:.0%})"
        result["found"] = "uncertain"
    elif customer_match_count >= 2:
        result["status"] = "likely_match"
        result["reason"] = f"{customer_match_count}词匹配"
        result["found"] = "uncertain"
    else:
        # customer_name 无实质匹配
        result["status"] = "no_match"
        result["reason"] = f"LinkedIn错配(客户名匹配{customer_match_count}词, 相似度{sim_customer:.0%})"
        result["found"] = "no"

    return result


def search_linkedin_internal(session: str, company_name: str,
                             matched_name: str, country: str) -> dict:
    """使用 LinkedIn 站内搜索"""
    result = {
        "found": "no",
        "url": "",
        "name": "",
        "employees": "",
        "country_match": "",
        "industry": "",
        "activity": "",
        "status": "",
        "reason": "",
    }

    # 构建搜索词 - 简化处理
    search_name = company_name
    if matched_name and len(matched_name) < len(company_name):
        search_name = matched_name

    # 清理搜索词
    clean_name = re.sub(r'[^\w\s]', ' ', search_name)
    clean_name = ' '.join(clean_name.split())
    # 取前3-4个单词
    words = clean_name.split()[:4]
    search_keyword = ' '.join(words)

    search_url = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(search_keyword)}"

    # 打开搜索页
    open_res = run_opencli(["browser", session, "open", search_url], timeout=25)
    if not open_res.get("url"):
        result["found"] = "no"
        result["status"] = "no_match"
        result["reason"] = "无法打开搜索页"
        return result

    time.sleep(4)

    # 检查搜索结果页
    ext_res = run_opencli(["browser", session, "extract"])
    content = ext_res.get("content", "")
    current_url = ext_res.get("url", "")
    content_lower = content.lower()

    # 检查限制
    limit_indicators = ["verification", "captcha", "sign in to continue"]
    if any(ind in content_lower for ind in limit_indicators):
        result["found"] = "uncertain"
        result["status"] = "access_limited"
        result["reason"] = "LinkedIn搜索需要验证"
        return result

    # 检查是否有结果
    if "未找到" in content or "no results" in content_lower:
        result["found"] = "no"
        result["status"] = "no_match"
        result["reason"] = "搜索无结果"
        return result

    # 查找公司链接
    find_res = run_opencli(["browser", session, "find", "--role", "link", "--limit", "100"])
    entries = find_res.get("entries", [])

    if not entries:
        result["found"] = "no"
        result["status"] = "no_match"
        result["reason"] = "搜索结果为空"
        return result

    # 收集公司链接
    company_links = []
    for e in entries:
        href = e.get("attrs", {}).get("href", "")
        if is_valid_linkedin_company_url(href):
            clean_url = href.split("?")[0]
            if clean_url not in company_links:
                company_links.append(clean_url)

    if not company_links:
        result["found"] = "no"
        result["status"] = "no_match"
        result["reason"] = "搜索结果无公司页"
        return result

    # 打开第一个公司页进行验证
    first_company = company_links[0]
    return verify_linkedin_page(session, company_name, matched_name, country, first_company, ws_result=None)


def verify_linkedin(session: str, customer_name: str, matched_name: str,
                    country: str, input_linkedin: str, website_result: dict) -> dict:
    """核验 LinkedIn"""
    result = {
        "found": "no",
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
        return verify_linkedin_page(session, customer_name, matched_name, country, input_linkedin, ws_result=website_result)

    # 2. 使用 LinkedIn 站内搜索（用 customer_name）
    result = search_linkedin_internal(session, customer_name, matched_name, country)

    # 3. 如果第一次搜索失败，尝试用 matched_name 搜索
    # 但验证时仍然使用 customer_name 作为主要依据
    if result["found"] == "no" and matched_name and matched_name != customer_name:
        result2 = search_linkedin_internal(session, matched_name, "", country)
        if result2["found"] != "no" and result2["url"]:
            # 重新验证：用 customer_name 验证找到的 LinkedIn 公司
            result2 = verify_linkedin_page(session, customer_name, matched_name, country, result2["url"], ws_result=website_result)
            # 只有当 customer_name 也有匹配时才接受
            if result2["status"] == "confirmed" and result2["found"] == "yes":
                result = result2
            else:
                # matched_name 找到的公司不匹配 customer_name，标记为 no_match
                result["found"] = "no"
                result["status"] = "no_match"
                result["reason"] = "matched_name搜索结果不匹配customer_name"

    return result


def calc_confidence(ws: dict, li: dict) -> dict:
    """计算置信度"""
    conf = 0
    parts = []

    # 官网评分
    if ws["accessible"] == "yes":
        conf += 20
        if ws["match"] == "confirmed":
            conf += 35
            parts.append("官网确认")
        elif ws["match"] == "partial_match":
            conf += 20
            parts.append("官网部分匹配")
        elif ws["match"] == "unconfirmed":
            conf += 5
        if ws.get("contact") == "yes":
            conf += 10
        if ws.get("relevance") == "relevant":
            conf += 10
    elif ws["match"] == "inaccessible":
        parts.append("官网无法访问")

    # LinkedIn评分
    if li["found"] == "yes" and li["status"] == "confirmed":
        conf += 30
        parts.append("LinkedIn确认")
    elif li["found"] == "yes" and li["status"] == "likely_match":
        conf += 15
        parts.append("LinkedIn可能匹配")
    elif li["found"] == "uncertain":
        conf += 5
        parts.append("LinkedIn待确认")
    elif li["status"] == "access_limited":
        parts.append("LinkedIn受限")
    # LinkedIn no_match 不扣分，只是没有加分

    conf = min(conf, 100)

    # 单短词公司或 LinkedIn uncertain 时，限制置信度不超过 60
    if li["found"] == "uncertain":
        conf = min(conf, 60)

    # manual_review 判断 - 收紧规则
    # 规则：如果 action = "待人工复核" 则 manual 必须为 yes
    #       如果 manual = no 则 action 不能是 "待人工复核"
    manual = "no"
    reason = ""

    if conf >= 70:
        action = "建议继续跟进"
    elif conf >= 40:
        # 需要判断是否真的需要人工复核
        if ws["match"] not in ("confirmed", "partial_match") and li["status"] == "no_match":
            manual = "yes"
            reason = "官网未确认且LinkedIn错配"
            action = "待人工复核"
        elif li["status"] == "access_limited":
            manual = "yes"
            reason = "LinkedIn访问受限"
            action = "待人工复核"
        else:
            # 中等置信度但无冲突，不需要人工复核
            action = "转官网/LinkedIn核验后跟进"
    else:
        action = "暂不跟进"
        # 只有明确错配才标记 manual_review
        if li["status"] == "no_match" and ws["match"] in ("confirmed", "partial_match"):
            # 官网确认但 LinkedIn 错配 - 可能是主体冲突
            manual = "yes"
            reason = "官网确认但LinkedIn错配(主体冲突?)"
            action = "待人工复核"
        elif ws["match"] == "unconfirmed" and li["status"] == "no_match":
            manual = "yes"
            reason = "官网和LinkedIn均未确认"
            action = "待人工复核"

    return {
        "confidence": str(conf),
        "summary": "; ".join(parts) or "无有效信号",
        "manual_flag": manual,
        "manual_reason": reason,
        "action": action,
    }


def safe_str(s, max_len=200):
    """安全转换为字符串"""
    if s is None:
        return ""
    s = str(s)
    # 移除可能导致问题的字符
    return s[:max_len]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--save-interval", type=int, default=5)
    args = parser.parse_args()

    print("=" * 60)
    print("OpenCLI 官网/LinkedIn 核验 (v4)")
    print("=" * 60)

    df = pd.read_excel(args.input)
    print(f"\n读取: {args.input} ({len(df)} 客户)")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    session = "verify_v4"

    run_opencli(["browser", session, "open", "about:blank"])

    try:
        for i, row in df.iterrows():
            cid = safe_str(row.get("internal_customer_id", ""), 50)
            name = safe_str(row.get("customer_name", ""), 100)
            country = safe_str(row.get("country_region", ""), 50)
            url = safe_str(row.get("website_input", ""), 200)
            input_linkedin = safe_str(row.get("linkedin", ""), 200)
            matched_name = safe_str(row.get("matched_company_name", ""), 100)

            print(f"\n[{i+1}/{len(df)}] {cid}")

            result = VerificationResult(
                internal_customer_id=cid,
                customer_name=name,
                country_region=country,
                matched_company_name=matched_name,
            )

            try:
                # 官网核验
                ws = verify_website(url, name, session)
                result.website_accessible = ws["accessible"]
                result.website_match_status = ws["match"]
                result.website_business_status = ws.get("business", "")
                result.website_product_relevance = ws.get("relevance", "")
                result.website_contact_found = ws.get("contact", "no")
                result.website_contact_email = ws.get("email", "")
                result.website_contact_phone = ws.get("phone", "")
                result.phone_cleaned_invalid = ws.get("invalid_phones", "")
                result.website_evidence_url = ws["evidence_url"]
                result.website_evidence_summary = ws["summary"]

                # LinkedIn 核验
                li = verify_linkedin(session, name, matched_name, country, input_linkedin, ws)
                result.linkedin_company_found = li["found"]
                result.linkedin_company_url = li["url"]
                result.linkedin_company_name = safe_str(li["name"], 100)
                result.linkedin_employee_range = li["employees"]
                result.linkedin_country_match = li["country_match"]
                result.linkedin_industry = li["industry"]
                result.linkedin_recent_activity = li["activity"]
                result.linkedin_clean_status = li["status"]
                result.linkedin_clean_reason = safe_str(li["reason"], 200)

                # 置信度
                c = calc_confidence(ws, li)
                result.external_check_confidence = c["confidence"]
                result.external_check_summary = c["summary"]
                result.manual_review_flag = c["manual_flag"]
                result.manual_review_reason_external = c["manual_reason"]
                result.external_recommended_action = c["action"]

                print(f"  WS:{ws['match'][:8]} LI:{li['found'][:8]} Status:{li['status'][:12]} Conf:{c['confidence']}%")

            except Exception as e:
                err_msg = safe_str(str(e), 150)
                result.error_message = err_msg
                print(f"  Error: {err_msg[:60]}")

            results.append(result)

            if len(results) % args.save_interval == 0:
                pd.DataFrame([asdict(r) for r in results]).to_excel(args.output, index=False)
                print(f"  Saved {len(results)}")

    finally:
        run_opencli(["browser", session, "close"])

    pd.DataFrame([asdict(r) for r in results]).to_excel(args.output, index=False)

    # 统计
    print("\n" + "=" * 60)
    print("统计")
    print("=" * 60)
    print(f"处理: {len(results)}")

    # 官网统计
    ws_accessible = sum(1 for r in results if r.website_accessible == "yes")
    ws_confirmed = sum(1 for r in results if r.website_match_status == "confirmed")
    ws_inaccessible = sum(1 for r in results if r.website_match_status == "inaccessible")

    print(f"\n官网统计:")
    print(f"  可访问: {ws_accessible}")
    print(f"  confirmed: {ws_confirmed}")
    print(f"  inaccessible: {ws_inaccessible}")

    # LinkedIn统计
    li_yes = sum(1 for r in results if r.linkedin_company_found == "yes")
    li_uncertain = sum(1 for r in results if r.linkedin_company_found == "uncertain")
    li_no = sum(1 for r in results if r.linkedin_company_found == "no")
    li_confirmed = sum(1 for r in results if r.linkedin_clean_status == "confirmed")
    li_likely = sum(1 for r in results if r.linkedin_clean_status == "likely_match")
    li_no_match = sum(1 for r in results if r.linkedin_clean_status == "no_match")
    li_limited = sum(1 for r in results if r.linkedin_clean_status == "access_limited")

    print(f"\nLinkedIn统计:")
    print(f"  found=yes: {li_yes}")
    print(f"  found=uncertain: {li_uncertain}")
    print(f"  found=no: {li_no}")
    print(f"  status=confirmed: {li_confirmed}")
    print(f"  status=likely_match: {li_likely}")
    print(f"  status=no_match: {li_no_match}")
    print(f"  status=access_limited: {li_limited}")

    manual_review = sum(1 for r in results if r.manual_review_flag == "yes")
    errors = sum(1 for r in results if r.error_message)

    print(f"\n其他统计:")
    print(f"  manual_review: {manual_review}")
    print(f"  phone_cleaned_invalid: {sum(1 for r in results if r.phone_cleaned_invalid)}")
    print(f"  error: {errors}")


if __name__ == "__main__":
    main()
