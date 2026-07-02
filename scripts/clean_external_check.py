"""
clean_external_check.py — 清洗 external_check_results_17.xlsx

基于已核验结果修正 LinkedIn 误匹配、重算置信度和优先级。
不联网，纯本地处理。
"""

from __future__ import annotations

import re
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime

# Force UTF-8 output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

INPUT_DIR = Path(__file__).parent.parent
INPUT_FILE = INPUT_DIR / "external_check_results_17.xlsx"
OUTPUT_FILE = INPUT_DIR / "external_check_results_17_cleaned.xlsx"

CLEAN_FIELDS = [
    "linkedin_clean_status",
    "linkedin_clean_reason",
    "external_confidence_cleaned",
    "priority_cleaned",
    "manual_review_reason_cleaned",
    "final_followup_recommendation",
]

# ── LinkedIn 已知错配列表（硬编码基于人工识别）──
# (internal_customer_id, linkedin_url, reason)
KNOWN_MISMATCHES = {
    "5-043": {
        "li_url": "https://www.linkedin.com/company/bystronicgroup/",
        "reason": "LinkedIn 公司页 Bystronic Group 与 Metal Tube Industrie 主体不一致",
    },
    "1-097": {
        "li_url": "https://www.linkedin.com/company/gic-international-catering/",
        "reason": "LinkedIn 公司页 GIC Catering 与 GIC International GmbH 主体不一致",
    },
    "6-029": {
        "li_url": "https://www.linkedin.com/company/radiotherapy-specialty-products-india-p-ltd/",
        "reason": "LinkedIn 公司页 Radiotherapy Specialty Products India 与 RAFIT srl 主体不一致",
    },
    "6-040": {
        "li_url": "https://www.linkedin.com/company/sugimat/",
        "reason": "LinkedIn 公司页 SUGIMAT 国家/地区不匹配（西班牙 vs 需进一步确认主体）",
    },
    "7-072": {
        "li_url": "https://www.linkedin.com/company/demep-consultants/",
        "reason": "LinkedIn 公司页 DEMEP Consultants 与 DeMEPA SRL 主体不一致",
    },
}

# ── 国家映射 ──
COUNTRY_MAP = {
    "波兰": ["poland"],
    "印度": ["india"],
    "美国": ["united states", "usa"],
    "加拿大": ["canada"],
    "澳大利亚": ["australia"],
    "泰国": ["thailand"],
    "斯里兰卡": ["sri lanka"],
    "比利时": ["belgium"],
    "土耳其": ["turkey", "türkiye"],
    "德国": ["germany", "deutschland"],
    "爱尔兰": ["ireland"],
    "意大利": ["italy", "italia"],
    "西班牙": ["spain", "españa"],
    "柬埔寨": ["cambodia"],
}


def extract_li_slug(url: str) -> str:
    """从 LinkedIn URL 提取 slug，如 /company/foo-bar/ → 'foo-bar'。"""
    if not url or url == "nan":
        return ""
    m = re.search(r"linkedin\.com/company/([^/?#]+)", url, re.IGNORECASE)
    return m.group(1) if m else ""


