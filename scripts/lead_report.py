"""
lead_report.py — 多源筛选结果 Excel 导出

用法:
    from scripts.lead_report import export_to_excel
    path = export_to_excel(results, output_dir="output")
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from models import LeadQualificationResult


def export_to_excel(results: list[LeadQualificationResult],
                    output_path: str = "") -> Path:
    """
    将筛选结果导出为 Excel 文件

    包含多个 Sheet:
    - 全部排名: 所有公司按分数排序
    - A级/优先跟进: 仅A级
    - B级/列入跟进: 仅B级
    - 打分明细: 含七维度拆解

    Returns:
        输出文件路径
    """
    if not results:
        raise ValueError("结果列表为空")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not output_path:
        output_dir = Path(__file__).parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"lead_qualification_{timestamp}.xlsx")
    else:
        output_path = str(Path(output_path).with_suffix(".xlsx"))

    # ── 构建主数据 ──
    rows = []
    for r in results:
        g = r.google
        li = r.linkedin
        td = r.tendata

        rows.append({
            # 基本信息
            "排名": r.rank,
            "公司名称": r.customer_name,
            "国家": r.country_region,
            "总分": r.final_score,
            "级别": r.tier,
            "建议动作": r.tier_label,

            # 评分拆解
            "源一致性(/15)": r.source_agreement_score,
            "产品匹配(/25)": r.product_fit_score,
            "采购意愿(/25)": r.purchase_intent_score,
            "可信度(/15)": r.legitimacy_score,
            "联系方式(/10)": r.contact_score,
            "行业规模(/5)": r.industry_scale_score,
            "风险扣分": r.risk_penalty,

            # 分析文字
            "产品匹配分析": r.product_fit_analysis,
            "采购信号分析": r.purchase_signals_analysis,
            "风险提醒": r.risk_notes,

            # Google
            "Google-找到": "是" if g.company_found else "否",
            "Google-官网": g.official_website,
            "Google-业务类型": g.business_type,
            "Google-产品关键词": ", ".join(g.product_keywords_found[:5]),
            "Google-邮箱": g.contact_email,
            "Google-电话": g.contact_phone,
            "Google-置信度": g.confidence,

            # LinkedIn
            "LinkedIn-找到": "是" if li.company_page_found else "否",
            "LinkedIn-链接": li.company_url,
            "LinkedIn-公司名": li.company_name_on_li,
            "LinkedIn-员工规模": li.employee_count_range,
            "LinkedIn-行业标签": ", ".join(li.industry_tags),
            "LinkedIn-联系人": ", ".join(c.get("name", "") for c in li.key_contacts),
            "LinkedIn-置信度": li.confidence,

            # 海关
            "海关-找到": "是" if td.found else "否",
            "海关-匹配状态": td.match_status,
            "海关-匹配公司名": td.matched_company_name,
            "海关-进口活跃": "是" if td.import_active else "否",
            "海关-最新进口": td.latest_import_date,
            "海关-12月次数": td.total_shipments_12m,
            "海关-HS编码": ", ".join(td.related_hs_codes),
            "海关-中国供应商": "是" if td.has_chinese_supplier else "否",
            "海关-主要产品": ", ".join(p.get("product_name", "")[:60] for p in td.top_products[:5]),
            "海关-产品相关性": td.product_relevance_level,

            # 元数据
            "耗时(秒)": round(r.elapsed_seconds, 1),
            "错误": r.error,
        })

    df = pd.DataFrame(rows)

    # ── 写入 Excel ──
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Sheet 1: 全部排名
        df.to_excel(writer, sheet_name="全部排名", index=False)

        # Sheet 2-4: 分级
        for tier, label in [("A", "优先跟进"), ("B", "列入跟进"), ("C", "人工复核")]:
            tier_df = df[df["级别"] == tier]
            if not tier_df.empty:
                tier_df.to_excel(writer, sheet_name=f"{tier}级-{label}", index=False)

        # Sheet 5: 精简版（方便快速浏览）
        summary_cols = [
            "排名", "公司名称", "国家", "总分", "级别", "建议动作",
            "产品匹配(/25)", "采购意愿(/25)", "可信度(/15)",
            "Google-官网", "LinkedIn-员工规模", "海关-进口活跃",
            "海关-12月次数", "海关-中国供应商", "风险提醒",
        ]
        summary_cols = [c for c in summary_cols if c in df.columns]
        df[summary_cols].to_excel(writer, sheet_name="精简版", index=False)

        # Sheet 6: 打分明细
        score_cols = [c for c in df.columns if "/" in c or "分" in c or c in [
            "排名", "公司名称", "国家", "级别", "总分",
            "产品匹配分析", "采购信号分析", "风险提醒",
        ]]
        df[[c for c in score_cols if c in df.columns]].to_excel(
            writer, sheet_name="打分明细", index=False,
        )

        # 调整列宽
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

    return Path(output_path)


def export_to_json(results: list[LeadQualificationResult],
                   output_path: str = "") -> Path:
    """导出为 JSON 文件"""
    import json

    if not output_path:
        output_dir = Path(__file__).parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(output_dir / f"lead_qualification_{timestamp}.json")

    data = [r.to_dict() for r in results]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return Path(output_path)
