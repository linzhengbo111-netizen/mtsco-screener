"""
normalize_input.py — 腾道输入 Excel 表头归一化与校验

功能：
- 读取 .xlsx 第一行表头
- 做轻量归一化（去空格、全角转半角、小写、下划线等价）
- 匹配最小中英文表头映射
- 校验 customer_name 必须存在
- 返回归一化后的 DataFrame
"""

import sys
import re
from pathlib import Path

import pandas as pd


# 中文表头 → 归一化字段名
CHINESE_ALIAS_MAP = {
    # 公司名称
    "客户名": "customer_name",
    "客户名称": "customer_name",
    "公司名": "customer_name",
    "公司名称": "customer_name",
    "客户标准名": "customer_name",
    # 国家地区
    "国家": "country_region",
    "国家地区": "country_region",
    "国家/地区": "country_region",
    # 网站
    "官网": "website",
    "网址": "website",
    "网站": "website_input",
    "公司网站": "website_input",
    # 邮箱
    "邮箱": "email",
    # 邮箱域名
    "邮箱域名": "email_domain",
    "域名": "email_domain",
    # 产品关键词
    "产品关键词": "product_keywords",
    "产品": "product_keywords",
    "品类": "product_keywords",
    "关注产品": "product_keywords",
    "关注产品/品类": "product_keywords",
    "主营产品": "product_keywords",
    # 内部客户编码
    "客户id": "internal_customer_id",
    "原始客户id": "internal_customer_id",
    "内部客户id": "internal_customer_id",
    "原始客户 ID": "internal_customer_id",
    "客户编码": "internal_customer_id",
    # 公司简称
    "公司简称": "company_short_name",
    # 联系人
    "联系人": "contact_name",
    # 电话
    "电话": "phone",
    "公司电话": "phone",
    # LinkedIn
    "linkedin": "linkedin",
    "领英": "linkedin",
}

# 标准英文字段集合
STANDARD_FIELDS = {
    "customer_name",
    "country_region",
    "website",
    "website_input",
    "email_domain",
    "email",
    "product_keywords",
    "internal_customer_id",
    "company_short_name",
    "contact_name",
    "phone",
    "linkedin",
}


def normalize_header(raw: str) -> str:
    """单表头归一化：去空格、全角转半角、小写、连续空格合并。"""
    # 全角转半角
    result = []
    for ch in raw:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(ch)
    s = "".join(result)
    # 去首尾空格
    s = s.strip()
    # 英文转小写
    s = s.lower()
    # 合并连续空格 / 下划线等价
    s = re.sub(r"[\s_]+", "_", s)
    return s


def map_header(normalized: str) -> str | None:
    """将归一化后的表头映射为标准字段名。"""
    if normalized in STANDARD_FIELDS:
        return normalized
    if normalized in CHINESE_ALIAS_MAP:
        return CHINESE_ALIAS_MAP[normalized]
    # 尝试模糊匹配（如 "国家 地区" 归一化为 "国家_地区"）
    for alias, std in CHINESE_ALIAS_MAP.items():
        if normalize_header(alias) == normalized:
            return std
    return None


def load_and_normalize(path: str) -> pd.DataFrame:
    """读取 Excel 并返回归一化列名的 DataFrame。"""
    df = pd.read_excel(path)
    old_cols = list(df.columns)
    new_cols = []
    for col in old_cols:
        mapped = map_header(normalize_header(str(col)))
        if mapped:
            new_cols.append(mapped)
        else:
            new_cols.append(str(col).strip())
    df.columns = new_cols
    return df


def validate(df: pd.DataFrame) -> list[str]:
    """校验输入 DataFrame，返回错误列表。"""
    errors = []
    if "customer_name" not in df.columns:
        errors.append("未识别到必填字段 customer_name，无法开始执行。")
    if "country_region" not in df.columns:
        errors.append("未识别到 country_region，执行结果将默认降置信度。")
    return errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python normalize_input.py <input.xlsx>")
        sys.exit(1)
    input_path = sys.argv[1]
    df = load_and_normalize(input_path)
    errors = validate(df)
    for e in errors:
        print(f"WARNING: {e}")
    print(f"列名: {list(df.columns)}")
    print(f"行数: {len(df)}")
    print("校验通过" if not any("customer_name" in e for e in errors) else "校验失败")