def normalize_name(name: str) -> str:
    """去除法律后缀、标点，转小写。"""
    if not name or name == "nan":
        return ""
    name = name.lower().strip()
    name = re.sub(r'[\(\[].*?[\)\)]', '', name)
    for suffix in [
        'inc', 'ltd', 'corp', 'gmbh', 'nv', 'pty', 'spa', 'srl', 'bv',
        'co.,ltd', 'co ltd', 'co ltd.', 'co., ltd', 'gesmbh', 'llc',
        'sa', 'de cv', 's de rl', 'pvt ltd', 'pvt\.? ltd',
        'sp z o o', 'sp. z o.o.', 'sp. z o.o',
        'ithalat ihracat sanayi ve ticaret limited sirketi',
        'sanayi ve ticaret ltd sti',
        'co ltd', 'ltd', 'inc', 'llc',
    ]:
        name = re.sub(rf'\b{re.escape(suffix)}\.?\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def slug_match(slug: str, customer_name: str, tendata_match: str, country: str) -> tuple[bool, str]:
    """判断 LinkedIn slug 是否与客户主体匹配。返回 (是否匹配, 原因)。"""
    if not slug:
        return False, "无 LinkedIn URL"

    cust_norm = normalize_name(customer_name)
    tendata_norm = normalize_name(tendata_match)
    slug_words = set(slug.split("-"))
    cust_words = set(cust_norm.split())
    tendata_words = set(tendata_norm.split())

    # 精确匹配 slug
    if slug == cust_norm.replace(" ", "-") or slug == tendata_norm.replace(" ", "-"):
        return True, "slug 与客户名称精确匹配"

    # 关键名称词重叠
    # 检查 slug 中的核心词是否出现在客户名称中
    meaningful_slug_words = {w for w in slug_words if len(w) > 2}
    meaningful_cust_words = {w for w in cust_words if len(w) > 2}
    meaningful_tendata_words = {w for w in tendata_words if len(w) > 2}

    overlap_cust = meaningful_slug_words & meaningful_cust_words
    overlap_tendata = meaningful_slug_words & meaningful_tendata_words

    # 如果 slug 核心词大部分匹配客户名
    if meaningful_slug_words and len(overlap_cust) / len(meaningful_slug_words) >= 0.6:
        return True, f"slug 与客户名称主体匹配 ({', '.join(overlap_cust)})"

    if meaningful_slug_words and len(overlap_tendata) / len(meaningful_slug_words) >= 0.6:
        return True, f"slug 与腾道匹配公司名主体匹配 ({', '.join(overlap_tendata)})"

    # 部分匹配（首词匹配）
    cust_first = cust_norm.split()[0] if cust_norm else ""
    tendata_first = tendata_norm.split()[0] if tendata_norm else ""
    if cust_first and len(cust_first) > 2 and cust_first in slug:
        return True, f"客户首词 '{cust_first}' 在 slug 中"
    if tendata_first and len(tendata_first) > 2 and tendata_first in slug:
        return True, f"腾道首词 '{tendata_first}' 在 slug 中"

    # 不匹配
    return False, f"slug '{slug}' 与客户名 '{customer_name[:40]}' 主体不一致"


def evaluate_linkedin(row: pd.Series) -> tuple[str, str]:
    """评估 LinkedIn 是否真正匹配目标客户。
    返回 (clean_status, reason)。
    clean_status: yes | uncertain | no
    """
    cid = str(row.get("internal_customer_id", ""))
    li_found = str(row.get("linkedin_company_found", "no"))
    li_url = str(row.get("linkedin_company_url", ""))
    li_country_orig = str(row.get("linkedin_country_match", ""))
    li_activity = str(row.get("linkedin_recent_activity", ""))
    customer_name = str(row.get("customer_name", ""))
    tendata_match = str(row.get("final_matched_company_name", ""))
    country = str(row.get("country_region", ""))

    if li_found != "yes" or not li_url or li_url == "nan":
        return "no", "未找到 LinkedIn 公司页"

    # 1. 检查已知错配
    if cid in KNOWN_MISMATCHES:
        km = KNOWN_MISMATCHES[cid]
        return "no", f"疑似错配: {km['reason']}"

    # 2. slug 主体匹配检查
    slug = extract_li_slug(li_url)
    matched, reason = slug_match(slug, customer_name, tendata_match, country)

    if not matched:
        return "no", f"疑似错配: {reason}"

    # 3. 国家匹配检查
    expected_countries = COUNTRY_MAP.get(country.lower(), [country.lower()])
    # 注意：原始脚本的 country_match 是基于页面文本判断的，可能不准确
    # 这里我们基于 slug 和国家做交叉验证
    if li_country_orig == "no":
        # LinkedIn 国家不匹配但不一定错配，可能是 slug 匹配了但文本没提到国家
        return "uncertain", f"{reason}; 但 LinkedIn 页面国家不匹配 ({country})"

    # 4. 全部通过
    parts = [reason]
    if li_country_orig == "yes":
        parts.append("国家匹配")
    if li_activity in ("yes",):
        parts.append("有最近动态")
    return "yes", "; ".join(parts)


def compute_cleaned_confidence(row: pd.Series, li_clean: str) -> int:
    """重算外部核验置信度。"""
    confidence = 0

    web_match = str(row.get("website_match_status", ""))
    web_acc = str(row.get("website_accessible", ""))
    web_bus = str(row.get("website_business_status", ""))
    web_prod = str(row.get("website_product_relevance", ""))
    web_url = str(row.get("website_evidence_url", ""))

    tendata = str(row.get("final_tendata_status", ""))
    tendata_name = str(row.get("final_matched_company_name", ""))

    # 官网权重
    if web_match == "confirmed" and web_bus == "active":
        confidence += 35
        if web_prod == "high":
            confidence += 15
        elif web_prod == "medium":
            confidence += 10
        elif web_prod == "low":
            confidence += 5
    elif web_match == "likely_match":
        confidence += 20
    elif web_match == "inaccessible":
        confidence += 0
    elif web_match == "no_website":
        confidence += 0

    # 腾道权重
    if tendata == "confirmed":
        confidence += 25
    elif tendata == "likely_match":
        confidence += 15

    # LinkedIn 权重（仅当 clean 后仍然有效）
    if li_clean == "yes":
        confidence += 15
        if str(row.get("linkedin_country_match", "")) == "yes":
            confidence += 10
        if str(row.get("linkedin_recent_activity", "")) == "yes":
            confidence += 5
    elif li_clean == "uncertain":
        confidence += 5

    return max(0, min(100, confidence))


def compute_cleaned_priority(row: pd.Series, li_clean: str, confidence: int) -> str:
    """重算跟进优先级。"""
    tendata = str(row.get("final_tendata_status", ""))
    web_match = str(row.get("website_match_status", ""))
    web_bus = str(row.get("website_business_status", ""))
    web_prod = str(row.get("website_product_relevance", ""))
    web_acc = str(row.get("website_accessible", ""))
    li_found = str(row.get("linkedin_company_found", ""))

    tendata_strong = tendata in ("confirmed", "likely_match")
    web_confirmed_active = web_match == "confirmed" and web_bus == "active"
    web_product_ok = web_prod in ("high", "medium")
    li_valid = li_clean == "yes"
    li_invalid = li_clean in ("no", "uncertain")

    # A: 腾道强 + 官网确认活跃 + 产品相关 + LinkedIn 有效(或缺失但官网腾道都强)
    if tendata_strong and web_confirmed_active and web_product_ok:
        if li_found == "no":
            # 没有 LinkedIn 但官网+腾道都强，也可以 A
            return "A"
        if li_valid:
            return "A"
        if li_invalid:
            # LinkedIn 缺失/不确定，但官网+腾道强 → B
            return "B"

    # A: 腾道强 + 官网不可访问 + LinkedIn 有效确认
    if tendata_strong and web_match == "inaccessible" and li_valid:
        return "A"

    # B: 腾道强 + 官网确认/likely 但 LinkedIn 缺失或不确定
    if tendata_strong and web_match in ("confirmed", "likely_match", "inaccessible") and li_invalid:
        return "B"

    # B: 腾道 needs_manual 但官网 confirmed 且产品 high
    if tendata == "needs_manual_tendata_check" and web_confirmed_active and web_prod == "high":
        if li_valid:
            return "B"
        if li_invalid:
            return "B"

    # B: 腾道 needs_manual + 无网站 + LinkedIn yes
    if tendata == "needs_manual_tendata_check" and web_match == "no_website" and li_valid:
        return "B"

    # B: 腾道 needs_manual + 官网 inaccessible + LinkedIn yes
    if tendata == "needs_manual_tendata_check" and web_match == "inaccessible" and li_valid:
        return "B"

    # C: 有部分证据但不充分
    if tendata == "needs_manual_tendata_check" and web_match == "inaccessible" and li_invalid:
        return "C"

    if tendata == "needs_manual_tendata_check" and web_match == "no_website" and li_invalid:
        return "C"

    # D: 无有效信号
    if web_acc == "no" and li_found == "no" and tendata not in ("confirmed", "likely_match"):
        return "D"

    # 默认 C
    return "C"


def compute_manual_review(row: pd.Series, li_clean: str, priority: str) -> tuple[str, str]:
    """计算人工复核标记和原因。"""
    flags = []
    reasons = []

    tendata = str(row.get("final_tendata_status", ""))
    web_match = str(row.get("website_match_status", ""))
    web_acc = str(row.get("website_accessible", ""))
    li_found = str(row.get("linkedin_company_found", ""))
    li_url = str(row.get("linkedin_company_url", ""))

    # LinkedIn 错配
    if li_found == "yes" and li_clean == "no":
        flags.append("yes")
        cid = str(row.get("internal_customer_id", ""))
        if cid in KNOWN_MISMATCHES:
            reasons.append(f"LinkedIn错配: {KNOWN_MISMATCHES[cid]['reason']}")
        else:
            reasons.append("LinkedIn疑似错配")

    # 官网不可访问且无 LinkedIn
    if web_acc == "no" and li_clean == "no" and web_match != "no_website":
        flags.append("yes")
        reasons.append("官网不可访问且无LinkedIn公司页")

    # 腾道 needs_manual 且无其他强证据
    if tendata == "needs_manual_tendata_check" and web_match != "confirmed" and li_clean != "yes":
        flags.append("yes")
        reasons.append("腾道需人工核验且外部证据不足")

    # 详情页失败但官网有效
    if tendata == "needs_manual_tendata_check" and web_match == "confirmed":
        reasons.append("详情页失败但官网有效，建议人工确认")

    if flags:
        final_flag = "yes"
        final_reason = "; ".join(reasons) if reasons else "综合判断需人工复核"
    else:
        final_flag = "no"
        final_reason = ""

    return final_flag, final_reason


def followup_recommendation(
    priority: str,
    manual_flag: str,
    web_match: str,
    tendata: str,
    li_clean: str,
) -> str:
    """生成跟进建议。"""
    if priority == "A" and manual_flag == "no":
        return "优先跟进：腾道+官网+LinkedIn多信号确认，建议立即导入"
    elif priority == "A" and manual_flag == "yes":
        return "建议跟进：多信号强但需人工确认细节"
    elif priority == "B" and tendata in ("confirmed", "likely_match"):
        if web_match == "confirmed":
            return "建议跟进：腾道确认+官网确认，但LinkedIn缺失/不确定"
        return "建议跟进：腾道有信号，但外部核验不充分"
    elif priority == "B" and tendata == "needs_manual_tendata_check":
        if web_match == "confirmed":
            return "建议跟进：官网确认且活跃，但腾道信号需人工核验"
        return "建议观察：腾道需人工核验，部分外部证据支持"
    elif priority == "C":
        return "观察：证据不足，建议暂缓跟进"
    elif priority == "D":
        return "暂不跟进：无有效信号"
    else:
        return "需人工判断"


def clean():
    df = pd.read_excel(INPUT_FILE)
    print(f"读取 {len(df)} 条记录")

    cleaned_rows = []

    for _, row in df.iterrows():
        # 1. LinkedIn 清洗
        li_clean_status, li_clean_reason = evaluate_linkedin(row)
        cid = str(row.get("internal_customer_id", ""))

        # 如果原始 li_country 是 no 但我们在错配列表中标记为 no，
        # 需要同时修正 linkedin_country_match
        if li_clean_status == "no":
            # 标记国家也不匹配
            pass  # 不修改原始字段，只在清洗字段中体现

        # 2. 重算置信度
        confidence_cleaned = compute_cleaned_confidence(row, li_clean_status)

        # 3. 重算优先级
        priority_cleaned = compute_cleaned_priority(row, li_clean_status, confidence_cleaned)

        # 4. 人工复核
        manual_flag, manual_reason = compute_manual_review(row, li_clean_status, priority_cleaned)

        # 5. 跟进建议
        web_match = str(row.get("website_match_status", ""))
        tendata = str(row.get("final_tendata_status", ""))
        followup = followup_recommendation(priority_cleaned, manual_flag, web_match, tendata, li_clean_status)

        # 构建清洗后的行
        new_row = row.to_dict()
        new_row["linkedin_clean_status"] = li_clean_status
        new_row["linkedin_clean_reason"] = li_clean_reason
        new_row["external_confidence_cleaned"] = confidence_cleaned
        new_row["priority_cleaned"] = priority_cleaned
        new_row["manual_review_reason_cleaned"] = manual_reason

        # 根据清洗结果修正原始 LinkedIn 字段
        if li_clean_status == "no":
            new_row["linkedin_company_found"] = "no"
            new_row["linkedin_company_url"] = ""
            new_row["linkedin_country_match"] = "no"
            new_row["linkedin_employee_range"] = ""
            new_row["linkedin_recent_activity"] = "unknown"

        # 如果 manual_flag 变了，更新
        new_row["manual_review_flag"] = manual_flag
        if manual_reason:
            # 合并到 manual_review_reason
            existing = str(new_row.get("manual_review_reason", ""))
            if existing and existing != "nan" and existing != manual_reason:
                new_row["manual_review_reason"] = f"{existing}; {manual_reason}"
            else:
                new_row["manual_review_reason"] = manual_reason

        new_row["final_followup_recommendation"] = followup

        # 更新 followup_priority_candidate
        new_row["followup_priority_candidate"] = priority_cleaned

        # 更新 external_check_confidence
        new_row["external_check_confidence"] = confidence_cleaned

        cleaned_rows.append(new_row)

    cleaned_df = pd.DataFrame(cleaned_rows)

    # ── 写输出 ──
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        cleaned_df.to_excel(writer, sheet_name="Cleaned_Results", index=False)

        # Summary sheet
        summary_data = []

        # 优先级统计
        for p in ["A", "B", "C", "D"]:
            count = len(cleaned_df[cleaned_df["priority_cleaned"] == p])
            summary_data.append({"指标": f"{p}级数量", "值": count})

        # manual_review
        mr_count = len(cleaned_df[cleaned_df["manual_review_flag"] == "yes"])
        summary_data.append({"指标": "manual_review数量", "值": mr_count})

        # LinkedIn 疑似错配
        mismatch_ids = [cid for cid in KNOWN_MISMATCHES]
        mismatch_names = []
        for cid in mismatch_ids:
            m = cleaned_df[cleaned_df["internal_customer_id"] == cid]
            if len(m) > 0:
                mismatch_names.append(f"{cid} | {m.iloc[0]['customer_name']}")

        summary_data.append({"指标": "LinkedIn疑似错配客户", "值": "; ".join(mismatch_names)})

        # 官网 confirmed 但 LinkedIn 错配
        web_conf_li_mismatch = []
        for _, r in cleaned_df.iterrows():
            if (str(r.get("website_match_status", "")) == "confirmed" and
                    str(r.get("linkedin_clean_status", "")) == "no" and
                    str(r.get("linkedin_company_found", "yes")) != "no" or
                    (str(r.get("internal_customer_id", "")) in KNOWN_MISMATCHES and
                     str(r.get("website_match_status", "")) == "confirmed")):
                web_conf_li_mismatch.append(f"{r['internal_customer_id']} | {r['customer_name']}")

        # 更精确：检查哪些客户官网 confirmed 但 LinkedIn 被我们标记为 no
        web_conf_li_no = []
        for _, r in cleaned_df.iterrows():
            if (str(r.get("website_match_status", "")) == "confirmed" and
                    str(r.get("linkedin_clean_status", "")) == "no"):
                web_conf_li_no.append(f"{r['internal_customer_id']} | {r['customer_name']}")

        summary_data.append({
            "指标": "官网confirmed但LinkedIn错配",
            "值": "; ".join(web_conf_li_no) if web_conf_li_no else "无"
        })

        # 详细客户列表
        summary_data.append({"指标": "", "值": ""})
        summary_data.append({"指标": "=== 各客户清洗详情 ===", "值": ""})
        for _, r in cleaned_df.iterrows():
            cid = r["internal_customer_id"]
            name = r["customer_name"]
            p_orig = r["followup_priority_candidate"]
            li_orig = r.get("linkedin_company_found", "")
            li_clean = r["linkedin_clean_status"]
            conf = r["external_confidence_cleaned"]
            mr = r["manual_review_flag"]
            reason = r["linkedin_clean_reason"]
            summary_data.append({
                "指标": f"{cid} | {name[:35]}",
                "值": f"priority={p_orig} | li_orig={li_orig} → li_clean={li_clean} | conf={conf} | mr={mr} | {reason}"
            })

        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    print(f"\n已导出: {OUTPUT_FILE}")

    # 终端汇总
    print(f"\n=== 清洗后优先级分布 ===")
    for p in ["A", "B", "C", "D"]:
        count = len(cleaned_df[cleaned_df["priority_cleaned"] == p])
        print(f"  {p}: {count} 条")

    print(f"\n=== manual_review: {mr_count} 条 ===")
    for _, r in cleaned_df[cleaned_df["manual_review_flag"] == "yes"].iterrows():
        print(f"  {r['internal_customer_id']} | {r['customer_name'][:35]} | {r['manual_review_reason_cleaned']}")

    print(f"\n=== LinkedIn 疑似错配 ===")
    for _, r in cleaned_df.iterrows():
        if r["linkedin_clean_status"] == "no" and str(r.get("linkedin_company_found", "")) != "no" or \
           r["internal_customer_id"] in KNOWN_MISMATCHES:
            print(f"  {r['internal_customer_id']} | {r['customer_name'][:35]} | {r['linkedin_clean_reason']}")

    print(f"\n=== 各客户清洗详情 ===")
    for _, r in cleaned_df.iterrows():
        cid = r["internal_customer_id"]
        name = r["customer_name"][:35]
        print(f"  {cid} | {name}")
        print(f"    priority={r['priority_cleaned']} | conf={r['external_confidence_cleaned']}")
        print(f"    li_orig={r.get('linkedin_company_found', '')} → li_clean={r['linkedin_clean_status']}")
        print(f"    mr={r['manual_review_flag']} | {r['linkedin_clean_reason']}")
        print(f"    rec={r['final_followup_recommendation']}")


if __name__ == "__main__":
    clean()
