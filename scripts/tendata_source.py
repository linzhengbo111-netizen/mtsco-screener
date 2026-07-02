"""
tendata_source.py — Tendata 海关数据源适配器

薄封装层，复用现有 extract_tendata_fields.py 的完整流水线。
将 EnrichmentResult 转换为统一的 TendataSourceSignals。

用法:
    from scripts.tendata_source import TendataSource
    td = TendataSource()
    signals = td.search("TEXON CO LTD", "韩国")
"""

from __future__ import annotations

import re
import sys
import json
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


class TendataSource:
    """Tendata 海关数据适配器（复用现有抓取引擎）"""

    def __init__(self):
        self._scraper = None

    def _get_scraper(self):
        """延迟初始化 scraper"""
        if self._scraper is not None:
            return self._scraper

        try:
            from extract_tendata_fields import _get_scraper
            self._scraper = _get_scraper(headless=False)
            return self._scraper
        except Exception as e:
            raise RuntimeError(f"Tendata scraper 初始化失败: {e}")

    def search(self, company_name: str, country: str = "",
               product_keywords: str = "") -> dict:
        """
        在 Tendata 搜索公司海关数据

        Returns: dict — 用于构建 TendataSourceSignals
        """
        result = {
            "found": False,
            "match_status": "no_result",
            "match_confidence": 0,
            "matched_company_name": "",
            "import_active": False,
            "latest_import_date": "",
            "total_shipments_12m": 0,
            "related_hs_codes": [],
            "top_products": [],
            "top_suppliers": [],
            "has_chinese_supplier": False,
            "product_relevance_level": "unknown",
            "error": "",
        }

        try:
            from extract_tendata_fields import enrich_one_customer
            from models import CustomerInput

            customer = CustomerInput(
                customer_name=company_name,
                country_region=country,
                product_keywords=product_keywords,
            )

            row = enrich_one_customer(
                customer=customer,
                batch_id=f"lead-qual-{int(time.time())}",
                headless=False,
            )

            if row is None:
                result["error"] = "enrich_one_customer 返回 None"
                return result

            # 映射字段
            result["found"] = row.match_status not in ("no_result", "")
            result["match_status"] = row.match_status
            result["match_confidence"] = row.match_confidence
            result["matched_company_name"] = row.matched_company_name
            result["import_active"] = row.import_active_status == "active"
            result["latest_import_date"] = row.latest_import_date

            # 12月进口次数
            result["total_shipments_12m"] = row.total_shipment_count or 0

            # HS 编码
            if row.target_hs_amount_json:
                try:
                    amounts = json.loads(row.target_hs_amount_json)
                    result["related_hs_codes"] = [
                        a.get("hs_code", "") for a in amounts
                        if a.get("hs_code")
                    ]
                except Exception:
                    pass

            # 产品
            if row.top_products_json:
                try:
                    result["top_products"] = json.loads(row.top_products_json)
                except Exception:
                    pass

            # 供应商
            if row.top_suppliers_json:
                try:
                    result["top_suppliers"] = json.loads(row.top_suppliers_json)
                    # 检查是否有中国供应商
                    for s in result["top_suppliers"]:
                        country = s.get("country", "").lower()
                        name = s.get("supplier_name", "").lower()
                        if any(c in country for c in ["china", "中国", "cn"]) or \
                           any(c in name for c in ["china", "chinese", "shenzhen", "shanghai", "guangdong", "zhejiang"]):
                            result["has_chinese_supplier"] = True
                            break
                except Exception:
                    pass

            # 产品相关性
            result["product_relevance_level"] = getattr(row, "product_relevance_level", "unknown") or "unknown"

            # 检查行列中的中国供应商信号
            china_signal = getattr(row, "china_supplier_signal", "") or ""
            if china_signal and "yes" in str(china_signal).lower():
                result["has_chinese_supplier"] = True

        except ImportError as e:
            result["error"] = f"无法导入 enrichment 模块: {e}"
        except Exception as e:
            result["error"] = f"Tendata 搜索异常: {str(e)[:200]}"

        return result

    def search_hs_code(self, hs_code: str, country: str = "",
                        max_results: int = 10) -> list[dict]:
        """按 HS 编码搜索（用于产品匹配搜索）"""
        try:
            from extract_tendata_fields import hs_quick_search, _close_scraper

            cards = hs_quick_search(
                hs_code=hs_code,
                country_filter=country,
                max_companies=max_results,
                headless=False,
                batch_id=f"hs-lead-{int(time.time())}",
            )
            _close_scraper()
            return cards or []
        except Exception as e:
            print(f"    [Tendata] HS搜索失败 {hs_code}: {e}")
            return []


# ============================================================================
# 测试
# ============================================================================

if __name__ == "__main__":
    td = TendataSource()
    print("Tendata 搜索: TEXON CO LTD (韩国)")
    s = td.search("TEXON CO LTD", "韩国")
    print(f"  找到: {s['found']}")
    print(f"  匹配: {s['matched_company_name']}")
    print(f"  状态: {s['match_status']}")
    print(f"  进口活跃: {s['import_active']}")
    print(f"  12月次数: {s['total_shipments_12m']}")
    print(f"  HS编码: {s['related_hs_codes']}")
    print(f"  中国供应商: {s['has_chinese_supplier']}")
    print(f"  产品: {[p.get('product_name','')[:50] for p in s['top_products'][:3]]}")
