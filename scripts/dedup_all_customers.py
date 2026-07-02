"""
dedup_all_customers.py — 全量去重客户表

从 all/all_customers_unique.xlsx 生成 all/all_customers_unique_deduped.xlsx
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
INPUT_FILE = INPUT_DIR / "all" / "all_customers_unique.xlsx"
OUTPUT_FILE = INPUT_DIR / "all" / "all_customers_unique_deduped.xlsx"

# ── 保留的原始字段 ──
KEEP_FIELDS = [
    "客户编码",
    "公司名称",
    "公司简称",
    "客户状态",
    "客户等级",
    "国家地区",
    "公司地址",
    "公司电话",
    "公司网站",
    "联系人",
    "职务职级",
    "邮箱",
    "电话",
    "LinkedIn",
    "主营产品",
    "首次成交时间",
    "最后一次购买时间",
    "最后联系时间",
    "销售进度",
    "最后跟进总结",
    "最新跟进情况",
    "客情关系注意事项",
    "原小满客户标签",
    "分管人",
]

# ── 新增字段 ──
NEW_FIELDS = [
    "email_domain",
    "has_website",
    "has_linkedin",
    "purchase_recency_bucket",
    "contact_recency_bucket",
]


def safe_str(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def extract_email_domain(email: str) -> str:
    """从邮箱提取域名。"""
    email = safe_str(email)
    if not email:
        return ""
    # 可能有多邮箱分号分隔，取第一个
    first = email.split(";")[0].strip()
    m = re.search(r"@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", first)
    return m.group(1).lower() if m else ""


def parse_date(val) -> pd.Timestamp | None:
    """尝试解析日期。"""
    s = safe_str(val)
    if not s:
        return None
    try:
        return pd.to_datetime(s, format="mixed", dayfirst=False)
    except Exception:
        return None


def recency_bucket(dt: pd.Timestamp | None, today: pd.Timestamp) -> str:
    """计算时间区间。"""
    if dt is None:
        return "unknown"
    delta = (today - dt).days
    if delta < 0:
        return "unknown"
    elif delta <= 30:
        return "0-30天"
    elif delta <= 90:
        return "31-90天"
    elif delta <= 180:
        return "91-180天"
    elif delta <= 365:
        return "181-365天"
    elif delta <= 730:
        return "1-2年"
    else:
        return ">2年"


def merge_semicolon(vals: list) -> str:
    """合并多个值，用分号分隔，去重去空。"""
    seen = set()
    result = []
    for v in vals:
        s = safe_str(v)
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return "; ".join(result)


def dedup():
    today = pd.Timestamp.now()

    # ── 1. 读取原始数据 ──
    df = pd.read_excel(INPUT_FILE, skiprows=3)
    raw_count = len(df)
    print(f"原始行数: {raw_count}")

    # ── 2. 合并两个 LinkedIn 列 ──
    if "LinkedIn.1" in df.columns:
        df["LinkedIn_merged"] = df.apply(
            lambda r: merge_semicolon([r.get("LinkedIn", ""), r.get("LinkedIn.1", "")]),
            axis=1,
        )
    else:
        df["LinkedIn_merged"] = df.get("LinkedIn", "")

    # ── 3. 按 客户编码 分组去重 ──
    groups = df.groupby("客户编码", dropna=False)

    deduped_rows = []

    for cid, group in groups:
        row = {}

        # 公司主信息（取第一个非空值）
        for col in KEEP_FIELDS:
            if col == "LinkedIn":
                # LinkedIn 用合并后的列
                vals = group["LinkedIn_merged"].tolist()
                row[col] = merge_semicolon(vals)
            else:
                vals = group[col].tolist() if col in group.columns else []
                # 取第一个非空值
                first = ""
                for v in vals:
                    s = safe_str(v)
                    if s:
                        first = s
                        break
                row[col] = first

        # 合并联系人、邮箱、电话、LinkedIn
        row["联系人"] = merge_semicolon(group["联系人"].tolist())
        row["职务职级"] = merge_semicolon(group["职务职级"].tolist())
        row["邮箱"] = merge_semicolon(group["邮箱"].tolist())
        row["电话"] = merge_semicolon(group["电话"].tolist())
        # LinkedIn 已在上面合并

        # 多个联系人对应的最后购买时间/最后联系时间取最新
        purchase_dates = group["最后一次购买时间"].apply(parse_date).dropna()
        contact_dates = group["最后联系时间"].apply(parse_date).dropna()

        latest_purchase = purchase_dates.max() if len(purchase_dates) > 0 else None
        latest_contact = contact_dates.max() if len(contact_dates) > 0 else None

        row["最后一次购买时间"] = latest_purchase.strftime("%Y-%m-%d") if latest_purchase else ""
        row["最后联系时间"] = latest_contact.strftime("%Y-%m-%d") if latest_contact else ""

        # ── 新增字段 ──
        row["email_domain"] = extract_email_domain(row["邮箱"])
        row["has_website"] = "yes" if safe_str(row["公司网站"]) else "no"
        row["has_linkedin"] = "yes" if safe_str(row["LinkedIn"]) else "no"
        row["purchase_recency_bucket"] = recency_bucket(latest_purchase, today)
        row["contact_recency_bucket"] = recency_bucket(latest_contact, today)

        deduped_rows.append(row)

    deduped_df = pd.DataFrame(deduped_rows, columns=KEEP_FIELDS + NEW_FIELDS)
    unique_count = len(deduped_df)
    print(f"去重后客户数: {unique_count}")

    # ── 4. 统计 ──
    has_website_count = (deduped_df["has_website"] == "yes").sum()
    has_email_domain_count = deduped_df["email_domain"].apply(lambda x: x != "").sum()
    has_linkedin_count = (deduped_df["has_linkedin"] == "yes").sum()

    print(f"有官网客户数: {has_website_count}")
    print(f"有邮箱域名客户数: {has_email_domain_count}")
    print(f"有 LinkedIn 客户数: {has_linkedin_count}")

    # ── 5. 导出 ──
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        deduped_df.to_excel(writer, sheet_name="全量去重客户表", index=False)

        # Summary
        summary = [
            {"指标": "原始行数", "值": raw_count},
            {"指标": "去重后客户数", "值": unique_count},
            {"指标": "有官网客户数", "值": int(has_website_count)},
            {"指标": "有邮箱域名客户数", "值": int(has_email_domain_count)},
            {"指标": "有 LinkedIn 客户数", "值": int(has_linkedin_count)},
            {"指标": "", "值": ""},
            {"指标": "purchase_recency_bucket 分布", "值": ""},
        ]
        for bucket, count in deduped_df["purchase_recency_bucket"].value_counts().items():
            summary.append({"指标": f"  {bucket}", "值": int(count)})
        summary.append({"指标": "", "值": ""})
        summary.append({"指标": "contact_recency_bucket 分布", "值": ""})
        for bucket, count in deduped_df["contact_recency_bucket"].value_counts().items():
            summary.append({"指标": f"  {bucket}", "值": int(count)})

        summary_df = pd.DataFrame(summary)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    print(f"\n已导出: {OUTPUT_FILE}")


if __name__ == "__main__":
    dedup()
