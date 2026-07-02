"""
generate_tendata_input.py — 生成腾道全量输入表

从 all/all_customers_unique_deduped.xlsx 生成 tendata_all_customers_input.xlsx
"""

from __future__ import annotations

import re
import sys
import pandas as pd
from pathlib import Path

# Force UTF-8 output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

INPUT_DIR = Path(__file__).parent.parent
INPUT_FILE = INPUT_DIR / "all" / "all_customers_unique_deduped.xlsx"
OUTPUT_FILE = INPUT_DIR / "tendata_all_customers_input.xlsx"

# ── 各国法律后缀 ──
LEGAL_SUFFIXES = [
    # 通用
    r'\binc\.?\b',
    r'\bltd\.?\b',
    r'\bcorp\.?\b',
    r'\bllc\.?\b',
    r'\bco\.?\b',
    r'\b(l\.l\.c\.|l\.l\.c|ltd\.|inc\.|corp\.|co\.)\b',
    # 全称
    r'\blimited\s*liability\s*company\b',
    r'\bpublic\s*limited\s*company\b',
    # 马来西亚/新加坡
    r'\bsdn\s*bhd\b\.?',
    r'\bsdn\b',
    r'\bbhd\b',
    # 土耳其
    r'\bsan\.?\s*ve\s*ticaret\b\.?',
    r'\bsan\.?\s*ve\b',
    r'\bdis\s*ticaret\b\.?',
    r'\bithalat\s*ihracat\b\.?',
    r'\bsanayii?\b',
    r'\btic\.?\b',
    r'\bltd\.?\s*sti\.?',
    r'\bltd\.?\s*şti\.?',
    r'\bşti\.?\b',
    r'\bsti\.?\b',
    r'\blimited\s*şirketi\b',
    r'\blimited\s*sti\b',
    r'\ba\.ş\.?\b',
    r'\bas\b\.?',  # A.Ş. without dots
    # 意大利
    r'\bsrl\b\.?',
    r'\bspa\b\.?',
    r'\bsas\b\.?',
    # 西班牙/拉美
    r'\bsa\b\.?',
    r'\bsa\s*de\s*cv\b',
    r'\bs\s*de\s*rl\b',
    r'\bs\.?l\.?\b',
    r'\bltda\b\.?',
    r'\beirl\b\.?',
    # 波兰
    r'\bsp\s*z\s*o\.?o\.?\b',
    r'\bsp\.\s*z\s*o\.?o\.?\b',
    # 法国/比利时
    r'\bsar[l]{1,2}\b\.?',
    # 德国/奥地利
    r'\bgmbh\b\.?',
    r'\bgesmbh\b\.?',
    # 荷兰/比利时
    r'\bnv\b\.?',
    r'\bbv\b\.?',
    # 南非
    r'\bpty\b\.?\s*\bltd\b\.?',
    # 俄罗斯/独联体
    r'\booo\b\.?',
    r'\bооо\b\.?',  # 西里尔
    r'\bao\b\.?',
    r'\bllc\b\.?',
    # 中东
    r'\b(for\s*)?contracting\b',
    r'\best\.?\b',
    r'\bdmcc\b',
    # 英国
    r'\bplc\b\.?',
    # 哥伦比亚
    # SAS already covered
    # 巴西
    # Ltda already covered
]

# 噪声后缀/备注（业务描述）
NOISE_PATTERNS = [
    r'\(照片.*?\)',
    r'（照片.*?）',
    r'（没事别发）',
    r'\(没事别发\)',
    r'杜塞展.*?询价',
    r'盘管客户',
    r'管件法兰询价',
    r'\(.*?展.*?\)',
    r'（.*?展.*?）',
    r'（.*?全.*?）',
    r'\d{4,8}.*?(成交|跟进|联系)',
    # 中文备注在括号内
    r'[（\(][^\)\）]*?土[^\)\）]*?[）\)]',
    r'[（\(][^\)\）]*?照片[^\)\）]*?[）\)]',
    r'[（\(][^\)\）]*?没事[^\)\）]*?[）\)]',
    r'[（\(][^\)\）]*?贸易[^\)\）]*?[）\)]',
    r'[（\(][^\)\）]*?认识[^\)\）]*?[）\)]',
    r'[（\(][^\)\）]*?库存[^\)\）]*?[）\)]',
    r'[（\(][^\)\）]*?采购[^\)\）]*?[）\)]',
    # 括号内中文通用
    r'[（\(][一-鿿]+[）\)]',
    # 短横线分隔的备注
    r'\s*[-–—]\s*盘管\s*$',
    r'\s*[-–—]\s*询价\s*$',
    # 后缀中文备注（非括号包裹）
    r'\s*[（\(]?采购型[：:].*?[）\)]?$',
    r'\s*贸易商\s*$',
]

# 国家到搜索语言映射
COUNTRY_LANG = {
    "土耳其": "tr",
    "德国": "de",
    "意大利": "it",
    "西班牙": "es",
    "法国": "fr",
    "波兰": "pl",
    "印度": "en",
    "俄罗斯": "ru",
    "哈萨克斯坦": "kk",
    "巴西": "pt",
    "中国": "zh",
    "中国香港": "zh",
    "中国台湾": "zh",
}


def safe_str(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s == "nan":
        return ""
    return s


def clean_noise(name: str) -> str:
    """去除公司名称中的业务备注噪声。"""
    for pat in NOISE_PATTERNS:
        name = re.sub(pat, '', name, flags=re.IGNORECASE)
    # 去除短的括号内容（如 (M), (PTY), (LLC) 等区域/法律标注）
    name = re.sub(r'\([a-zA-Z]{1,3}\)', '', name)
    name = re.sub(r'（[a-zA-Z]{1,3}）', '', name)
    # 去除多余空格和前后标点
    name = re.sub(r'\s+', ' ', name).strip().strip("()-./,;：，")
    return name


def strip_legal_suffixes(name: str) -> str:
    """去除各种法律后缀。"""
    n = name.lower().strip()
    for pat in LEGAL_SUFFIXES:
        n = re.sub(pat, '', n, flags=re.IGNORECASE)
    # 去除清理后残留的标点
    n = re.sub(r'[\.\,\;\&\(\)]+', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def extract_domain_from_website(url: str) -> str:
    """从网站URL提取主域名。"""
    url = safe_str(url)
    if not url:
        return ""
    url = url.lower().strip().rstrip("/")
    # 去掉协议
    url = re.sub(r'https?://', '', url)
    # 去掉路径
    url = url.split("/")[0].split("?")[0]
    # 过滤无效结果
    if len(url) < 3 or url in ("http:", "https:", "www.", "http", "https"):
        return ""
    return url


def generate_search_variants(company_name: str, short_name: str, country: str,
                             email_domain: str, website: str) -> list[str]:
    """生成腾道搜索变体。"""
    variants = []
    seen = set()

    # 1. 清洗噪声后的原始名称
    cleaned = clean_noise(company_name)
    if cleaned and cleaned.lower() not in seen:
        variants.append(cleaned)
        seen.add(cleaned.lower())

    # 2. 去除法律后缀版本
    stripped = strip_legal_suffixes(cleaned)
    if stripped and len(stripped) > 2 and stripped.lower() not in seen:
        variants.append(stripped)
        seen.add(stripped.lower())

    # 3. 公司简称（如有）— 也清洗噪声
    short = clean_noise(safe_str(short_name))
    if short and short.lower() not in seen and len(short) > 1:
        variants.append(short)
        seen.add(short.lower())

    # 4. 主体词版本 — 取清洗后名称中长度>=3的关键词
    # 使用 Unicode 字母匹配（含土耳其语 ğ ş ı ö ü ç 等）
    words = re.findall(r'[\w]{3,}', cleaned, re.UNICODE)
    # 过滤纯数字
    words = [w for w in words if not w.isdigit()]
    if words:
        core = " ".join(words[:3])  # 取前3个主体词
        if core.lower() not in seen:
            variants.append(core)
            seen.add(core.lower())
        # 只取第一个主体词
        if len(words) > 1 and words[0].lower() not in seen:
            variants.append(words[0])
            seen.add(words[0].lower())

    # 5. 邮箱域名作为搜索词（如果是公司域名而非 gmail/yahoo 等）
    free_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "163.com",
                    "qq.com", "mail.com", "live.com", "msn.com", "icloud.com"}
    ed = email_domain.lower()
    if ed and ed not in free_domains:
        # 去掉 .com/.net/.org/.co 等TLD，取主域名
        core_domain = re.sub(r'\.(com|net|org|co|io|info|biz|com\.\w{2})$', '', ed)
        if core_domain and core_domain.lower() not in seen and len(core_domain) > 2:
            variants.append(core_domain)
            seen.add(core_domain.lower())

    # 6. 网站域名
    ws_domain = extract_domain_from_website(website)
    if ws_domain:
        core_ws = re.sub(r'\.(com|net|org|co|io|info|biz|com\.\w{2})$', '', ws_domain)
        if core_ws and core_ws.lower() not in seen and len(core_ws) > 2:
            variants.append(core_ws)
            seen.add(core_ws.lower())

    # 7. 针对特定国家生成额外变体
    country = safe_str(country)
    # 土耳其: & -> ve, ve -> &
    if country == "土耳其":
        for v in list(variants):
            if "&" in v:
                alt = v.replace("&", "ve")
                if alt.lower() not in seen:
                    variants.append(alt)
                    seen.add(alt.lower())
            elif " ve " in v:
                alt = v.replace(" ve ", " & ")
                if alt.lower() not in seen:
                    variants.append(alt)
                    seen.add(alt.lower())
            # Ltd. Sti. -> Ltd Sti (去点)
            alt = re.sub(r'\.', '', v)
            if alt.lower() not in seen:
                variants.append(alt)
                seen.add(alt.lower())

    # 俄罗斯: 西里尔转拉丁（如果原名是西里尔字母）
    if country == "俄罗斯":
        has_cyrillic = any('Ѐ' <= c <= 'ӿ' for c in cleaned)
        if has_cyrillic:
            # 保留原始西里尔版本（已在 variants 中），同时添加英文名如果有
            en_parts = re.findall(r'[a-zA-Z()]+', cleaned)
            if en_parts:
                en_name = " ".join(en_parts).strip("() ")
                if en_name.lower() not in seen:
                    variants.append(en_name)
                    seen.add(en_name.lower())

    # 过滤太短或纯数字的变体
    filtered = []
    for v in variants:
        v_stripped = v.strip()
        if len(v_stripped) < 2:
            continue
        # 纯数字不要
        if re.match(r'^[\d\s\.]+$', v_stripped):
            continue
        filtered.append(v_stripped)

    return filtered if filtered else [cleaned or ""]


def _clean_keyword(text: str) -> str:
    """清理 keyword：去法律后缀、去引号、去多余标点。"""
    # 提取并保护 initial&initial 模式（如 U&A）
    initials_match = re.match(r'^([A-Z])\s*&\s*([A-Z])\b', text)
    initials_prefix = ""
    if initials_match:
        initials_prefix = f"{initials_match.group(1)}&{initials_match.group(2)} "
        text = text[initials_match.end():].strip()

    # 去法律后缀
    text = strip_legal_suffixes(text)

    # 去 guillemets 和其他引号
    text = re.sub(r'[«»""„‟""‟‹›]', '', text)
    # 去残留的单独字母（如 s p a 从 S.p.A. 拆分出来的）
    words = text.split()
    if len(words) > 1:
        words = [w for w in words if len(w) > 1]
    text = initials_prefix + ' '.join(words)
    # 清理多余空格和前后标点
    text = re.sub(r'\s+', ' ', text).strip().strip("()-./,;：，")
    return text.lower()


def generate_search_keyword(variants: list[str], country: str) -> str:
    """选择主搜索关键词 — 取去后缀后的主体名称，不选域名/网址类变体。"""
    for v in variants:
        if "www." in v or v.startswith("http"):
            continue
        cleaned = _clean_keyword(v)
        if cleaned and len(cleaned) > 2:
            return cleaned
    # 兜底：返回第一个非域名变体
    for v in variants:
        if "www." not in v and not v.startswith("http") and len(v) > 2:
            return v
    return variants[0] if variants else ""


def format_date(val) -> str:
    """格式化日期。"""
    s = safe_str(val)
    if not s:
        return ""
    try:
        dt = pd.to_datetime(s)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return s


OUTPUT_COLUMNS = [
    "internal_customer_id",
    "customer_name",
    "company_short_name",
    "country_region",
    "website_input",
    "email",
    "email_domain",
    "linkedin",
    "product_keywords",
    "customer_status",
    "customer_level",
    "first_deal_date",
    "last_purchase_date",
    "last_contact_date",
    "sales_progress",
    "latest_followup_summary",
    "owner",
    "search_keyword",
    "search_variants",
]


def generate():
    df = pd.read_excel(INPUT_FILE)
    print(f"读取 {len(df)} 条客户记录")

    rows = []
    variant_counts = []

    for _, r in df.iterrows():
        cid = safe_str(r["客户编码"])
        name = safe_str(r["公司名称"])
        short = safe_str(r["公司简称"])
        country = safe_str(r["国家地区"])
        website = safe_str(r["公司网站"])
        email_all = safe_str(r["邮箱"])
        email_dom = safe_str(r["email_domain"])
        linkedin = safe_str(r["LinkedIn"])
        products = safe_str(r["主营产品"])
        status = safe_str(r["客户状态"])
        level = safe_str(r["客户等级"])
        first_deal = format_date(r["首次成交时间"])
        last_purchase = format_date(r["最后一次购买时间"])
        last_contact = format_date(r["最后联系时间"])
        sales_progress = safe_str(r["销售进度"])
        followup = safe_str(r["最后跟进总结"])
        owner = safe_str(r["分管人"])

        # 生成搜索变体
        variants = generate_search_variants(name, short, country, email_dom, website)
        keyword = generate_search_keyword(variants, country)

        variant_counts.append(len(variants))

        row = {
            "internal_customer_id": cid,
            "customer_name": clean_noise(name),
            "company_short_name": short,
            "country_region": country,
            "website_input": website,
            "email": email_all,
            "email_domain": email_dom,
            "linkedin": linkedin,
            "product_keywords": products,
            "customer_status": status,
            "customer_level": level,
            "first_deal_date": first_deal,
            "last_purchase_date": last_purchase,
            "last_contact_date": last_contact,
            "sales_progress": sales_progress,
            "latest_followup_summary": followup,
            "owner": owner,
            "search_keyword": keyword,
            "search_variants": "; ".join(variants),
        }
        rows.append(row)

    out_df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name="Tendata_Input", index=False)

    vc = pd.Series(variant_counts)
    print(f"\n已导出: {OUTPUT_FILE}")
    print(f"客户总数: {len(out_df)}")
    print(f"搜索变体数: 平均 {vc.mean():.1f}, 中位数 {vc.median():.0f}, "
          f"最小 {vc.min()}, 最大 {vc.max()}")

    # 展示几个样例
    print(f"\n=== 搜索变体样例 ===")
    for i in [0, 10, 50, 100, 150, 200, 300, 400]:
        if i < len(out_df):
            r = out_df.iloc[i]
            print(f"  {r['internal_customer_id']} | {r['customer_name'][:35]} | {r['country_region']}")
            print(f"    keyword: {r['search_keyword']}")
            print(f"    variants: {r['search_variants'][:120]}...")
            print()


if __name__ == "__main__":
    generate()
