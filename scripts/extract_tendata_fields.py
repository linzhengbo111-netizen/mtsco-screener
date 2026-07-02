"""
extract_tendata_fields.py — 腾道页面字段提取（Playwright 实现）

功能:
- 登录态检查（通过已登录页面的 DOM 元素判断，不依赖 URL）
- 在已登录腾道的浏览器中搜索公司名称
- 结果页选择 top1
- 详情页字段提取
- 进口分析页字段提取
- 失败处理和超时处理

重要：本 skill 不做自动登录。用户必须先手动登录腾道，skill 只复用已登录的浏览器会话。

用法:
    # 作为模块被 run_batch.py 调用
    # 或独立测试:
    python scripts/extract_tendata_fields.py --test
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# Windows console encoding fix: replace undecodable chars instead of crashing
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    from playwright.sync_api import (
        sync_playwright,
        Page,
        TimeoutError as PlaywrightTimeout,
    )
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ============================================================================
# 腾道页面 URL 配置
# ============================================================================

TENDATA_CONFIG = {
    # 腾道主站（营销首页）
    "base_url": "https://www.tendata.cn",
    # 业务后台入口（商情发现搜索页）
    # 已确认正确的业务搜索页 URL
    "app_url": "https://bizr.tendata.cn/search#/index",
    # 登录页（仅用于判断是否未登录，不主动访问）
    "login_url": "https://login.tendata.cn/login#/",
    # 每步操作超时（毫秒）
    "timeout": 15000,
    # 页面加载等待超时（毫秒）
    "load_timeout": 30000,
    # === 搜索页选择器（商情发现 → 公司名称搜索） ===
    # 搜索区域容器：先找到顶部搜索栏区域
    "search_area": (
        ".search-bar", ".search-container", ".search-header",
        ".search-box", ".main-search", ".page-header-search",
        "[class*='searchBar']", "[class*='searchContainer']",
        "[class*='searchBox']",
    ),
    # 搜索模式下拉/Tab 选择器（用于切换到"公司名称"模式）
    "company_name_mode": "公司名称",
    "search_mode_selector": (
        "select, [role='combobox'], .search-type, .mode-select, "
        ".search-tab, .tab-item, [class*='searchType'], [class*='search-mode'], "
        "span:has-text('公司名称'), div:has-text('公司名称')"
    ),
    # 搜索输入框 — 使用 JS 评估找到的真实 <input> 元素
    "search_input_placeholders": [
        "请输入公司名称",
        "公司名称",
        "搜索公司",
        "请输入公司",
        "请输入企业名称",
    ],
    "search_input_fallback_selectors": [
        "input[placeholder*='公司']",
        "input[placeholder*='company']",
        "input[placeholder*='企业名称']",
        "input[placeholder*='输入']",
        "input[placeholder*='搜索']",
        "input[name='company']",
        "input[name='keyword']",
        "#searchInput",
        ".search-input input",
        "input.ant-input",
        "input.el-input__inner",
        ".ant-input-affix-wrapper input",
    ],
    # 搜索按钮
    "search_button_text": "搜索",
    "search_button_fallback_selectors": [
        "button[type='submit']",
        ".search-btn",
        "button.ant-btn-primary",
        "button.el-button--primary",
        "button:has-text('查询')",
    ],
    # === 搜索结果页选择器 ===
    "search_result_item": (
        ".search-result-item, .company-item, .result-row, "
        "table tbody tr, .trade-data-item, .company-list-item, "
        "[class*='resultItem'], [class*='companyItem'], "
        "ant-table-tbody tr, .el-table__row"
    ),
    "result_company_name": (
        ".company-name, .company-title, h3, h4, a.company, "
        "td:first-child, [class*='companyName'], .name"
    ),
    "detail_link": (
        "a.company-name, a.title, a:has(.company-name), "
        "td:first-child a, [class*='companyName'] a, a[class*='name']"
    ),
    # === 企业详情页选择器 ===
    "detail_company_name": (
        ".company-name, h1, .title, .company-title, "
        "[class*='companyName'], h2"
    ),
    "detail_website": (
        ".website, .company-url, a[href*='http'], "
        "a:has-text('官网'), a:has-text('网站'), "
        "td:has-text('网址') + td a, td:has-text('官网') + td a, "
        "dd:has-text('http'), dd a"
    ),
    "detail_status": (
        ".status, .company-status, .operating-status, "
        "[class*='status'], td:has-text('状态') + td"
    ),
    "country_field": (
        ".country, .region, [data-field='country'], "
        "td:has-text('国家') + td, td:has-text('地区') + td, "
        "dd:has-text('国家')"
    ),
    # === 进口分析页选择器 ===
    "import_analysis_tab": (
        "a:has-text('进口分析'), a:has-text('进口'), "
        "[data-tab='import'], .tab-import, "
        "li:has-text('进口') a, .nav-item:has-text('进口'), "
        "[class*='importTab'], [class*='import-tab']"
    ),
    "import_date": (
        ".import-date, .latest-date, .trade-date, td.date, "
        "[class*='importDate'], [class*='tradeDate']"
    ),
    "import_count": (
        ".trade-count, .record-count, .total-count, "
        "[class*='tradeCount'], [class*='recordCount']"
    ),
    "import_amount": (
        ".amount, .trade-amount, .total-amount, "
        "[class*='amount'], [class*='tradeValue']"
    ),
    "import_product": (
        ".product, .hs-code, .commodity, "
        "[class*='product'], [class*='hsCode']"
    ),
}

# ============================================================================
# 登录态检测元素
# ============================================================================

# 这些元素/文本只会在已登录的腾道后台页面中出现
# 用于判断用户是否已完成登录
LOGIN_INDICATORS = [
    # 左侧菜单/导航中的功能入口
    {"type": "text", "value": "商情发现"},
    {"type": "text", "value": "商情洞察"},
    {"type": "text", "value": "数据通"},
    {"type": "text", "value": "云邮通"},
    # 搜索区域的 Tab（公司名称/产品/产品描述/HS编码）
    {"type": "text", "value": "公司名称"},
    {"type": "text", "value": "HS编码"},
    {"type": "text", "value": "产品描述"},
    # 用户信息区域
    {"type": "text", "value": "退出登录"},
    {"type": "text", "value": "个人中心"},
    # 数据相关文字（只在后台出现）
    {"type": "text", "value": "贸易数据"},
    {"type": "text", "value": "进口分析"},
]

# ============================================================================
# 搜索页状态识别
# ============================================================================

def classify_search_page(page: Page) -> str:
    """在已确认 biz_search 页面内进一步区分子状态。

    Returns:
        'search_results' : 结果页态 — 已出现结果列表/结果计数
        'search_landing' : 搜索首页态 — 有输入框，尚未出现结果
        'search_unknown' : 无法确定
    """
    # 1) 检测"共搜索到"类文本
    try:
        results_text = page.query_selector("text=/共搜索到.*/")
        if results_text and results_text.is_visible():
            return "search_results"
    except Exception:
        pass

    # 2) 检测结果数文本（如"找到 X 条结果"、"搜索结果：X"）
    try:
        for pattern in ["共找到", "条结果", "搜索结果"]:
            el = page.query_selector(f"text={pattern}")
            if el and el.is_visible():
                return "search_results"
    except Exception:
        pass

    # 3) 检测结果卡片/公司列表容器
    try:
        for sel in [
            ".search-result-item",
            ".company-item",
            ".result-row",
            ".company-list > div",
            ".trade-data-item",
            "[class*='resultItem']",
            "[class*='companyItem']",
            "[class*='result-card']",
            "[class*='company-card']",
        ]:
            els = page.query_selector_all(sel)
            if els and len(els) > 0:
                # 确认不是空壳元素 — 检查有实际文本
                for el in els[:3]:
                    text = el.inner_text().strip()
                    if text and len(text) > 5:
                        return "search_results"
    except Exception:
        pass

    # 4) 检测结果区域内的公司名链接
    try:
        company_links = page.query_selector_all("a[href*='company'], a[href*='detail'], a[href*='enterprise']")
        if company_links and len(company_links) > 0:
            return "search_results"
    except Exception:
        pass

    return "search_landing"


# ============================================================================
# 页面分类逻辑
# ============================================================================

def classify_page(page: Page) -> str:
    """根据 URL/title/DOM 特征判断当前页面类型。

    Returns:
        'biz_search' : 商情发现搜索页（bizr.tendata.cn/search#/index）
        'biz_home'   : 业务首页/工作台（account.tendata.cn 等）
        'learning'   : 学习中心（knowledge.tendata.cn）
        'login'      : 登录页
        'unknown'    : 其他页面
    """
    url = page.url.lower()

    # 1. 学习中心页
    if "knowledge.tendata.cn" in url:
        return "learning"

    # 2. 商情发现搜索页（最高优先级）
    if "bizr.tendata.cn/search" in url:
        return "biz_search"

    # 3. 业务首页/工作台
    if any(kw in url for kw in ["account.tendata.cn", "bizr.tendata.cn"]):
        return "biz_home"

    # 4. 登录页
    if any(kw in url for kw in ["login.tendata.cn", "/login", "/signin"]):
        return "login"

    # 5. 基于 title 辅助判断
    title = page.title().lower()
    if "学习中心" in title or "knowledge" in title:
        return "learning"

    # 6. 基于 DOM 特征：包含商情发现搜索特征的视为搜索页
    search_indicators = ["公司名称", "产品描述", "HS编码"]
    found = 0
    for kw in search_indicators:
        try:
            el = page.query_selector(f"text={kw}")
            if el and el.is_visible():
                found += 1
                if found >= 2:
                    return "biz_search"
        except Exception:
            continue

    return "unknown"


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass
class SearchResult:
    """搜索结果页 top1 候选。"""
    company_name: str = ""
    rank: int = 0
    recent_trade_date: str = ""
    contact_count: int = 0
    company_brief: str = ""
    country: str = ""
    page_url: str = ""
    bounding_x: float = 0
    bounding_y: float = 0

    # HS 搜索结果额外字段
    hs_trade_count: int = 0       # 货运匹配次数
    hs_supplier_count: int = 0    # 供应商数量
    hs_product_desc: str = ""     # 产品描述

    # 卡片 DOM 定位信息（用于精确点击，避免文本回查）
    card_selector: str = ""       # 唯一 CSS 选择器，如 div:nth-of-type(N)
    card_index: int = 0           # 在同级兄弟中的索引（1-based）


@dataclass
class CompanyDetail:
    """企业详情页信息。"""
    standard_name: str = ""
    website: str = ""
    company_status: str = "unknown"
    country: str = ""
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    contact_summary: str = ""
    basic_info: str = ""
    # 新增结构化字段
    location: str = ""
    whatsapp: str = ""
    linkedin: str = ""


@dataclass
class ImportAnalysis:
    """进口分析页信息。"""
    latest_import_date: str = ""
    trade_count: int = 0
    amount: str = ""
    weight: str = ""
    product_hs: str = ""
    trade_direction: str = ""
    total_records: int = 0
    analysis_entry_status: str = "unknown"
    analysis_data_status: str = "unknown"
    # ── 统计卡片（overviewReport）──
    stats_card_total_value_usd: str = ""      # 美元总价
    stats_card_total_weight_kg: str = ""      # 千克重量
    stats_card_total_quantity: str = ""       # 贸易数量
    stats_card_supplier_count: str = ""       # 供应商数
    # ── 贸易明细表提取 ──
    trade_dates: list = field(default_factory=list)            # 所有贸易日期
    trade_products: list = field(default_factory=list)         # 产品列表
    trade_product_descriptions: list = field(default_factory=list)  # 产品描述
    trade_hs_codes: list = field(default_factory=list)         # HS 编码列表
    trade_suppliers: list = field(default_factory=list)        # 出口商/供应商列表
    trade_countries: list = field(default_factory=list)        # 原产国列表
    # ── HS 排名表 ──
    hs_ranking: list = field(default_factory=list)             # [{hs_code, description, trade_count, weight, quantity, usd_amount}]
    # ── 供应商排名（partnerReport）──
    partner_suppliers: list = field(default_factory=list)      # [{supplier_name, trade_count, pct, weight, weight_pct, quantity, qty_pct}]
    # ── 原有 JSON 字段 ──
    target_hs_amount_json: str = ""
    top_suppliers_json: str = ""
    top_3_import_countries_json: str = ""


@dataclass
class EnrichmentRow:
    """单条客户丰富化结果。"""
    # ── 输入列 ──
    customer_name: str = ""
    country_region: str = ""
    website_input: str = ""
    email_domain: str = ""
    product_keywords: str = ""
    internal_customer_id: str = ""
    search_keyword: str = ""
    search_variants: str = ""
    used_search_variant: str = ""

    # ── 基础匹配字段 ──
    matched_company_name: str = ""
    matched_country: str = ""
    match_status: str = "no_result"
    match_confidence: int = 0
    candidate_score: int = 0
    name_match_level: str = ""
    country_match: str = ""
    domain_match: str = ""
    product_match_level: str = ""
    conflict_reason: str = ""
    website_result: str = ""
    company_status: str = "unknown"
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    location: str = ""
    whatsapp: str = ""
    linkedin: str = ""

    # ── 采购活跃度字段 ──
    import_active_status: str = "unknown"
    latest_import_date: str = ""
    raw_candidate_latest_import_date: str = ""
    last_12m_import_count: str = ""
    last_24m_import_count: str = ""
    last_36m_import_count: str = ""
    import_frequency_level: str = ""
    import_activity_summary: str = ""

    # ── 产品相关字段 ──
    top_import_products: str = ""
    matched_product_keywords: str = ""
    related_hs_codes: str = ""
    product_relevance_level: str = ""
    product_relevance_score: str = ""
    top_products_json: str = ""
    target_hs_amount_json: str = ""

    # ── 供应链字段 ──
    supplier_count: str = ""
    top_suppliers: str = ""
    main_supplier_countries: str = ""
    china_supplier_signal: str = ""
    supplier_stability_level: str = ""
    top_suppliers_json: str = ""
    top_3_import_countries_json: str = ""

    # ── 体量字段 ──
    total_shipment_count: str = ""
    estimated_trade_volume_level: str = ""
    buyer_activity_level: str = ""
    total_import_volume: str = ""     # HS 搜索兼容

    # ── 推荐字段 ──
    recommended_action: str = "暂不跟进"
    evidence_excerpt: str = ""
    current_url: str = ""
    error_message: str = ""
    elapsed_seconds: str = ""

    # ── 其他 ──
    business_summary: str = ""
    source_page_title: str = ""
    analysis_entry_status: str = "unknown"
    analysis_data_status: str = "unknown"
    hs_product: str = ""              # HS 搜索兼容
    source_system: str = "tendata"
    source_capture_time: str = ""
    source_search_keyword: str = ""
    source_candidate_rank: int = 0
    source_page_url: str = ""
    manual_review_flag: str = "no"
    manual_review_reason: str = ""
    run_batch_id: str = ""

    # ── 候选摘要（用于 no_result/unconfirmed/detail_page_failed 时保留候选信息）──
    candidate_summary_json: str = ""  # JSON 字符串，包含 top 3 候选摘要


# ============================================================================
# 浏览器健康检查与恢复函数（V5 新增）
# ============================================================================

class BrowserContextClosedError(Exception):
    """浏览器上下文已关闭异常。"""
    pass


def check_browser_health(scraper: "TendataScraper") -> dict:
    """检查浏览器健康状态。

    Returns:
        {
            "healthy": bool,
            "browser_exists": bool,
            "context_exists": bool,
            "pages_count": int,
            "page_is_closed": bool,
            "error": str or None
        }
    """
    result = {
        "healthy": False,
        "browser_exists": False,
        "context_exists": False,
        "pages_count": 0,
        "page_is_closed": True,
        "error": None
    }

    try:
        # 检查 browser
        if scraper.browser is None:
            result["error"] = "browser is None"
            return result
        result["browser_exists"] = True

        # 检查 context
        if scraper.context is None:
            result["error"] = "context is None"
            return result
        result["context_exists"] = True

        # 检查 pages
        try:
            pages = scraper.context.pages
            result["pages_count"] = len(pages)
        except Exception as e:
            result["error"] = f"获取 pages 失败: {e}"
            return result

        # 检查 page 是否关闭
        if scraper.page:
            try:
                is_closed = scraper.page.is_closed()
                result["page_is_closed"] = is_closed
            except Exception as e:
                result["page_is_closed"] = True
                result["error"] = f"检查 page.is_closed 失败: {e}"
                return result
        else:
            result["error"] = "page is None"
            return result

        # 综合判断
        result["healthy"] = (
            result["browser_exists"]
            and result["context_exists"]
            and result["pages_count"] > 0
            and not result["page_is_closed"]
        )

    except Exception as e:
        result["error"] = f"健康检查异常: {e}"

    return result


def recover_browser_page(scraper: "TendataScraper", target_url: str = "https://bizr.tendata.cn/search#/index") -> bool:
    """尝试恢复浏览器页面。

    Args:
        scraper: TendataScraper 实例
        target_url: 目标 URL

    Returns:
        bool: 是否成功恢复
    """
    print(f"  [浏览器恢复] 尝试恢复页面...")

    try:
        # 检查 context 是否存在
        if scraper.context is None:
            print(f"  [浏览器恢复] context 不存在，无法恢复")
            return False

        # 尝试获取现有页面
        try:
            pages = scraper.context.pages
            if pages:
                # 找一个可用的页面
                for p in pages:
                    try:
                        if not p.is_closed():
                            scraper.page = p
                            print(f"  [浏览器恢复] 找到可用页面: {p.url[:50]}")
                            # 导航到目标页面
                            p.goto(target_url, timeout=15000)
                            p.wait_for_load_state("domcontentloaded", timeout=10000)
                            return True
                    except Exception:
                        continue
        except Exception as e:
            print(f"  [浏览器恢复] 获取 pages 失败: {e}")

        # 创建新页面
        try:
            new_page = scraper.context.new_page()
            new_page.goto(target_url, timeout=15000)
            new_page.wait_for_load_state("domcontentloaded", timeout=10000)
            scraper.page = new_page
            print(f"  [浏览器恢复] 创建新页面成功: {target_url}")
            return True
        except Exception as e:
            print(f"  [浏览器恢复] 创建新页面失败: {e}")
            return False

    except Exception as e:
        print(f"  [浏览器恢复] 恢复异常: {e}")
        return False


# ============================================================================
# 腾道浏览器抓取类
# ============================================================================


class TendataScraper:
    """腾道浏览器自动化抓取。

    通过 CDP 连接用户已登录的 Chrome 实例。
    用户需先以远程调试模式启动 Chrome:
        chrome.exe --remote-debugging-port=9222
    并在浏览器中手动完成腾道登录。

    本类不做任何自动登录操作，只复用已有的登录状态。
    """

    def __init__(self, config: dict | None = None, headless: bool = False):
        self.config = config or TENDATA_CONFIG
        self.headless = headless
        self.browser = None        # Playwright Browser 对象
        self.context = None        # BrowserContext 对象
        self.page: Page | None = None
        self._pw = None
        self._is_persistent = False  # 标记是否通过持久化上下文启动

    # ── 内部页过滤 & 业务页选择 ──────────────────────────────────────────

    @staticmethod
    def _is_internal_page(url: str, title: str) -> bool:
        """判断是否为浏览器内部页（chrome://, devtools://, about:blank 等）。"""
        if not url or url.startswith("chrome://"):
            return True
        if url.startswith("devtools://"):
            return True
        if url.startswith("edge://"):
            return True
        if url.startswith("about:blank"):
            return True
        if "Omnibox Popup" in title:
            return True
        return False

    @staticmethod
    def _is_tendata_business_page(url: str) -> bool:
        """判断是否为腾道业务域名。"""
        return any(kw in url for kw in [
            "account.tendata.cn",
            "bizr.tendata.cn",
            "knowledge.tendata.cn",
            "login.tendata.cn",
        ])

    def _select_business_page(self) -> bool:
        """从 CDP context 的所有页面中筛选并选择腾道业务页。

        过滤规则：
        - 排除 chrome:// / devtools:// / edge:// / about:blank / Omnibox Popup
        - 只保留 tendata 业务域名的页面

        优先级（多候选时）：
        1. account.tendata.cn/#/index
        2. bizr.tendata.cn/search#/index
        3. bizr.tendata.cn/enterprise#/
        4. 其他 bizr.tendata.cn 页面
        5. 其他 tendata 业务页面

        Returns:
            True 如果成功选择了业务页，False 如果没有业务页
        """
        if not self.context:
            return False

        # 诊断日志
        all_pages = list(self.context.pages) if self.context.pages else []
        total = len(all_pages)
        print(f"  [浏览器] CDP pages total: {total}")

        all_urls = []
        filtered_internal = 0
        business_candidates = []

        for pg in all_pages:
            try:
                url = pg.url or "(empty)"
                title = pg.title() or ""
            except Exception:
                url = "(unreachable)"
                title = ""
            all_urls.append(url)

            # 过滤内部页
            if self._is_internal_page(url, title):
                filtered_internal += 1
                print(f"  [浏览器] 过滤内部页: {url} title='{title}'")
                continue

            # 只保留腾道业务域名
            if self._is_tendata_business_page(url):
                priority = 5
                if url.startswith("https://account.tendata.cn/#/index") or \
                   url.startswith("https://account.tendata.cn/#/home"):
                    priority = 1
                elif "bizr.tendata.cn/search#/index" in url:
                    priority = 2
                elif "bizr.tendata.cn/enterprise#" in url:
                    priority = 3
                elif "bizr.tendata.cn" in url:
                    priority = 4
                business_candidates.append((pg, url, title, priority))

        print(f"  [浏览器] filtered_internal_pages: {filtered_internal}")
        print(f"  [浏览器] business_candidate_pages: {len(business_candidates)}")

        if not business_candidates:
            print(f"  [浏览器] cdp_connected_but_no_valid_business_page=true")
            print(f"  [浏览器] 所有 {total} 个页面均为非业务页")
            for u in all_urls:
                print(f"    -> {u}")
            return False

        # 按优先级排序
        business_candidates.sort(key=lambda x: x[3])
        selected_pg, selected_url, selected_title, selected_priority = business_candidates[0]

        self.page = selected_pg
        self.page.set_default_timeout(self.config["timeout"])

        reason_map = {
            1: "account.tendata.cn 业务首页（最高优先级）",
            2: "bizr.tendata.cn 商情发现搜索页",
            3: "bizr.tendata.cn 企业详情页",
            4: "bizr.tendata.cn 其他业务页",
            5: "其他 tendata 业务页",
        }

        print(f"  [浏览器] selected_page_url: {selected_url}")
        print(f"  [浏览器] selected_page_title: {selected_title}")
        print(f"  [浏览器] selected_page_reason: {reason_map.get(selected_priority, 'unknown')}")

        return True

    def connect(self):
        """连接浏览器。优先通过 CDP 连接用户已登录的 Chrome。"""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright 未安装，请先运行: pip install playwright && playwright install chromium"
            )

        self._pw = sync_playwright().start()

        # ---- 尝试 1: CDP 连接（用户已登录的 Chrome） ----
        cdp_errors = {}
        for port in [9222, 9223, 9224, 9225]:
            try:
                self.browser = self._pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}"
                )
                # connect_over_cdp 返回 Browser，需要从中获取 context
                self.context = (
                    self.browser.contexts[0]
                    if self.browser.contexts
                    else self.browser.new_context()
                )
                print(f"  [浏览器] 通过 CDP 端口 {port} 连接成功")

                # 从所有页面中选择腾道业务页（过滤 chrome:// 等内部页）
                if not self._select_business_page():
                    raise RuntimeError(
                        "已连接到 Chrome 但未找到腾道业务页面。\n"
                        "请确认：\n"
                        "  1. 已在腾道助手中登录腾道\n"
                        "  2. 腾道业务页面（bizr.tendata.cn 或 account.tendata.cn）已打开\n"
                        "  3. 不要关闭腾道助手窗口"
                    )
                self._is_persistent = False
                return
            except RuntimeError:
                raise
            except Exception as e:
                cdp_errors[port] = str(e)
                print(f"  [浏览器] CDP 端口 {port} 连接失败: {e}")

        # ---- 尝试 2: 新建持久化上下文（用户之前可能在此配置中登录过） ----
        persist_error = None
        try:
            user_dir = str(Path(__file__).parent.parent / ".tendata-chrome-profile")
            self.context = self._pw.chromium.launch_persistent_context(
                user_dir,
                headless=self.headless,
                locale="zh-CN",
            )
            self.browser = self.context  # 持久化模式下 context 兼作 browser
            self.page = (
                self.context.pages[0]
                if self.context.pages
                else self.context.new_page()
            )
            self.page.set_default_timeout(self.config["timeout"])
            print("  [浏览器] 启动持久化上下文成功")
            self._is_persistent = True
            return
        except Exception as e:
            persist_error = str(e)
            print(f"  [浏览器] 持久化上下文启动失败: {e}")

        # ---- 全部失败：汇总报错 ----
        raise RuntimeError(
            "无法连接到腾道助手窗口。\n"
            "\n"
            "请确认：\n"
            "  1. 已通过 start_tendata_helper.bat 启动腾道助手\n"
            "  2. 腾道助手窗口未被完全关闭\n"
            "  3. 如仍有问题，请关闭所有 Chrome 窗口后重新运行腾道助手"
        )

    def close(self):
        """关闭浏览器连接。分别安全关闭 browser / context / playwright。"""
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        if self.browser and not self._is_persistent:
            try:
                self.browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass

    def check_login(self) -> bool:
        """
        检查腾道登录态。

        策略：不导航到任何 URL，而是检查当前活动标签页的状态。
        1. 先排除浏览器内部页（chrome://, devtools://, about:blank, Omnibox Popup）
        2. 若当前页为内部页，尝试切换到业务页
        3. 对业务页执行登录态判断：URL / 标题 / 页面类型 / 特征元素

        注意：本方法不会改变用户当前页面（除非当前页是内部页需要切换）。
        """
        try:
            current_url = self.page.url
            current_title = self.page.title()

            # 安全兜底：如果当前页是浏览器内部页，先切换到业务页
            if self._is_internal_page(current_url, current_title):
                print(f"  [登录检查] 当前为浏览器内部页: {current_url} title='{current_title}'")
                print(f"  [登录检查] 尝试切换到腾道业务页...")
                if self._select_business_page():
                    current_url = self.page.url
                    current_title = self.page.title()
                else:
                    print(f"  [登录检查] 无可用业务页，跳过登录检查")
                    return False

            page_type = classify_page(self.page)

            print(f"  [登录检查] 当前 URL: {current_url}")
            print(f"  [登录检查] 当前标题: {current_title}")
            print(f"  [登录检查] 页面类型: {page_type}")

            if page_type == "login":
                print("  [登录检查] 页面为登录页，判断为未登录")
                return False

            if page_type == "biz_search":
                print("  [登录检查] 当前已在商情发现搜索页，直接接管")
                return True

            if page_type == "biz_home":
                print("  [登录检查] 当前在业务首页，已登录，后续将导航到搜索页")
                return True

            if page_type == "learning":
                print("  [登录检查] 当前处于学习中心（knowledge.tendata.cn）")
                print("  [登录检查] 请先切换到商情发现页面（bizr.tendata.cn）后再重试")
                return True

            # unknown 页：通过 DOM 特征判断是否已登录
            found_indicators = 0
            for indicator in LOGIN_INDICATORS:
                try:
                    el = self.page.query_selector(f"text={indicator['value']}")
                    if el and el.is_visible():
                        found_indicators += 1
                        if found_indicators >= 2:
                            print(f"  [登录检查] 已登录（找到 {found_indicators} 个特征元素）")
                            return True
                except Exception:
                    continue

            if found_indicators >= 1:
                print(f"  [登录检查] 可能已登录（找到 {found_indicators} 个特征元素）")
                return True

            print(f"  [登录检查] 未找到登录态特征元素（扫描了 {len(LOGIN_INDICATORS)} 个标识）")
            return False

        except PlaywrightTimeout as e:
            print(f"  [登录检查] 页面加载超时: {e}")
            return False
        except Exception as e:
            print(f"  [登录检查] 异常: {e}")
            return False

    def navigate_to_search(self) -> bool:
        """
        导航到商情发现搜索页面。

        优先级：
        A. biz_search -> 直接接管，查找搜索输入框
        B. biz_home   -> 跳转到 bizr.tendata.cn/search#/index
        C. learning   -> 跳转到 bizr.tendata.cn/search#/index
        D. unknown    -> 尝试点击"商情发现"菜单，失败后跳转
        """
        search_url = self.config["app_url"]
        try:
            current_url = self.page.url
            current_title = self.page.title()
            page_type = classify_page(self.page)
            print(f"  [搜索] 当前 URL: {current_url}")
            print(f"  [搜索] 当前标题: {current_title}")
            print(f"  [搜索] 页面类型: {page_type}")

            # A. 已在商情发现搜索页，直接接管
            if page_type == "biz_search":
                print("  [搜索] 已在商情发现搜索页，直接接管当前页")
                return self._find_search_input()

            # B. 在业务首页，跳转到搜索页
            if page_type == "biz_home":
                print(f"  [搜索] 当前在业务首页，跳转到搜索页: {search_url}")
                return self._goto_search(search_url)

            # C. 在学习中心，跳转到搜索页
            if page_type == "learning":
                print(f"  [搜索] 当前在学习中心，跳转到搜索页: {search_url}")
                return self._goto_search(search_url)

            # D. 未知页：尝试点击"商情发现"菜单
            try:
                sq_link = self.page.wait_for_selector("text=商情发现", timeout=5000)
                if sq_link and sq_link.is_visible():
                    sq_link.click()
                    self.page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
                    time.sleep(2)
                    new_type = classify_page(self.page)
                    print(f"  [搜索] 点击了'商情发现'，当前页面类型: {new_type}")
                    if new_type == "biz_search":
                        print("  [搜索] 已到达搜索页，尝试接管")
                        return self._find_search_input()
                    elif new_type in ("biz_home", "learning", "unknown"):
                        print(f"  [搜索] 点击后到达 {new_type}，继续跳转到搜索页")
                        return self._goto_search(search_url)
            except PlaywrightTimeout:
                print("  [搜索] 未找到'商情发现'菜单链接")

            # 兜底：直接跳转
            print(f"  [搜索] 未找到菜单入口，直接跳转到搜索页: {search_url}")
            return self._goto_search(search_url)

        except PlaywrightTimeout as e:
            print(f"  [搜索] 导航超时: {e}")
            return False
        except Exception as e:
            print(f"  [搜索] 导航异常: {e}")
            return False

    def _goto_search(self, search_url: str) -> bool:
        """跳转到搜索页并查找搜索输入框。"""
        print(f"  [搜索] 正在跳转: {search_url}")
        try:
            self.page.goto(search_url, timeout=self.config["load_timeout"], wait_until="domcontentloaded")
            self.page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
            time.sleep(2)
            new_type = classify_page(self.page)
            print(f"  [搜索] 跳转后 URL: {self.page.url}")
            print(f"  [搜索] 跳转后类型: {new_type}")
            if new_type == "biz_search":
                print("  [搜索] 已检测到商情发现搜索页")
            return self._find_search_input()
        except PlaywrightTimeout as e:
            print(f"  [搜索] 跳转超时: {e}")
            return False

    def _find_search_input(self) -> bool:
        """在当前搜索页查找搜索输入框，支持点击'公司名称'Tab。

        修复：使用 JS 评估方式定位真实 <input> 元素，不再依赖
        Playwright 的逗号分隔多选择器（可能不生效）。
        """
        page = self.page

        # 如果已经是结果页态，快速返回，让后续逻辑处理
        sub_state = classify_search_page(page)
        if sub_state == "search_results":
            print(f"  [搜索] 检测到页面已处于结果页态 (search_results)，跳过输入框等待")
            return True

        # 先定位搜索区域容器（可选优化，用于缩小查找范围）
        area_sel = self.config.get("search_area", "")
        if area_sel:
            try:
                # 类型保护：search_area 可能是 tuple 或 string
                if isinstance(area_sel, (list, tuple)):
                    first_sel = area_sel[0] if area_sel else None
                elif isinstance(area_sel, str):
                    first_sel = area_sel.split(",")[0].strip()
                else:
                    first_sel = None

                if first_sel:
                    area_el = page.wait_for_selector(first_sel, timeout=3000)
                    if area_el:
                        print(f"  [搜索] 已定位搜索区域容器 (selector={first_sel})")
            except PlaywrightTimeout:
                pass  # 容器可能不存在或选择器不对，不影响后续
            except Exception as e:
                print(f"  [搜索] 搜索区域检测异常: {e}")

        # 用 JS 在 DOM 中查找真实可见的 <input> 元素
        input_el = self._locate_search_input_via_js()
        if input_el:
            print(f"  [搜索] 通过 JS 策略检测到搜索输入框")
            return True

        # 尝试点击"公司名称"Tab 后再查找
        try:
            company_tab = page.wait_for_selector("text=公司名称", timeout=3000)
            if company_tab and company_tab.is_visible():
                company_tab.click()
                page.wait_for_timeout(1000)
                print("  [搜索] 点击了'公司名称'Tab，等待渲染")
                input_el = self._locate_search_input_via_js()
                if input_el:
                    print(f"  [搜索] 点击Tab后通过 JS 策略检测到搜索输入框")
                    return True
        except PlaywrightTimeout:
            pass

        print("  [搜索] 警告：未找到搜索输入框，但将继续尝试")
        return True

    def _locate_search_input_via_js(self):
        """通过 JS 评估定位真实可见的搜索输入框 <input> 元素。

        策略：
        1. 遍历 placeholder 关键词找匹配 input
        2. 遍历 fallback CSS 选择器
        3. 搜索所有可见 input 中靠近"公司名称"文本的

        Returns:
            Playwright ElementHandle 或 None
        """
        page = self.page

        # 策略 1: 通过 placeholder 文本匹配
        placeholders = self.config.get("search_input_placeholders", [])
        for ph in placeholders:
            try:
                el = page.query_selector(f"input[placeholder*='{ph}']")
                if el and el.is_visible():
                    print(f"  [搜索] 命中输入框 selector: input[placeholder*='{ph}']")
                    return el
            except Exception:
                pass

        # 策略 2: 遍历 fallback CSS 选择器
        fallback_sels = self.config.get("search_input_fallback_selectors", [])
        for sel in fallback_sels:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    tag = el.evaluate("e => e.tagName")
                    if tag.upper() == "INPUT":
                        print(f"  [搜索] 命中输入框 selector: {sel}")
                        return el
            except Exception:
                pass

        # 策略 3: JS 扫描页面，找靠近"公司名称"文本的 input
        try:
            js_result = page.evaluate("""() => {
                // 找所有可见的 input 元素
                const inputs = Array.from(document.querySelectorAll('input')).filter(inp => {
                    const r = inp.getBoundingClientRect();
                    return r.width > 20 && r.height > 10 && getComputedStyle(inp).display !== 'none';
                });
                // 找"公司名称"文本的位置
                let companyNamePos = null;
                for (const el of document.querySelectorAll('*')) {
                    if (el.textContent.trim() === '公司名称' ||
                        el.textContent.trim().startsWith('公司名称')) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            companyNamePos = { x: r.x, y: r.y };
                            break;
                        }
                    }
                }
                // 如果找到"公司名称"，优先返回离它最近的 input
                if (companyNamePos) {
                    let best = null, bestDist = Infinity;
                    for (const inp of inputs) {
                        const r = inp.getBoundingClientRect();
                        const cx = r.x + r.width / 2;
                        const cy = r.y + r.height / 2;
                        const d = Math.abs(cx - companyNamePos.x) + Math.abs(cy - companyNamePos.y);
                        if (d < bestDist) { bestDist = d; best = inp; }
                    }
                    if (best && bestDist < 500) return { found: true, tag: 'nearest_to_company', value: best.value || '', placeholder: best.placeholder || '' };
                }
                // 返回第一个可见 input
                if (inputs.length > 0) {
                    const inp = inputs[0];
                    return { found: true, tag: 'first_visible', value: inp.value || '', placeholder: inp.placeholder || '' };
                }
                return { found: false };
            }""")
            if js_result and js_result.get("found"):
                tag = js_result["tag"]
                val = js_result.get("value", "")
                ph = js_result.get("placeholder", "")
                print(f"  [搜索] JS 扫描发现 input: strategy={tag}, placeholder='{ph}', current_value='{val}'")

                # 用 JS 返回的元素 ref，通过 Playwright 的 evaluate 方式操作
                # 这里我们返回第一个可见 input 的 ElementHandle
                # 由于 JS 返回的是序列化对象，需要重新定位
                # 用 placeholder 反查
                if ph:
                    el = page.query_selector(f"input[placeholder*='{ph[:6]}']")
                    if el and el.is_visible():
                        return el
                # 或者用第一个可见 input 的 fallback selector
                for sel in fallback_sels:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            return el
                    except Exception:
                        pass
        except Exception as e:
            print(f"  [搜索] JS 扫描异常: {e}")

        return None

    def search_company(self, company_name: str) -> bool:
        """在商情发现搜索页用公司名称模式搜索。

        修复要点：
        1. 如果页面已经是 search_results 态，直接进入结果解析
        2. 在输入框中用 Ctrl+A + Backspace 强制清空旧值
        3. 点击搜索前校验输入框值 == company_name
        4. 显式忽略历史搜索 chip
        """
        try:
            page = self.page

            # 打印页面状态
            current_url = page.url
            page_type = classify_page(page)
            sub_state = classify_search_page(page)
            print(f"  [搜索] 当前 URL: {current_url}")
            print(f"  [搜索] 当前页面分类: {page_type}")
            print(f"  [搜索] 当前搜索页子状态: {sub_state}")

            # 如果当前页已是 biz_search，明确打印
            if page_type == "biz_search":
                print("  [搜索] 已处于商情发现搜索页，跳过导航，进入搜索输入阶段")

            # 先确保在搜索页面
            if not self.navigate_to_search():
                # 导航失败，打印更精确的错误
                if page_type == "biz_search":
                    print("  [搜索] 导航异常：已处于 biz_search 但搜索输入框定位失败")
                else:
                    print("  [搜索] 导航异常：未能到达商情发现搜索页")
                return False

            # --- 检查是否已经是结果页态 ---
            page_sub_state = classify_search_page(page)
            print(f"  [搜索] 导航后页面子状态: {page_sub_state}")

            # 如果页面已经显示了结果（可能是之前的搜索残留），
            # 不直接复用旧结果，需要重新执行搜索
            if page_sub_state == "search_results":
                print("  [搜索] 检测到页面已存在结果，将清空后执行新搜索")

            # --- 切换搜索模式为"公司名称" ---
            self._select_company_name_mode()

            # --- 清空并写入搜索词 ---
            input_ok = self._type_search_keyword(company_name)
            if not input_ok:
                print("  [搜索] 搜索词写入失败")
                return False

            # --- 点击搜索并等待结果 ---
            return self._click_search_and_wait()

        except PlaywrightTimeout as e:
            print(f"  [搜索] 超时: {e}")
            return False
        except Exception as e:
            print(f"  [搜索] 异常: {e}")
            return False

    # 国家名称 → 3位代码映射
    COUNTRY_CODE_MAP = {
        "canada": "CAN", "can": "CAN",
        "united states": "USA", "usa": "USA", "us": "USA", "america": "USA",
        "mexico": "MEX", "mex": "MEX",
        "india": "IND", "ind": "IND",
        "vietnam": "VNM", "viet nam": "VNM",
        "japan": "JPN", "jp": "JPN",
        "south korea": "KOR", "korea": "KOR", "kr": "KOR",
        "germany": "DEU", "de": "DEU",
        "uk": "GBR", "united kingdom": "GBR", "britain": "GBR", "england": "GBR",
        "france": "FRA", "fr": "FRA",
        "italy": "ITA", "it": "ITA",
        "spain": "ESP", "es": "ESP",
        "brazil": "BRA", "br": "BRA",
        "russia": "RUS", "ru": "RUS",
        "australia": "AUS", "au": "AUS",
        "thailand": "THA", "th": "THA",
        "indonesia": "IDN", "id": "IDN",
        "malaysia": "MYS", "my": "MYS",
        "philippines": "PHL", "ph": "PHL",
        "singapore": "SGP", "sg": "SGP",
        "china": "CHN", "cn": "CHN",
        "turkey": "TUR", "tr": "TUR",
        "poland": "POL", "pl": "POL",
        "netherlands": "NLD", "nl": "NLD",
        "belgium": "BEL", "be": "BEL",
        "uae": "ARE", "united arab emirates": "ARE",
    }

    # 英文/拼音国家名 → 中文名称（用于在筛选面板文本中匹配）
    COUNTRY_CHINESE_MAP = {
        "canada": "加拿大", "加拿大": "加拿大",
        "united states": "美国", "usa": "美国", "america": "美国",
        "mexico": "墨西哥",
        "india": "印度",
        "vietnam": "越南", "viet nam": "越南",
        "japan": "日本",
        "south korea": "韩国", "korea": "韩国",
        "germany": "德国",
        "uk": "英国", "united kingdom": "英国", "britain": "英国", "england": "英国",
        "france": "法国",
        "italy": "意大利",
        "spain": "西班牙",
        "brazil": "巴西",
        "russia": "俄罗斯",
        "australia": "澳大利亚",
        "thailand": "泰国",
        "indonesia": "印度尼西亚",
        "malaysia": "马来西亚",
        "philippines": "菲律宾",
        "singapore": "新加坡",
        "china": "中国",
        "turkey": "土耳其",
        "poland": "波兰",
        "netherlands": "荷兰",
        "belgium": "比利时",
        "uae": "阿联酋", "united arab emirates": "阿联酋",
    }

    @classmethod
    def resolve_country_code(cls, country_name: str) -> str:
        """将国家名称映射为 3 位代码。如已是 3 位大写字母则直接返回。"""
        raw = country_name.strip()
        if len(raw) == 3 and raw.isupper():
            return raw
        key = raw.lower().strip()
        return cls.COUNTRY_CODE_MAP.get(key, raw.upper())

    @classmethod
    def resolve_country_chinese(cls, country_name: str) -> str:
        """将国家名称映射为中文名称（用于在筛选面板文本中匹配）。"""
        key = country_name.strip().lower()
        return cls.COUNTRY_CHINESE_MAP.get(key, country_name.strip())

    def _apply_country_filter(self, country_code: str):
        """在 HS 搜索结果页上，勾选国家筛选 checkbox。

        真实 DOM：
        - label.tendata-ui-checkbox-wrapper 内层文本为中文国家名，如"加拿大(34)"
        - 选中后：label 带 tendata-ui-checkbox-wrapper-checked 类

        失败直接抛 RuntimeError，不继续。
        """
        page = self.page
        try:
            # 映射到中文国家名（如 "Canada" → "加拿大", "加拿大" → "加拿大"）
            chinese_name = self.resolve_country_chinese(country_code)
            code = self.resolve_country_code(country_code)
            print(f"  [国家筛选] 将筛选国家: {country_code} → 中文: {chinese_name} → 代码: {code}")

            # 等待筛选区域渲染
            page.wait_for_timeout(2000)

            # 优先按 wrapper 的中文文本匹配，不再依赖 input.value
            click_result = page.evaluate(r"""({chineseName, code}) => {
                const allWrappers = document.querySelectorAll('[class*="tendata-ui-checkbox-wrapper"]');
                for (const wrapper of allWrappers) {
                    if (wrapper.tagName.toLowerCase() !== 'label') continue;

                    const text = (wrapper.innerText || '').trim();

                    // 策略 1: 精确匹配中文国家名（可能带数量，如"加拿大(34)"）
                    if (text.startsWith(chineseName) || text === chineseName) {
                        wrapper.click();
                        return { ok: true, method: 'click_by_chinese_text_exact', wrapper_text: text };
                    }

                    // 策略 2: 宽松匹配——文本包含中文国家名
                    if (text.includes(chineseName)) {
                        wrapper.click();
                        return { ok: true, method: 'click_by_chinese_text_contains', wrapper_text: text };
                    }

                    // 策略 3: 如果中文映射失败，回退到 input.value 匹配 3 位代码
                    const inp = wrapper.querySelector('input');
                    if (inp) {
                        const val = (inp.getAttribute('value') || '').trim().toUpperCase();
                        if (val === code) {
                            wrapper.click();
                            return { ok: true, method: 'click_by_input_value', wrapper_class: wrapper.className || '' };
                        }
                    }
                }

                // 诊断
                const diagWrappers = [];
                document.querySelectorAll('[class*="tendata-ui-checkbox-wrapper"]').forEach((w, i) => {
                    diagWrappers.push({
                        idx: i,
                        tag: w.tagName,
                        class: (w.className || '').substring(0, 120),
                        text: (w.innerText || '').trim().substring(0, 50),
                    });
                });

                return { ok: false, reason: 'no_wrapper_matched', chineseName: chineseName, code: code, diag_wrappers: diagWrappers };
            }""", {"chineseName": chinese_name, "code": code})

            if not click_result.get("ok"):
                reason = click_result.get('reason', 'unknown')
                diag = f" | wrappers={click_result.get('diag_wrappers', [])}"
                raise RuntimeError(
                    f"国家筛选 checkbox 未找到 — 中文: {chinese_name}, "
                    f"原因: {reason}{diag}"
                )

            print(f"  [国家筛选] 已点击国家筛选 checkbox，方法: {click_result.get('method')}")
            page.wait_for_timeout(2000)

            # 校验：确认 label 已带上 tendata-ui-checkbox-wrapper-checked 类
            is_checked = page.evaluate(r"""({chineseName}) => {
                const wrappers = document.querySelectorAll('[class*="tendata-ui-checkbox-wrapper"]');
                for (const w of wrappers) {
                    const cls = w.className || '';
                    if (typeof cls === 'string' && cls.includes('tendata-ui-checkbox-wrapper-checked')) {
                        const text = (w.innerText || '').trim();
                        if (text.startsWith(chineseName) || text.includes(chineseName)) return true;
                    }
                }
                return false;
            }""", {"chineseName": chinese_name})

            if not is_checked:
                raise RuntimeError(
                    f"国家筛选校验失败 — 点击了 {code} 的 checkbox 但 label 未变成选中态。"
                    f"请检查页面筛选区域是否已加载。"
                )

            print(f"  [国家筛选] 校验通过：国家筛选 checkbox 已激活")

        except RuntimeError:
            raise
        except Exception as e:
            print(f"  [国家筛选] 异常: {e}")
            raise

    def restore_search_page(self, search_url: str, country_filter: str = "") -> int:
        """返回搜索页并等待卡片渲染，返回卡片数（为 0 则恢复失败）。

        策略：
        1. page.goto(search_url) + 如果带国家筛选则校验并重新勾选
        2. 等待卡片出现（最多 5s）
        3. 如果卡片数 0，reload 页面再试
        4. 如果仍为 0，重新执行 HS 搜索（含国家筛选）
        """
        page = self.page

        # ── 策略 1: 直接 goto ──
        try:
            page.goto(search_url, timeout=15000)
            # 等待结果渲染
            for attempt in range(10):
                page.wait_for_timeout(500)
                card_count = page.evaluate(
                    r"""() => document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').length"""
                )
                if card_count > 0:
                    break
            # 检查国家筛选是否仍然生效
            if country_filter and card_count > 0:
                still_active = self._is_country_filter_active(country_filter)
                if not still_active:
                    print(f"  [返回搜索页] 国家筛选已丢失，重新勾选...")
                    self._apply_country_filter(country_filter)
                    page.wait_for_timeout(1000)
                    card_count = page.evaluate(
                        r"""() => document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').length"""
                    )

            if card_count > 0:
                self._print_search_page_status()
                return card_count
            print(f"  [返回搜索页] goto 成功但无卡片渲染（URL: {page.url}）")
        except Exception as e:
            print(f"  [返回搜索页] goto 异常: {e}")

        # ── 策略 2: reload 页面 ──
        try:
            print(f"  [返回搜索页] 尝试 reload...")
            page.reload(timeout=15000)
            for attempt in range(10):
                page.wait_for_timeout(500)
                card_count = page.evaluate(
                    r"""() => document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').length"""
                )
                if card_count > 0:
                    break
            if country_filter and card_count > 0:
                still_active = self._is_country_filter_active(country_filter)
                if not still_active:
                    print(f"  [返回搜索页] 国家筛选已丢失，重新勾选...")
                    self._apply_country_filter(country_filter)
                    page.wait_for_timeout(1000)
                    card_count = page.evaluate(
                        r"""() => document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').length"""
                    )

            if card_count > 0:
                self._print_search_page_status()
                return card_count
            print(f"  [返回搜索页] reload 后仍无卡片")
        except Exception as e:
            print(f"  [返回搜索页] reload 异常: {e}")

        # ── 策略 3: 重新执行 HS 搜索 ──
        print(f"  [返回搜索页] 尝试重新执行 HS 搜索...")
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(search_url)
            qs = parse_qs(parsed.fragment)
            hs_code = qs.get('value', [''])[0]
            mode = qs.get('mode', [''])[0]

            if not hs_code or mode != 'hs':
                print(f"  [返回搜索页] 无法从 URL 解析 HS 编码, fragment={parsed.fragment}")
                return 0

            print(f"  [返回搜索页] 重新执行 HS 搜索: code={hs_code}, country_filter={country_filter or '无'}")
            ok = self.search_by_hs_code(hs_code, country_filter=country_filter)
            if ok:
                for attempt in range(10):
                    page.wait_for_timeout(500)
                    card_count = page.evaluate(
                        r"""() => document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').length"""
                    )
                    if card_count > 0:
                        self._print_search_page_status()
                        return card_count
        except Exception as e:
            print(f"  [返回搜索页] 重新搜索异常: {e}")

        print(f"  [返回搜索页] 所有恢复策略均失败")
        return 0

    def _is_country_filter_active(self, country_filter: str) -> bool:
        """检查当前搜索页是否已激活指定的国家筛选。"""
        try:
            chinese_name = self.resolve_country_chinese(country_filter)
            is_active = self.page.evaluate(r"""({chineseName}) => {
                const wrappers = document.querySelectorAll('[class*="tendata-ui-checkbox-wrapper"]');
                for (const w of wrappers) {
                    const cls = w.className || '';
                    if (typeof cls === 'string' && cls.includes('tendata-ui-checkbox-wrapper-checked')) {
                        const text = (w.innerText || '').trim();
                        if (text.startsWith(chineseName) || text.includes(chineseName)) return true;
                    }
                }
                return false;
            }""", {"chineseName": chinese_name})
            return is_active
        except Exception:
            return False

    def _print_search_page_status(self):
        """打印当前搜索页状态：URL、筛选是否激活、前3家公司名。"""
        page = self.page
        print(f"  [搜索页状态] URL: {page.url}")
        # 检查筛选状态
        filter_status = page.evaluate(r"""() => {
            const checked = [];
            document.querySelectorAll('[class*="tendata-ui-checkbox-wrapper"]').forEach(w => {
                const cls = w.className || '';
                if (cls.includes('tendata-ui-checkbox-wrapper-checked')) {
                    const text = (w.innerText || '').trim();
                    if (text) checked.push(text);
                }
            });
            return checked;
        }""")
        if filter_status:
            print(f"  [搜索页状态] 已激活筛选: {filter_status}")
        else:
            print(f"  [搜索页状态] 国家筛选: 未激活")
        # 前3家公司名
        first3 = page.evaluate(r"""() => {
            const names = [];
            document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').forEach((card, i) => {
                if (i >= 3) return;
                const ne = card.querySelector('span[class*="reocrdItem--companyName"]');
                if (ne) names.push((ne.innerText || '').trim());
            });
            return names;
        }""")
        if first3:
            print(f"  [搜索页状态] 前3家公司: {first3}")
        else:
            print(f"  [搜索页状态] 前3家公司: 无卡片")

    def search_by_hs_code(self, hs_code: str, country_filter: str = "") -> bool:
        """在商情发现搜索页用 HS 编码模式搜索。

        先切换到"HS编码"tab（失败会抛 RuntimeError），然后输入+搜索。
        """
        try:
            page = self.page

            current_url = page.url
            page_type = classify_page(page)
            sub_state = classify_search_page(page)
            print(f"  [HS搜索] 当前 URL: {current_url}")
            print(f"  [HS搜索] 当前页面分类: {page_type}")
            print(f"  [HS搜索] 当前搜索页子状态: {sub_state}")

            if page_type == "biz_search":
                print("  [HS搜索] 已处于商情发现搜索页，跳过导航")

            if not self.navigate_to_search():
                if page_type == "biz_search":
                    print("  [HS搜索] 导航异常：已处于 biz_search 但搜索输入框定位失败")
                else:
                    print("  [HS搜索] 导航异常：未能到达商情发现搜索页")
                return False

            page_sub_state = classify_search_page(page)
            print(f"  [HS搜索] 导航后页面子状态: {page_sub_state}")

            if page_sub_state == "search_results":
                print("  [HS搜索] 检测到页面已存在结果，将清空后执行新搜索")

            # 切换到"HS编码"搜索模式（失败会直接报 RuntimeError）
            self._select_hs_code_mode()

            # 清空并写入 HS 编码
            input_ok = self._type_search_keyword(hs_code)
            if not input_ok:
                print("  [HS搜索] 搜索词写入失败")
                return False

            # 点击搜索并等待结果
            ok = self._click_search_and_wait()

            # 搜索结果出来后，应用国家筛选
            if ok and country_filter:
                self._apply_country_filter(country_filter)

            return ok

        except RuntimeError:
            raise
        except PlaywrightTimeout as e:
            print(f"  [HS搜索] 超时: {e}")
            return False
        except Exception as e:
            print(f"  [HS搜索] 异常: {e}")
            return False

    def _select_company_name_mode(self):
        """尝试切换到"公司名称"搜索模式。

        直接点击"公司名称"文本，不依赖复杂的选择器。
        """
        try:
            # 直接点击"公司名称"文本
            company_tab = self.page.query_selector("text=公司名称")
            if company_tab and company_tab.is_visible():
                company_tab.click()
                self.page.wait_for_timeout(800)
                print(f"  [搜索] 已点击'公司名称'选项")
                return

            # 如果找不到，用 wait_for_selector 再试一次
            company_tab = self.page.wait_for_selector("text=公司名称", timeout=2000)
            if company_tab and company_tab.is_visible():
                company_tab.click()
                self.page.wait_for_timeout(800)
                print(f"  [搜索] 已点击'公司名称'选项（等待后）")
                return

            print(f"  [搜索] 未找到'公司名称'选项，使用默认模式")
        except PlaywrightTimeout:
            print("  [搜索] 未找到搜索模式选择器，使用默认模式")

    def _select_hs_code_mode(self):
        """切换到"HS编码"搜索模式。

        真实 DOM 结构：
        - div[class*="search--mode--"] （所有 mode tab 的公共基类）
        - 激活态时额外包含 class "search--selectMode--"（激活后缀）
        - 内层文本为"HS编码"

        校验：点击后必须确认 class 已包含 "selectMode"，否则直接报错退出。
        """
        try:
            page = self.page

            # 步骤 1: 找到文本为"HS编码"的 mode tab
            hs_tab = page.evaluate_handle(r"""() => {
                const allDivs = document.querySelectorAll('div');
                for (const div of allDivs) {
                    const cls = div.className || '';
                    if (cls.includes('search--mode--')) {
                        const text = (div.innerText || '').trim();
                        if (text === 'HS编码') {
                            return div;
                        }
                    }
                }
                return null;
            }""")

            if not hs_tab:
                raise RuntimeError("未找到'HS编码'tab 元素（div.search--mode-- 内文本='HS编码'）")

            print("  [HS模式] 已定位到'HS编码'tab")

            # 检查当前是否已经是激活态
            is_active = hs_tab.evaluate(r"""el => {
                const cls = el.className || '';
                return cls.includes('selectMode');
            }""")

            if is_active:
                print("  [HS模式] 'HS编码'已是激活态，无需点击")
                return

            # 步骤 2: 点击切换
            hs_tab.click()
            page.wait_for_timeout(1000)
            print("  [HS模式] 已点击'HS编码'tab")

            # 步骤 3: 校验 — 确认 class 已包含 selectMode（激活态）
            now_active = page.evaluate(r"""() => {
                const allDivs = document.querySelectorAll('div');
                for (const div of allDivs) {
                    const cls = div.className || '';
                    if (cls.includes('search--mode--')) {
                        const text = (div.innerText || '').trim();
                        if (text === 'HS编码') {
                            return cls.includes('selectMode');
                        }
                    }
                }
                return false;
            }""")

            if not now_active:
                # 再等一会重试校验
                page.wait_for_timeout(500)
                now_active = page.evaluate(r"""() => {
                    const allDivs = document.querySelectorAll('div');
                    for (const div of allDivs) {
                        const cls = div.className || '';
                        if (cls.includes('search--mode--')) {
                            const text = (div.innerText || '').trim();
                            if (text === 'HS编码') {
                                return cls.includes('selectMode');
                            }
                        }
                    }
                    return false;
                }""")

            if not now_active:
                raise RuntimeError(
                    "'HS编码'tab 点击后仍未激活（class 缺少 selectMode）。"
                    "当前可能仍在'公司名称'模式，中止搜索。"
                )

            print("  [HS模式] 校验通过，'HS编码'tab 已激活")

        except PlaywrightTimeout as e:
            raise RuntimeError(f"切换'HS编码'tab 超时: {e}") from e
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"切换'HS编码'tab 异常: {e}") from e

    def _get_search_input_element(self):
        """获取搜索输入框的 ElementHandle。

        与 _locate_search_input_via_js 逻辑相同，但直接返回元素。
        """
        page = self.page

        # 策略 1: 通过 placeholder 文本匹配
        placeholders = self.config.get("search_input_placeholders", [])
        for ph in placeholders:
            try:
                el = page.query_selector(f"input[placeholder*='{ph}']")
                if el and el.is_visible():
                    return el
            except Exception:
                pass

        # 策略 2: 遍历 fallback CSS 选择器
        fallback_sels = self.config.get("search_input_fallback_selectors", [])
        for sel in fallback_sels:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    tag = el.evaluate("e => e.tagName")
                    if tag.upper() == "INPUT":
                        return el
            except Exception:
                pass

        # 策略 3: 找第一个可见 input
        try:
            inputs = page.query_selector_all("input")
            for inp in inputs:
                try:
                    if inp.is_visible():
                        box = inp.bounding_box()
                        if box and box["width"] > 20:
                            return inp
                except Exception:
                    continue
        except Exception:
            pass

        return None

    def _type_search_keyword(self, company_name: str) -> bool:
        """清空搜索框并写入关键词，写入后做一致性校验。

        Returns:
            True 如果输入框中的值与 company_name 一致
        """
        page = self.page

        # 1) 定位搜索输入框（重新定位，确保拿到 ElementHandle）
        search_input = self._get_search_input_element()

        if not search_input:
            # 尝试点击"公司名称"Tab 后再找
            try:
                company_tab = page.wait_for_selector("text=公司名称", timeout=3000)
                if company_tab and company_tab.is_visible():
                    company_tab.click()
                    page.wait_for_timeout(1000)
                    print("  [搜索] 点击了'公司名称'Tab，等待渲染")
            except PlaywrightTimeout:
                pass
            search_input = self._get_search_input_element()

        if not search_input:
            print("  [搜索] 未找到搜索输入框，DOM 转储前 50 个 input 标签：")
            try:
                inputs_info = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('input')).map(inp => {
                        const r = inp.getBoundingClientRect();
                        return {
                            type: inp.type || '',
                            placeholder: inp.placeholder || '',
                            visible: r.width > 0 && r.height > 0,
                            value: inp.value || ''
                        };
                    }).slice(0, 50);
                }""")
                for info in inputs_info:
                    print(f"    input: type='{info['type']}' placeholder='{info['placeholder']}' visible={info['visible']} value='{info['value']}'")
            except Exception:
                print("    (无法获取 input 信息)")
            return False

        # 2) 检测历史搜索 chip 并记录日志
        self._log_and_ignore_history_chips()

        # 3) 聚焦 + 强制清空：点击 -> Ctrl+A -> Delete
        search_input.click()
        page.wait_for_timeout(300)
        search_input.focus()
        page.wait_for_timeout(200)
        search_input.press("Control+a")
        page.wait_for_timeout(100)
        search_input.press("Backspace")
        page.wait_for_timeout(200)

        # 4) 再次确认输入框已清空
        current_val = ""
        try:
            current_val = search_input.input_value()
        except Exception:
            pass
        if current_val:
            print(f"  [搜索] 第一次清空后仍有残留: '{current_val}'，再次清空")
            search_input.press("Control+a")
            page.wait_for_timeout(100)
            search_input.press("Backspace")
            page.wait_for_timeout(200)

        # 5) 填入新值
        search_input.fill(company_name)
        page.wait_for_timeout(500)

        # 6) 一致性校验：读取输入框当前值
        final_val = ""
        try:
            final_val = search_input.input_value()
        except Exception:
            pass
        expected = company_name.strip()
        actual = final_val.strip()

        print(f"  [搜索] Excel 客户名: '{expected}'")
        print(f"  [搜索] 输入框写入值: '{actual}'")

        if actual != expected:
            print(f"  [搜索] 警告：输入框值与目标不一致，尝试重新输入")
            # 重试：重新聚焦、清空、再填
            search_input.click()
            page.wait_for_timeout(200)
            search_input.press("Control+a")
            page.wait_for_timeout(100)
            search_input.press("Backspace")
            page.wait_for_timeout(200)
            search_input.fill(company_name)
            page.wait_for_timeout(500)

            retry_val = ""
            try:
                retry_val = search_input.input_value().strip()
            except Exception:
                pass
            print(f"  [搜索] 重新输入后值: '{retry_val}'")
            if retry_val != expected:
                print(f"  [搜索] 严重警告：输入值 '{retry_val}' 与目标 '{expected}' 不一致，搜索可能失败")

        # 7) 确认最终值
        confirmed_val = ""
        try:
            confirmed_val = search_input.input_value().strip()
        except Exception:
            pass
        print(f"  [搜索] 点击搜索前最终确认值: '{confirmed_val}'")

        return True

    def _log_and_ignore_history_chips(self):
        """检测并记录历史搜索 chip，显式不点击。"""
        try:
            # 常见的历史搜索 chip 选择器
            chip_selectors = [
                ".history-tag",
                ".history-search",
                "[class*='history']",
                "[class*='recentSearch']",
                "[class*='recent-search']",
                "[class*='hotSearch']",
                "[class*='hot-search']",
                ".search-history",
                ".tag-item",
                "[class*='chip']",
            ]
            chip_count = 0
            for sel in chip_selectors:
                try:
                    els = self.page.query_selector_all(sel)
                    if els:
                        chip_count += len(els)
                except Exception:
                    continue

            # 也检测"历史搜索"/"最近搜索"文本
            history_text = self.page.query_selector("text=/历史搜索|最近搜索|热门搜索/")
            if history_text and history_text.is_visible():
                print(f"  [搜索] 检测到历史搜索区域，已跳过历史搜索 chip (共 {chip_count} 个)")
            elif chip_count > 0:
                print(f"  [搜索] 检测到 {chip_count} 个疑似历史搜索 chip，已跳过")
            else:
                print(f"  [搜索] 未检测到历史搜索 chip")
        except Exception:
            print(f"  [搜索] 历史搜索检测未触发")

    def _click_search_and_wait(self) -> bool:
        """点击搜索按钮并等待进入结果页态。"""
        page = self.page

        # 查找搜索按钮 — 优先按文本"搜索"
        search_btn = page.query_selector("button:has-text('搜索')")
        hit_sel = "button:has-text('搜索')"
        if search_btn and search_btn.is_visible():
            pass  # 使用此按钮
        else:
            search_btn = page.query_selector("button:has-text('查询')")
            hit_sel = "button:has-text('查询')"
            if not search_btn or not search_btn.is_visible():
                search_btn = None
                # fallback: 遍历搜索按钮选择器
                for sel in self.config.get("search_button_fallback_selectors", []):
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            search_btn = el
                            hit_sel = sel
                            break
                    except Exception:
                        pass

        if search_btn:
            print(f"  [搜索] 命中搜索按钮 selector: {hit_sel}")
            search_btn.click()
            print("  [搜索] 点击了搜索按钮")
        else:
            # 尝试用 Enter 键触发搜索
            search_input = self._get_search_input_element()
            if search_input:
                search_input.press("Enter")
                print("  [搜索] 按 Enter 搜索")
            else:
                print("  [搜索] 未找到搜索按钮或输入框")
                return False

        # 等待进入结果页态 — 等待"共搜索到"文本或结果卡片出现
        try:
            page.wait_for_function(
                """() => {
                    // 检测"共搜索到"文本
                    const textEls = Array.from(document.querySelectorAll('*'));
                    for (const el of textEls) {
                        if (el.textContent.includes('共搜索到') ||
                            el.textContent.includes('共找到') ||
                            el.textContent.includes('条结果')) {
                            return true;
                        }
                    }
                    // 检测结果卡片
                    const resultSelectors = [
                        '.search-result-item', '.company-item', '.result-row',
                        '.company-list > div', '.trade-data-item',
                        '[class*="resultItem"]', '[class*="companyItem"]'
                    ];
                    for (const sel of resultSelectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            if (el.textContent.trim().length > 5) return true;
                        }
                    }
                    return false;
                }""",
                timeout=self.config["load_timeout"],
            )
            print("  [搜索] 检测到结果页已加载")
        except PlaywrightTimeout:
            print("  [搜索] 等待结果页超时，尝试 networkidle 兜底")

        # 兜底等待
        try:
            page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
        except PlaywrightTimeout:
            pass

        time.sleep(2)

        # 确认页面子状态
        sub_state = classify_search_page(page)
        print(f"  [搜索] 搜索后页面子状态: {sub_state}")

        # 如果还是 search_landing，额外等待并重新检测
        if sub_state == "search_landing":
            print("  [搜索] 页面仍在 landing 态，额外等待结果渲染...")
            time.sleep(3)
            sub_state = classify_search_page(page)
            print(f"  [搜索] 二次检测页面子状态: {sub_state}")

        if sub_state == "search_results":
            print("  [搜索] 已确认进入结果页态")
        else:
            print(f"  [搜索] 警告：搜索后仍未检测到结果卡片 (子状态={sub_state})")

        return True

    def extract_search_results(self) -> list[SearchResult]:
        """从搜索结果页提取候选公司列表。

        核心策略：优先使用已知 CSS 选择器定位卡片根节点
        div[class*='reocrdItem--tradeRecordItem']，
        公司名直接从 span[class*='reocrdItem--companyName'] 提取。
        如果选择器定位失败，回退到旧版 DOM-walking 方案。
        """
        page = self.page
        results = []
        try:
            sub_state = classify_search_page(page)
            print(f"  [结果] 当前页面状态: {sub_state}")

            # ── 策略 A: 使用已知 CSS 选择器直接抓取卡片 ──
            cards = page.evaluate(r"""() => {
                const cards = [];
                const excluded = [];

                const cardEls = document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]');
                if (cardEls.length === 0) return { cards, excluded, used_selector: false };

                for (const cardEl of cardEls) {
                    const rect = cardEl.getBoundingClientRect();
                    if (rect.width < 50 || rect.height < 20) continue;
                    const cs = getComputedStyle(cardEl);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;

                    // ── 公司名：优先从 span[class*='reocrdItem--companyName'] 提取 ──
                    const nameEl = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                    let companyName = '';
                    if (nameEl) {
                        companyName = (nameEl.innerText || '').trim();
                    }

                    // 如果没有 nameEl 或公司名无效，回退到从整卡文本提取
                    if (!companyName || companyName.length < 2) {
                        const fullText = (cardEl.innerText || '').trim();
                        if (fullText.length < 10) {
                            excluded.push({ reason: '文本过短', preview: fullText.substring(0, 60) });
                            continue;
                        }
                        const lines = fullText.split('\n').map(l => l.trim()).filter(l => l);
                        for (const line of lines) {
                            if (line.length >= 3 && /CO|LTD|LLC|CORP|INC|INDUSTRIES|INDUSTRY|LIMITED|COMPANY|GMBH|SA\s|SRL/i.test(line)) {
                                companyName = line;
                                break;
                            }
                        }
                        if (!companyName) {
                            for (const line of lines) {
                                if (line.length >= 4 && /公司|企业|集团/.test(line)) {
                                    companyName = line;
                                    break;
                                }
                            }
                        }
                        if (!companyName && lines.length > 0) {
                            companyName = lines[0].trim();
                        }
                    }

                    if (!companyName || companyName.length < 2) {
                        excluded.push({ reason: '公司名为空', preview: (cardEl.innerText || '').substring(0, 60) });
                        continue;
                    }

                    // ── 无效公司名校验 ──
                    // 包含"公司简介："前缀 → 无效
                    if (companyName.startsWith('公司简介') || companyName.startsWith('简介')) {
                        excluded.push({ reason: '公司名含"公司简介"前缀', preview: companyName.substring(0, 80) });
                        continue;
                    }
                    // 纯 6 位数字（HS 编码） → 无效
                    if (/^\d{6}$/.test(companyName)) {
                        excluded.push({ reason: '公司名为纯 6 位 HS 编码', preview: companyName });
                        continue;
                    }
                    // 纯数字 → 无效
                    if (/^\d+$/.test(companyName)) {
                        excluded.push({ reason: '公司名为纯数字', preview: companyName });
                        continue;
                    }

                    // ── 全卡文本 ──
                    const fullText = (cardEl.innerText || '').trim();

                    // ── 排除非公司卡片 ──
                    if (/共搜索到|共找到|条?结果/.test(fullText) && !/[A-Za-z]/.test(fullText)) {
                        excluded.push({ reason: '结果数量统计', preview: fullText.substring(0, 60) });
                        continue;
                    }
                    if (/历史搜索|最近搜索|热门搜索|猜你喜欢|推荐公司|换一批|查看更多/.test(fullText)) {
                        excluded.push({ reason: '历史/推荐区域', preview: fullText.substring(0, 60) });
                        continue;
                    }
                    if (/加载中|loading|暂无数据/.test(fullText)) {
                        excluded.push({ reason: '骨架屏', preview: fullText.substring(0, 60) });
                        continue;
                    }

                    // 提取贸易日期
                    let tradeDate = '';
                    const dateMatch = fullText.match(/(\d{4}[-/]\d{1,2}[-/]\d{1,2})/);
                    if (dateMatch) tradeDate = dateMatch[1].replace('/', '-');

                    // 提取 enterprise 链接
                    let enterpriseLink = '';
                    const aEls = cardEl.querySelectorAll('a');
                    for (const a of aEls) {
                        const href = a.getAttribute('href') || '';
                        if (/search#\/trade|mode=company|tab=true|import|analysis/i.test(href)) continue;
                        if (/enterprise/.test(href)) {
                            enterpriseLink = href.startsWith('/') ? 'https://bizr.tendata.cn' + href : href;
                            break;
                        }
                    }

                    // 业务摘要
                    const hasDate = /\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(fullText);
                    const hasContact = /联系人/.test(fullText);
                    const hasTrade = /贸易|交易|供货|采购商|供应商/.test(fullText);
                    const hasDesc = /简介|主营|业务|产品/.test(fullText);
                    const hasMatchCount = /匹配|次数|次/.test(fullText);
                    const summaryParts = [];
                    if (tradeDate) summaryParts.push('供货时间:' + tradeDate);
                    if (hasContact) summaryParts.push('有联系人');
                    if (hasTrade) summaryParts.push('贸易记录');
                    if (hasDesc) summaryParts.push('有公司简介');
                    if (hasMatchCount) summaryParts.push('匹配次数');

                    // HS 额外字段
                    let hsTradeCount = 0;
                    let hsSupplierCount = 0;
                    let hsProductDesc = '';
                    const tradeCountMatch = fullText.match(/货运匹配[^：:\n]*[：:]\s*(\d+)/);
                    if (tradeCountMatch) hsTradeCount = parseInt(tradeCountMatch[1]);
                    const supplierMatch = fullText.match(/供应商[^：:\n]*[：:]\s*(\d+)/);
                    if (supplierMatch) hsSupplierCount = parseInt(supplierMatch[1]);
                    const prodDescMatch = fullText.match(/产品描述[：:]\s*([^\n]+)/);
                    if (prodDescMatch) hsProductDesc = prodDescMatch[1].trim().substring(0, 200);

                    // 卡片定位信息：在同级中的索引
                    const parent = cardEl.parentElement;
                    const siblings = parent ? Array.from(parent.children).filter(c => c.tagName === cardEl.tagName) : [];
                    const cardIndex = siblings.indexOf(cardEl) + 1; // 1-based

                    // 提取地区/国家信息（通常在卡片文本的不同位置）
                    let country = '';
                    const countryPatterns = [
                        // 标签格式: 所在地区：XXX / 国家：XXX / 地区：XXX
                        /所在[国家地区域]*[：:]\s*([^\n,，]+)/,
                        /[国家地区][：:]\s*([^\n,，]+)/,
                        // 卡片区标签格式（如 div 中的标签）
                        /(?:region|country|area|location)[：:]*\s*([^，,\n]+)/i,
                        // 腾道卡片常见格式: 国家 - 公司名 或 公司名 - 国家
                        /[\-–—]\s*([^\n]{2,30}?)\s*[\-–—]/,
                    ];
                    for (const pat of countryPatterns) {
                        const m = fullText.match(pat);
                        if (m) {
                            const val = m[1].trim().substring(0, 50);
                            // 排除明显的非国家内容
                            if (!/供货|贸易|联系人|简介|匹配|产品|采购|金额|重量/.test(val)) {
                                country = val;
                                break;
                            }
                        }
                    }

                    cards.push({
                        company_name: companyName.substring(0, 150),
                        trade_date: tradeDate,
                        page_url: enterpriseLink,
                        text_preview: fullText.substring(0, 300),
                        summary: summaryParts.join(', '),
                        country: country,
                        bounding: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                        hs_trade_count: hsTradeCount,
                        hs_supplier_count: hsSupplierCount,
                        hs_product_desc: hsProductDesc,
                        card_index: cardIndex,
                    });
                }

                return { cards, excluded, used_selector: true };
            }""")

            if cards.get("used_selector") and cards["cards"]:
                # 策略 A 成功
                print(f"  [结果] 使用 CSS 选择器定位卡片，找到 {len(cards['cards'])} 个有效候选")
                for exc in cards.get("excluded", []):
                    print(f"  [结果] 排除: {exc.get('reason')} — {exc.get('preview', '')[:80]}")
            else:
                # 策略 A 失败或未找到，回退到旧版 DOM-walking
                print(f"  [结果] CSS 选择器未找到卡片，回退到 DOM-walking 方案")
                cards = page.evaluate(r"""() => {
                    let expectedCount = null;
                    let countEl = null;
                    const allEls = Array.from(document.querySelectorAll('*'));
                    for (const el of allEls) {
                        const t = el.innerText;
                        if (!t) continue;
                        const childHasIt = Array.from(el.children).some(c =>
                            c.innerText && c.innerText.includes('共搜索到'));
                        if (childHasIt) continue;
                        const m = t.match(/共搜索到\s*(\d+)\s*个/);
                        if (m && !countEl) { countEl = el; expectedCount = parseInt(m[1]); }
                        const m2 = t.match(/共找到\s*(\d+)\s*条?结果/);
                        if (m2 && !countEl) { countEl = el; expectedCount = parseInt(m2[1]); }
                    }
                    let resultArea = null;
                    if (countEl) {
                        let container = countEl.parentElement;
                        for (let i = 0; i < 10 && container; i++) {
                            const children = Array.from(container.children).filter(c => {
                                const r = c.getBoundingClientRect();
                                if (r.width < 50 || r.height < 20) return false;
                                const cs = getComputedStyle(c);
                                if (cs.display === 'none' || cs.visibility === 'hidden') return false;
                                return (c.innerText || '').trim().length >= 30;
                            });
                            if (children.length >= 1) { resultArea = container; break; }
                            container = container.parentElement;
                        }
                    }
                    let fallbackCards = [];
                    let excluded = [];
                    if (resultArea) {
                        const children = Array.from(resultArea.children);
                        for (const child of children) {
                            const r = child.getBoundingClientRect();
                            if (r.width < 50 || r.height < 20) continue;
                            const cs = getComputedStyle(child);
                            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                            const text = (child.innerText || '').trim();
                            if (text.length < 10) continue;
                            if (/共搜索到|共找到|条?结果/.test(text) && !/[A-Z]/.test(text)) {
                                excluded.push({ reason: '统计', preview: text.substring(0, 60) }); continue;
                            }
                            if (/历史搜索|最近搜索|热门搜索|猜你喜欢|推荐公司|换一批|查看更多/.test(text)) {
                                excluded.push({ reason: '推荐', preview: text.substring(0, 60) }); continue;
                            }
                            if (/加载中|loading|暂无数据/.test(text)) {
                                excluded.push({ reason: '骨架屏', preview: text.substring(0, 60) }); continue;
                            }
                            // 提取公司名 — 优先用 span[class*='reocrdItem--companyName']
                            const nameEl = child.querySelector('span[class*="reocrdItem--companyName"]');
                            let companyName = nameEl ? (nameEl.innerText || '').trim() : '';
                            if (!companyName || companyName.length < 2) {
                                const lines = text.split('\n').map(l => l.trim()).filter(l => l);
                                for (const line of lines) {
                                    if (line.length >= 3 && /CO|LTD|LLC|CORP|INC|INDUSTRIES|INDUSTRY|LIMITED|COMPANY|GMBH|SA\s|SRL/i.test(line)) {
                                        companyName = line; break;
                                    }
                                }
                                if (!companyName) {
                                    for (const line of lines) {
                                        if (line.length >= 4 && /公司|企业|集团/.test(line)) {
                                            companyName = line; break;
                                        }
                                    }
                                }
                                if (!companyName && lines.length > 0) companyName = lines[0].trim();
                            }
                            if (!companyName || companyName.length < 2 || /^\d+$/.test(companyName)) {
                                excluded.push({ reason: '公司名无效', preview: companyName.substring(0, 60) }); continue;
                            }
                            if (companyName.startsWith('公司简介') || companyName.startsWith('简介')) {
                                excluded.push({ reason: '含"公司简介"前缀', preview: companyName.substring(0, 80) }); continue;
                            }
                            let tradeDate = '';
                            const dateMatch = text.match(/(\d{4}[-/]\d{1,2}[-/]\d{1,2})/);
                            if (dateMatch) tradeDate = dateMatch[1].replace('/', '-');
                            let link = '';
                            const aEls = child.querySelectorAll('a');
                            for (const a of aEls) {
                                const href = a.getAttribute('href') || '';
                                if (/search#\/trade|mode=company|tab=true|import|analysis/i.test(href)) continue;
                                if (/enterprise/.test(href)) {
                                    link = href.startsWith('/') ? 'https://bizr.tendata.cn' + href : href;
                                    break;
                                }
                            }
                            const hasTrade = /贸易|交易|供货|采购商|供应商/.test(text);
                            const hasDesc = /简介|主营|业务|产品/.test(text);
                            const hasMatchCount = /匹配|次数|次/.test(text);
                            const summaryParts = [];
                            if (tradeDate) summaryParts.push('供货时间:' + tradeDate);
                            if (hasTrade) summaryParts.push('贸易记录');
                            if (hasDesc) summaryParts.push('有公司简介');
                            if (hasMatchCount) summaryParts.push('匹配次数');
                            let hsTradeCount = 0, hsSupplierCount = 0, hsProductDesc = '';
                            const tcM = text.match(/货运匹配[^：:\n]*[：:]\s*(\d+)/);
                            if (tcM) hsTradeCount = parseInt(tcM[1]);
                            const sM = text.match(/供应商[^：:\n]*[：:]\s*(\d+)/);
                            if (sM) hsSupplierCount = parseInt(sM[1]);
                            const pdM = text.match(/产品描述[：:]\s*([^\n]+)/);
                            if (pdM) hsProductDesc = pdM[1].trim().substring(0, 200);
                            fallbackCards.push({
                                company_name: companyName.substring(0, 150),
                                trade_date: tradeDate,
                                page_url: link,
                                text_preview: text.substring(0, 300),
                                summary: summaryParts.join(', '),
                                bounding: { x: r.x, y: r.y, w: r.width, h: r.height },
                                hs_trade_count: hsTradeCount,
                                hs_supplier_count: hsSupplierCount,
                                hs_product_desc: hsProductDesc,
                            });
                        }
                    }
                    return { cards: fallbackCards, excluded, used_selector: false };
                }""")

            # ── 构建 SearchResult 对象 ──
            js_cards = cards.get("cards", [])
            for idx, card in enumerate(js_cards[:20]):
                b = card.get("bounding", {})
                page_url = card.get("page_url", "")
                # 从 URL 提取国家代码（如 &country=USA, &country=KAZ）
                url_country = ""
                if page_url:
                    country_match = re.search(r'[?&]country=([A-Z]{3})', page_url)
                    if country_match:
                        code = country_match.group(1)
                        url_country = _COUNTRY_CODE_TO_NAME.get(code, "")

                country_from_card = card.get("country", "")
                country = country_from_card if country_from_card else url_country

                result = SearchResult(
                    company_name=card["company_name"],
                    rank=idx + 1,
                    recent_trade_date=card["trade_date"],
                    page_url=page_url,
                    company_brief=card["text_preview"][:100],
                    country=country,
                    contact_count=0,
                    bounding_x=b.get("x", 0),
                    bounding_y=b.get("y", 0),
                    hs_trade_count=card.get("hs_trade_count", 0),
                    hs_supplier_count=card.get("hs_supplier_count", 0),
                    hs_product_desc=card.get("hs_product_desc", ""),
                    card_index=card.get("card_index", 0),
                )
                results.append(result)
                summary = card.get("summary", "")
                print(f"  [结果] #{idx+1}: '{result.company_name[:50]}' 日期='{result.recent_trade_date}' 摘要='{summary}'")

            # 去重
            seen = set()
            unique_results = []
            for r in results:
                norm = r.company_name.lower().strip()
                if norm not in seen:
                    seen.add(norm)
                    unique_results.append(r)
            results = unique_results

            print(f"  [结果] 过滤后有效候选数: {len(results)}")
            if results:
                print(f"  [结果] 第一条公司名: '{results[0].company_name}'")

            return results

        except Exception as e:
            print(f"  [结果] 提取异常: {e}")
            return []
    def go_to_detail(self, result: SearchResult, expected_company_name: str = "", name_similarity: float = 0.0) -> bool:
        """进入候选企业详情页 — 多策略点击入口。

        已知 DOM 结构：
        - 卡片根: div[class*='reocrdItem--tradeRecordItem']
        - 公司名信息块: div[class*='reocrdItem--companyNameInfo']
        - 公司名文本: span[class*='reocrdItem--companyName']

        expected_company_name: 如果传入，用它来在当前页定位正确的卡片，
        并在进入详情页后校验公司名是否匹配。

        name_similarity: 候选名称相似度，用于判断是否需要重试

        成功条件：URL 进入 enterprise#/base-info 或 enterprise#/company 或 enterprise#/import-analysis，
                 或页面出现 overviewReport 统计卡片/贸易数据。
        """
        try:
            page = self.page
            original_url = page.url
            target_name = expected_company_name or result.company_name
            is_high_match = name_similarity >= 0.80

            if is_high_match:
                print(f"  [详情] 高分候选 (sim={name_similarity:.2f})，将尝试多种方式进入详情页")

            # ── 策略 0: 如果 page_url 已包含 enterprise，直接 goto ──
            if result.page_url and "enterprise" in result.page_url:
                print(f"  [详情] result.page_url 包含 enterprise，直接跳转")
                print(f"  [详情] result.page_url: {result.page_url}")
                page.goto(result.page_url, timeout=self.config["load_timeout"])
                print(f"  [详情] final_active_page_url: {page.url}")
                return self._verify_detail_page_with_name(page, original_url, target_name)

            # ── 策略 1: 尝试从卡片中提取企业详情 URL 并直接跳转 ──
            direct_url = page.evaluate(r"""({companyName}) => {
                const cardEls = document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]');
                for (const cardEl of cardEls) {
                    const nameEl = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                    if (!nameEl) continue;
                    const nameText = (nameEl.innerText || '').trim().toLowerCase();
                    if (nameText !== companyName.toLowerCase().trim()) continue;

                    // 查找卡片中的所有链接
                    const links = cardEl.querySelectorAll('a[href*="enterprise"]');
                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        if (href.includes('enterprise#/base-info') || href.includes('enterprise#/company')) {
                            return { found: true, url: href, method: 'link_in_card' };
                        }
                    }

                    // 查找点击事件可能跳转的 URL
                    const onclick = cardEl.getAttribute('onclick') || '';
                    if (onclick.includes('enterprise')) {
                        const match = onclick.match(/['"]([^'"]*enterprise[^'"]*)['"]/);
                        if (match) return { found: true, url: match[1], method: 'onclick_attr' };
                    }

                    // 从 data 属性中查找
                    for (const attr of cardEl.attributes || []) {
                        if (attr.value && attr.value.includes('enterprise') && attr.value.includes('base-info')) {
                            return { found: true, url: attr.value, method: 'data_attr' };
                        }
                    }
                }
                return { found: false };
            }""", {"companyName": target_name})

            if direct_url.get("found"):
                url_to_go = direct_url.get("url", "")
                if url_to_go.startswith("http"):
                    pass  # 完整 URL
                elif url_to_go.startswith("/"):
                    url_to_go = "https://bizr.tendata.cn" + url_to_go
                elif url_to_go.startswith("enterprise"):
                    url_to_go = "https://bizr.tendata.cn/" + url_to_go
                else:
                    url_to_go = ""

                if url_to_go:
                    print(f"  [详情] 从卡片提取到 enterprise URL，直接跳转: {url_to_go[:100]}")
                    try:
                        page.goto(url_to_go, timeout=self.config["load_timeout"])
                        print(f"  [详情] final_active_page_url: {page.url}")
                        if self._verify_detail_page_with_name(page, original_url, target_name):
                            return True
                        print(f"  [详情] 直接跳转后验证失败，尝试点击方式")
                    except Exception as e:
                        print(f"  [详情] 直接跳转失败: {e}")

            # ── 策略 2: 用公司名在当前页找到正确的卡片并点击 ──
            if target_name:
                click_result = page.evaluate(r"""({companyName}) => {
                    const cardEls = document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]');
                    if (cardEls.length === 0) return { ok: false, reason: 'no_cards_found' };

                    for (let i = 0; i < cardEls.length; i++) {
                        const cardEl = cardEls[i];
                        const nameEl = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                        if (!nameEl) continue;
                        const nameText = (nameEl.innerText || '').trim();
                        if (!nameText) continue;

                        const normA = companyName.toLowerCase().trim();
                        const normB = nameText.toLowerCase().trim();
                        // 严格匹配：完全相等
                        const matched = (normA === normB);

                        if (!matched) continue;

                        const cs = getComputedStyle(cardEl);
                        if (cs.display === 'none' || cs.visibility === 'hidden') continue;

                        const nr = nameEl.getBoundingClientRect();
                        if (nr.width > 0 && nr.height > 0) {
                            nameEl.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
                            nameEl.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                            nameEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                            nameEl.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
                            nameEl.click();
                            return { ok: true, card_index: i + 1, matched_name: nameText, method: 'click_by_company_name' };
                        }

                        const blockEl = cardEl.querySelector('div[class*="reocrdItem--companyNameInfo"]');
                        if (blockEl) {
                            const br = blockEl.getBoundingClientRect();
                            if (br.width > 0 && br.height > 0) {
                                blockEl.click();
                                return { ok: true, card_index: i + 1, matched_name: nameText, method: 'click_block_by_name' };
                            }
                        }

                        cardEl.click();
                        return { ok: true, card_index: i + 1, matched_name: nameText, method: 'click_card_by_name' };
                    }

                    const allNames = [];
                    for (const cardEl of cardEls) {
                        const ne = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                        if (ne) allNames.push((ne.innerText || '').trim());
                    }
                    return { ok: false, reason: 'company_name_not_found_in_cards', expected: companyName, found_names: allNames };
                }""", {"companyName": target_name})

                if click_result.get("ok"):
                    actual_card = click_result.get("card_index", "?")
                    matched = click_result.get("matched_name", "")
                    print(f"  [详情] 使用公司名定位: 目标='{target_name[:60]}', 匹配='{matched[:60]}', card_index={actual_card}")
                    if self._wait_for_detail_tab(page, original_url, target_name):
                        return True
                    print(f"  [详情] 第一次点击失败，尝试其他方式...")
                else:
                    reason = click_result.get("reason", "unknown")
                    found = click_result.get("found_names", [])
                    print(f"  [详情] 按公司名定位失败: reason={reason}, 找到卡片公司名={found}")
                    print(f"  [详情] 目标公司名: '{target_name[:60]}'")

            # ── 策略 3: 如果有 card_index，用 DOM 索引直接定位并点击 ──
            if result.card_index > 0:
                # 直接用 JS 找到对应卡片的 span[class*='reocrdItem--companyName'] 并 dispatchEvent('click')
                click_ok = page.evaluate(r"""({cardIndex}) => {
                    const cardEls = document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]');
                    if (cardEls.length < cardIndex) return false;

                    const cardEl = cardEls[cardIndex - 1]; // 1-based
                    const cs = getComputedStyle(cardEl);
                    if (cs.display === 'none' || cs.visibility === 'hidden') return false;

                    // 优先点击 span[class*='reocrdItem--companyName']
                    const nameEl = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                    if (nameEl) {
                        const nr = nameEl.getBoundingClientRect();
                        if (nr.width > 0 && nr.height > 0) {
                            nameEl.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
                            nameEl.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                            nameEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                            nameEl.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
                            nameEl.click();
                            return true;
                        }
                    }

                    // 回退：点击 div[class*='reocrdItem--companyNameInfo']
                    const blockEl = cardEl.querySelector('div[class*="reocrdItem--companyNameInfo"]');
                    if (blockEl) {
                        const br = blockEl.getBoundingClientRect();
                        if (br.width > 0 && br.height > 0) {
                            blockEl.click();
                            return true;
                        }
                    }

                    // 兜底：点击卡片根节点
                    cardEl.click();
                    return true;
                }""", {"cardIndex": result.card_index})

                if click_ok:
                    print(f"  [详情] 使用 card_index={result.card_index} 定位并点击公司名")
                    if self._wait_for_detail_tab(page, original_url, target_name):
                        return True
                    print(f"  [详情] card_index 点击失败，尝试其他方式...")
                else:
                    print(f"  [详情] card_index={result.card_index} 定位失败: 卡片不存在或隐藏，回退到文本搜索")

            # ── 策略 4: 尝试点击详情/复制按钮旁边的可进入区域 ──
            detail_button_click = page.evaluate(r"""({companyName}) => {
                const cardEls = document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]');
                for (const cardEl of cardEls) {
                    const nameEl = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                    if (!nameEl) continue;
                    const nameText = (nameEl.innerText || '').trim().toLowerCase();
                    if (nameText !== companyName.toLowerCase().trim()) continue;

                    // 查找"详情"、"查看详情"、"复制"等按钮
                    const buttons = cardEl.querySelectorAll('button, [class*="btn"], [class*="button"], [role="button"]');
                    for (const btn of buttons) {
                        const btnText = (btn.innerText || '').trim().toLowerCase();
                        if (btnText.includes('详情') || btnText.includes('查看') || btnText.includes('detail')) {
                            btn.click();
                            return { ok: true, method: 'click_detail_button' };
                        }
                    }

                    // 查找带 cursor:pointer 的可点击元素
                    const clickableEls = cardEl.querySelectorAll('*');
                    for (const el of clickableEls) {
                        const cs = getComputedStyle(el);
                        if (cs.cursor === 'pointer' && el !== cardEl) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 20 && r.width < 200 && r.height < 100) {
                                // 排除公司名元素（已经在策略2尝试过）
                                if (!el.className || !el.className.includes('companyName')) {
                                    el.click();
                                    return { ok: true, method: 'click_pointer_element' };
                                }
                            }
                        }
                    }
                }
                return { ok: false };
            }""", {"companyName": target_name})

            if detail_button_click.get("ok"):
                print(f"  [详情] 点击详情按钮: {detail_button_click.get('method')}")
                page.wait_for_timeout(2000)
                if self._wait_for_detail_tab(page, original_url, target_name):
                    return True
                print(f"  [详情] 详情按钮点击失败，尝试其他方式...")

            # ── 策略 5: 双击卡片 ──
            print(f"  [详情] 尝试双击卡片...")
            double_click_result = page.evaluate(r"""({companyName}) => {
                const cardEls = document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]');
                for (const cardEl of cardEls) {
                    const nameEl = cardEl.querySelector('span[class*="reocrdItem--companyName"]');
                    if (!nameEl) continue;
                    const nameText = (nameEl.innerText || '').trim().toLowerCase();
                    if (nameText !== companyName.toLowerCase().trim()) continue;

                    const cs = getComputedStyle(cardEl);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;

                    const r = cardEl.getBoundingClientRect();
                    return { found: true, x: r.x + r.width / 2, y: r.y + r.height / 2 };
                }
                return { found: false };
            }""", {"companyName": target_name})

            if double_click_result.get("found"):
                pos = double_click_result
                page.mouse.dblclick(pos["x"], pos["y"])
                page.wait_for_timeout(2000)
                if self._wait_for_detail_tab(page, original_url, target_name):
                    return True
                print(f"  [详情] 双击卡片失败，尝试其他方式...")

            # ── 回退：按公司名文本搜索 ──
            company_name = result.company_name
            print(f"  [详情] 回退：按公司名文本查找入口: '{company_name}'")

            targets = page.evaluate(r"""(name) => {
                // 策略 A: 通过已知 CSS 选择器定位卡片
                let cardEl = null;
                for (const el of document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]')) {
                    const innerText = (el.innerText || '').trim();
                    if (innerText.includes(name) && el.innerText.length < 2000) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 100 && r.height > 30) {
                            cardEl = el;
                            break;
                        }
                    }
                }

                if (cardEl) {
                    const cardRect = cardEl.getBoundingClientRect();
                    let nameTarget = null;
                    const nameEls = cardEl.querySelectorAll('span[class*="reocrdItem--companyName"]');
                    if (nameEls.length > 0) nameTarget = nameEls[0];
                    const nameRect = nameTarget ? nameTarget.getBoundingClientRect() : null;

                    let blockTarget = null;
                    const blockEls = cardEl.querySelectorAll('div[class*="reocrdItem--companyNameInfo"]');
                    if (blockEls.length > 0) blockTarget = blockEls[0];
                    const blockRect = blockTarget ? blockTarget.getBoundingClientRect() : null;

                    return {
                        card_found: true,
                        method: 'css_selector',
                        card_rect: { x: cardRect.x + cardRect.width / 2, y: cardRect.y + cardRect.height / 2 },
                        name_center: nameRect && nameRect.width > 0 && nameRect.height > 0
                            ? { x: nameRect.x + nameRect.width / 2, y: nameRect.y + nameRect.height / 2 }
                            : null,
                        block_center: blockRect && blockRect.width > 0 && blockRect.height > 0
                            ? { x: blockRect.x + blockRect.width / 2, y: blockRect.y + blockRect.height / 2 }
                            : null,
                    };
                }

                // 策略 B: CSS 选择器失败时，用公司名文本直接定位可点击元素
                console.log('[go_to_detail] CSS selector failed, falling back to text-based search');
                const allEls = Array.from(document.querySelectorAll('*'));
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    if (!t.includes(name)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 80 || r.height < 20) continue;
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    // 排除过大的容器（取最小包裹元素）
                    if (r.width > 800 && r.height > 400) continue;
                    // 优先取有交互特征的子元素
                    const isInteractive = el.tagName === 'A' || el.tagName === 'BUTTON'
                        || cs.cursor === 'pointer'
                        || el.getAttribute('role') === 'link';
                    if (isInteractive) {
                        console.log('[go_to_detail] fallback: found interactive element containing company name');
                        return {
                            card_found: true,
                            method: 'text_fallback_interactive',
                            card_rect: { x: r.x + r.width / 2, y: r.y + r.height / 2 },
                            name_center: null,
                            block_center: null,
                        };
                    }
                }
                // 最后兜底：取包含公司名的最小可见叶子元素
                let bestEl = null;
                let bestArea = Infinity;
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    if (!t.includes(name)) continue;
                    if (el.children.length > 0) continue; // 跳过有子元素的容器
                    const r = el.getBoundingClientRect();
                    if (r.width < 50 || r.height < 10) continue;
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    const area = r.width * r.height;
                    if (area < bestArea) { bestArea = area; bestEl = r; }
                }
                if (bestEl) {
                    console.log('[go_to_detail] fallback: found leaf text element');
                    return {
                        card_found: true,
                        method: 'text_fallback_leaf',
                        card_rect: { x: bestEl.x + bestEl.width / 2, y: bestEl.y + bestEl.height / 2 },
                        name_center: null,
                        block_center: null,
                    };
                }

                return {
                    card_found: false,
                    method: 'none',
                    reason: 'CSS selector 和文本兜底均未找到可点击元素',
                };
            }""", company_name)

            if not targets.get("card_found"):
                print(f"  [详情] company_name_click_target_found = false")
                print(f"  [详情] company_name_block_found = false")
                print(f"  [详情] card_root_found = false")
                print(f"  [详情] 未找到搜索结果卡片: {targets.get('reason', '未知')}")
                return False

            print(f"  [详情] company_name_click_target_found = {targets.get('name_center') is not None}")
            print(f"  [详情] company_name_block_found = {targets.get('block_center') is not None}")
            print(f"  [详情] card_root_found = True")

            # ── 构建点击队列（按优先级排序） ──
            click_queue = []
            if targets.get("name_center"):
                click_queue.append(("company_name_span", targets["name_center"]))
            if targets.get("block_center"):
                click_queue.append(("company_name_block", targets["block_center"]))
            click_queue.append(("card_root", targets["card_rect"]))

            print(f"  [详情] 点击队列: {[t[0] for t in click_queue]}")

            # ── 逐层点击 ──
            clicked_target_type = ""
            for target_type, pos in click_queue:
                clicked_target_type = target_type
                print(f"  [详情] 尝试点击: {target_type} ({pos['x']:.0f}, {pos['y']:.0f})")

                # 记录点击前的 URL 和已有页面
                url_before_click = page.url
                existing_urls = {p.url for p in self.context.pages} if self.context else set()

                page.mouse.click(pos["x"], pos["y"])
                page.wait_for_timeout(1200)

                # 检查当前页 URL 是否变化
                current_url = page.url
                print(f"  [详情] current_url_after_click: {current_url}")

                # 如果 URL 已变成 enterprise#/，直接成功
                if "enterprise#/base-info" in current_url or "enterprise#/company" in current_url:
                    print(f"  [详情] 当前页已跳转到 enterprise 详情页")
                    print(f"  [详情] clicked_target_type: {clicked_target_type}")
                    print(f"  [详情] final_active_page_url: {current_url}")
                    return self._verify_detail_page(page, original_url)

                # 检查是否有新标签页
                if self.context:
                    time.sleep(0.5)
                    new_page_found = False
                    for p in self.context.pages:
                        if p.url in existing_urls:
                            continue
                        if p.url == "about:blank":
                            continue
                        if p.url == url_before_click:
                            continue

                        print(f"  [详情] new_page_url_if_any: {p.url}")

                        # 白名单校验
                        lower_url = p.url.lower()
                        if (lower_url.startswith("chrome://") or
                            lower_url.startswith("devtools://") or
                            lower_url.startswith("edge://") or
                            lower_url.startswith("about:") or
                            lower_url.startswith("chrome-extension://")):
                            print(f"  [详情] 忽略内部页面: {p.url}")
                            continue

                        if "tendata.cn" not in p.url:
                            print(f"  [详情] 忽略非 tendata.cn 页面: {p.url}")
                            continue

                        if "enterprise#/" not in p.url:
                            print(f"  [详情] 新页不是 enterprise#/ 页面，保持原页: {p.url}")
                            continue

                        print(f"  [详情] candidate_new_page_accepted = true")
                        self.page = p
                        page = p
                        new_page_found = True
                        break

                    if new_page_found:
                        print(f"  [详情] 已接管 enterprise 新标签页")
                        print(f"  [详情] clicked_target_type: {clicked_target_type}")
                        print(f"  [详情] final_active_page_url: {page.url}")
                        return self._verify_detail_page(page, original_url)

                # 如果当前 URL 仍在 search#/ 或变成 trade#/，说明这层点击无效，尝试下一个
                if "search#/" in current_url or "trade#/" in current_url or "trade?value=" in current_url:
                    print(f"  [详情] URL 仍在搜索/贸易页，尝试下一层点击...")
                    continue

            # 所有点击都尝试过，仍未进入 enterprise 详情页
            print(f"  [详情] 所有点击目标均未跳转到 enterprise 详情页")
            print(f"  [详情] clicked_target_type: {clicked_target_type}")
            print(f"  [详情] final_active_page_url: {page.url}")
            return self._verify_detail_page(page, original_url)

        except PlaywrightTimeout:
            print("  [详情] 进入详情页超时")
            return False
        except Exception as e:
            print(f"  [详情] 进入异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _wait_for_detail_tab(self, page, original_url: str, target_name: str) -> bool:
        """捕获点击卡片后新打开的 enterprise 标签页。

        调用方应在点击前关闭所有 enterprise 标签，
        这样点击后出现的 enterprise 标签就是唯一的、目标公司页。
        """
        print(f"  [详情] 等待新标签页打开（目标公司: {target_name[:60]}）")
        print(f"  [详情] 点击前当前页 URL: {page.url}")

        context = page.context

        # 等待 3s 让新标签完全打开（点击已在 evaluate 中完成）
        page.wait_for_timeout(3000)

        # 查找所有 enterprise 页
        enterprise_pages = [p for p in context.pages if "enterprise" in p.url.lower()]

        print(f"  [详情] 当前共 {len(enterprise_pages)} 个 enterprise 页")
        for i, p in enumerate(enterprise_pages):
            try:
                print(f"  [详情]   enterprise[{i}] URL: {p.url[:120]}")
            except Exception:
                print(f"  [详情]   enterprise[{i}] URL: (无法读取)")

        if not enterprise_pages:
            # 没有新标签，可能当前页已导航
            print(f"  [详情] 无 enterprise 页，用当前页验证")
            print(f"  [详情] 当前页 URL: {page.url}")
            return self._verify_detail_page_with_name(page, original_url, target_name)

        # 取最后一个（最新打开的）enterprise 页
        detail_page = enterprise_pages[-1]
        print(f"  [详情] 选择最新 enterprise 页: {detail_page.url[:120]}")

        try:
            detail_page.wait_for_load_state("domcontentloaded", timeout=5000)
            detail_page.wait_for_timeout(2000)
        except Exception:
            pass

        self.page = detail_page
        detail_page.bring_to_front()
        print(f"  [详情] 已切换到 enterprise 页")
        return self._verify_detail_page_with_name(detail_page, original_url, target_name)

    def _verify_detail_page_with_name(self, page, original_url: str, expected_name: str) -> bool:
        """验证是否成功进入详情页，且详情页公司名与目标一致。

        详情页成功判定规则（放宽）：
        - URL 包含 enterprise#/base-info 或 enterprise#/company
        - URL 包含 enterprise#/import-analysis（进口分析页）
        - 页面包含统计卡片 overviewReport
        - 页面有最近一次进口记录 / 供应商 / 贸易次数 / 贸易记录表
        """
        current_url = page.url
        current_title = page.title()
        print(f"  [详情] current_url_after_click: {current_url}")
        print(f"  [详情] 页面标题: {current_title}")

        if "search#/" in current_url:
            print(f"  [详情] detail_page_confirmed = false (URL 仍在 search#/...)")
            return False

        if "trade?value=" in current_url or "trade#/index" in current_url:
            print(f"  [详情] detail_page_confirmed = false (URL 包含 trade 特征)")
            return False

        # 新增：检测页面是否有统计卡片或贸易数据特征
        has_trade_data = page.evaluate(r"""() => {
            // 检测 overviewReport 统计卡片
            const overviewCards = document.querySelectorAll('[class*="overviewReport"], [class*="OverviewReport"]');
            if (overviewCards.length > 0) return { found: true, reason: 'overviewReport 卡片存在' };

            // 检测贸易次数/贸易记录相关元素
            const tradeKeywords = ['贸易次数', '进口次数', '交易次数', '最近进口', '供应商', '采购商'];
            const allText = document.body.innerText || '';
            for (const kw of tradeKeywords) {
                if (allText.includes(kw)) return { found: true, reason: `发现关键词: ${kw}` };
            }

            // 检测贸易明细表格
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const headerText = table.innerText || '';
                if (headerText.includes('日期') || headerText.includes('供应商') || headerText.includes('采购商')) {
                    return { found: true, reason: '发现贸易明细表' };
                }
            }

            return { found: false, reason: 'no_trade_data' };
        }""")

        if has_trade_data.get("found"):
            print(f"  [详情] 检测到贸易数据特征: {has_trade_data.get('reason')}")
            # 如果检测到贸易数据，即使不是 base-info 页面，也视为详情页成功
            if "enterprise" in current_url.lower() or "import-analysis" in current_url.lower():
                print(f"  [详情] detail_page_confirmed = true (检测到贸易数据特征)")
                return True

        detail_indicators = [
            "enterprise#/base-info", "enterprise#/company", "enterprise#/import-analysis",
            "基本信息", "企业联系方式",
            "公司网址", "公司电话", "公司所在地",
            "经营状态", "联系人",
        ]

        if "enterprise" in current_url.lower():
            print(f"  [详情] URL 包含 enterprise 特征")
            # 新增：如果是 import-analysis 页面且有贸易数据，直接成功
            if "import-analysis" in current_url.lower():
                print(f"  [详情] URL 包含 import-analysis，判定为详情页成功")
                print(f"  [详情] detail_page_confirmed = true")
                return True

            # 提取详情页公司名并校验
            actual_name = page.evaluate(r"""() => {
                const titleEls = document.querySelectorAll('h1, h2, [class*="companyName"], [class*="CompanyName"], [class*="companyNameLabel"]');
                for (const el of titleEls) {
                    const t = (el.innerText || '').trim();
                    if (t.length > 2 && t.length < 200) return t;
                }
                const bodyText = (document.body.innerText || '');
                const lines = bodyText.split('\n').map(l => l.trim()).filter(l => l.length > 3 && l.length < 200);
                for (const line of lines) {
                    if (/CO|LTD|LLC|CORP|INC|INDUSTRIES|LIMITED|COMPANY|GMBH|公司|企业/i.test(line)) {
                        return line;
                    }
                }
                return '';
            }""")

            if expected_name and actual_name:
                norm_expected = expected_name.lower().strip()
                # 去掉尾部"更新"后缀（详情页公司名常带此尾缀）
                norm_actual = actual_name.lower().strip()
                if norm_actual.endswith("更新"):
                    norm_actual = norm_actual[:-2].strip()
                # 放宽匹配：允许高相似度匹配（>=0.85）
                name_sim = _name_similarity(norm_expected, norm_actual) if '_name_similarity' in dir() else (norm_expected == norm_actual)
                if norm_expected == norm_actual or (isinstance(name_sim, float) and name_sim >= 0.85):
                    print(f"  [详情] detail_page_confirmed = true, 公司名匹配: '{actual_name[:60]}' = 目标 '{expected_name[:60]}'")
                    return True
                else:
                    # 名称不匹配但有贸易数据，也允许通过
                    if has_trade_data.get("found"):
                        print(f"  [详情] 公司名不完全匹配但有贸易数据，允许进入")
                        print(f"  [详情] detail_page_confirmed = true (有贸易数据)")
                        return True
                    print(f"  [详情] detail_page_confirmed = false (公司名不匹配)")
                    print(f"  [详情] 目标公司名: '{expected_name[:80]}'")
                    print(f"  [详情] 实际详情页公司名: '{actual_name[:80]}'")
                    return False

            print(f"  [详情] detail_page_confirmed = true")
            return True

        for indicator in detail_indicators:
            try:
                el = page.query_selector(f"text={indicator}")
                if el and el.is_visible():
                    print(f"  [详情] 页面包含详情特征: '{indicator}'")
                    print(f"  [详情] detail_page_confirmed = true")
                    return True
            except Exception:
                continue

        try:
            body_text = page.inner_text("body")
            if 500 < len(body_text) < 50000:
                detail_keywords = ["基本信息", "联系方式", "公司网址", "公司电话",
                                   "公司所在地", "经营状态", "联系人", "进口", "供应商",
                                   "贸易次数", "进口次数", "交易次数"]
                if any(kw in body_text for kw in detail_keywords):
                    print(f"  [详情] 页面文本长度合理 ({len(body_text)} 字符) 且包含详情关键词，判定已进入详情页")
                    print(f"  [详情] detail_page_confirmed = true")
                    return True
        except Exception:
            pass

        print(f"  [详情] detail_page_confirmed = false (未检测到任何详情页特征)")
        return False

    def _verify_detail_page(self, page, original_url: str) -> bool:
        """验证是否成功进入详情页（旧版，不校验公司名）。"""
        return self._verify_detail_page_with_name(page, original_url, "")

    def _cleanup_tabs(self) -> dict:
        """清理多余标签页，只保留 1 个主工作页。

        规则：
        - 关闭所有 enterprise#/ 详情页
        - 关闭 about:blank 空白页
        - 关闭 chrome:// / devtools:// 内部页
        - 保留最早的 bizr.tendata.cn/search 页面作为主工作页
        - 如果无搜索页，保留最早的 bizr.tendata.cn 页面
        - 【V5 新增】永远至少保留一个 bizr.tendata.cn 页面，避免 pages=0

        Returns:
            {"closed_count": int, "remaining_count": int, "main_page_url": str}
        """
        try:
            context = self.page.context
            all_pages = list(context.pages)
            initial_count = len(all_pages)
        except Exception as e:
            print(f"  [清理] 获取 context/pages 异常: {e}")
            return {"closed_count": 0, "remaining_count": 0, "main_page_url": "(none)"}

        internal_prefixes = ("chrome://", "devtools://", "about:blank")

        try:
            # 第一步：找出必须保留的 bizr 页面（至少一个）
            bizr_pages = [p for p in all_pages if "bizr.tendata.cn" in p.url.lower()]
            if not bizr_pages:
                # 没有 bizr 页面，尝试保留任意非内部页
                bizr_pages = [p for p in all_pages if not any(p.url.startswith(prefix) for prefix in internal_prefixes)]

            # 确定主页面（必须保留）
            if bizr_pages:
                search_pages = [p for p in bizr_pages if "search" in p.url.lower() or "trade" in p.url.lower()]
                main_page = search_pages[0] if search_pages else bizr_pages[0]
            else:
                main_page = all_pages[0] if all_pages else None

            # 第二步：关闭内部页面（但确保不关闭 main_page）
            for p in all_pages:
                if p is main_page:
                    continue
                try:
                    url = p.url
                    if any(url.startswith(prefix) for prefix in internal_prefixes):
                        p.close()
                except Exception:
                    pass

            # 刷新页面列表
            try:
                all_pages = list(context.pages)
            except Exception:
                all_pages = []

            # 第三步：关闭所有 enterprise 详情页（但确保不关闭 main_page）
            enterprise_pages = [p for p in all_pages if "enterprise" in p.url.lower() and p is not main_page]
            for p in enterprise_pages:
                try:
                    p.close()
                except Exception:
                    pass

            # 刷新页面列表
            try:
                all_pages = list(context.pages)
            except Exception:
                all_pages = []

            # 第四步：如果仍有多个 bizr 页面，只保留最早的搜索页
            bizr_pages = [p for p in all_pages if "bizr.tendata.cn" in p.url.lower()]
            if len(bizr_pages) > 1:
                search_pages = [p for p in bizr_pages if "search" in p.url.lower() or "trade" in p.url.lower()]
                if search_pages:
                    main_page = search_pages[0]  # 最早的搜索页
                else:
                    main_page = bizr_pages[0]    # 最早的 bizr 页

                for p in bizr_pages:
                    if p is not main_page:
                        try:
                            p.close()
                        except Exception:
                            pass
            elif bizr_pages:
                main_page = bizr_pages[0]
            else:
                main_page = all_pages[0] if all_pages else None

            # 【V5 新增】检查清理后是否还有页面
            try:
                remaining = list(context.pages)
            except Exception:
                remaining = []

            if not remaining:
                # 所有页面都被关闭了，需要重建主页面
                print(f"  [清理] 警告: 清理后 pages=0，尝试重建主页面")
                try:
                    new_page = context.new_page()
                    new_page.goto("https://bizr.tendata.cn/search#/index", timeout=15000)
                    self.page = new_page
                    main_page = new_page
                    remaining = [new_page]
                except Exception as e:
                    print(f"  [清理] 重建主页面失败: {e}")
                    return {"closed_count": initial_count, "remaining_count": 0, "main_page_url": "(none)"}

            if main_page:
                self.page = main_page
                try:
                    main_page.bring_to_front()
                except Exception:
                    pass

            closed_count = initial_count - len(remaining)

            return {
                "closed_count": closed_count,
                "remaining_count": len(remaining),
                "main_page_url": main_page.url if main_page else "(none)",
            }

        except Exception as e:
            print(f"  [清理] _cleanup_tabs 异常: {e}")
            return {"closed_count": 0, "remaining_count": -1, "main_page_url": "(error)"}

    def _force_cleanup_tabs(self) -> dict:
        """激进清理：关闭所有非搜索首页的 bizr 页面，只保留最早的 search#/index 页面。

        Returns:
            {"closed_count": int, "remaining_count": int, "main_page_url": str}
        """
        try:
            context = self.page.context
            all_pages = list(context.pages)
            initial_count = len(all_pages)

            # 找最早的 search#/index 页面
            main_page = None
            for p in all_pages:
                try:
                    url = p.url.lower()
                    if "bizr.tendata.cn" in url and ("search" in url or "trade" in url):
                        if "index" in url or not main_page:
                            main_page = p
                            if "index" in url:
                                break
                except Exception:
                    pass

            if not main_page and all_pages:
                # 兜底：保留第一个非 about:blank 页面
                for p in all_pages:
                    try:
                        if not p.url.startswith("about:blank"):
                            main_page = p
                            break
                    except Exception:
                        pass

            # 关闭所有非主页面
            for p in all_pages:
                try:
                    if p is not main_page:
                        p.close()
                except Exception:
                    pass

            if main_page:
                self.page = main_page
                try:
                    main_page.bring_to_front()
                except Exception:
                    pass

            remaining = [p for p in context.pages]
            return {
                "closed_count": initial_count - len(remaining),
                "remaining_count": len(remaining),
                "main_page_url": main_page.url if main_page else "(none)",
            }
        except Exception as e:
            print(f"  [清理] _force_cleanup_tabs 异常: {e}")
            return {"closed_count": 0, "remaining_count": -1, "main_page_url": "(error)"}

    def close_detail_tab(self, search_url: str) -> bool:
        """关闭详情页标签，切回搜索页。

        修复：强制关闭所有 enterprise 标签页（不再受"只剩一个不关"的限制），
        避免多家公司深挖时旧标签干扰新公司。

        规则：
        - 查找所有 enterprise 页面标签
        - 全部关闭（无论数量）
        - 关闭后切回搜索页
        """
        try:
            context = self.page.context
            all_pages = list(context.pages)
            current_url = self.page.url

            # 查找所有 enterprise 页
            enterprise_pages = [p for p in all_pages if "enterprise" in p.url.lower()]
            if not enterprise_pages:
                print(f"  [标签管理] 无 enterprise 页，无需清理")
                return False

            print(f"  [标签管理] 发现 {len(enterprise_pages)} 个 enterprise 页，全部关闭")
            for p in enterprise_pages:
                try:
                    p.close()
                except Exception:
                    pass

            # 切回搜索页：找包含 search 或 trade 的标签
            remaining = [p for p in context.pages if p.url != "about:blank"]
            search_page = None
            for p in remaining:
                if "search" in p.url.lower() or "trade" in p.url.lower():
                    search_page = p
                    break
            if not search_page and remaining:
                search_page = remaining[0]

            if search_page:
                self.page = search_page
                search_page.bring_to_front()
                remaining_count = len([p for p in context.pages if p.url != "about:blank"])
                print(f"  [标签管理] 已关闭所有 enterprise 页，剩余标签数: {remaining_count}")
                print(f"  [标签管理] 已切回搜索页 URL: {search_page.url[:100]}")
                return True
            else:
                print(f"  [标签管理] 未找到可切回的标签页")
                return False

        except Exception as e:
            print(f"  [标签管理] 关闭详情页异常: {e}")
            return False

    def _close_all_enterprise_tabs(self) -> int:
        """关闭所有 enterprise 标签页（不切回搜索页）。

        在每次点击卡片进入详情页前调用，确保之后只会出现
        本次目标公司的 enterprise 页，避免旧标签干扰。

        Returns:
            关闭的标签页数
        """
        try:
            context = self.page.context
            enterprise_pages = [p for p in context.pages if "enterprise" in p.url.lower()]
            if not enterprise_pages:
                return 0

            print(f"  [标签管理] 清理遗留 enterprise 页: {len(enterprise_pages)} 个")
            for p in enterprise_pages:
                try:
                    p.close()
                except Exception:
                    pass

            # 确保 scraper.page 仍指向搜索页
            remaining = [p for p in context.pages if p.url != "about:blank"]
            if remaining and self.page not in context.pages:
                self.page = remaining[0]
            return len(enterprise_pages)
        except Exception as e:
            print(f"  [标签管理] 清理 enterprise 页异常: {e}")
            return 0

    def _extract_basic_info(self) -> dict:
        """按真实 DOM 提取基本信息区字段。

        真实结构（base-info 页）：
        - 公司名称：div[class*='companyNameLabel--text']
        - 基本信息表：div[class*='companyBaseView--CompanyBaseView']
          > div[class*='companyBaseView--baseTable']
          > div[class*='companyBaseView--item']
          > div[class*='companyBaseView--label'] + div[class*='companyBaseView--value']
        - 所在地：div[class*='companyPreview--companyLocation']
        - 网址：span[class*='companyPreview--websiteaddress']
        """
        try:
            result = self.page.evaluate(r"""() => {
                const kv = {};
                const items = [];

                // ── 1. 公司名称 ──
                const nameEl = document.querySelector('div[class*="companyNameLabel--text"]');
                if (nameEl) {
                    const name = nameEl.innerText.trim();
                    if (name) {
                        kv['公司名称'] = name;
                        items.push({ label: '公司名称', value: name });
                    }
                }

                // ── 2. 遍历基本信息模块内全部 item（label + value 配对） ──
                const baseTable = document.querySelector('div[class*="companyBaseView--baseTable"]');
                const allLabels = [];
                if (baseTable) {
                    const itemEls = baseTable.querySelectorAll('div[class*="companyBaseView--item"]');
                    for (const itemEl of itemEls) {
                        const labelEl = itemEl.querySelector('div[class*="companyBaseView--label"]');
                        const valueEl = itemEl.querySelector('div[class*="companyBaseView--value"]');
                        const label = labelEl ? labelEl.innerText.trim() : '';
                        const value = valueEl ? valueEl.innerText.trim() : '';
                        allLabels.push(label);
                        if (label && value && value !== '-' && value !== '--') {
                            // 不覆盖已设置的 kv 键（如公司名称已由步骤1设置）
                            if (!(label in kv)) {
                                kv[label] = value;
                            }
                            items.push({ label, value });
                        }
                    }
                }

                // ── 3. 所在地（优先级高于 table 中的，如果有的话） ──
                const locationEl = document.querySelector('div[class*="companyPreview--companyLocation"]');
                if (locationEl) {
                    const locText = locationEl.innerText.trim();
                    let loc = locText.replace(/^公司所在地[：:]?\s*/, '').replace(/^Company Location[：:]?\s*/, '');
                    if (loc) {
                        kv['公司所在地'] = loc;
                        items.push({ label: '公司所在地', value: loc });
                    }
                }

                // ── 4. 网址（优先级高于 table 中的，如果有的话） ──
                const websiteEl = document.querySelector('span[class*="companyPreview--websiteaddress"]');
                if (websiteEl) {
                    const web = websiteEl.innerText.trim();
                    if (web && web !== '-' && web !== '--') {
                        kv['公司网址'] = web;
                        items.push({ label: '公司网址', value: web });
                    }
                }

                // ── 5. 从 baseInfo 区域全文中尝试提取地址 ──
                const baseInfo = document.querySelector('div[class*="companyPreview--companyBaseInfo"]');
                if (baseInfo) {
                    const fullText = baseInfo.innerText || '';
                    const addrMatch = fullText.match(/地址[：:]?\s*(.+?)(?:\n|$)/);
                    if (addrMatch) {
                        kv['地址'] = addrMatch[1].trim();
                        items.push({ label: '地址', value: addrMatch[1].trim() });
                    }
                }

                const found = items.length > 0;
                return {
                    found,
                    section_title: 'companyPreview',
                    items,
                    kv,
                    allLabels,
                };
            }""") or {}

            if not result.get("found"):
                print(f"  [详情] basic_info module not found")
                return {}

            print(f"  [详情] basic_info_items_found: {len(result.get('items', []))}")
            print(f"  [详情] basic_info_all_labels: {result.get('allLabels', [])}")

            pairs = result.get("items", [])
            for p in pairs:
                print(f"  [详情]   label='{p['label']}' → value='{p['value']}'")

            return result

        except Exception as e:
            print(f"  [详情] 基本信息提取异常: {e}")
            return {}

    def extract_company_detail(self) -> CompanyDetail:
        """从企业详情页提取公司信息。

        使用 _extract_basic_info() 按真实 DOM 结构提取基本信息。
        使用 _extract_contact_info() 按真实 DOM 结构提取联系方式。
        """
        detail = CompanyDetail()
        print(f"  [详情] 开始提取详情字段...")

        # ── 基本信息区（按真实 DOM 结构提取） ──
        basic = self._extract_basic_info()

        if basic.get("found"):
            kv = basic.get("kv", {})

            # matched_company_name — 从 span[class*='companyBaseView--name'] 提取
            detail.standard_name = kv.get("公司名称", "")
            if detail.standard_name:
                print(f"  [详情] matched_company_name: '{detail.standard_name}' (source=basic_info, matched_company_name_source=name_span)")
            else:
                print(f"  [详情] matched_company_name: (未提取到, matched_company_name_source=missed)")

            # location
            detail.location = kv.get("公司所在地", "")
            if detail.location:
                print(f"  [详情] location: '{detail.location}' (source=basic_info)")
            else:
                print(f"  [详情] location: (未提取到)")

            # website
            raw_web = kv.get("公司网址", "")
            detail.website = raw_web
            if detail.website and "tendata" in detail.website.lower():
                detail.website = ""
                print(f"  [详情] website_result: (已排除 tendata 平台地址)")
            elif detail.website:
                print(f"  [详情] website_result: '{detail.website}' (source=basic_info, website_result_source=website_div)")
            else:
                print(f"  [详情] website_result: (未提取到)")

            # company_status — 精确匹配 label="公司运营状态"/"Company Status"/"Status"
            # 若取到 Active/active → 标准化为 active
            raw_status = (
                kv.get("公司运营状态", "")
                or kv.get("Company Status", "")
                or kv.get("Status", "")
            )
            status_label_matched = (
                "公司运营状态" if kv.get("公司运营状态")
                else "Company Status" if kv.get("Company Status")
                else "Status" if kv.get("Status")
                else "none"
            )
            if raw_status:
                sl = raw_status.lower()
                if sl in ("active", "active ", "存续", "在营", "开业", "valid", "正常"):
                    detail.company_status = "active"
                elif sl in ("inactive", "注销", "吊销", "关闭", "closed"):
                    detail.company_status = "inactive"
                else:
                    detail.company_status = raw_status
                print(f"  [详情] company_status: '{detail.company_status}' "
                      f"(company_status_label_matched='{status_label_matched}', "
                      f"company_status_raw_value='{raw_status}', "
                      f"company_status_source=basic_info)")
            else:
                print(f"  [详情] company_status: (未提取到) "
                      f"(company_status_label_matched='{status_label_matched}', "
                      f"company_status_raw_value='', "
                      f"company_status_source=none)")

            # address
            detail.address = kv.get("公司地址", "")
            if detail.address:
                print(f"  [详情] address: '{detail.address}' (source=basic_info)")
            else:
                print(f"  [详情] address: (未提取到基本信息区)")

            detail.country = detail.location
        else:
            print(f"  [详情] 基本信息区块未定位，跳过字段提取")

        # ── 联系方式区（按真实 DOM 结构提取） ──
        contact_data = self._extract_contact_info()
        contact_kv = contact_data.get("kv", {})

        # phone
        detail.phone = contact_kv.get("公司电话", "")
        if detail.phone:
            import re as _re_ph
            detail.phone = _re_ph.sub(r'\s*更多\d*\s*$', '', detail.phone).strip()
            detail.phone = _re_ph.sub(r'[^\d+\-()（）\s]', '', detail.phone).strip()
            print(f"  [详情] phone: '{detail.phone}' (source=contact_info, phone_source=contact_item)")
        else:
            print(f"  [详情] phone: (未提取到)")

        # email
        raw_email = contact_kv.get("公司邮箱", "")
        if raw_email and raw_email not in ("-", "--", "—", "暂无", "空", ""):
            detail.email = raw_email
            print(f"  [详情] email: '{detail.email}' (source=contact_info)")
        else:
            detail.email = ""
            print(f"  [详情] email: (未提取到或为空)")

        # WhatsApp
        raw_wa = contact_kv.get("WhatsApp", "")
        if raw_wa:
            if "wa.me" in raw_wa or raw_wa.startswith("http"):
                detail.whatsapp = raw_wa
            elif re.match(r'^\+?\d+$', raw_wa.replace(" ", "").replace("-", "")):
                detail.whatsapp = f"https://wa.me/{raw_wa.replace('+', '').replace(' ', '').replace('-', '')}"
            else:
                detail.whatsapp = raw_wa
            print(f"  [详情] whatsapp: '{detail.whatsapp}' (source=contact_info, whatsapp_source=social_link)")
        else:
            print(f"  [详情] whatsapp: (未提取到)")

        # Linkedin
        raw_li = contact_kv.get("Linkedin", "")
        if raw_li:
            if raw_li.startswith("http"):
                detail.linkedin = raw_li
            elif "linkedin.com" in raw_li:
                detail.linkedin = f"https://{raw_li}" if not raw_li.startswith("http") else raw_li
            else:
                detail.linkedin = ""  # 不是链接
            if detail.linkedin:
                print(f"  [详情] linkedin: '{detail.linkedin}' (source=contact_info, linkedin_source=social_link)")
            else:
                print(f"  [详情] linkedin: (提取到的值不是链接，丢弃)")
        else:
            print(f"  [详情] linkedin: (未提取到)")

        # 地址兜底
        if not detail.address:
            detail.address = self._extract_address_fallback()
            if detail.address:
                print(f"  [详情] address: '{detail.address}' (source=address_fallback)")
            else:
                print(f"  [详情] address: (全页面扫描也未找到)")

        # ── 企业基础信息摘要 ──
        basic_parts = []
        if detail.location:
            basic_parts.append(f"所在地: {detail.location}")
        if detail.website:
            basic_parts.append(f"官网: {detail.website}")
        if detail.company_status != "unknown":
            basic_parts.append(f"状态: {detail.company_status}")
        if detail.address:
            basic_parts.append(f"地址: {detail.address}")
        detail.basic_info = " | ".join(basic_parts[:10])

        print(f"  [详情] 详情字段提取完成")
        return detail

    def _extract_table_kv(self, heading_text: str, keys: list[str]) -> dict[str, str]:
        """通过标题文本定位区块，在区块内提取 key-value 对。

        优先级：
        1. 找到标题容器 → 在容器内提取
        2. 容器未找到 → 全页面 brute-force 提取

        支持结构：table td/th, div-key-value, ant-descriptions, colon-pattern, 纯文本。
        """
        try:
            result = self.page.evaluate(r"""(params) => {
                const { title, keys } = params;
                const EMPTY_MARKERS = ['-', '--', '—', '暂无', '空'];

                function isEmpty(val) {
                    return EMPTY_MARKERS.includes(val) || !val || !val.trim();
                }

                const kv = {};
                const detail = {};

                function recordValue(k, val, source) {
                    if (k in kv) return;  // 已有更高优先级的
                    kv[k] = val;
                    detail[k] = { key_found: true, value_node_found: true, extraction_source: source, raw_value: val };
                }
                function recordMissed(k, reason) {
                    if (k in detail) return;
                    detail[k] = {
                        key_found: reason === 'value_empty',
                        value_node_found: false,
                        extraction_source: reason,
                        raw_value: ''
                    };
                }

                // ── 在指定容器内提取 ──
                function extractInContainer(container) {
                    // Mode 1: <table> td/th 对
                    for (const table of container.querySelectorAll('table')) {
                        for (const row of table.querySelectorAll('tr')) {
                            const tds = row.querySelectorAll('td, th');
                            for (let i = 0; i < tds.length - 1; i++) {
                                const keyText = tds[i].innerText.trim();
                                if (!keyText || keyText.length > 60) continue;
                                for (const k of keys) {
                                    if (k in kv) continue;
                                    if (k.toLowerCase() === keyText.toLowerCase() || keyText.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(keyText.toLowerCase())) {
                                        const valTd = tds[i + 1];
                                        if (!valTd) { recordMissed(k, 'value_missing'); break; }
                                        const a = valTd.querySelector('a[href]');
                                        let value = a && a.href && a.href.startsWith('http') ? a.href : valTd.innerText.trim();
                                        if (isEmpty(value)) { recordMissed(k, 'value_empty'); }
                                        else { recordValue(k, value, 'table_td'); }
                                        break;
                                    }
                                }
                            }
                        }
                    }

                    // Mode 2: Ant Design Descriptions
                    for (const dl of container.querySelectorAll('dl, [class*="descriptions"], [class*="desc"]')) {
                        const dts = dl.querySelectorAll('dt, [class*="label"], [class*="term"], [class*="key"]');
                        for (const dt of dts) {
                            const keyText = dt.innerText.trim();
                            if (!keyText || keyText.length > 60) continue;
                            for (const k of keys) {
                                if (k in kv) continue;
                                if (k.toLowerCase() === keyText.toLowerCase() || keyText.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(keyText.toLowerCase())) {
                                    const dd = dt.nextElementSibling;
                                    if (!dd) { recordMissed(k, 'value_missing'); break; }
                                    const a = dd.querySelector('a[href]');
                                    let value = a && a.href && a.href.startsWith('http') ? a.href : dd.innerText.trim();
                                    if (isEmpty(value)) { recordMissed(k, 'value_empty'); }
                                    else { recordValue(k, value, 'ant_descriptions'); }
                                    break;
                                }
                            }
                        }
                    }

                    // Mode 3: div-based key-value rows
                    const itemRows = container.querySelectorAll(
                        '[class*="item"], [class*="row"], [class*="field"], [class*="info-row"], [class*="detail-row"], li'
                    );
                    for (const row of itemRows) {
                        const labels = row.querySelectorAll(
                            '[class*="label"], [class*="key"], [class*="name"], [class*="title"], '
                            + '[class*="field"], span:first-child, div:first-child'
                        );
                        for (const labelEl of labels) {
                            const keyText = labelEl.innerText.trim();
                            if (!keyText || keyText.length > 50 || keyText.length < 2) continue;
                            if (/^[：:]/.test(keyText)) continue;
                            for (const k of keys) {
                                if (k in kv) continue;
                                if (k.toLowerCase() === keyText.toLowerCase() || keyText.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(keyText.toLowerCase())) {
                                    let valueEl = labelEl.nextElementSibling;
                                    if (valueEl && /^[：:]+$/.test(valueEl.innerText.trim())) {
                                        valueEl = valueEl.nextElementSibling;
                                    }
                                    if (!valueEl) { recordMissed(k, 'value_missing'); break; }
                                    const a = valueEl.querySelector('a[href]');
                                    let value = a && a.href && a.href.startsWith('http') ? a.href : valueEl.innerText.trim();
                                    if (isEmpty(value)) { recordMissed(k, 'value_empty'); }
                                    else { recordValue(k, value, 'div_kv_row'); }
                                    break;
                                }
                            }
                        }
                    }

                    // Mode 4: 容器内冒号模式
                    for (const k of keys) {
                        if (k in kv) continue;
                        const candidates = container.querySelectorAll('span, div, td, label, p');
                        for (const el of candidates) {
                            const t = (el.innerText || '').trim();
                            if (!t) continue;
                            const regex = new RegExp(
                                '(^|[^\\w])' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '[\\uFF1A:]+\\s*(.+?)(?:\\s*$)',
                                'i'
                            );
                            const m = t.match(regex);
                            if (m && m[2] && m[2].trim() && !isEmpty(m[2].trim())) {
                                recordValue(k, m[2].trim(), 'colon_pattern');
                                break;
                            }
                        }
                    }
                }

                // ── Step 1: 找标题容器 ──
                let container = null;
                for (const heading of document.querySelectorAll(
                    'h1, h2, h3, h4, h5, [class*="title"], [class*="header"], [class*="block"], [class*="section"]'
                )) {
                    const t = (heading.innerText || '').trim();
                    if (t === title || t.startsWith(title)) {
                        const r = heading.getBoundingClientRect();
                        if (r.width < 30 || r.height < 8) continue;
                        let c = heading;
                        for (let i = 0; i < 8 && c; i++) {
                            const rect = c.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 40
                                && c.children.length >= 2 && c.children.length < 80) {
                                container = c; break;
                            }
                            c = c.parentElement;
                        }
                        break;
                    }
                }

                if (container) {
                    extractInContainer(container);
                }

                // ── Step 2: 容器未找到 或 容器内提取不完整 → 全页面 brute-force ──
                if (Object.keys(kv).length === 0 || Object.keys(kv).length < keys.length) {
                    // 全页面 table 扫描
                    for (const table of document.querySelectorAll('table')) {
                        for (const row of table.querySelectorAll('tr')) {
                            const tds = row.querySelectorAll('td, th');
                            for (let i = 0; i < tds.length - 1; i++) {
                                const keyText = tds[i].innerText.trim();
                                if (!keyText || keyText.length > 60) continue;
                                for (const k of keys) {
                                    if (k in kv) continue;
                                    if (k.toLowerCase() === keyText.toLowerCase() || keyText.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(keyText.toLowerCase())) {
                                        const valTd = tds[i + 1];
                                        if (!valTd) break;
                                        const a = valTd.querySelector('a[href]');
                                        let value = a && a.href && a.href.startsWith('http') ? a.href : valTd.innerText.trim();
                                        if (!isEmpty(value)) { recordValue(k, value, 'page_table'); }
                                        break;
                                    }
                                }
                            }
                        }
                    }

                    // 全页面 div-key-value 扫描
                    const allItems = document.querySelectorAll(
                        '[class*="item"], [class*="row"], [class*="field"], [class*="info-row"], [class*="detail-row"], li'
                    );
                    for (const row of allItems) {
                        const labels = row.querySelectorAll(
                            '[class*="label"], [class*="key"], [class*="name"], [class*="title"], [class*="field"]'
                        );
                        for (const labelEl of labels) {
                            const keyText = labelEl.innerText.trim();
                            if (!keyText || keyText.length > 50 || keyText.length < 2) continue;
                            if (/^[：:]/.test(keyText)) continue;
                            for (const k of keys) {
                                if (k in kv) continue;
                                if (k.toLowerCase() === keyText.toLowerCase() || keyText.toLowerCase().includes(k.toLowerCase()) || k.toLowerCase().includes(keyText.toLowerCase())) {
                                    let valueEl = labelEl.nextElementSibling;
                                    if (valueEl && /^[：:]+$/.test(valueEl.innerText.trim())) {
                                        valueEl = valueEl.nextElementSibling;
                                    }
                                    if (!valueEl) break;
                                    const a = valueEl.querySelector('a[href]');
                                    let value = a && a.href && a.href.startsWith('http') ? a.href : valueEl.innerText.trim();
                                    if (!isEmpty(value)) { recordValue(k, value, 'page_div_kv'); }
                                    break;
                                }
                            }
                        }
                    }

                    // 全页面冒号模式扫描
                    for (const k of keys) {
                        if (k in kv) continue;
                        const candidates = document.querySelectorAll('span, div, td, label, p');
                        for (const el of candidates) {
                            const t = (el.innerText || '').trim();
                            if (!t) continue;
                            const regex = new RegExp(
                                '(^|[^\\w])' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '[\\uFF1A:]+\\s*(.+?)(?:\\s*$)',
                                'i'
                            );
                            const m = t.match(regex);
                            if (m && m[2] && m[2].trim() && !isEmpty(m[2].trim())) {
                                recordValue(k, m[2].trim(), 'page_colon');
                                break;
                            }
                        }
                    }

                    // 全页面链接扫描（用于 website, linkedin, whatsapp 等）
                    for (const k of keys) {
                        if (k in kv) continue;
                        if (k.toLowerCase().includes('网址') || k.toLowerCase().includes('link') || k.toLowerCase().includes('whats')) {
                            for (const a of document.querySelectorAll('a[href]')) {
                                const href = a.href || '';
                                const linkText = (a.innerText || '').trim();
                                if (k.toLowerCase().includes('link') && (href.includes('linkedin') || linkText.toLowerCase().includes('linkedin'))) {
                                    recordValue(k, href.startsWith('http') ? href : linkText, 'page_link_scan');
                                    break;
                                }
                                if (k.toLowerCase().includes('whats') && (href.includes('wa.me') || linkText.toLowerCase().includes('whatsapp') || linkText.toLowerCase().includes('whats'))) {
                                    recordValue(k, href.startsWith('http') ? href : linkText, 'page_link_scan');
                                    break;
                                }
                            }
                        }
                    }
                }

                // 记录未命中
                for (const k of keys) {
                    if (!(k in detail)) {
                        detail[k] = { key_found: false, value_node_found: false, extraction_source: 'selector_missed', raw_value: '' };
                    }
                }

                return { containerFound: !!container, kv, detail, found: Object.keys(kv).length > 0 };
            }""", {"title": heading_text, "keys": keys})

            if not result:
                for k in keys:
                    print(f"  [详情]   key='{k}' → key_found=false, value_node_found=false, "
                          f"extracted_value=(evaluate_failed), extraction_source=exception")
                return {}

            kv = result.get("kv", {})
            detail = result.get("detail", {})
            container_found = result.get("containerFound", False)

            for k in keys:
                d = detail.get(k, {})
                kf = d.get("key_found", False)
                vf = d.get("value_node_found", False)
                src = d.get("extraction_source", "unknown")
                val = d.get("raw_value", "")
                if kf and vf and val:
                    print(f"  [详情]   key='{k}' → key_found=true, value_node_found=true, "
                          f"extracted_value='{val}', extraction_source={src}")
                elif kf and not vf:
                    print(f"  [详情]   key='{k}' → key_found=true, value_node_found=false, "
                          f"extracted_value=(value_empty), extraction_source={src}")
                elif kf and vf:
                    print(f"  [详情]   key='{k}' → key_found=true, value_node_found=true, "
                          f"extracted_value=(empty_string), extraction_source={src}")
                else:
                    print(f"  [详情]   key='{k}' → key_found=false, value_node_found=false, "
                          f"extracted_value=(selector_missed), extraction_source={src}")

            return kv
        except Exception as e:
            print(f"  [详情] 提取区块 '{heading_text}' 异常: {e}")
            return {}

    def _extract_company_info_page_level(self, detail) -> dict:
        """全页面级公司信息提取（独立于 _extract_table_kv）。

        直接从整个页面扫描所有表格、div-row、冒号模式。
        只在 _extract_table_kv 返回为空时补充未提取到的字段。
        """
        try:
            result = self.page.evaluate(r"""() => {
                const EMPTY_MARKERS = ['-', '--', '—', '暂无', '空', ''];
                function isEmpty(v) { return !v || !v.trim() || EMPTY_MARKERS.includes(v.trim()); }

                const fields = {};

                // ── 策略 1: 全页面所有 table 的 td/th 对 ──
                for (const table of document.querySelectorAll('table')) {
                    for (const row of table.querySelectorAll('tr')) {
                        const tds = row.querySelectorAll('td, th');
                        for (let i = 0; i < tds.length - 1; i++) {
                            const keyText = (tds[i].innerText || '').trim();
                            if (!keyText || keyText.length > 60 || keyText.length < 2) continue;
                            // 跳过纯数字、日期、金额等列头
                            if (/^\d+$|^[\d,.]+$/.test(keyText)) continue;

                            const valTd = tds[i + 1];
                            let val = '';
                            const a = valTd.querySelector('a[href]');
                            if (a && a.href && a.href.startsWith('http') && !a.href.includes('tendata')) {
                                val = a.href;
                            } else {
                                val = valTd.innerText.trim();
                            }
                            if (isEmpty(val)) continue;

                            // 匹配所有公司信息键
                            const kl = keyText.toLowerCase();
                            if (!fields.location && (/所在地|公司所在地|注册地|国家|地区|location|country/i.test(kl))) {
                                fields.location = val;
                            }
                            if (!fields.website && (/网址|网站|官网|公司网址|公司网站|website/i.test(kl))) {
                                if (!val.includes('tendata')) fields.website = val;
                            }
                            if (!fields.company_status && (/运营状态|公司状态|经营状态|存续|状态|status|active/i.test(kl))) {
                                fields.company_status = val;
                            }
                            if (!fields.address && (/公司地址|地址|办公地址|注册地址|address/i.test(kl))) {
                                fields.address = val;
                            }
                            if (!fields.phone && (/公司电话|电话|手机|联系电话|联系电话|phone|tel|mobile/i.test(kl))) {
                                fields.phone = val;
                            }
                            if (!fields.email && (/公司邮箱|邮箱|电子邮件|email|e-mail/i.test(kl))) {
                                if (!val.includes('tendata')) fields.email = val;
                            }
                            if (!fields.whatsapp && (/whatsapp|whats\s*app|wa\s*/i.test(kl))) {
                                fields.whatsapp = val;
                            }
                            if (!fields.linkedin && (/linkedin|linked\s*in|领英/i.test(kl))) {
                                if (val.startsWith('http') || /linkedin\.com/i.test(val)) {
                                    fields.linkedin = val.startsWith('http') ? val : 'https://' + val;
                                }
                            }
                            if (!fields.standard_name && (/公司名称|公司名|企业名称|company\s*name/i.test(kl))) {
                                fields.standard_name = val;
                            }
                        }
                    }
                }

                // ── 策略 2: 全页面 div/item/row 容器的 label + value 对 ──
                for (const row of document.querySelectorAll(
                    '[class*="item"], [class*="row"], [class*="field"], [class*="info"], [class*="detail"], li'
                )) {
                    const rowText = (row.innerText || '').trim();
                    if (rowText.length < 5 || rowText.length > 500) continue;
                    // 跳过纯数字/统计行
                    if (/^[\d,\s%]+$/.test(rowText)) continue;

                    const children = Array.from(row.children);
                    if (children.length < 2) continue;

                    // 两列结构：label | value
                    for (let ci = 0; ci < children.length - 1; ci++) {
                        const keyText = (children[ci].innerText || '').trim();
                        if (!keyText || keyText.length > 50 || keyText.length < 2) continue;
                        if (/^[：:]/.test(keyText)) continue;
                        // 跳过纯数字
                        if (/^\d+$/.test(keyText)) continue;

                        let valueEl = children[ci + 1];
                        if (valueEl && /^[：:]+$/.test((valueEl.innerText || '').trim())) {
                            valueEl = valueEl.nextElementSibling;
                        }
                        if (!valueEl) continue;
                        let val = '';
                        const a = valueEl.querySelector('a[href]');
                        if (a && a.href && a.href.startsWith('http') && !a.href.includes('tendata')) {
                            val = a.href;
                        } else {
                            val = valueEl.innerText.trim();
                        }
                        if (isEmpty(val)) continue;

                        const kl = keyText.toLowerCase();
                        if (!fields.location && (/所在地|公司所在地|注册地|国家|地区|location|country/i.test(kl))) {
                            fields.location = val;
                        }
                        if (!fields.website && (/网址|网站|官网|公司网址|公司网站|website/i.test(kl))) {
                            if (!val.includes('tendata')) fields.website = val;
                        }
                        if (!fields.company_status && (/运营状态|公司状态|经营状态|存续|状态|status|active/i.test(kl))) {
                            fields.company_status = val;
                        }
                        if (!fields.address && (/公司地址|地址|办公地址|注册地址|address/i.test(kl))) {
                            fields.address = val;
                        }
                        if (!fields.phone && (/公司电话|电话|手机|联系电话|联系电话|phone|tel|mobile/i.test(kl))) {
                            fields.phone = val;
                        }
                        if (!fields.email && (/公司邮箱|邮箱|电子邮件|email|e-mail/i.test(kl))) {
                            if (!val.includes('tendata')) fields.email = val;
                        }
                        if (!fields.whatsapp && (/whatsapp|whats\s*app|wa\s*/i.test(kl))) {
                            fields.whatsapp = val;
                        }
                        if (!fields.linkedin && (/linkedin|linked\s*in|领英/i.test(kl))) {
                            if (val.startsWith('http') || /linkedin\.com/i.test(val)) {
                                fields.linkedin = val.startsWith('http') ? val : 'https://' + val;
                            }
                        }
                        if (!fields.standard_name && (/公司名称|公司名|企业名称|company\s*name/i.test(kl))) {
                            fields.standard_name = val;
                        }
                    }
                }

                // ── 策略 3: 全页面链接扫描（website, linkedin, whatsapp） ──
                for (const a of document.querySelectorAll('a[href]')) {
                    const href = a.href || '';
                    const linkText = (a.innerText || '').trim().toLowerCase();

                    // LinkedIn
                    if (!fields.linkedin && (href.includes('linkedin.com') || linkText.includes('linkedin') || linkText.includes('领英'))) {
                        if (href.includes('linkedin.com')) fields.linkedin = href;
                    }
                    // WhatsApp
                    if (!fields.whatsapp && (href.includes('wa.me') || href.includes('whatsapp') || linkText.includes('whatsapp') || linkText.includes('whats'))) {
                        fields.whatsapp = href.startsWith('http') ? href : linkText;
                    }
                    // Website (external link, not tendata)
                    if (!fields.website && href.startsWith('http') && !href.includes('tendata') && !href.includes('linkedin') && !href.includes('whatsapp') && !href.includes('mailto')) {
                        const parentText = (a.parentElement?.innerText || '').toLowerCase();
                        if (/网址|网站|官网|公司网址|官网链接|website|official/i.test(parentText) || /网址|网站|官网/i.test(linkText)) {
                            fields.website = href;
                        }
                    }
                }

                // ── 策略 4: 冒号模式 "Key：Value" ──
                for (const el of document.querySelectorAll('span, div, p, td, label')) {
                    const t = (el.innerText || '').trim();
                    if (!t || t.length > 300) continue;
                    const keyValRegex = /^(.+?)[：:]\s*(.+)$/;
                    const m = t.match(keyValRegex);
                    if (!m) continue;
                    const keyText = m[1].trim();
                    const valText = m[2].trim();
                    if (keyText.length > 30 || valText.length < 1) continue;
                    if (isEmpty(valText)) continue;

                    const kl = keyText.toLowerCase();
                    if (!fields.location && (/所在地|公司所在地|注册地|国家|地区|location|country/i.test(kl))) {
                        fields.location = valText;
                    }
                    if (!fields.website && (/网址|网站|官网|公司网址|公司网站|website/i.test(kl))) {
                        if (!valText.includes('tendata')) fields.website = valText;
                    }
                    if (!fields.company_status && (/运营状态|公司状态|经营状态|存续|状态|status|active/i.test(kl))) {
                        fields.company_status = valText;
                    }
                    if (!fields.address && (/公司地址|地址|办公地址|注册地址|address/i.test(kl))) {
                        fields.address = valText;
                    }
                    if (!fields.phone && (/公司电话|电话|手机|联系电话|联系电话|phone|tel|mobile/i.test(kl))) {
                        fields.phone = valText;
                    }
                    if (!fields.email && (/公司邮箱|邮箱|电子邮件|email|e-mail/i.test(kl))) {
                        if (!valText.includes('tendata')) fields.email = valText;
                    }
                    if (!fields.whatsapp && (/whatsapp|whats\s*app|wa\s*/i.test(kl))) {
                        fields.whatsapp = valText;
                    }
                    if (!fields.linkedin && (/linkedin|linked\s*in|领英/i.test(kl))) {
                        if (valText.includes('linkedin.com')) {
                            fields.linkedin = valText.startsWith('http') ? valText : 'https://' + valText;
                        }
                    }
                }

                return fields;
            }""") or {}

            # 打印提取结果
            field_labels = {
                'standard_name': 'matched_company_name',
                'location': 'location',
                'website': 'website_result',
                'company_status': 'company_status',
                'address': 'address',
                'phone': 'phone',
                'email': 'email',
                'whatsapp': 'whatsapp',
                'linkedin': 'linkedin',
            }
            for k, label in field_labels.items():
                val = result.get(k, '')
                if val:
                    print(f"  [详情] [page_level] {label}: '{val}'")

            return result

        except Exception as e:
            print(f"  [详情] 全页面级公司提取异常: {e}")
            return {}

    def _extract_contact_info(self) -> dict:
        """专用联系方式提取器（按真实 DOM 结构）。

        真实结构：
        - 外层模块：div[class*='companyCorporateInfoView--CompanyCorporateInfoView']
        - 每一项：div[class*='companyCorporateInfoView--item']
        - label：div[class*='companyCorporateInfoView--label']
        - value：div[class*='companyCorporateInfoView--value']
        - 社交链接：span[class*='companyCorporateInfoView--socialLink']
        - 电话文本：div[class*='textTooltip--textTooltipBox']
        """
        try:
            result = self.page.evaluate(r"""() => {
                const EMPTY_MARKERS = ['-', '--', '—', '暂无', '空', ''];
                function isEmpty(v) { return !v || !v.trim() || EMPTY_MARKERS.includes(v.trim()); }

                // 定位企业联系方式模块
                const contactModule = document.querySelector(
                    'div[class*="companyCorporateInfoView--CompanyCorporateInfoView"], '
                    + 'div[class*="corporateInfo"], div[class*="contact-info"]'
                );
                if (!contactModule) {
                    return { found: false, kv: {} };
                }

                const kv = {};

                // 遍历所有 item
                const items = [];
                const labelValuePairs = [];
                const itemList = contactModule.querySelectorAll('div[class*="companyCorporateInfoView--item"]');

                for (const item of itemList) {
                    const labelEl = item.querySelector('div[class*="companyCorporateInfoView--label"]');
                    const valueEl = item.querySelector('div[class*="companyCorporateInfoView--value"]');
                    const label = labelEl ? labelEl.innerText.trim() : '';
                    if (!label) continue;

                    let value = '';

                    if (label === '公司电话') {
                        // 优先取 div[class*='textTooltip--textTooltipBox']
                        const textEl = item.querySelector('div[class*="textTooltip--textTooltipBox"]');
                        if (textEl) {
                            value = textEl.innerText.trim();
                        } else if (valueEl) {
                            value = valueEl.innerText.trim();
                        }
                        // 去掉"更多N"后缀
                        value = value.replace(/\s*更多\d*\s*$/, '').trim();
                        // 只保留电话号码部分
                        const phoneMatch = value.match(/[\+]?[\d\s\-]{6,}/);
                        if (phoneMatch) value = phoneMatch[0].trim();

                    } else if (label === '公司邮箱') {
                        if (valueEl) {
                            value = valueEl.innerText.trim();
                        }
                        // "-" 视为空
                        if (isEmpty(value)) value = '';

                    } else if (label === 'WhatsApp') {
                        // 取 span[class*='companyCorporateInfoView--socialLink'] 下的文本
                        const linkEl = item.querySelector('span[class*="companyCorporateInfoView--socialLink"]');
                        if (linkEl) {
                            // 优先取 href
                            const a = linkEl.querySelector('a[href]') || linkEl.closest('a[href]');
                            if (a && a.href && a.href.startsWith('http')) {
                                value = a.href;
                            } else {
                                value = linkEl.innerText.trim();
                            }
                        }
                        // 如果不是 http 链接，构造 wa.me
                        if (value && !value.startsWith('http') && /^[\+]?[\d]+$/.test(value.replace(/[ -]/g, ''))) {
                            value = 'https://wa.me/' + value.replace(/[+ -]/g, '');
                        }

                    } else if (label === 'Linkedin' || label === 'LinkedIn') {
                        const linkEl = item.querySelector('span[class*="companyCorporateInfoView--socialLink"]');
                        if (linkEl) {
                            const a = linkEl.querySelector('a[href]') || linkEl.closest('a[href]');
                            if (a && a.href && a.href.startsWith('http')) {
                                value = a.href;
                            } else {
                                value = linkEl.innerText.trim();
                            }
                        }
                        // 确保是链接
                        if (value && !value.startsWith('http') && value.includes('linkedin.com')) {
                            value = 'https://' + value;
                        }
                        if (value && !value.startsWith('http')) {
                            value = '';
                        }
                    } else {
                        // 通用提取
                        if (valueEl) {
                            value = valueEl.innerText.trim();
                        }
                    }

                    if (isEmpty(value)) value = '';

                    labelValuePairs.push({ label, value });
                    if (value) {
                        kv[label] = value;
                    }
                }

                return { found: true, kv, items: labelValuePairs };
            }""") or {}

            if not result.get("found"):
                print(f"  [详情] contact_info module not found")
                return {"kv": {}}

            print(f"  [详情] contact_items_found: {len(result.get('items', []))}")
            for p in result.get("items", []):
                print(f"  [详情]   contact label='{p['label']}' → value='{p['value']}'")

            return result

        except Exception as e:
            print(f"  [详情] 联系方式提取异常: {e}")
            return {"kv": {}}

    def _extract_address_fallback(self) -> str:
        """全页面扫描提取公司地址（独立方法，不影响 _extract_table_kv 冻结逻辑）。

        支持中英文双语 key。
        """
        try:
            return self.page.evaluate(r"""() => {
                const ADDRESS_KEYS = [
                    '公司地址', '地址', '公司地址（中文）', '公司地址（英文）',
                    'Address', 'Company Address', '公司地点', 'Location',
                ];
                const EMPTY_MARKERS = ['-', '--', '—', '暂无', '空', '', '/ EN', '/ 中文', 'EN', '中/EN'];
                const NAV_NOISE = [
                    '用户中心', '退出登录', '我的账户', '收起菜单', '中/EN', '/ EN',
                    // 侧边栏表单字段名（非实际值，排除以免误匹配）
                    '公司名称', '公司简称', '公司地址（中文）', '公司地址（英文）',
                    '公司所在地', '所在国家', '联系人', '联系电话', '联系邮箱',
                    // 常见 UI 操作词（非地址值）
                    '更新', '刷新', '收起', '展开', '点击', '更多', '保存', '编辑', '删除', '搜索',
                    '腾道数据中心', '帮助中心', '消息通知', '全球搜', 'AI 助手',
                ];

                function isEmpty(v) {
                    const trimmed = (v || '').trim();
                    return !trimmed || EMPTY_MARKERS.includes(trimmed) || trimmed.length <= 5;
                }
                function isNavNoise(v) { const c = (v||'').replace(/\s+/g,' ').trim(); return NAV_NOISE.some(n => c.includes(n)); }
                function isSocialOrLink(v) {
                    const t = (v||'').trim().toLowerCase();
                    return t.startsWith('http') || t.startsWith('www.') || t.startsWith('//')
                        || /linkedin\.com|facebook\.com|twitter\.com|x\.com|instagram\.com|tiktok\.com|wa\.me|whatsapp\.com/.test(t);
                }

                function isAddressKey(text) {
                    const t = (text || '').trim().toLowerCase();
                    return ADDRESS_KEYS.some(k => t === k.toLowerCase() || t.includes(k.toLowerCase()) || k.toLowerCase().includes(t));
                }

                // 策略 1: 全页面 table 行中找
                for (const table of document.querySelectorAll('table')) {
                    for (const row of table.querySelectorAll('tr')) {
                        const tds = row.querySelectorAll('td, th');
                        for (let i = 0; i < tds.length - 1; i++) {
                            if (isAddressKey(tds[i].innerText)) {
                                const val = tds[i + 1].innerText.trim();
                                if (!isEmpty(val) && !isNavNoise(val) && !isSocialOrLink(val)) return val;
                            }
                        }
                    }
                }

                // 策略 2: div/item 行中找 label + value
                for (const row of document.querySelectorAll('[class*="item"], [class*="row"], [class*="field"], li')) {
                    const labels = row.querySelectorAll('[class*="label"], [class*="key"], [class*="name"], span:first-child, div:first-child');
                    for (const labelEl of labels) {
                        if (isAddressKey(labelEl.innerText)) {
                            let valEl = labelEl.nextElementSibling;
                            if (valEl && /^[：:]+$/.test(valEl.innerText.trim())) valEl = valEl.nextElementSibling;
                            if (valEl && !isEmpty(valEl.innerText) && !isNavNoise(valEl.innerText) && !isSocialOrLink(valEl.innerText)) return valEl.innerText.trim();
                        }
                    }
                }

                // 策略 3: 页面中 "公司地址：xxx" / "Address: xxx" 冒号模式
                for (const el of document.querySelectorAll('span, div, p, td')) {
                    const t = (el.innerText || '').trim();
                    if (!t || t.length > 300) continue;
                    // 排除导航栏及表单字段名噪音
                    if (NAV_NOISE.some(n => t === n || t.includes(n))) continue;
                    // 候选值本身为纯导航文本则跳过
                    if (isNavNoise(t)) continue;
                    for (const k of ADDRESS_KEYS) {
                        const idx = t.toLowerCase().indexOf(k.toLowerCase());
                        if (idx >= 0) {
                            let after = t.substring(idx + k.length).trim();
                            if (after.startsWith('：') || after.startsWith(':')) after = after.substring(1).trim();
                            if (after && after.length > 5 && after.length < 200) {
                                // 最终验证：含导航噪音或社媒链接则拒绝
                                if (NAV_NOISE.some(n => after.includes(n))) continue;
                                if (isSocialOrLink(after)) continue;
                                // 清理多行导航残留文本
                                const cleaned = after.replace(/\s+/g, ' ').trim();
                                if (NAV_NOISE.some(n => cleaned.includes(n))) continue;
                                return after;
                            }
                        }
                    }
                }

                return '';
            }""") or ""
        except Exception as e:
            print(f"  [详情] 地址兜底提取异常: {e}")
            return ""

    def _extract_website(self) -> str:
        """提取官网 URL。

        策略：
        1. 查找"官网"/"网址"/"网站"标签，取相邻或包含的链接
        2. 查找表格行中含"官网"关键词的 td/th 对
        3. 查找公司主页/主页等标签
        4. 排除 tendata 平台域名
        """
        TENDATA_DOMAINS = [
            "tendata.cn", "tendata.com",
            "account.tendata.cn", "bizr.tendata.cn",
            "knowledge.tendata.cn", "login.tendata.cn",
            "www.tendata.cn",
        ]

        def is_tendata_url(url: str) -> bool:
            u = url.lower().strip().rstrip("/")
            for d in TENDATA_DOMAINS:
                if d in u:
                    return True
            return False

        def normalize_url(raw: str) -> str:
            raw = raw.strip().rstrip("/")
            if not raw:
                return ""
            if raw.startswith("http://") or raw.startswith("https://"):
                return raw if not is_tendata_url(raw) else ""
            # 纯域名如 maxvalue.net → https://maxvalue.net
            if "." in raw and "/" not in raw and " " not in raw:
                return f"https://{raw}" if not is_tendata_url(raw) else ""
            # 带路径的
            if "." in raw and not is_tendata_url(raw):
                return f"https://{raw}"
            return ""

        # 策略 1: "官网"/"网址"/"网站"标签 + 相邻 <a> 链接
        for label in ["官网", "网址", "网站", "公司网址", "Company Website", "Website"]:
            try:
                # 找包含该标签的元素，然后在其附近找 <a> 标签
                label_els = self.page.query_selector_all(f"text={label}")
                for le in label_els:
                    if not le.is_visible():
                        continue
                    # 检查父元素的兄弟或子元素中的 <a>
                    parent = le.evaluate("e => e.parentElement")
                    if parent:
                        # 用 JS 在父元素中找 <a>
                        a_info = le.evaluate("""(el) => {
                            const p = el.parentElement;
                            if (!p) return null;
                            const a = p.querySelector('a');
                            if (a) return { href: a.href, text: a.innerText.trim() };
                            // 也检查兄弟元素
                            const siblings = Array.from(p.children);
                            for (const sib of siblings) {
                                if (sib === el) continue;
                                const sa = sib.querySelector('a');
                                if (sa) return { href: sa.href, text: sa.innerText.trim() };
                            }
                            return null;
                        }""")
                        if a_info and a_info.get("href"):
                            url = normalize_url(a_info["href"])
                            if url:
                                return url
            except Exception:
                continue

        # 策略 2: 表格中 key-value 对（key 包含"官网"等关键词）
        for label in ["官网", "网址", "网站", "Company Website"]:
            try:
                rows = self.page.query_selector_all("table tr, dl, .info-row")
                for row in rows:
                    text = row.inner_text()
                    if label not in text:
                        continue
                    cells = row.query_selector_all("td, th, dt, dd")
                    for cell in cells:
                        ct = cell.inner_text().strip()
                        if label in ct:
                            # 取下一个 cell 作为 value
                            nxt = cell.evaluate("""(el) => {
                                const next = el.nextElementSibling;
                                if (!next) return null;
                                const a = next.querySelector('a');
                                if (a) return a.href;
                                return next.innerText.trim();
                            }""")
                            if nxt:
                                url = normalize_url(nxt)
                                if url:
                                    return url
            except Exception:
                continue

        # 策略 3: 页面中 <a> 标签文本包含"官网"/"网站"
        for label in ["官网", "网站", "Website"]:
            try:
                a_el = self.page.query_selector(f"a:has-text('{label}')")
                if a_el and a_el.is_visible():
                    href = a_el.get_attribute("href") or ""
                    url = normalize_url(href)
                    if url:
                        return url
            except Exception:
                continue

        # 策略 4: JS 扫描"基本信息"区域内所有 <a>，取第一个非 tendata 域名
        try:
            js_url = self.page.evaluate(r"""() => {
                // 找包含"基本信息"的容器
                const containers = Array.from(document.querySelectorAll('*')).filter(el => {
                    const t = (el.innerText || '').trim();
                    return t.includes('基本信息') && el.children.length > 2 && el.children.length < 30;
                });
                for (const container of containers) {
                    const links = container.querySelectorAll('a[href]');
                    for (const a of links) {
                        const href = a.href || '';
                        const text = (a.innerText || '').trim().toLowerCase();
                        // 跳过平台内部链接（非官网）
                        if (!href || href.includes('javascript') || href.startsWith('#')) continue;
                        if (href.includes('tendata.cn') || href.includes('tendata.com')) continue;
                        // 跳过"查看地图""编辑"等按钮
                        if (/地图|编辑|更新|保存|关闭|取消/.test(text)) continue;
                        // 返回第一个看起来像外部域名的链接
                        if (href.startsWith('http')) return href;
                    }
                }
                // 如果没找到容器，扫描页面上所有可见的外部链接（排除 tendata）
                const allLinks = Array.from(document.querySelectorAll('a[href]'));
                for (const a of allLinks) {
                    const href = a.href || '';
                    if (!href.startsWith('http')) continue;
                    if (href.includes('tendata.cn') || href.includes('tendata.com')) continue;
                    if (href.includes('google') || href.includes('baidu')) continue;
                    const r = a.getBoundingClientRect();
                    if (r.width < 30 || r.height < 5) continue;
                    const cs = getComputedStyle(a);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    return href;
                }
                return null;
            }""")
            if js_url:
                url = normalize_url(js_url)
                if url:
                    return url
        except Exception as e:
            print(f"  [详情] website JS兜底扫描异常: {e}")

        return ""

    def _extract_contact_fields(self) -> dict:
        """通过 key-value 方式抽取联系方式。

        Returns dict with keys: contact_name, phone, email, address,
        plus {field}_reason 说明为何未提取到。
        """
        result = {
            "contact_name": "", "phone": "", "email": "", "address": "",
            "contact_name_reason": "", "phone_reason": "",
            "email_reason": "", "address_reason": "",
        }

        # 定义关键词映射
        key_map = {
            "contact_name": ["联系人", "联系人姓名", "姓名", "Contact Person", "Contact Name", "联系人信息"],
            "phone": ["电话", "手机", "座机", "联系电话", "Phone", "Tel", "Mobile", "Mobile Phone"],
            "email": ["邮箱", "电子邮件", "Email", "E-mail", "电子邮箱", "邮箱地址"],
            "address": ["地址", "公司地址", "办公地址", "详细地址", "Address", "公司住址"],
        }

        for target_key, keywords in key_map.items():
            found = False
            # 策略 1: 用 JS 在"企业联系方式"区域做定向提取
            try:
                js_value = self.page.evaluate(r"""(keywords) => {
                    // 先找"企业联系方式"或"联系方式"区域容器
                    const containers = Array.from(document.querySelectorAll('*')).filter(el => {
                        const t = (el.innerText || '').trim();
                        return /(企业联系方式|联系方式|Contact Info|Contact Information)/.test(t) &&
                            el.children.length > 2 && el.children.length < 30;
                    });
                    for (const container of containers) {
                        // 在该容器内找 key-value 对
                        const rows = Array.from(container.querySelectorAll('tr, .info-row, dl, [class*="item"]'));
                        for (const row of rows) {
                            const rt = row.innerText || '';
                            for (const kw of keywords) {
                                if (rt.includes(kw)) {
                                    // 提取值：排除 key 本身
                                    const cells = Array.from(row.querySelectorAll('td, dd, .value, .content'));
                                    for (const cell of cells) {
                                        const ct = cell.innerText.trim();
                                        if (ct && ct !== kw && !ct.startsWith(kw) && ct.length > 1 && ct.length < 200) {
                                            return ct;
                                        }
                                    }
                                    // 兜底：取行文本去掉关键词的部分
                                    const cleaned = rt.replace(new RegExp(keywords.join('|'), 'g'), '').trim().replace(/^[:：\s]+/, '');
                                    if (cleaned && cleaned.length > 1 && cleaned.length < 200) return cleaned;
                                }
                            }
                        }
                    }
                    return null;
                }""", keywords)
                if js_value:
                    result[target_key] = js_value
                    found = True
            except Exception:
                pass

            if found:
                continue

            # 策略 2: 遍历页面元素 + 相邻值提取（原有逻辑）
            for kw in keywords:
                try:
                    label_els = self.page.query_selector_all(f"text={kw}")
                    for le in label_els:
                        if not le.is_visible():
                            continue
                        value = le.evaluate("""(el) => {
                            const sib = el.nextSibling;
                            if (sib && sib.nodeType === 3) {
                                const v = sib.textContent.trim();
                                if (v && v.length > 1 && v.length < 200) return v;
                            }
                            const next = el.nextElementSibling;
                            if (next) {
                                const v = next.innerText.trim();
                                if (v && v.length > 1 && v.length < 200) return v;
                            }
                            const p = el.parentElement;
                            if (p && p.nextElementSibling) {
                                const v = p.nextElementSibling.innerText.trim();
                                if (v && v.length > 1 && v.length < 200) return v;
                            }
                            const parentTd = el.closest('td');
                            if (parentTd) {
                                const nextTd = parentTd.nextElementSibling;
                                if (nextTd) {
                                    const v = nextTd.innerText.trim();
                                    if (v && v.length > 1 && v.length < 200) return v;
                                }
                            }
                            return null;
                        }""")
                        if value:
                            all_kws = [k for kws in key_map.values() for k in kws]
                            if not any(k in value for k in all_kws):
                                result[target_key] = value
                                found = True
                                break
                except Exception:
                    continue
                if found:
                    break

            if not found:
                result[f"{target_key}_reason"] = "页面无该关键词标签或值为空"

        # 策略 2: 从联系摘要中补充（兜底）
        if not any(result.values()):
            try:
                # 尝试用 JS 一次性扫描页面上的联系信息区域
                contact_info = self.page.evaluate(r"""() => {
                    // 查找包含联系信息的区域
                    const contactSections = Array.from(document.querySelectorAll('*')).filter(el => {
                        const t = el.innerText || '';
                        return /(联系人|电话|邮箱|地址|Phone|Email|Contact)/.test(t) &&
                            el.children.length < 10 &&
                            el.innerText.trim().length > 5 &&
                            el.innerText.trim().length < 500;
                    });
                    for (const sec of contactSections) {
                        const lines = sec.innerText.trim().split('\n').map(l => l.trim()).filter(l => l);
                        const info = {};
                        for (const line of lines) {
                            if (/联系人|Contact/.test(line)) {
                                info.contact_name = line.replace(/[:：联系人ContactPersonName\s]*/g, '').trim();
                            } else if (/电话|Phone|Tel|Mobile/.test(line)) {
                                info.phone = line.replace(/[:：电话PhoneTelMobile\s]*/g, '').trim();
                            } else if (/邮箱|Email|E-mail/.test(line)) {
                                info.email = line.replace(/[:：邮箱EmailE-mail\s]*/g, '').trim();
                            } else if (/地址|Address/.test(line)) {
                                info.address = line.replace(/[:：地址Address\s]*/g, '').trim();
                            }
                        }
                        if (Object.keys(info).length > 0) return info;
                    }
                    return null;
                }""")
                if contact_info:
                    for k, v in contact_info.items():
                        if k in result and v and not result[k]:
                            result[k] = v
            except Exception:
                pass

        return result

    # ========================================================================
    # 产品信息页
    # ========================================================================

    def go_to_product_info_tab(self) -> bool:
        """导航到"产品信息"Tab。

        Returns:
            True 如果成功进入产品信息页
        """
        page = self.page
        try:
            clicked = False

            # 尝试 1: 找"产品信息"Tab 并点击
            for sel in [
                "a:has-text('产品信息')",
                "li:has-text('产品信息') a",
                "[data-tab='product']",
                ".tab-product",
                ".ant-tabs-tab:has-text('产品信息')",
            ]:
                try:
                    el = page.wait_for_selector(sel, timeout=3000)
                    if el and el.is_visible():
                        el.click()
                        page.wait_for_timeout(800)
                        page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
                        page.wait_for_timeout(1500)
                        print(f"  [产品] 已点击'产品信息'Tab (selector={sel})")
                        clicked = True
                        break
                except PlaywrightTimeout:
                    continue

            # 尝试 2: 滚动后再找
            if not clicked:
                try:
                    page.evaluate("window.scrollBy(0, 100)")
                    page.wait_for_timeout(500)
                    el = page.query_selector("text=产品信息")
                    if el and el.is_visible():
                        el.click()
                        page.wait_for_timeout(800)
                        page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
                        page.wait_for_timeout(1500)
                        print(f"  [产品] 滚动后找到并点击'产品信息'")
                        clicked = True
                except Exception:
                    pass

            if not clicked:
                print(f"  [产品] 未找到'产品信息'Tab")
                return False

            # 验证: 等待产品信息表格/列表容器出现
            try:
                page.wait_for_function(
                    """() => {
                        const text = document.body.innerText;
                        return /采购产品|供应产品|产品信息|产品名称|HS编码/.test(text);
                    }""",
                    timeout=self.config["load_timeout"],
                )
                print(f"  [产品] 产品信息页已加载")
            except PlaywrightTimeout:
                print(f"  [产品] 产品信息页加载超时，继续使用当前页面")

            return True

        except Exception as e:
            print(f"  [产品] 导航异常: {e}")
            return False

    def extract_top_products(self, max_items: int = 3) -> list[dict]:
        """从产品信息页提取 top N 采购产品。

        必须确认当前子 tab 为"采购产品"。
        过滤掉汇总统计项（原产国数量、供应商数量等）。
        支持三种模式：table / 榜单(div-grid) / 列表(纯文本)
        输出格式: [{"product_name":"...","trade_count":"..."}, ...]
        """
        page = self.page
        EXCLUDE_KEYWORDS = [
            "原产国数量", "供应商数量", "采购总次数", "采购总数量",
            "采购总重量", "平均单价", "首次采购时间", "最新采购时间",
            "采购持续时间", "采购间隔",
            "原产国", "供应商数", "采购总", "采购持续", "采购间隔",
        ]

        JS_EXCLUDE = [
            "原产国数量", "供应商数量", "采购总次数", "采购总数量",
            "采购总重量", "平均单价", "首次采购时间", "最新采购时间",
            "采购持续时间", "采购间隔",
            "原产国", "供应商数", "合计", "总计", "平均",
        ]
        try:
            # ── 诊断：Dump 产品页所有表格结构 ──
            dump = page.evaluate(r"""() => {
                const tables = [];
                for (const t of document.querySelectorAll('table')) {
                    const hdr = (t.innerText || '').substring(0, 500);
                    const trs = Array.from(t.querySelectorAll('tr'));
                    const headerRow = t.querySelector('thead tr') || t.querySelector('tr');
                    const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')).map(c => c.innerText.trim()) : [];
                    const rows = [];
                    for (const tr of trs.slice(0, 6)) {
                        const cells = Array.from(tr.querySelectorAll('td, th'));
                        rows.push(cells.map(c => c.innerText.trim()));
                    }
                    tables.push({ headers, rowCount: trs.length, sampleRows: rows, preview: hdr.substring(0, 200) });
                }
                return tables;
            }""")
            for ti, t in enumerate(dump):
                print(f"  [产品诊断] 表格 {ti+1}: headers={t['headers']}, rows={t['rowCount']}, preview='{t['preview'][:100]}'")
                for ri, row in enumerate(t['sampleRows'][:4]):
                    print(f"  [产品诊断]   行 {ri}: {row}")

            js_result = page.evaluate(r"""(opts) => {
                const maxItems = opts.max_items || 3;

                // ── 确认当前子 tab ──
                let activeTab = '';
                for (const el of document.querySelectorAll('[role="tab"], .ant-tabs-tab-active, .active')) {
                    const t = (el.innerText || '').trim();
                    if (t.includes('采购产品') || t.includes('产品')) { activeTab = t; break; }
                }

                // ── Mode A: 真实 DOM 结构 — 产品榜单项 ──
                const items = document.querySelectorAll(
                    'div[class*="partnerRankingView--item"]'
                );
                const products = [];
                if (items.length > 0) {
                    for (const item of items) {
                        // 产品名
                        const nameEl = item.querySelector(
                            'span[class*="partnerRankingView--name"]'
                        );
                        const nameVal = nameEl ? nameEl.textContent.trim() : '';

                        // 次数
                        const countEl = item.querySelector(
                            'span[class*="rankAndProportion--rankingTimes"]'
                        );
                        let countRaw = countEl ? countEl.textContent.trim() : '';
                        let countVal = '';
                        if (countRaw) {
                            const m = countRaw.match(/(\d[\d,]*)\s*次/);
                            countVal = m ? m[1] : countRaw.replace(/[^0-9,]/g, '');
                        }

                        if (nameVal && nameVal.length >= 2) {
                            products.push({
                                product_name: nameVal,
                                trade_count: countVal,
                            });
                            console.log(`[产品] top_product_item_found: product_name='${nameVal}', trade_count_raw='${countRaw}', trade_count='${countVal}'`);
                        }
                        if (products.length >= maxItems) break;
                    }
                    if (products.length > 0) {
                        return { found: true, products, source: 'ranking_item', mode: 'ranking_item', active_tab: activeTab };
                    }
                }

                // ── Mode B: 标准 table 兜底 ──
                let productTable = null;
                for (const table of document.querySelectorAll('table')) {
                    const hdr = (table.innerText || '').substring(0, 500);
                    if (/产品名称|采购产品|产品明细/.test(hdr)) { productTable = table; break; }
                }
                if (productTable) {
                    const headerRow = productTable.querySelector('thead tr') || productTable.querySelector('tr');
                    if (headerRow) {
                        const headers = Array.from(headerRow.querySelectorAll('th, td')).map(c => c.innerText.trim());
                        let nameIdx = -1, countIdx = -1;
                        for (let i = 0; i < headers.length; i++) {
                            if (/产品名称|产品|品名|商品/.test(headers[i])) nameIdx = i;
                            if (/次数|频次|count|次\s*数|贸易次|贸易次数|采购次数|笔数/i.test(headers[i])) countIdx = i;
                        }
                        if (nameIdx >= 0) {
                            const rows = Array.from(productTable.querySelectorAll('tbody tr, tr')).slice(1);
                            for (const row of rows) {
                                const cells = Array.from(row.querySelectorAll('td, th'));
                                if (cells.length <= nameIdx) continue;
                                const pName = cells[nameIdx].innerText.trim();
                                let pCount = countIdx >= 0 && countIdx < cells.length ? cells[countIdx].innerText.trim() : '';
                                if (pName && pName.length >= 2 && !/合计|总计|平均/.test(pName)) {
                                    products.push({ product_name: pName, trade_count: pCount });
                                    if (products.length >= maxItems) break;
                                }
                            }
                            if (products.length > 0) {
                                return { found: true, products, source: 'table', mode: 'table', active_tab: activeTab };
                            }
                        }
                    }
                }

                return { found: false, reason: '未找到产品数据', active_tab: activeTab, mode: null };
            }""", {"max_items": max_items})

            if not js_result.get("found"):
                print(f"  [产品] 提取失败: {js_result.get('reason', '未知')}, tab={js_result.get('active_tab', '')}, mode={js_result.get('mode')}")
                return []

            products = js_result.get("products", [])
            src = js_result.get("source", "")
            count_source = js_result.get("count_source", "")
            headers_info = js_result.get("headers", [])
            print(f"  [产品] products_block_found=true, count_source={count_source}, source={src}, tab={js_result.get('active_tab', '')}")
            if headers_info:
                hdr_texts = [f"{h['idx']}={h['text']}" for h in headers_info]
                print(f"  [产品] 表头: {', '.join(hdr_texts)}")
            for i, p in enumerate(products):
                tc = p.get('trade_count', '')
                cs = p.get('count_source', count_source)
                node_found = bool(tc)
                print(f"  [产品]   top {i+1}: product_name='{p.get('product_name', '')}', trade_count='{tc}', count_source='{cs}', count_node_found={node_found}")

            return products

        except Exception as e:
            print(f"  [产品] 提取异常: {e}")
            return []

    # ========================================================================
    # 进口分析页
    # ========================================================================

    def go_to_import_analysis(self) -> ImportAnalysis:
        """导航到进口分析页，返回 ImportAnalysis 对象（含 entry/data 状态）。

        同时检测页面是否更偏"出口分析"——如果是，会在日志中明确提示。

        analysis_entry_status:
            entered_confirmed   — 确认进入了进口分析页
            clicked_not_confirmed — 点了入口但未确认进入
            entry_not_found     — 未找到进口分析入口

        analysis_data_status:
            has_data            — 检测到进口数据
            no_data             — 明确显示"暂无进口数据"或无数据
            extraction_failed   — 进入了但提取失败
            unknown             — 未进入，未知
        """
        imp = ImportAnalysis()
        imp.analysis_entry_status = "entry_not_found"
        imp.analysis_data_status = "unknown"

        print(f"  [进口] 开始查找进口分析入口...")
        print(f"  [进口] 当前 URL: {self.page.url}")

        # ── 预检测：页面是否更偏"出口分析" ──
        self._detect_export_vs_import()

        try:
            clicked = False
            # 尝试 1：在详情页内查找进口分析链接/Tab
            for sel in [
                "a:has-text('进口分析')",
                "a:has-text('进口数据')",
                "a:has-text('进口')",
                "[data-tab='import']",
                ".tab-import",
                "li:has-text('进口分析') a",
                "li:has-text('进口') a",
                ".nav-item:has-text('进口分析')",
                ".menu-item:has-text('进口分析')",
            ]:
                try:
                    el = self.page.wait_for_selector(sel, timeout=3000)
                    if el and el.is_visible():
                        el.click()
                        self.page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
                        self.page.wait_for_timeout(1500)
                        print(f"  [进口] 命中 selector: {sel}")
                        print(f"  [进口] 已点击进口分析入口，等待页面加载")
                        clicked = True
                        break
                except PlaywrightTimeout:
                    continue

            # 尝试 2：滚动后再找
            if not clicked:
                try:
                    self.page.evaluate("window.scrollBy(0, 200)")
                    self.page.wait_for_timeout(500)
                    el = self.page.query_selector("text=进口分析")
                    if el and el.is_visible():
                        el.click()
                        self.page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
                        self.page.wait_for_timeout(1500)
                        print(f"  [进口] 滚动后找到进口分析并点击")
                        clicked = True
                except Exception:
                    pass

            if clicked:
                imp.analysis_entry_status = "clicked_not_confirmed"
                # SPA 页面可能需要更长时间渲染内容，额外等待
                self.page.wait_for_timeout(2000)
                # 综合判断是否真正进入
                entry_confirmed = self._confirm_import_page_entry()
                if entry_confirmed:
                    # 进一步验证：是否有实际数据（避免"简版页"仅凭关键词通过确认）
                    data_status = self._detect_import_data_status()
                    if data_status == "no_data" and not entry_confirmed:
                        # 确认进入但确实无数据，也算成功
                        imp.analysis_entry_status = "entered_confirmed"
                        imp.analysis_data_status = data_status
                        print(f"  [进口] 进口分析页进入确认，但无数据 (analysis_entry_status=entered_confirmed, analysis_data_status=no_data)")
                    else:
                        imp.analysis_entry_status = "entered_confirmed"
                        imp.analysis_data_status = data_status
                        if data_status == "has_data":
                            print(f"  [进口] 检测到进口数据 (analysis_data_status=has_data)")
                        elif data_status == "no_data":
                            print(f"  [进口] 进入进口分析页成功，但无进口数据 (analysis_data_status=no_data)")
                        else:
                            print(f"  [进口] 进口数据提取失败 (analysis_data_status=extraction_failed)")
                else:
                    print(f"  [进口] 已点击进口分析入口但未确认进入 (analysis_entry_status=clicked_not_confirmed)")
            else:
                print(f"  [进口] 未找到进口分析入口 (analysis_entry_status=entry_not_found)")
                return imp

            # 如果确认进入了，进一步检查数据状态
            if imp.analysis_entry_status == "entered_confirmed":
                data_status = self._detect_import_data_status()
                imp.analysis_data_status = data_status
                if data_status == "has_data":
                    print(f"  [进口] 检测到进口数据 (analysis_data_status=has_data)")
                elif data_status == "no_data":
                    print(f"  [进口] 进入进口分析页成功，但无进口数据 (analysis_data_status=no_data)")
                else:
                    print(f"  [进口] 进口数据提取失败 (analysis_data_status=extraction_failed)")
            else:
                print(f"  [进口] 未确认进入进口分析页，跳过数据状态检测")

        except Exception as e:
            print(f"  [进口] 导航异常: {e}")
            imp.analysis_entry_status = "entry_not_found"

        return imp

    def _detect_export_vs_import(self):
        """预检测页面是否更偏"出口分析"而非"进口分析"。

        如果页面明显展示出口数据（如顶部 tab 有"出口记录 / 出口分析"），
        会在日志中明确提示，避免把 clicked_not_confirmed 误判为数据问题。
        """
        try:
            page_text = self.page.evaluate("""() => document.body.innerText""")
            has_export = bool(re.search(r"出口(记录|分析|数据)", page_text))
            has_import = bool(re.search(r"进口(记录|分析|数据)", page_text))

            if has_export and not has_import:
                print(f"  [进口] ⚠ 当前样本页面更偏出口分析/出口记录，不适合作为进口分析验证样本")
                print(f"  [进口]   当前版本仅支持进口分析，出口数据不在本 skill 范围内")
            elif has_export and has_import:
                print(f"  [进口] 页面同时包含进出口信息，将尝试进入进口分析")
            elif has_import:
                print(f"  [进口] 页面包含进口分析信息")
            else:
                print(f"  [进口] 页面未检测到明确的进出口分析标识")
        except Exception:
            print(f"  [进口] 页面预检测异常，跳过")

    def _confirm_import_page_entry(self) -> bool:
        """综合判断是否真正进入了进口分析页。

        判定条件（满足任一组合即可）：
        1. URL 包含 import 特征 + 有分析内容容器
        2. 当前活动标签页已切换 + 出现进口分析标题 + 有数据容器/空态文本
        3. 页面内 import 相关 tab 被激活（active 状态）
        4. 兜底：页面正文包含进口分析关键词（SPA 页无 URL 变化时的最后防线）
        """
        page = self.page
        url = page.url.lower()

        # 信号 1: URL 变化
        url_has_import = "import" in url or "importanalysis" in url

        # 信号 2: 页面中有进口分析标题（且是可见的）
        title_found = False
        for text in ["进口分析", "进口数据分析"]:
            try:
                js = page.evaluate(f"""() => {{
                    const allEls = Array.from(document.querySelectorAll('*'));
                    for (const el of allEls) {{
                        const t = (el.innerText || '').trim();
                        if (t === '{text}') {{
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 10) {{
                                const parent = el.closest('[role="tab"], .ant-tabs-tab, .tab-item, [class*="tab"]');
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }}""")
                if js:
                    title_found = True
                    break
            except Exception:
                continue

        # 信号 3: 有分析内容容器（表格、图表、空态提示）
        content_found = False
        for sel in [
            ".import-analysis-content", ".import-data", ".trade-data",
            "[class*='importAnalysis']", "[class*='importData']",
            "[class*='tradeData']", ".ant-table", "table",
        ]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    content_found = True
                    break
            except Exception:
                continue

        # 信号 4: "暂无进口数据"空态文本（也说明进入了分析页）
        empty_state = False
        try:
            el = page.query_selector("text=暂无进口数据")
            if el and el.is_visible():
                empty_state = True
        except Exception:
            pass

        # 信号 5: 进口分析 tab 是否被激活
        tab_active = False
        try:
            js = page.evaluate("""() => {
                const tabs = Array.from(document.querySelectorAll('[role="tab"], .ant-tabs-tab-active, .active, [class*="active"]'));
                for (const tab of tabs) {
                    const t = (tab.innerText || '').trim();
                    if (t.includes('进口')) return true;
                }
                return false;
            }""")
            if js:
                tab_active = True
        except Exception:
            pass

        # 信号 6（兜底）: 页面正文包含进口分析关键词
        body_has_import_text = False
        try:
            js = page.evaluate("""() => {
                const text = document.body ? document.body.innerText : '';
                return /进口分析|进口数据/.test(text);
            }""")
            if js:
                body_has_import_text = True
        except Exception:
            pass

        # 综合判定
        if url_has_import and (content_found or title_found or empty_state):
            print(f"  [进口] 确认信号: URL变化+内容容器/标题/空态")
            return True
        if tab_active and (content_found or empty_state):
            print(f"  [进口] 确认信号: active tab+内容容器/空态")
            return True
        if empty_state:
            print(f"  [进口] 确认信号: 空态文本'暂无进口数据'")
            return True
        if title_found and content_found:
            print(f"  [进口] 确认信号: 标题+内容容器")
            return True
        # 兜底判定：正文包含进口分析关键词
        if body_has_import_text:
            print(f"  [进口] 确认信号: 页面正文包含进口分析关键词 (兜底)")
            return True

        print(f"  [进口] 确认信号不足: url_has_import={url_has_import}, title_found={title_found}, content_found={content_found}, empty_state={empty_state}, tab_active={tab_active}, body_has_import_text={body_has_import_text}")
        return False

    def _detect_import_data_status(self) -> str:
        """检测进口分析页的数据状态。

        不再只靠日期和"暂无进口数据"。
        以下任一出现即视为 has_data：
        - 环图/排行区出现
        - HS 编码表出现
        - 供应商/出口商表出现

        Returns: has_data / no_data / extraction_failed
        """
        page = self.page
        try:
            js = page.evaluate(r"""() => {
                const bodyText = document.body.innerText || '';

                // 明确无数据
                if (/暂无进口数据|暂无数据|No data found/.test(bodyText)) {
                    return 'no_data';
                }

                // 信号 A: 环图/排行区
                const hasRing = Array.from(document.querySelectorAll('*')).some(el => {
                    const cls = (el.className || '').toString().toLowerCase();
                    const tag = el.tagName.toLowerCase();
                    if (tag === 'svg' || tag === 'canvas') return true;
                    return /chart|ring|donut|pie|echarts|antv/.test(cls);
                });

                // 信号 B: HS 编码表（按表头识别）
                let hsTable = null;
                for (const table of document.querySelectorAll('table')) {
                    const hdr = (table.innerText || '').substring(0, 300);
                    if (/HS编码|HS Code|商品编码/.test(hdr)) {
                        hsTable = table;
                        break;
                    }
                }

                // 信号 C: 供应商表（按表头识别）
                let supplierTable = null;
                for (const table of document.querySelectorAll('table')) {
                    const hdr = (table.innerText || '').substring(0, 300);
                    if (/出口商|供应商|采购商|采购商名称/.test(hdr)) {
                        supplierTable = table;
                        break;
                    }
                }

                // 信号 D: 表格数据行
                let dataRowCount = 0;
                for (const table of document.querySelectorAll('table')) {
                    const rows = table.querySelectorAll('tbody tr, tr');
                    for (const row of Array.from(rows).slice(1)) {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 3) {
                            const txt = row.innerText.trim();
                            if (txt.length > 20 && !/^序号/.test(txt)) {
                                dataRowCount++;
                            }
                        }
                    }
                }

                // 信号 E: 日期出现
                const hasDate = /\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(bodyText);

                return JSON.stringify({
                    has_ring: hasRing,
                    hs_table: !!hsTable,
                    supplier_table: !!supplierTable,
                    data_rows: dataRowCount,
                    has_date: hasDate,
                });
            }""")

            if not js:
                return "extraction_failed"

            if isinstance(js, str):
                import json as _json
                try:
                    sigs = _json.loads(js)
                except Exception:
                    sigs = {"data_rows": 0, "has_date": False, "has_ring": False, "hs_table": False, "supplier_table": False}
            else:
                sigs = js

            print(f"  [进口] 数据状态信号: ring={sigs.get('has_ring')}, "
                  f"HS表={sigs.get('hs_table')}, 供应商表={sigs.get('supplier_table')}, "
                  f"数据行={sigs.get('data_rows')}, 日期={sigs.get('has_date')}")

            # 必须有真实数据行 + 至少一个结构化信号（HS表/供应商表），
            # 不再仅凭 SVG/canvas（has_ring）就判定 has_data
            has_real_table = (sigs.get("hs_table") or sigs.get("supplier_table")) and sigs.get("data_rows", 0) > 0
            if has_real_table:
                return "has_data"

            if sigs.get("data_rows", 0) == 0 and sigs.get("has_date", False) is False:
                return "no_data"

            return "extraction_failed"

        except Exception as e:
            print(f"  [进口] 数据状态检测异常: {e}")
            return "extraction_failed"

    def extract_import_analysis(self, imp: ImportAnalysis, target_hs_codes: list[str] | None = None) -> ImportAnalysis:
        """从进口分析页提取进口数据。

        拆分为多个独立 extractor：
        A. _extract_stats_cards: 统计卡片 overviewReport (label-value 配对)
        B. _extract_hs_amounts: HS 表格，按目标 HS 编码提取 hs_code + usd_amount
        C. _extract_top_suppliers: 滚动到供应商区块，提取前3行 supplier_name + trade_count + usd_amount
        D. _extract_trade_records: 贸易明细表完整提取（日期、产品、HS、供应商等）
        E. _extract_import_base_info: 基础字段（日期和贸易次数备选）

        analysis_data_status 只表示页面是否存在分析数据块，
        不直接推导 no_data。
        """
        import json as _json
        import re as _re
        print(f"  [进口] 开始提取进口数据字段...")
        if target_hs_codes:
            print(f"  [进口] 目标 HS 编码: {target_hs_codes}")

        # ── 诊断：扫描进口分析页所有 tbody 结构 ──
        tbody_dump = self.page.evaluate(r"""() => {
            const bodies = [];
            const tbodies = document.querySelectorAll('tbody.tendata-ui-table-tbody');
            for (const tbody of tbodies) {
                // 找最近的父容器标题
                let parent = tbody.parentElement;
                let sectionTitle = '';
                for (let depth = 0; depth < 10 && parent; depth++) {
                    // 查找标题元素
                    const headings = parent.querySelectorAll('h1, h2, h3, h4, h5, [class*="title"], [class*="section"], [class*="header"]');
                    for (const h of headings) {
                        const t = (h.innerText || '').trim();
                        if (t.length > 2 && t.length < 100 && /HS|海关|商品编码|供应商|top.*供|出口商|进口|分析/.test(t)) {
                            sectionTitle = t;
                            break;
                        }
                    }
                    if (sectionTitle) break;
                    // 也检查 class 名中的关键词
                    const cls = parent.className || '';
                    if (/hs|supplier|export|import|analysis/.test(cls.toLowerCase())) {
                        sectionTitle = '[class:' + cls.substring(0, 80) + ']';
                        break;
                    }
                    parent = parent.parentElement;
                }

                // 统计 data-row-key 行
                const rows = Array.from(tbody.querySelectorAll('tr.tendata-ui-table-row[data-row-key]'));
                const rowCount = rows.length;

                // 第一行前 8 列预览
                let firstRowPreview = [];
                if (rowCount > 0) {
                    const firstRow = rows[0];
                    const cells = firstRow.querySelectorAll('td');
                    for (let i = 0; i < Math.min(8, cells.length); i++) {
                        firstRowPreview.push(cells[i].innerText.trim().substring(0, 60));
                    }
                }

                bodies.push({
                    sectionTitle,
                    rowCount,
                    firstRowPreview,
                    columnCount: rows.length > 0 ? rows[0].querySelectorAll('td').length : 0,
                });
            }

            // 补充：当前 URL 和可见的 tab/模块标题
            const allSectionTitles = [];
            for (const el of document.querySelectorAll('h1, h2, h3, h4, h5, span, div')) {
                const t = (el.innerText || '').trim();
                const r = el.getBoundingClientRect();
                if (r.width > 50 && r.height > 10 && /HS|海关|供应商|top.*供|出口商|进口分析|采购产品|基本信息/.test(t) && t.length < 100) {
                    allSectionTitles.push(t);
                }
            }

            return {
                tbodyCount: tbodies.length,
                bodies,
                visibleSectionTitles: [...new Set(allSectionTitles)].slice(0, 20),
            };
        }""")

        print(f"  [进口诊断] 页面共有 {tbody_dump['tbodyCount']} 个 tbody.tendata-ui-table-tbody")
        for i, body in enumerate(tbody_dump['bodies']):
            print(f"  [进口诊断]   tbody #{i+1}: sectionTitle='{body['sectionTitle']}', "
                  f"rows={body['rowCount']}, cols={body['columnCount']}, "
                  f"firstRowPreview={body['firstRowPreview'][:6]}")
        print(f"  [进口诊断] 可见模块标题: {tbody_dump['visibleSectionTitles']}")

        # 先等待页面数据加载完成
        self.page.wait_for_timeout(3000)

        # A. 统计卡片提取器 (overviewReport)
        cards = self._extract_stats_cards()
        if cards.get("latest_import_date"):
            imp.latest_import_date = cards["latest_import_date"]
            print(f"  [进口] 统计卡片-最近一次进口记录: '{imp.latest_import_date}'")
        if cards.get("supplier_count"):
            imp.stats_card_supplier_count = str(cards["supplier_count"])
            print(f"  [进口] 统计卡片-供应商: {cards['supplier_count']}")
        if cards.get("trade_count"):
            imp.total_records = cards["trade_count"]
            print(f"  [进口] 统计卡片-贸易次数: {cards['trade_count']}")
        if cards.get("total_value_usd"):
            imp.stats_card_total_value_usd = cards["total_value_usd"]
        if cards.get("total_weight_kg"):
            imp.stats_card_total_weight_kg = cards["total_weight_kg"]
        if cards.get("total_quantity"):
            imp.stats_card_total_quantity = cards["total_quantity"]

        # B. HS 金额提取器 → (data, diagnostics)
        hs_amounts, hs_diag = self._extract_hs_amounts(target_hs_codes or [])
        if hs_amounts is not None and len(hs_amounts) > 0:
            imp.target_hs_amount_json = _json.dumps(hs_amounts, ensure_ascii=False)
        print(f"  [进口] hs_rows_total={hs_diag.get('hs_rows_total', 0)}, "
              f"matched_hs_rows_count={hs_diag['matched_hs_rows_count']}, "
              f"target_hs_amount_json={'已填充' if imp.target_hs_amount_json else '空'}")

        # C. top 供应商提取器 → (data, diagnostics)
        suppliers, sup_diag = self._extract_top_suppliers()
        if suppliers is not None and len(suppliers) > 0:
            imp.top_suppliers_json = _json.dumps(suppliers, ensure_ascii=False)
        print(f"  [进口] suppliers_rows_found={sup_diag['suppliers_rows_found']}, "
              f"top_suppliers_json={'已填充' if imp.top_suppliers_json else '空'}")

        # D. 进口国家提取器 → (data, diagnostics)
        countries, ctry_diag = self._extract_import_countries()
        if countries is not None and len(countries) > 0:
            imp.top_3_import_countries_json = _json.dumps(countries, ensure_ascii=False)
        print(f"  [进口] countries_found={ctry_diag.get('countries_found', 0)}, "
              f"top_3_import_countries_json={'已填充' if imp.top_3_import_countries_json else '空'}")

        # E. 贸易明细表完整提取 → (data, diagnostics)
        trade_records, trade_diag = self._extract_trade_records()
        if trade_records:
            # 提取日期列表
            dates = [r.get("date", "") for r in trade_records if r.get("date")]
            imp.trade_dates = dates
            # 提取产品列表
            products = [r.get("product", "") for r in trade_records if r.get("product")]
            imp.trade_products = products
            # 提取产品描述
            descriptions = [r.get("product_description", "") for r in trade_records if r.get("product_description")]
            imp.trade_product_descriptions = descriptions
            # 提取 HS 编码（过滤 N/A、-、空值）
            hs_codes = [r.get("hs_code", "") for r in trade_records if r.get("hs_code") and r["hs_code"] not in ("N/A", "-", "", "n/a")]
            imp.trade_hs_codes = list(dict.fromkeys(hs_codes))  # 去重保序
            # 提取供应商/出口商
            suppliers_list = [r.get("exporter", "") for r in trade_records if r.get("exporter")]
            imp.trade_suppliers = list(dict.fromkeys(suppliers_list))  # 去重保序
            # 提取原产国
            countries_list = [r.get("origin_country", "") for r in trade_records if r.get("origin_country")]
            imp.trade_countries = list(dict.fromkeys(countries_list))
            # 如果统计卡片没有日期，用贸易明细表的最大日期作为 latest_import_date
            if not imp.latest_import_date and dates:
                try:
                    from datetime import datetime as _dt
                    parsed_dates = []
                    for d in dates:
                        try:
                            parsed_dates.append(_dt.strptime(d, "%Y-%m-%d"))
                        except ValueError:
                            try:
                                parsed_dates.append(_dt.strptime(d, "%Y/%m/%d"))
                            except ValueError:
                                pass
                    if parsed_dates:
                        imp.latest_import_date = max(parsed_dates).strftime("%Y-%m-%d")
                        print(f"  [进口] 贸易明细表-最新日期(备选): '{imp.latest_import_date}'")
                except Exception:
                    pass
            print(f"  [进口] 贸易明细表: 提取到 {len(trade_records)} 条记录, {len(imp.trade_hs_codes)} 个HS编码, {len(imp.trade_suppliers)} 个供应商")

        # E2. 供应商排名表 (partnerReport)
        partner_suppliers, partner_diag = self._extract_partner_suppliers()
        if partner_suppliers:
            imp.partner_suppliers = partner_suppliers
            print(f"  [进口] 供应商排名表: {len(partner_suppliers)} 家")

        # F. 基础字段备选：日期和贸易次数（仅当统计卡片和明细表都没有时）
        if not imp.latest_import_date or imp.total_records == 0:
            base_info = self._extract_import_base_info()
            if not imp.latest_import_date and base_info.get("latest_date"):
                imp.latest_import_date = base_info["latest_date"]
                print(f"  [进口] 基础信息-最新进口日期(备选2): '{imp.latest_import_date}'")
            if imp.total_records == 0 and base_info.get("trade_count", 0) > 0:
                imp.trade_count = base_info["trade_count"]
                imp.total_records = base_info["trade_count"]
                print(f"  [进口] 基础信息-贸易记录数(备选2): {imp.trade_count}")

        # G. analysis_data_status 只表示数据块是否存在
        if imp.analysis_entry_status == "entered_confirmed":
            hs_table_exists = (hs_amounts is not None)
            supplier_exists = (suppliers is not None)
            country_exists = (countries is not None and len(countries) > 0)
            base_exists = bool(base_info.get("has_data_block"))
            has_trade_records = bool(trade_records)
            if hs_table_exists or supplier_exists or country_exists or base_exists or has_trade_records:
                imp.analysis_data_status = "has_data"
                print(f"  [进口] 检测到进口数据块 (analysis_data_status=has_data)")
            else:
                imp.analysis_data_status = "no_data"
                print(f"  [进口] 未检测到进口数据块 (analysis_data_status=no_data)")
        else:
            print(f"  [进口] 未确认进入进口分析页，跳过数据提取")

        return imp

    def _extract_stats_cards(self) -> dict:
        """从统计卡片区 overviewReport 提取 label-value 配对。

        DOM 结构：
        <div class="overviewReport--cardLabel">最近一次进口记录</div>
        <div class="overviewReport--cardValue">2026-04-04</div>

        映射：
        最近一次进口记录 → latest_import_date
        供应商 → supplier_count
        贸易次数 → trade_count
        美元总价 → total_value_usd
        千克重量 → total_weight_kg
        贸易数量 → total_quantity
        """
        import re as _re
        try:
            result = self.page.evaluate(r"""() => {
                const cards = {};
                const labels = document.querySelectorAll('[class*="overviewReport--cardLabel"], [class*="overviewReport--card-label"], [class*="statCard--label"]');
                for (const labelEl of labels) {
                    const label = (labelEl.innerText || '').trim();
                    if (!label) continue;
                    // 找相邻的 cardValue 元素
                    let valueEl = labelEl.nextElementSibling;
                    while (valueEl && !valueEl.className) {
                        valueEl = valueEl.nextElementSibling;
                    }
                    // 也尝试找父容器内的下一个 cardValue
                    if (!valueEl || !valueEl.className || !valueEl.className.includes('cardValue')) {
                        // 尝试通过父容器找 value
                        const parent = labelEl.parentElement;
                        if (parent) {
                            const siblings = parent.querySelectorAll('[class*="cardValue"], [class*="card-value"]');
                            for (const s of siblings) {
                                if (s.compareDocumentPosition(labelEl) & Node.DOCUMENT_POSITION_PRECEDING) {
                                    valueEl = s;
                                    break;
                                }
                            }
                        }
                    }
                    let value = '';
                    if (valueEl) {
                        value = (valueEl.innerText || '').trim();
                    }

                    // 映射 label → key
                    if (label.includes('最近一次') || label.includes('最新') || label.includes('进口记录')) {
                        cards.latest_import_date = value;
                    } else if (label === '供应商' || label === '出口商' || label === '供应商数') {
                        cards.supplier_count = parseInt(value.replace(/,/g, '')) || value;
                    } else if (label === '贸易次数' || label === '交易次数') {
                        cards.trade_count = parseInt(value.replace(/,/g, '')) || 0;
                    } else if (label.includes('美元') || label.includes('总价') || label.includes('USD')) {
                        cards.total_value_usd = value;
                    } else if (label.includes('千克') || label.includes('重量')) {
                        cards.total_weight_kg = value;
                    } else if (label.includes('贸易数量') || label.includes('数量')) {
                        cards.total_quantity = value;
                    }
                }

                // 备选方案：通过 cardValue 反向查找
                if (Object.keys(cards).length === 0) {
                    const values = document.querySelectorAll('[class*="overviewReport--cardValue"], [class*="overviewReport--card-value"], [class*="statCard--value"]');
                    for (const valEl of values) {
                        const value = (valEl.innerText || '').trim();
                        if (!value) continue;
                        // 找前一个 label 元素
                        let labelEl = valEl.previousElementSibling;
                        while (labelEl && labelEl.tagName === 'DIV') {
                            const label = (labelEl.innerText || '').trim();
                            if (label && label.length < 30) {
                                if (label.includes('最近一次') || label.includes('最新') || label.includes('进口记录')) {
                                    cards.latest_import_date = value;
                                } else if (label === '供应商' || label === '出口商') {
                                    cards.supplier_count = parseInt(value.replace(/,/g, '')) || value;
                                } else if (label === '贸易次数') {
                                    cards.trade_count = parseInt(value.replace(/,/g, '')) || 0;
                                } else if (label.includes('美元') || label.includes('总价')) {
                                    cards.total_value_usd = value;
                                } else if (label.includes('千克') || label.includes('重量')) {
                                    cards.total_weight_kg = value;
                                } else if (label.includes('贸易数量') || label.includes('数量')) {
                                    cards.total_quantity = value;
                                }
                                break;
                            }
                            labelEl = labelEl.previousElementSibling;
                        }
                    }
                }

                return cards;
            }""")

            print(f"  [进口] 统计卡片提取: {result}")
            return result
        except Exception as e:
            print(f"  [进口] _extract_stats_cards 异常: {e}")
            return {}

    def _extract_trade_records(self) -> tuple[list[dict] | None, dict]:
        """从贸易记录明细表 recordView 提取完整贸易记录。

        表头：日期/进口商/出口商/海关编码/产品/产品描述/原产国/目的国/美元总价/千克重量/数量/重量美元单价/数量美元单价/数量单位

        列索引：0=查看, 1=日期, 2=进口商, 3=出口商, 4=海关编码,
               5=产品, 6=产品描述, 7=原产国, 8=目的国, 9=美元总价,
               10=千克重量, 11=数量, 12=重量美元单价, 13=数量美元单价, 14=数量单位
        """
        diag = {"trade_rows_total": 0, "record_view_found": False, "table_found": False}
        try:
            result = self.page.evaluate(r"""() => {
                const records = [];

                const recordView = document.querySelector('div[class*="recordView--container"]');
                if (!recordView) {
                    return { found: false, records: [], record_view_found: false };
                }

                const table = recordView.querySelector('table');
                if (!table) {
                    return { found: false, records: [], record_view_found: true };
                }

                const tbody = table.querySelector('tbody');
                if (!tbody) {
                    return { found: false, records: [], record_view_found: true, table_found: true };
                }

                const rows = tbody.querySelectorAll('tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    // 至少 10 列才认为是数据行
                    if (cells.length >= 10) {
                        const record = {
                            date: cells[1] ? cells[1].innerText.trim() : '',
                            importer: cells[2] ? cells[2].innerText.trim() : '',
                            exporter: cells[3] ? cells[3].innerText.trim() : '',
                            hs_code: cells[4] ? cells[4].innerText.trim() : '',
                            product: cells[5] ? cells[5].innerText.trim() : '',
                            product_description: cells[6] ? cells[6].innerText.trim() : '',
                            origin_country: cells[7] ? cells[7].innerText.trim() : '',
                            destination_country: cells[8] ? cells[8].innerText.trim() : '',
                            total_usd: cells[9] ? cells[9].innerText.trim() : '',
                            weight_kg: cells.length > 10 ? cells[10].innerText.trim() : '',
                            quantity: cells.length > 11 ? cells[11].innerText.trim() : '',
                            unit_price_weight: cells.length > 12 ? cells[12].innerText.trim() : '',
                            unit_price_quantity: cells.length > 13 ? cells[13].innerText.trim() : '',
                            quantity_unit: cells.length > 14 ? cells[14].innerText.trim() : '',
                        };
                        records.push(record);
                    }
                }

                return {
                    found: records.length > 0,
                    records,
                    record_view_found: true,
                    table_found: true,
                    tbody_found: true,
                };
            }""")

            records = result.get("records", [])
            diag["trade_rows_total"] = len(records)
            diag["record_view_found"] = result.get("record_view_found", False)
            diag["table_found"] = result.get("table_found", False)

            if not records:
                print(f"  [进口] 贸易明细表无数据 (recordView={diag['record_view_found']}, table={diag['table_found']})")
                return None, diag

            print(f"  [进口] 贸易明细表提取: {len(records)} 条记录")
            # 打印前 2 条样例
            for i, r in enumerate(records[:2]):
                print(f"  [进口]   记录{i+1}: 日期={r['date']}, 出口商={r['exporter'][:40]}, "
                      f"HS={r['hs_code']}, 产品={r['product'][:30]}, "
                      f"原产国={r['origin_country']}, 美元={r['total_usd']}")

            return records, diag
        except Exception as e:
            print(f"  [进口] _extract_trade_records 异常: {e}")
            return None, diag

    def _extract_partner_suppliers(self) -> tuple[list[dict] | None, dict]:
        """从供应商排名区 partnerReport 提取供应商排名。

        表头：出口商/贸易次数/次数占比/千克重量/重量占比/数量/数量占比
        """
        diag = {"partner_rows": 0, "partner_section_found": False}
        try:
            result = self.page.evaluate(r"""() => {
                const suppliers = [];

                // 查找 partnerReport 区域
                const partnerSections = document.querySelectorAll(
                    'div[class*="partnerReport--container"], ' +
                    'div[class*="partnerRankingView--container"], ' +
                    'div[class*="supplierRanking"]'
                );

                for (const section of partnerSections) {
                    const table = section.querySelector('table');
                    if (!table) continue;
                    const tbody = table.querySelector('tbody');
                    if (!tbody) continue;

                    // 检查表头是否包含"出口商"或"供应商"
                    const headers = table.querySelectorAll('thead th, thead td');
                    let isPartnerTable = false;
                    for (const th of headers) {
                        const text = (th.innerText || '').trim();
                        if (/出口商|供应商|exporter|supplier/.test(text)) {
                            isPartnerTable = true;
                            break;
                        }
                    }
                    if (!isPartnerTable) continue;

                    const rows = tbody.querySelectorAll('tr');
                    for (const row of rows) {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 4) {
                            suppliers.push({
                                supplier_name: cells[0] ? cells[0].innerText.trim() : '',
                                trade_count: cells[1] ? cells[1].innerText.trim() : '',
                                trade_count_pct: cells[2] ? cells[2].innerText.trim() : '',
                                weight_kg: cells[3] ? cells[3].innerText.trim() : '',
                                weight_pct: cells.length > 4 ? cells[4].innerText.trim() : '',
                                quantity: cells.length > 5 ? cells[5].innerText.trim() : '',
                                quantity_pct: cells.length > 6 ? cells[6].innerText.trim() : '',
                            });
                        }
                    }
                }

                return { found: suppliers.length > 0, suppliers };
            }""")

            suppliers = result.get("suppliers", [])
            diag["partner_rows"] = len(suppliers)
            diag["partner_section_found"] = True

            if not suppliers:
                print(f"  [进口] 供应商排名表无数据")
                return None, diag

            print(f"  [进口] 供应商排名表提取: {len(suppliers)} 家供应商")
            for i, s in enumerate(suppliers[:3]):
                print(f"  [进口]   供应商{i+1}: {s['supplier_name'][:40]}, 贸易次数={s['trade_count']}")

            return suppliers, diag
        except Exception as e:
            print(f"  [进口] _extract_partner_suppliers 异常: {e}")
            return None, diag

    def _extract_hs_amounts(self, target_hs_codes: list[str]) -> tuple[list[dict] | None, dict]:
        """从进口分析页统一表格中提取 HS 编码 + 美元金额。

        真实结构：div[class*='recordView--container'] 内的单一表格
        表头：查看/日期/进口商/出口商/海关编码/产品/产品描述/原产国/目的国/美元总价/...
        第4列=hs_code(col_index=4), 第9列=usd_amount(col_index=9)

        Returns:
            (data, diagnostics) 元组
        """
        diag = {
            "hs_rows_total": 0,
            "matched_hs_rows_count": 0,
            "target_hs_amount_json": "",
            "hs_table_found_in_container": False,
        }
        try:
            result = self.page.evaluate(r"""() => {
                const allRows = [];

                // 定位 recordView 容器内的表格
                const recordView = document.querySelector('div[class*="recordView--container"]');
                if (!recordView) {
                    return { found: false, rows: [], record_view_found: false };
                }

                const table = recordView.querySelector('table');
                if (!table) {
                    return { found: false, rows: [], record_view_found: true, table_found: false };
                }

                const tbody = table.querySelector('tbody');
                if (!tbody) {
                    return { found: false, rows: [], record_view_found: true, table_found: true, tbody_found: false };
                }

                const rows = tbody.querySelectorAll('tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    // 表格列数 >= 10 才认为是数据行
                    if (cells.length >= 10) {
                        const hs_code = cells[4] ? cells[4].innerText.trim() : '';
                        const usd_amount = cells[9] ? cells[9].innerText.trim() : '';
                        const supplier = cells[3] ? cells[3].innerText.trim() : '';
                        const date = cells[1] ? cells[1].innerText.trim() : '';
                        if (hs_code && /^\d/.test(hs_code)) {
                            allRows.push({ hs_code, usd_amount, supplier_name: supplier, date, source: 'recordView_table' });
                        }
                    }
                }

                return {
                    found: allRows.length > 0,
                    rows: allRows,
                    record_view_found: true,
                    table_found: true,
                    tbody_found: true,
                };
            }""")

            rows = result.get("rows", [])
            diag["hs_rows_total"] = len(rows)
            diag["hs_table_found_in_container"] = result.get("table_found", False)

            if not result.get("found"):
                print(f"  [进口] hs_block_not_found "
                      f"(recordView={result.get('record_view_found')}, "
                      f"table={result.get('table_found')}, "
                      f"tbody={result.get('tbody_found')}, "
                      f"rows_extracted={len(rows)})")
                return None, diag

            # 去重
            seen = set()
            unique_rows = []
            for r in rows:
                key = f"{r['hs_code']}_{r.get('usd_amount', '')}_{r.get('date', '')}"
                if key not in seen:
                    seen.add(key)
                    unique_rows.append(r)
            rows = unique_rows

            # 匹配目标 HS 编码
            matched = []
            if target_hs_codes:
                for row in rows:
                    for target in target_hs_codes:
                        if target.replace("-", "").replace(" ", "") in row["hs_code"].replace("-", "").replace(" ", ""):
                            matched.append(row)
                            break
            else:
                matched = rows

            diag["matched_hs_rows_count"] = len(matched)

            # 打印前3行样例日志
            for i, row in enumerate(rows[:3]):
                print(f"  [进口] hs_row_{i+1}_code='{row['hs_code']}' "
                      f"hs_row_{i+1}_usd_amount='{row.get('usd_amount', '')}' "
                      f"source={row.get('source', 'unknown')}")

            if matched:
                import json as _json
                diag["target_hs_amount_json"] = _json.dumps(matched, ensure_ascii=False)
                for item in matched:
                    print(f"  [进口] matched hs_code='{item['hs_code']}' usd_amount='{item.get('usd_amount', '')}'")

            return matched if matched else rows, diag

        except Exception as e:
            print(f"  [进口] _extract_hs_amounts 异常: {e}")
            return None, diag

    def _extract_top_suppliers(self) -> tuple[list[dict] | None, dict]:
        """从进口分析页统一表格中提取前 3 家供应商（按出口商去重）。

        真实结构：div[class*='recordView--container'] 内的单一表格
        第3列=出口商(supplier_name), 按出现频次排序

        Returns:
            (data, diagnostics) 元组
        """
        diag = {
            "suppliers_rows_found": 0,
            "top_suppliers_json": "",
            "suppliers_table_found_in_container": False,
        }
        try:
            result = self.page.evaluate(r"""() => {
                const recordView = document.querySelector('div[class*="recordView--container"]');
                if (!recordView) {
                    return { found: false, suppliers: [], total: 0, record_view_found: false };
                }
                const table = recordView.querySelector('table');
                if (!table) {
                    return { found: false, suppliers: [], total: 0, record_view_found: true, table_found: false };
                }
                const tbody = table.querySelector('tbody');
                if (!tbody) {
                    return { found: false, suppliers: [], total: 0, record_view_found: true, table_found: true, tbody_found: false };
                }
                const rows = tbody.querySelectorAll('tr');

                // 逐行提取：col[3]=出口商, col[9]=美元总价
                const allRows = [];
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 10) {
                        const supplier = cells[3] ? cells[3].innerText.trim() : '';
                        const usd_raw = cells[9] ? cells[9].innerText.trim() : '';
                        // 清理金额：去掉千位分隔符
                        const usd_amount = usd_raw.replace(/,/g, '') || '';
                        if (supplier && supplier.length > 2) {
                            allRows.push({ supplier, usd_amount });
                        }
                    }
                }

                // 输出前 3 行完整列预览
                const colTexts = {};
                for (let i = 0; i < Math.min(3, rows.length); i++) {
                    const cells = rows[i].querySelectorAll('td');
                    colTexts[`row_${i+1}_col_texts`] = Array.from(cells).map(c => c.innerText.trim());
                }

                // 按供应商聚合：汇总 USD 金额
                const supplierMap = {};
                for (const r of allRows) {
                    if (!supplierMap[r.supplier]) {
                        supplierMap[r.supplier] = { count: 0, usd_total: 0 };
                    }
                    supplierMap[r.supplier].count += 1;
                    const amt = parseFloat(r.usd_amount);
                    if (!isNaN(amt)) {
                        supplierMap[r.supplier].usd_total += amt;
                    }
                }

                // 按贸易次数排序，取前 3
                const sorted = Object.entries(supplierMap)
                    .sort((a, b) => b[1].count - a[1].count)
                    .slice(0, 3)
                    .map(([name, data]) => ({
                        supplier_name: name,
                        trade_count: String(data.count),
                        usd_amount: data.usd_total > 0 ? data.usd_total.toFixed(2) : ''
                    }));

                return {
                    found: sorted.length > 0,
                    suppliers: sorted,
                    total: Object.keys(supplierMap).length,
                    record_view_found: true,
                    table_found: true,
                    tbody_found: true,
                    colTexts
                };
            }""")

            if not result:
                print(f"  [进口] suppliers_block_not_found (evaluate 返回空)")
                return None, diag

            if not result.get("found"):
                print(f"  [进口] suppliers_block_not_found "
                      f"(recordView={result.get('record_view_found')}, "
                      f"table={result.get('table_found')}, "
                      f"tbody={result.get('tbody_found')})")
                return None, diag

            suppliers = result.get("suppliers", [])
            total = result.get("total", 0)
            diag["suppliers_rows_found"] = len(suppliers)
            diag["suppliers_table_found_in_container"] = result.get("table_found", False)

            # 输出前 3 行完整列预览
            colTexts = result.get("colTexts", {})
            for i in range(1, 4):
                key = f"row_{i}_col_texts"
                if key in colTexts:
                    diag[f"supplier_{key}"] = colTexts[key]
                    print(f"  [进口] supplier_row_{i}_col_texts={colTexts[key]}")

            print(f"  [进口] 供应商共 {total} 家, 提取前 {len(suppliers)} 家")

            if len(suppliers) > 0:
                import json as _json
                diag["top_suppliers_json"] = _json.dumps(suppliers, ensure_ascii=False)
                for i, s in enumerate(suppliers[:3]):
                    name = s['supplier_name'].encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                    diag[f"supplier_row_{i+1}_usd_amount"] = s.get('usd_amount', '')
                    print(f"  [进口] supplier_row_{i+1}_name='{name}' "
                          f"supplier_row_{i+1}_trade_count='{s['trade_count']}' "
                          f"supplier_row_{i+1}_usd_amount='{s.get('usd_amount', '')}'")
            else:
                print(f"  [进口] 供应商区块存在但未提取到数据")

            return suppliers, diag

        except Exception as e:
            print(f"  [进口] _extract_top_suppliers 异常: {str(e)[:200]}")
            return None, diag

    def _extract_import_countries(self) -> tuple[list[dict] | None, dict]:
        """从进口分析页提取 Top 3 进口国家。

        真实 DOM 结构：
        - 锚点：div.marketReport--title-- 文本包含"原产地分析"
        - 外层：div.marketReport--container--
        - 排行容器：div.marketReport--RatioDisplay-- > div.rankingView--container--
        - 表格 body：tbody.tendata-ui-table-tbody
        - 行：tr.tendata-ui-table-row
        - 国家名：div.rankingView--noLinkContent--
        - 右列：贸易次数
        """
        diag = {"countries_found": 0}
        try:
            result = self.page.evaluate(r"""() => {
                // ── 1. 定位"原产地分析"模块 ──
                let originAnalysisModule = null;
                const titleEls = document.querySelectorAll('div[class*="marketReport--title--"]');
                for (const titleEl of titleEls) {
                    const text = (titleEl.innerText || '').trim();
                    if (text === '原产地分析' || text.includes('原产地分析')) {
                        // 向上找 marketReport--container-- 作为模块根
                        originAnalysisModule = titleEl.closest('div[class*="marketReport--container--"]');
                        break;
                    }
                }

                if (!originAnalysisModule) {
                    return { found: false, countries: [], reason: 'origin_analysis_module_not_found' };
                }

                // ── 2. 在模块内找 rankingView 表格 ──
                let rankingContainer = originAnalysisModule.querySelector('div[class*="rankingView--container--"], div[class*="rankingView--container"]');
                if (!rankingContainer) {
                    // 回退：找 div.marketReport--RatioDisplay-- 内的 rankingView
                    const ratioDisplay = originAnalysisModule.querySelector('div[class*="marketReport--RatioDisplay--"]');
                    if (ratioDisplay) {
                        rankingContainer = ratioDisplay.querySelector('div[class*="rankingView--container--"], div[class*="rankingView--container"]');
                    }
                }
                if (!rankingContainer) {
                    return { found: false, countries: [], reason: 'rankingView_in_module_not_found' };
                }

                // ── 3. 找 tbody ──
                let tbody = rankingContainer.querySelector('tbody[class*="tendata-ui-table-tbody"]');
                if (!tbody) {
                    tbody = rankingContainer.querySelector('tbody');
                }
                if (!tbody) {
                    return { found: false, countries: [], reason: 'tbody_not_found' };
                }

                // ── 4. 找行 ──
                let rows = tbody.querySelectorAll('tr[class*="tendata-ui-table-row"]');
                if (!rows || rows.length === 0) {
                    rows = tbody.querySelectorAll('tr');
                }
                if (!rows || rows.length === 0) {
                    return { found: false, countries: [], reason: 'no_rows_found' };
                }

                // ── 5. 取前3行 ──
                const countries = [];
                for (let i = 0; i < Math.min(rows.length, 3); i++) {
                    const row = rows[i];

                    // 国家名：div[class*='rankingView--noLinkContent--']
                    let countryEl = row.querySelector('div[class*="rankingView--noLinkContent--"]');
                    if (!countryEl) {
                        // 回退：取 td 文本
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 2) {
                            let rawCountry = cells[0] ? cells[0].innerText.trim() : '';
                            rawCountry = rawCountry.replace(/^\d+\.\s*/, '').trim();
                            const tradeCount = cells[1] ? cells[1].innerText.trim() : '';
                            const countMatch = tradeCount.match(/(\d[\d,]*)/);
                            const count = countMatch ? countMatch[1].replace(/,/g, '') : '';
                            if (rawCountry && rawCountry.length > 1) {
                                countries.push({
                                    country: rawCountry,
                                    trade_count: count,
                                    source: 'table_cells_fallback',
                                });
                            }
                        }
                        continue;
                    }

                    let rawCountry = (countryEl.innerText || '').trim();
                    // 去掉序号前缀：如 "1.中国" → "中国"
                    rawCountry = rawCountry.replace(/^\d+\.\s*/, '').trim();

                    // 贸易次数：同排 td 列
                    let tradeCount = '';
                    const cells = row.querySelectorAll('td');
                    for (let j = 0; j < cells.length; j++) {
                        const t = cells[j].innerText.trim();
                        const m = t.match(/^(\d[\d,]*)$/);
                        if (m) { tradeCount = m[1].replace(/,/g, ''); break; }
                    }
                    if (!tradeCount) {
                        for (let j = 0; j < cells.length; j++) {
                            const t = cells[j].innerText.trim();
                            const m = t.match(/(\d[\d,]*)/);
                            if (m) { tradeCount = m[1].replace(/,/g, ''); break; }
                        }
                    }

                    if (rawCountry && rawCountry.length > 1 && rawCountry.length < 50) {
                        countries.push({
                            country: rawCountry,
                            trade_count: tradeCount,
                            source: 'rankingView',
                        });
                    }
                }

                return {
                    found: countries.length > 0,
                    countries,
                    reason: '',
                };
            }""")

            if not result or not result.get("found"):
                reason = result.get('reason', 'unknown') if result else 'unknown'
                print(f"  [进口] 原产地分析提取失败 (reason={reason})")
                return None, diag

            countries = result.get("countries", [])
            diag["countries_found"] = len(countries)

            print(f"  [进口] 进口国家共 {len(countries)} 家, 提取前 {min(len(countries), 3)} 家")
            for i, c in enumerate(countries[:3]):
                print(f"  [进口] country_{i+1}='{c['country']}' "
                      f"trade_count='{c.get('trade_count', '')}' "
                      f"source='{c.get('source', '')}'")

            return countries, diag

        except Exception as e:
            print(f"  [进口] _extract_import_countries 异常: {str(e)[:200]}")
            return None, diag

    def _extract_import_base_info(self) -> dict:
        """提取进口分析页的基础字段：最新进口日期、贸易次数、是否有数据块。"""
        try:
            return self.page.evaluate(r"""() => {
                const summaryText = document.body.innerText.substring(0, 2000);
                const result = { latest_date: '', trade_count: 0, has_data_block: false };

                const dateMatch = summaryText.match(/(\d{4}[-/]\d{1,2}[-/]\d{1,2})/);
                if (dateMatch) result.latest_date = dateMatch[1].replace('/', '-');

                const countMatch = summaryText.match(/(\d+)\s*次/);
                if (countMatch) result.trade_count = parseInt(countMatch[1]);

                result.has_data_block = /HS编码|供应商|出口商|采购次数|贸易记录|进口数据/.test(summaryText)
                    || !!document.querySelector('svg, canvas');

                return result;
            }""")
        except Exception as e:
            print(f"  [进口] _extract_import_base_info 异常: {e}")
            return {"latest_date": "", "trade_count": 0, "has_data_block": False}


    def _fill_hs_filters(self, hs_codes: list[str]):
        """在进口分析页的 HS 编码筛选框中依次填入多个编码。

        每填入一个编码后等待页面刷新，显式等待表格容器重新出现。
        """
        page = self.page
        try:
            for idx, hs_code in enumerate(hs_codes):
                input_found = False
                for sel in [
                    "input[placeholder*='HS']",
                    "input[placeholder*='hs']",
                    "input[placeholder*='编码']",
                    ".hs-filter input",
                    "[class*='hsCode'] input",
                ]:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.click()
                            page.wait_for_timeout(200)
                            el.press("Control+a")
                            page.wait_for_timeout(100)
                            el.press("Backspace")
                            page.wait_for_timeout(200)
                            el.fill(hs_code)
                            page.wait_for_timeout(500)
                            print(f"  [进口] 已填入 HS 编码 '{hs_code}' (selector={sel})")
                            input_found = True

                            # 触发搜索：按 Enter 或点搜索按钮
                            el.press("Enter")
                            page.wait_for_timeout(1000)
                            page.wait_for_load_state("networkidle", timeout=self.config["load_timeout"])
                            page.wait_for_timeout(2000)
                            print(f"  [进口] 已触发搜索，等待表格加载...")

                            # 显式等待表格出现
                            try:
                                page.wait_for_function(
                                    """() => document.querySelectorAll('table').length > 0""",
                                    timeout=self.config["load_timeout"],
                                )
                                print(f"  [进口] 表格已加载")
                            except PlaywrightTimeout:
                                print(f"  [进口] 等待表格超时，继续")
                            break
                    except PlaywrightTimeout:
                        continue

                if not input_found:
                    print(f"  [进口] 未找到 HS 编码筛选框 (hs={hs_code})")
        except Exception as e:
            print(f"  [进口] HS 编码填入异常: {e}")


# ============================================================================
# 提取辅助函数（供 TendataScraper 内部调用）
# ============================================================================


def _log_field_result(field_name: str, value: str, missed: bool, empty: bool):
    """统一字段日志：成功打印值，selector_missed / page_value_empty 明确区分。"""
    if value:
        print(f"  [详情] {field_name}: '{value}'")
    elif missed:
        print(f"  [详情] {field_name}: (selector_missed)")
    elif empty:
        print(f"  [详情] {field_name}: (page_value_empty)")
    else:
        print(f"  [详情] {field_name}: (空)")


def _extract_company_name_via_js(page) -> str:
    """从页面顶部标题区域提取公司名，排除按钮。"""
    try:
        name = page.evaluate("""() => {
            for (const sel of ['h1', 'h2', '.company-name', '.company-title', '.page-title']) {
                const el = document.querySelector(sel);
                if (!el) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 50) continue;
                const clone = el.cloneNode(true);
                clone.querySelectorAll('button, a, [role="button"], input').forEach(b => b.remove());
                const t = clone.textContent.trim();
                if (t && t.length > 2 && t.length < 200) return t;
            }
            return '';
        }""")
        return name or ""
    except Exception:
        return ""


def _scan_block_for_external_links(page, block_title: str) -> str:
    """在指定区块内扫描外部链接（排除 tendata），作为官网兜底。"""
    try:
        url = page.evaluate("""(title) => {
            const TENDATA = ['tendata.cn', 'tendata.com', 'bizr.tendata', 'account.tendata',
                            'login.tendata', 'knowledge.tendata', 'www.tendata'];
            // 定位区块
            let container = null;
            for (const heading of document.querySelectorAll('h3, h4, h5, span, div')) {
                const t = (heading.innerText || '').trim();
                if (t.startsWith(title)) {
                    let c = heading;
                    for (let i = 0; i < 10 && c; i++) {
                        const r = c.getBoundingClientRect();
                        if (r.width > 100 && r.height > 40 &&
                            c.children.length >= 3 && c.children.length < 60) {
                            container = c; break;
                        }
                        c = c.parentElement;
                    }
                    if (container) break;
                }
            }
            if (!container) container = document.body;

            // 扫描所有外部链接
            const links = container.querySelectorAll('a[href]');
            for (const a of links) {
                const href = a.href || '';
                if (!href.startsWith('http')) continue;
                if (TENDATA.some(d => href.includes(d))) continue;
                if (href.includes('javascript') || href.startsWith('#')) continue;
                const r = a.getBoundingClientRect();
                if (r.width < 20 || r.height < 5) continue;
                const cs = getComputedStyle(a);
                if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                return href;
            }
            return '';
        }""", block_title)
        if url:
            # 标准化
            url = url.strip().rstrip("/")
            if not url.startswith("http"):
                url = f"https://{url}"
            return url
    except Exception:
        pass
    return ""


# ============================================================================
# 匹配逻辑
# ============================================================================


def _parse_hs_codes(keywords: str) -> list[str]:
    """从 product_keywords 中提取多个 HS 编码列表。

    支持格式：'73064090, 73063000'、'HS:8471.30'、'73064090; 84713000' 等。
    """
    if not keywords:
        return []
    # 提取所有 4-10 位数字序列（允许带点）
    codes = re.findall(r"\b(\d{4}\.?\d{0,4})\b", keywords)
    # 过滤掉不像 HS 编码的（如年份、短数字）
    valid = []
    for c in codes:
        # 去掉点
        clean = c.replace(".", "")
        if len(clean) >= 6:
            valid.append(clean)
        elif len(clean) >= 4 and any(ch in keywords.upper() for ch in ["HS", "编"]):
            valid.append(clean)
    # 去重保持顺序
    seen = set()
    unique = []
    for c in valid:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# 中英文国家名称映射表（双向）
_COUNTRY_MAP: dict[str, str] = {}

# 3-letter ISO 国家代码 → 中文名称（用于从企业详情 URL 提取）
_COUNTRY_CODE_TO_NAME: dict[str, str] = {
    "USA": "美国", "CHN": "中国", "JPN": "日本", "KOR": "韩国",
    "DEU": "德国", "GBR": "英国", "FRA": "法国", "ITA": "意大利",
    "RUS": "俄罗斯", "TUR": "土耳其", "POL": "波兰", "EGY": "埃及",
    "KAZ": "哈萨克斯坦", "NLD": "荷兰", "ESP": "西班牙", "IND": "印度",
    "IDN": "印度尼西亚", "MEX": "墨西哥", "CAN": "加拿大", "AUS": "澳大利亚",
    "BRA": "巴西", "THA": "泰国", "VNM": "越南", "MYS": "马来西亚",
    "SGP": "新加坡", "PHL": "菲律宾", "PAK": "巴基斯坦", "SAU": "沙特阿拉伯",
    "ARE": "阿联酋", "ISR": "以色列", "COL": "哥伦比亚", "PER": "秘鲁",
    "CHL": "智利", "ARG": "阿根廷", "UKR": "乌克兰", "CZE": "捷克",
    "ROU": "罗马尼亚", "HUN": "匈牙利", "AUT": "奥地利", "BEL": "比利时",
    "SWE": "瑞典", "NOR": "挪威", "DNK": "丹麦", "FIN": "芬兰",
    "CHE": "瑞士", "PRT": "葡萄牙", "GRC": "希腊", "IRL": "爱尔兰",
}

def _init_country_map():
    """初始化国家映射表，使中英文互查都能映射到统一小写英文键。"""
    global _COUNTRY_MAP
    if _COUNTRY_MAP:
        return
    pairs = [
        ("united states", "美国"),
        ("china", "中国"),
        ("japan", "日本"),
        ("south korea", "韩国"),
        ("germany", "德国"),
        ("united kingdom", "英国"),
        ("france", "法国"),
        ("india", "印度"),
        ("brazil", "巴西"),
        ("russia", "俄罗斯"),
        ("australia", "澳大利亚"),
        ("canada", "加拿大"),
        ("mexico", "墨西哥"),
        ("italy", "意大利"),
        ("spain", "西班牙"),
        ("netherlands", "荷兰"),
        ("thailand", "泰国"),
        ("vietnam", "越南"),
        ("indonesia", "印度尼西亚"),
        ("malaysia", "马来西亚"),
        ("singapore", "新加坡"),
        ("philippines", "菲律宾"),
        ("turkey", "土耳其"),
        ("israel", "以色列"),
        ("saudi arabia", "沙特阿拉伯"),
        ("uae", "阿联酋"),
        ("united arab emirates", "阿联酋"),
        ("south africa", "南非"),
        ("egypt", "埃及"),
        ("nigeria", "尼日利亚"),
        ("argentina", "阿根廷"),
        ("chile", "智利"),
        ("colombia", "哥伦比亚"),
        ("peru", "秘鲁"),
        ("poland", "波兰"),
        ("ukraine", "乌克兰"),
        ("sweden", "瑞典"),
        ("norway", "挪威"),
        ("denmark", "丹麦"),
        ("switzerland", "瑞士"),
        ("austria", "奥地利"),
        ("belgium", "比利时"),
        ("portugal", "葡萄牙"),
        ("greece", "希腊"),
        ("czech", "捷克"),
        ("romania", "罗马尼亚"),
        ("hungary", "匈牙利"),
        ("pakistan", "巴基斯坦"),
        ("bangladesh", "孟加拉国"),
        ("myanmar", "缅甸"),
        ("cambodia", "柬埔寨"),
        ("laos", "老挝"),
        ("new zealand", "新西兰"),
        ("ireland", "爱尔兰"),
        ("finland", "芬兰"),
        ("kenya", "肯尼亚"),
        ("ethiopia", "埃塞俄比亚"),
        ("ghana", "加纳"),
        ("tanzania", "坦桑尼亚"),
        ("morocco", "摩洛哥"),
        ("iran", "伊朗"),
        ("iraq", "伊拉克"),
        ("kuwait", "科威特"),
        ("qatar", "卡塔尔"),
        ("bahrain", "巴林"),
        ("oman", "阿曼"),
        ("jordan", "约旦"),
        ("lebanon", "黎巴嫩"),
        ("tunisia", "突尼斯"),
        ("algeria", "阿尔及利亚"),
        ("libya", "利比亚"),
        ("angola", "安哥拉"),
        ("mozambique", "莫桑比克"),
        ("senegal", "塞内加尔"),
        ("ivory coast", "科特迪瓦"),
        ("cameroon", "喀麦隆"),
        ("dr congo", "刚果"),
        ("zambia", "赞比亚"),
        ("zimbabwe", "津巴布韦"),
        ("botswana", "博茨瓦纳"),
        ("namibia", "纳米比亚"),
        ("madagascar", "马达加斯加"),
        ("mauritius", "毛里求斯"),
        ("nepal", "尼泊尔"),
        ("sri lanka", "斯里兰卡"),
        ("mongolia", "蒙古"),
        ("kazakhstan", "哈萨克斯坦"),
        ("uzbekistan", "乌兹别克斯坦"),
        ("georgia", "格鲁吉亚"),
        ("azerbaijan", "阿塞拜疆"),
        ("armenia", "亚美尼亚"),
        ("serbia", "塞尔维亚"),
        ("bulgaria", "保加利亚"),
        ("croatia", "克罗地亚"),
        ("slovakia", "斯洛伐克"),
        ("slovenia", "斯洛文尼亚"),
        ("lithuania", "立陶宛"),
        ("latvia", "拉脱维亚"),
        ("estonia", "爱沙尼亚"),
        ("iceland", "冰岛"),
        ("luxembourg", "卢森堡"),
        ("malta", "马耳他"),
        ("cyprus", "塞浦路斯"),
        ("venezuela", "委内瑞拉"),
        ("ecuador", "厄瓜多尔"),
        ("uruguay", "乌拉圭"),
        ("paraguay", "巴拉圭"),
        ("bolivia", "玻利维亚"),
        ("panama", "巴拿马"),
        ("costa rica", "哥斯达黎加"),
        ("guatemala", "危地马拉"),
        ("honduras", "洪都拉斯"),
        ("el salvador", "萨尔瓦多"),
        ("nicaragua", "尼加拉瓜"),
        ("cuba", "古巴"),
        ("dominican republic", "多米尼加"),
        ("jamaica", "牙买加"),
        ("trinidad and tobago", "特立尼达和多巴哥"),
        ("papua new guinea", "巴布亚新几内亚"),
        ("fiji", "斐济"),
    ]
    for eng, chn in pairs:
        _COUNTRY_MAP[eng.lower()] = eng.lower()
        _COUNTRY_MAP[chn] = eng.lower()
    # 常见别名
    _COUNTRY_MAP["usa"] = "united states"
    _COUNTRY_MAP["us"] = "united states"
    _COUNTRY_MAP["uk"] = "united kingdom"
    _COUNTRY_MAP["cn"] = "china"
    _COUNTRY_MAP["kr"] = "south korea"
    _COUNTRY_MAP["de"] = "germany"
    _COUNTRY_MAP["jp"] = "japan"
    _COUNTRY_MAP["in"] = "india"
    _COUNTRY_MAP["ru"] = "russia"
    _COUNTRY_MAP["au"] = "australia"
    _COUNTRY_MAP["ca"] = "canada"
    _COUNTRY_MAP["mx"] = "mexico"
    _COUNTRY_MAP["fr"] = "france"
    _COUNTRY_MAP["sg"] = "singapore"
    _COUNTRY_MAP["ae"] = "uae"
    _COUNTRY_MAP["阿联酋"] = "uae"
    _COUNTRY_MAP["韩国"] = "south korea"
    _COUNTRY_MAP["朝鲜"] = "north korea"
    _COUNTRY_MAP["台湾"] = "taiwan"
    _COUNTRY_MAP["taiwan"] = "taiwan"
    _COUNTRY_MAP["香港"] = "hong kong"
    _COUNTRY_MAP["hong kong"] = "hong kong"
    _COUNTRY_MAP["澳门"] = "macau"
    _COUNTRY_MAP["macau"] = "macau"


def _normalize_country(name: str) -> str:
    """将国家名称统一为小写英文（支持中英文输入）。"""
    if not name:
        return ""
    _init_country_map()
    key = name.strip().lower()
    return _COUNTRY_MAP.get(key, key)


def compute_match_status(
    input_name: str,
    matched_name: str,
    input_country: str,
    page_country: str,
    input_website: str,
    page_website: str,
    has_country: bool,
) -> tuple[str, int]:
    """根据匹配规则计算 match_status 和 match_confidence。"""
    if not matched_name:
        return "no_result", 0

    name_sim = _name_similarity(input_name, matched_name)
    # 国家匹配：支持中英文映射
    norm_input = _normalize_country(input_country)
    norm_page = _normalize_country(page_country)
    country_match = has_country and (norm_input == norm_page)
    website_match = bool(input_website and page_website and _domain_match(input_website, page_website))

    # 无国家输入：上限 70
    cap = 100 if has_country else 70

    # 根据 status 限制上限，防止 unconfirmed + 100 等矛盾组合
    status_caps = {"confirmed": 100, "likely_match": 84, "unconfirmed": 59}

    if name_sim >= 0.85 and country_match:
        conf = 85
        if website_match:
            conf = min(conf + 10, cap)
        elif name_sim >= 0.95:
            conf = min(conf + 5, cap)
        status = "confirmed" if conf >= 85 else "likely_match"
    elif name_sim >= 0.6 and (country_match or not has_country):
        conf = 60
        if website_match:
            conf += 10
        status = "likely_match" if conf >= 60 else "unconfirmed"
    elif name_sim >= 0.4:
        conf = max(int(name_sim * 100), 30)
        status = "unconfirmed"
    else:
        conf = max(int(name_sim * 100), 0)
        status = "unconfirmed"

    conf = min(conf, cap, status_caps[status])
    return status, conf


def _extract_company_body(name: str) -> str:
    """提取公司名主体部分（去掉法律后缀和常见前缀）。"""
    if not name:
        return ""
    result = name
    # 去掉常见法律后缀
    for suffix in [
        r"\s+S\.?\s*r\.?\s*l\.?\s*\.?\s*$",
        r"\s+A\.?\s*[ŞS]\.?\s*\.?\s*$",
        r"\s+ŞİRKETİ\s*$",
        r"\s+LİMİTED\s*$",
        r"\s+LLC\s*$", r"\s+LLP\s*$",
        r"\s+Ltd\.?\s*$", r"\s+Inc\.?\s*$",
        r"\s+Corp\.?\s*$", r"\s+GmbH\s*$",
        r"\s+BV\s*$", r"\s+NV\s*$",
        r"\s+S\.?\s*p\.?\s*A\.?\s*$",
        r"\s+S\.?\s*A\.?\s*$",
        r"\s+A\.?\s*Ş\.?\s*$",
        r"\s+Sanayi\s+ve\s+Ticaret\s*$",
        r"\s+İthalat\s+İhracat\s*$",
    ]:
        result = re.sub(suffix, "", result, flags=re.IGNORECASE).strip()
    # 去掉 ТОО、ООО 前缀
    result = re.sub(r"^(?:ТОО|ООО)\s+", "", result).strip()
    return result


def _score_search_candidate(candidate, input_name: str, input_country: str, input_website: str = "") -> tuple[int, dict]:
    """对搜索结果的单个候选打分。

    打分规则：
    - 公司名主体匹配：+40 (high >=0.7), +20 (partial >=0.4), -50 (low)
    - 输入国家与候选所在地区匹配：+40
    - 公司名包含输入关键词或输入关键词包含候选主体：+20
    - 候选国家与输入国家明显不一致：-60
    - 公司名主体明显不一致：-50

    新规则：
    - 候选国家缺失时，如果名称高度匹配，给予补偿分，不直接跳过
    - 域名类候选降权：如果候选名看起来是域名，降低分数

    Returns:
        (score, detail_dict)
    """
    score = 0
    details = {}

    cand_name = candidate.company_name
    cand_country = getattr(candidate, 'country', '') or ''

    # 检测候选名是否是域名（web/目录站候选）
    domain_patterns = [r'\.com', r'\.org', r'\.net', r'\.io', r'\.co\.', r'\.tr', r'\.com\.tr', r'\.co\.uk']
    is_domain_candidate = False
    cand_name_lower = cand_name.lower()
    for pattern in domain_patterns:
        if re.search(pattern, cand_name_lower):
            is_domain_candidate = True
            break

    # 如果候选名完全是域名格式（没有公司后缀），大幅降权
    if is_domain_candidate:
        has_company_suffix = any(suffix in cand_name_lower for suffix in [
            'ltd', 'llc', 'inc', 'corp', 'gmbh', 'a.ş', 'a.s', 'srl', 'spa', 'co.', 'limited', 'company'
        ])
        if not has_company_suffix:
            score -= 80
            details["domain_penalty"] = "yes (candidate appears to be a website/domain, not a company)"
            print(f"  [候选打分] 候选 '{cand_name[:50]}' 看起来是域名，降权 -80")
        else:
            score -= 30
            details["domain_penalty"] = "partial (candidate contains domain pattern but has company suffix)"

    # 提取主体名用于比较（支持土耳其缩写展开）
    input_body = _extract_company_body(_expand_turkish_abbreviations(input_name)).lower()
    cand_body = _extract_company_body(_expand_turkish_abbreviations(cand_name)).lower()

    # 1. 公司名主体匹配
    name_sim = _name_similarity(input_body, cand_body)
    if name_sim >= 0.7:
        score += 40
        details["name_body_match"] = f"high ({name_sim:.2f})"
        details["name_match_level"] = "high"
    elif name_sim >= 0.4:
        score += 20
        details["name_body_match"] = f"partial ({name_sim:.2f})"
        details["name_match_level"] = "medium"
    else:
        score -= 50
        details["name_body_match"] = f"low ({name_sim:.2f})"
        details["name_match_level"] = "low"

    # 2. 国家匹配（仅当候选国家看起来是有效国家名时才比较）
    if input_country:
        norm_input = _normalize_country(input_country)
        # 过滤掉不靠谱的国家提取结果（如纯数字、过短的字符串）
        is_valid_cand_country = bool(
            cand_country
            and len(cand_country) >= 2
            and not cand_country.strip().isdigit()
        )
        if is_valid_cand_country:
            norm_cand = _normalize_country(cand_country)
            if norm_input and norm_cand:
                if norm_input == norm_cand:
                    score += 40
                    details["country_match"] = f"match ({norm_input})"
                else:
                    score -= 60
                    details["country_match"] = f"mismatch (input={norm_input}, cand={norm_cand})"
            else:
                details["country_match"] = "unknown"
        else:
            # 候选国家缺失时，如果名称高度匹配，给予补偿分（不跳过）
            # 规则：name_sim >= 0.70 或 name_match_level = high 时，允许进入详情页
            if name_sim >= 0.70:
                score += 10  # 补偿分：名称高度匹配时，国家缺失不扣分反而给一点补偿
                details["country_match"] = "unknown (high name match, will enter detail page)"
            else:
                details["country_match"] = "unknown (candidate country not extracted or invalid)"
    else:
        details["country_match"] = "skipped (no input country)"

    # 3. 包含关系加分
    if cand_body and input_body:
        if input_body in cand_body or cand_body in input_body:
            score += 20
            details["contain_bonus"] = "yes"
        else:
            details["contain_bonus"] = "no"

    # 4. exact/near-exact 公司名匹配额外加分
    # 如果标准化后的公司名几乎完全匹配，给予额外加分
    if name_sim >= 0.85:
        score += 15
        details["exact_match_bonus"] = "yes"

    # 记录最终分数对应的 name_similarity 值，供后续判断使用
    details["name_similarity"] = round(name_sim, 2)

    return score, details


def _expand_turkish_abbreviations(name: str) -> str:
    """展开土耳其公司名缩写。

    常见缩写：
    - SAN. -> SANAYI
    - TIC. -> TICARET
    - LTD. STI -> LIMITED SIRKETI
    - STI -> SIRKETI
    - A.S. -> ANONIM SIRKETI
    """
    if not name:
        return name

    result = name
    # 展开顺序很重要：先展开更长的模式
    expansions = [
        (r'\bLTD\s*\.?\s*ŞTİ\b', 'LİMİTED ŞİRKETİ'),
        (r'\bLTD\s*\.?\s*STI\b', 'LIMITED SIRKETI'),
        (r'\bŞTİ\b', 'ŞİRKETİ'),
        (r'\bSTI\b', 'SIRKETI'),
        (r'\bSAN\s*\.?\b', 'SANAYI'),
        (r'\bTİC\s*\.?\b', 'TİCARET'),
        (r'\bTIC\s*\.?\b', 'TICARET'),
        (r'\bA\s*\.?\s*Ş\s*\.?\b', 'ANONİM ŞİRKETİ'),
        (r'\bA\s*\.?\s*S\s*\.?\b', 'ANONIM SIRKETI'),
    ]

    for pattern, replacement in expansions:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def _name_similarity(a: str, b: str) -> float:
    """名称相似度（Jaccard 字符级 + 子串加分）。"""
    if not a or not b:
        return 0.0
    # 标准化后缀后再比较
    a_norm = _normalize_company_name(a).lower().strip()
    b_norm = _normalize_company_name(b).lower().strip()
    if a_norm == b_norm:
        return 1.0
    # 子串匹配加分
    if a_norm in b_norm or b_norm in a_norm:
        return 0.85
    # Jaccard
    set_a = set(a_norm)
    set_b = set(b_norm)
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union) if union else 0.0


def _domain_match(a: str, b: str) -> bool:
    """判断两个 URL 的域名是否匹配。"""
    def extract_domain(url: str) -> str:
        url = url.lower().strip()
        url = re.sub(r"^https?://", "", url)
        url = re.sub(r"^www\.", "", url)
        return url.split("/")[0].split(":")[0]
    try:
        return extract_domain(a) == extract_domain(b)
    except Exception:
        return False


def _detect_conflict(
    input_country: str,
    page_country: str,
    input_website: str,
    page_website: str,
    input_email_domain: str,
    page_email: str,
    top_products_json: str,
) -> tuple[bool, list[str]]:
    """检测输入客户与命中公司是否存在明确冲突。

    冲突条件（满足任一即判定为 conflict）：
    1. 国家不一致 + 官网/邮箱域名不一致
    2. 国家不一致 + 无官网/无邮箱（无法佐证同一性）

    Returns:
        (is_conflict, reasons_list)
    """
    reasons = []

    norm_input = _normalize_country(input_country)
    norm_page = _normalize_country(page_country)
    country_mismatch = bool(norm_input and norm_page and norm_input != norm_page)

    if not country_mismatch:
        return False, []

    # 官网域名对比
    domain_mismatch = True  # 默认不匹配（无输入或无命中时视为无法佐证）
    if input_website and page_website:
        domain_mismatch = not _domain_match(input_website, page_website)

    # 邮箱域名对比
    email_domain_mismatch = True
    if input_email_domain and page_email and "@" in page_email:
        page_email_domain = page_email.split("@")[-1].lower().strip()
        email_domain_mismatch = (input_email_domain.lower() != page_email_domain)
    elif input_email_domain and page_email:
        # page_email 无 @ 时可能是纯域名
        email_domain_mismatch = (input_email_domain.lower() != page_email.lower().strip())
    elif input_email_domain and not page_email:
        # 有输入邮箱但命中公司无邮箱
        email_domain_mismatch = True

    # 冲突判定 1：国家不一致 + 官网域名也不一致
    if country_mismatch and domain_mismatch:
        reasons.append(f"国家不匹配：输入={input_country}，搜索结果={page_country}")
        reasons.append(f"官网不匹配：输入={input_website}，搜索结果={page_website}")

    # 冲突判定 2：国家不一致 + 无官网但输入有邮箱域名，且命中公司邮箱不匹配
    if country_mismatch and not input_website and input_email_domain and email_domain_mismatch:
        reasons.append(f"国家不匹配：输入={input_country}，搜索结果={page_country}")
        reasons.append(f"邮箱域名不匹配：输入={input_email_domain}")

    # 冲突判定 3：国家不一致 + 既无官网也无邮箱，完全无法佐证
    if country_mismatch and not input_website and not input_email_domain:
        reasons.append(f"国家不匹配：输入={input_country}，搜索结果={page_country}")
        reasons.append("无官网/邮箱可佐证同一性")

    if top_products_json and reasons:
        try:
            import json as _json
            products = _json.loads(top_products_json)
            if products:
                product_names = [p.get("product_name", "") for p in products if p.get("product_name")]
                if product_names:
                    reasons.append(f"命中公司产品: {', '.join(product_names[:3])}")
        except Exception:
            pass

    return len(reasons) >= 2, reasons


# 中文业务备注噪声词，用于搜索词清洗
_NOISE_SUFFIX_PATTERNS = [
    # 交易/跟进类
    r"询价", r"报价", r"报过价", r"已报价", r"询盘", r"订单",
    r"库存", r"备用",
    # 展会类
    r"杜塞展", r"展会", r"参展", r"展位", r"上海展", r"北京展",
    r"汉诺威展", r"广交会", r"进博会",
    # 产品类（通用工业词）
    r"管件", r"法兰", r"阀门", r"管道", r"管材", r"钢管",
    r"螺栓", r"螺母", r"接头", r"弯头", r"三通",
    # 客户/业务备注
    r"客户", r"采购", r"项目", r"开发", r"目标客户",
    r"老客", r"新客", r"潜在", r"重点",
]
_NOISE_RE = re.compile("|".join(_NOISE_SUFFIX_PATTERNS))

# 业务备注/内部备注噪声词（用于判断公司简称是否合格）
_BUSINESS_NOTE_WORDS = [
    "under the umbrella", "询价", "报价", "报过价", "已报价", "询盘", "订单",
    "库存", "备用", "杜塞展", "展会", "参展", "展位", "上海展", "北京展",
    "汉诺威展", "广交会", "进博会", "客户", "采购", "项目", "开发",
    "目标客户", "老客", "新客", "潜在", "重点", "管件", "法兰", "阀门",
    "管道", "管材", "钢管",
]

# 内部简称模式：G.I.S、F-TLC、E.ZANON 等短缩写
_SHORT_ABBR_RE = re.compile(r"^[A-Z][.\-][\w.\-]{0,14}$")


def _contains_business_note(text: str) -> bool:
    """判断文本是否包含业务备注/内部备注。"""
    lower = text.lower()
    for word in _BUSINESS_NOTE_WORDS:
        if word in lower:
            return True
    return False


def _is_likely_abbr(text: str) -> bool:
    """判断文本是否像内部简称（如 G.I.S、F-TLC、E.ZANON）。"""
    if _SHORT_ABBR_RE.match(text):
        return True
    # 含点分隔的大写缩写，如 A.B.C、X.Y.Z.
    parts = re.split(r"[.\-\s]+", text)
    if len(parts) >= 2 and all(len(p) <= 4 for p in parts):
        upper_count = sum(1 for p in parts if p and p.isupper())
        if upper_count == len(parts):
            return True
    return False


def _has_chinese_bracket_notes(text: str) -> bool:
    """判断文本是否在括号中包含中文备注（如（库存））。"""
    import re as _re
    for m in _re.findall(r"[（(]([^）)]*)[）)]", text):
        if any("一" <= ch <= "鿿" for ch in m):
            return True
    return False


def _clean_chinese_notes(text: str) -> str:
    """移除中文业务备注后缀，返回清洗后的文本。"""
    keyword = _NOISE_RE.sub("", text).strip()
    keyword = re.sub(r"[（）]+", "", keyword).strip()
    keyword = keyword.rstrip(" ，。、；：,;.!！-")
    return keyword if keyword else text.strip()


# 法律实体后缀（用于从公司名中去掉，不用于搜索）
_LEGAL_ENTITY_SUFFIXES = [
    r"\bLLC\b", r"\bInc\b", r"\bCorp\b", r"\bLtd\b",
    r"\bLLP\b", r"\bGmbH\b", r"\bS\.?r\.?l\.?\b", r"\bS\.?A\.?\b",
    r"\bA\.Ş\.?\b", r"\bA\.G\.?\b", r"\bB\.?V\.?\b",
    r"\bS\.?p\.?A\.?\b", r"\bK\.?K\.?\b", r"\bY\.?K\.?\b",
    r"\bPte\b", r"\bBV\b", r"\bNV\b",
    r"\bEndustri\b", r"\bSanayi\b", r"\bTicaret\b", r"\bIthalat\b", r"\bIhracat\b",
    r"\bLimited\b", r"\bCorporation\b", r"\bIncorporated\b",
]
_LEGAL_SUFFIX_RE = re.compile("|".join(_LEGAL_ENTITY_SUFFIXES), re.IGNORECASE)


def _clean_company_name(name: str) -> str:
    """清洗公司名称：去掉中文备注、日期、编号、法律后缀，保留正式公司主体名。"""
    if not name:
        return name

    # 去掉中文业务备注
    result = _NOISE_RE.sub("", name).strip()
    # 去掉全角空括号
    result = re.sub(r"[（）]+", "", result).strip()

    # 去掉 ASCII 括号中包含法律实体后缀的内容，如 "(LLC ANEP)", "(Ltd.)", "(Inc)"
    _LEGAL_PAREN_RE = re.compile(
        r"\s*[\(\[]\s*(?:LLC|LLP|LTD|INC|CORP|GMBH|BV|NV|SPA|SRL|S\.?A\.?|A\.?G\.?|PTE|CO|JSC|OJSC|ZAO|OOO)"
        r"[\s\w.\-/]*[\)\]]\s*",
        flags=re.IGNORECASE,
    )
    result = _LEGAL_PAREN_RE.sub("", result).strip()

    # 去掉常见的法律实体后缀（从末尾匹配）
    # 注意：保留 GmbH、Corp 等常用于搜索的后缀，只去掉 S.r.L./A.Ş./Ltd./LLC 等短后缀
    result = re.sub(r"\s+S\.?\s*r\.?\s*l\.?\s*\.?$", "", result, flags=re.IGNORECASE).strip()
    result = re.sub(r"\s+A\.?\s*Ş\.?\s*\.?$", "", result).strip()
    result = re.sub(r"\s+ŞİRKETİ\s*$", "", result).strip()  # 土耳其语 "公司"
    result = re.sub(r"\s+LİMİTED\s*$", "", result, flags=re.IGNORECASE).strip()
    result = re.sub(r"\s+END\.\s*$", "", result).strip()  # 土耳其语 "工业" 缩写

    # 去掉末尾的日期后缀（如 -20240101、2024-01-01）
    result = re.sub(r"[\-\s]+\d{4}[\-/]?\d{2}[\-/]?\d{2}$", "", result).strip()

    # 去掉末尾的编号后缀（如 -210714、NO.123）
    result = re.sub(r"[\-\s]+(?:NO\.?)?\s*\d{4,}$", "", result).strip()

    result = result.rstrip(" ，。、；：,;.!！-")

    # 去掉 ТОО、ООО 等前缀（俄罗斯/哈萨克斯坦公司常见前缀，非公司主体名）
    result = re.sub(r"^(?:ТОО|ООО)\s+", "", result).strip()

    return result if result else name.strip()


def clean_search_keyword(full_name: str, short_name: str = "") -> str:
    """生成用于腾道搜索的公司关键词。

    优先使用清洗后的公司名称。
    公司简称仅在不合格时使用（作为备选），见 clean_search_keyword_with_fallback。

    Args:
        full_name: 公司全称（customer_name）
        short_name: 公司简称（company_short_name），可为空（仅用于内部判断）

    Returns:
        清洗后的搜索关键词（优先全名）
    """
    # 优先使用清洗后的公司名称
    cleaned = _clean_company_name(full_name)

    return cleaned if cleaned else full_name.strip()


def get_fallback_keyword(short_name: str) -> str:
    """从公司简称生成备用搜索词。

    仅当简称通过质量检查时才返回有效值，否则返回空字符串。
    检查条件：
    - 长度 >= 6 个字符；
    - 不是纯缩写（如 G.I.S、F-TLC、E.ZANON）；
    - 不包含括号里的中文业务备注；
    - 不包含 under the umbrella、询价、报价等业务备注词。
    """
    if not short_name or not short_name.strip():
        return ""
    sn = short_name.strip()

    if len(sn) < 6:
        return ""
    if _is_likely_abbr(sn):
        return ""
    if _contains_business_note(sn):
        return ""
    if _has_chinese_bracket_notes(sn):
        return ""

    cleaned = _clean_chinese_notes(sn)
    return cleaned if cleaned else ""


def _search_and_select(scraper, search_kw: str, customer_name: str, country_region: str, score_threshold: int = 60):
    """执行搜索 + 提取候选 + 打分 + 选择最佳候选。

    Args:
        scraper: TendataScraper 实例
        search_kw: 搜索关键词（已标准化）
        customer_name: 原始客户名称（用于打分）
        country_region: 输入国家
        score_threshold: 最低通过分数

    Returns:
        (top1_candidate, candidates_list) 或 (None, None)
    """
    if not scraper.search_company(search_kw):
        print(f"  [备用搜索] 搜索失败: '{search_kw}'")
        return None, None

    candidates = scraper.extract_search_results()
    if not candidates:
        print(f"  [备用搜索] 无搜索结果")
        return None, None

    scored = []
    for c in candidates:
        s, d = _score_search_candidate(c, customer_name, country_region)
        scored.append((s, d, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best_details, top1 = scored[0]
    print(f"  [备用搜索] 候选打分:")
    for s, d, c in scored[:5]:
        print(f"    分数={s}  候选: '{c.company_name[:50]}' 地区: '{(getattr(c, 'country', '') or '')[:30]}'")

    if best_score < score_threshold:
        print(f"  [备用搜索] 最高分 {best_score} < 阈值 {score_threshold}，跳过")
        return None, None

    # 国家不匹配检查（仅对有效国家名做检查）
    if country_region:
        cand_country = getattr(top1, 'country', '') or ''
        is_valid_cand = bool(cand_country and len(cand_country) >= 2 and not cand_country.strip().isdigit())
        if is_valid_cand:
            norm_input = _normalize_country(country_region)
            norm_cand = _normalize_country(cand_country)
            if norm_input and norm_cand and norm_input != norm_cand:
                # 寻找国家匹配的候选
                for s, d, c in scored:
                    cc = getattr(c, 'country', '') or ''
                    cc_valid = bool(cc and len(cc) >= 2 and not cc.strip().isdigit())
                    if cc_valid and _normalize_country(cc) == norm_input and s >= score_threshold:
                        print(f"  [备用搜索] 改用国家匹配候选: '{c.company_name[:50]}' (分数={s})")
                        return c, candidates
                print(f"  [备用搜索] 最佳候选国家不匹配，跳过")
                return None, None

    return top1, candidates


def _normalize_company_name(name: str) -> str:
    """标准化公司名后缀，提升匹配召回率。

    将常见后缀统一为小写缩写形式，减少因 co./co, ltd./limited 等差异导致的匹配失败。
    """
    if not name:
        return name

    result = name

    # 第一轮：处理组合后缀（带逗号/点的完整形式），包含末尾可能的标点
    combos = [
        (r"\bco\.?\s*,\s*ltd\.?\.*", "co ltd"),
        (r"\bco\.?\s*,?\s*llc\.?\.*", "co llc"),
        (r"\bco\.?\s*,?\s*inc\.?\.*", "co inc"),
        (r"\bco\.?\s*,?\s*corp\.?\.*", "co corp"),
        (r"\bpte\.?\s*,?\s*ltd\.?\.*", "pte ltd"),
        (r"\bsa\s*,?\s*de\s*,?\s*cv\b", "sa de cv"),
    ]
    for pattern, repl in combos:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)

    # 第二轮：处理单个后缀
    singles = [
        (r"\bcorporation\b", "corp"),
        (r"\bincorporated\b", "inc"),
        (r"\blimited\b", "ltd"),
        (r"\bllc\.?\.*", "llc"),
        (r"\bl\.l\.c\.?\.*", "llc"),
        (r"\binc\.?\.*", "inc"),
        (r"\bcorp\.?\.*", "corp"),
        (r"\bco\.?\.*", "co"),
        (r"\bltd\.?\.*", "ltd"),
        (r"\bd\.?o\.?o\.?\.*", "doo"),
        (r"\bpte\.?\.*", "pte"),
        (r"\bgmbh\b", "gmbh"),
        (r"\bsa\b", "sa"),
        (r"\bsrl\b", "srl"),
        (r"\bllp\.?\.*", "llp"),
    ]
    for pattern, repl in singles:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)

    # 清理多余空格和末尾标点
    result = re.sub(r"\s+", " ", result).strip(" .,")
    return result


def determine_import_active(latest_date: str) -> str:
    """根据最近进口日期判断 import_active_status。（旧版，保留兼容）"""
    if not latest_date:
        return "unknown"
    try:
        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m-%d-%Y"]:
            try:
                dt = datetime.strptime(latest_date, fmt)
                break
            except ValueError:
                continue
        else:
            return "unknown"
        cutoff = datetime.now() - timedelta(days=365)
        return "active" if dt >= cutoff else "inactive"
    except Exception:
        return "unknown"


def determine_import_active_new(latest_date: str, match_status: str) -> str:
    """根据最近进口日期和匹配状态判断 import_active_status（新版）。

    - conflict: invalid_for_target
    - confirmed/likely_match 且 latest_import_date 在近 12 个月: active
    - confirmed/likely_match 且 latest_import_date 在 12-24 个月: recent
    - confirmed/likely_match 且 latest_import_date 超 24 个月: inactive
    - 无有效日期: unknown
    """
    if match_status == "conflict":
        return "invalid_for_target"
    if not latest_date:
        return "unknown"
    try:
        for fmt in ["%Y-%m-%d", "%Y/%m/%d"]:
            try:
                dt = datetime.strptime(latest_date, fmt)
                break
            except ValueError:
                continue
        else:
            return "unknown"
        days = (datetime.now() - dt).days
        if days < 0:
            return "unknown"
        if days <= 365:
            return "active"
        elif days <= 730:
            return "recent"
        else:
            return "inactive"
    except Exception:
        return "unknown"


def determine_import_frequency(latest_date: str, total_records: int) -> str:
    """根据最近进口日期和总记录数判断进口频率等级。（旧版，保留兼容）"""
    if not latest_date:
        return "unknown"
    try:
        for fmt in ["%Y-%m-%d", "%Y/%m/%d"]:
            try:
                dt = datetime.strptime(latest_date, fmt)
                break
            except ValueError:
                continue
        else:
            return "unknown"
        days = (datetime.now() - dt).days
        if days <= 90 and total_records >= 10:
            return "high"
        elif days <= 180 and total_records >= 5:
            return "medium"
        elif days <= 365:
            return "low"
        else:
            return "inactive"
    except Exception:
        return "unknown"


def determine_import_frequency_new(last_12m: str, last_24m: str, last_36m: str) -> str:
    """根据 12/24/36 个月进口计数判断频率等级。

    - last_12m_import_count >= 10: high
    - last_12m_import_count 3-9: medium
    - last_12m_import_count 1-2: low
    - last_12m = 0 且 24/36 个月有记录: inactive_or_old
    - 无法计算: unknown
    """
    def to_int(s):
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    c12 = to_int(last_12m)
    c24 = to_int(last_24m)
    c36 = to_int(last_36m)

    if c12 is None:
        return "unknown"
    if c12 >= 10:
        return "high"
    elif c12 >= 3:
        return "medium"
    elif c12 >= 1:
        return "low"
    else:
        # last_12m = 0
        if (c24 is not None and c24 > 0) or (c36 is not None and c36 > 0):
            return "inactive_or_old"
        return "inactive_or_old"


def _assess_product_relevance(input_keywords: str, top_products_json: str, hs_product: str) -> str:
    """评估腾道命中公司产品与输入产品关键词的相关性。"""
    if not input_keywords and not hs_product:
        return "unknown"
    if not top_products_json and not hs_product:
        return "unknown"
    # 简单关键词匹配
    keywords_lower = input_keywords.lower()
    # 检查 top products
    if top_products_json:
        try:
            import json as _json
            products = _json.loads(top_products_json)
            for p in products:
                pn = p.get("product_name", "").lower()
                if pn and any(kw in pn for kw in keywords_lower.split()):
                    return "high"
        except Exception:
            pass
    # 检查 HS product
    if hs_product and any(kw in hs_product.lower() for kw in keywords_lower.split()):
        return "high"
    return "unknown"


def _estimate_trade_volume(total_records: int, amount_json: str) -> str:
    """根据贸易记录数和金额估算体量等级。"""
    if total_records > 0:
        if total_records >= 100:
            return "large"
        elif total_records >= 20:
            return "medium"
        else:
            return "small"
    if amount_json:
        try:
            import json as _json
            amounts = _json.loads(amount_json)
            if amounts:
                return "medium"
        except Exception:
            pass
    return "unknown"


def _populate_supplier_fields(result: EnrichmentRow, imp: ImportAnalysis):
    """填充供应商相关字段：supplier_count, top_suppliers, supplier_stability_level, china_supplier_signal。"""
    import json as _json

    # ── supplier_count: 优先统计卡片 → 贸易明细去重 → top_suppliers_json 解析 ──
    if imp.stats_card_supplier_count:
        try:
            result.supplier_count = str(int(imp.stats_card_supplier_count))
        except (ValueError, TypeError):
            result.supplier_count = ""
    elif imp.trade_suppliers:
        result.supplier_count = str(len(set(imp.trade_suppliers)))
    elif imp.top_suppliers_json:
        try:
            suppliers_list = _json.loads(imp.top_suppliers_json)
            if isinstance(suppliers_list, list):
                result.supplier_count = str(len(suppliers_list))
        except Exception:
            pass

    # ── top_suppliers: 优先 partnerReport → 贸易明细出口商 → top_suppliers_json ──
    if imp.partner_suppliers:
        names = []
        for s in imp.partner_suppliers[:10]:
            name = s.get("supplier_name", "") if isinstance(s, dict) else str(s)
            if name:
                names.append(name)
        result.top_suppliers = "; ".join(names)
        if imp.stats_card_supplier_count:
            try:
                result.supplier_count = str(int(imp.stats_card_supplier_count))
            except (ValueError, TypeError):
                pass
    elif imp.trade_suppliers:
        seen = set()
        names = []
        for s in imp.trade_suppliers:
            s_stripped = s.strip() if isinstance(s, str) else str(s).strip()
            if s_stripped and s_stripped not in seen:
                seen.add(s_stripped)
                names.append(s_stripped)
                if len(names) >= 10:
                    break
        result.top_suppliers = "; ".join(names)
    elif imp.top_suppliers_json:
        try:
            suppliers_list = _json.loads(imp.top_suppliers_json)
            if isinstance(suppliers_list, list):
                names = []
                for s in suppliers_list[:10]:
                    if isinstance(s, dict):
                        name = s.get("supplier_name", "") or s.get("name", "")
                    else:
                        name = str(s)
                    if name:
                        names.append(name.strip())
                result.top_suppliers = "; ".join(names)
        except Exception:
            pass

    # ── supplier_stability_level: 1=单一, 2-3=适度, >=4=多元化 ──
    if result.supplier_count:
        try:
            n = int(result.supplier_count)
            if n <= 1:
                result.supplier_stability_level = "single_supplier"
            elif n <= 3:
                result.supplier_stability_level = "moderate"
            else:
                result.supplier_stability_level = "diversified"
        except (ValueError, TypeError):
            pass

    # ── china_supplier_signal: 检查供应商名是否含中国相关关键词 ──
    china_keywords = ["china", "chinese", "中国", "中国 ", "china ", "cn,", ",cn", "prc"]
    all_supplier_text = (result.top_suppliers + " " + imp.top_suppliers_json).lower()
    if any(kw in all_supplier_text for kw in china_keywords):
        result.china_supplier_signal = "yes"
    else:
        result.china_supplier_signal = "no"


def _populate_import_counts_from_dates(result: EnrichmentRow, imp: ImportAnalysis):
    """从 trade_dates 列表按当前日期倒推，计算 12/24/36 个月窗口内的贸易记录数。

    last_12m/24m/36m_import_count 必须为数字（字符串形式的整数）。

    兜底规则：如果 trade_dates 为空但 latest_import_date 有值，
    则根据 latest_import_date 判断至少有一次进口记录。
    """
    from datetime import datetime as _dt

    now = _dt.now()
    dates_12m = 0
    dates_24m = 0
    dates_36m = 0

    for d in imp.trade_dates:
        if not d:
            continue
        if isinstance(d, str):
            # 尝试多种格式
            dt_parsed = None
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y.%m.%d"]:
                try:
                    dt_parsed = _dt.strptime(d, fmt)
                    break
                except ValueError:
                    continue
            if dt_parsed is None:
                continue
        elif isinstance(d, _dt):
            dt_parsed = d
        else:
            continue

        delta_days = (now - dt_parsed).days
        if delta_days < 0:
            continue
        if delta_days <= 365:
            dates_12m += 1
        if delta_days <= 730:
            dates_24m += 1
        if delta_days <= 1095:
            dates_36m += 1

    # 兜底规则：如果 trade_dates 为空但 latest_import_date 有值
    if dates_12m == 0 and dates_24m == 0 and dates_36m == 0 and imp.latest_import_date:
        latest_dt = None
        if isinstance(imp.latest_import_date, str):
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y.%m.%d"]:
                try:
                    latest_dt = _dt.strptime(imp.latest_import_date, fmt)
                    break
                except ValueError:
                    continue
        elif isinstance(imp.latest_import_date, _dt):
            latest_dt = imp.latest_import_date

        if latest_dt:
            delta_days = (now - latest_dt).days
            if delta_days >= 0:
                if delta_days <= 365:
                    dates_12m = 1
                    dates_24m = 1
                    dates_36m = 1
                elif delta_days <= 730:
                    dates_24m = 1
                    dates_36m = 1
                elif delta_days <= 1095:
                    dates_36m = 1

    result.last_12m_import_count = str(dates_12m)
    result.last_24m_import_count = str(dates_24m)
    result.last_36m_import_count = str(dates_36m)


def build_import_summary_v2(imp: ImportAnalysis) -> str:
    """生成 import_activity_summary（v2），确保不出现自相矛盾的描述。"""
    parts = []

    if imp.latest_import_date:
        parts.append(f"最近一次进口时间为 {imp.latest_import_date}")

    # 用 trade_dates 实际数量，不依赖可能有误的 total_records
    actual_trade_count = len(imp.trade_dates) if imp.trade_dates else imp.total_records

    if actual_trade_count > 0:
        parts.append(f"页面展示 {actual_trade_count} 次贸易记录")
    elif imp.latest_import_date:
        # 有日期但无明细记录 — 说明页面可能有数据但提取失败，不写矛盾描述
        parts.append("页面有进口记录但明细未完整提取")
    else:
        # 无日期无记录
        if imp.analysis_entry_status == "entered_confirmed":
            if imp.analysis_data_status == "no_data":
                parts.append("进口分析页确认无数据")
            else:
                parts.append("进入进口分析页但未提取到贸易记录")

    # 补充统计卡片信息（如有）
    if imp.stats_card_total_value_usd:
        parts.append(f"美元总价: {imp.stats_card_total_value_usd}")
    if imp.stats_card_supplier_count:
        parts.append(f"供应商数: {imp.stats_card_supplier_count}")

    return "，".join(parts) if parts else ""


def _populate_product_fields(result: EnrichmentRow, imp: ImportAnalysis, product_keywords: str):
    """填充产品相关字段：top_import_products, related_hs_codes,
    product_relevance_level, product_relevance_score, matched_product_keywords。"""
    from collections import Counter

    # ── top_import_products: 从贸易产品+产品描述聚合，取 top 5 按频率 ──
    all_products = []
    for p in imp.trade_products:
        p_stripped = p.strip() if isinstance(p, str) else str(p).strip()
        if p_stripped and len(p_stripped) > 1:
            all_products.append(p_stripped)
    for pdesc in imp.trade_product_descriptions:
        pd_stripped = pdesc.strip() if isinstance(pdesc, str) else str(pdesc).strip()
        if pd_stripped and len(pd_stripped) > 2:
            all_products.append(pd_stripped)

    if all_products:
        counter = Counter(all_products)
        top5 = counter.most_common(5)
        result.top_import_products = "; ".join([name for name, _ in top5])

    # ── related_hs_codes: 从贸易明细的 HS 编码列表，过滤 N/A/空/- ──
    valid_hs = []
    for hs in imp.trade_hs_codes:
        hs_stripped = hs.strip().upper() if isinstance(hs, str) else str(hs).strip().upper()
        if hs_stripped and hs_stripped not in ("N/A", "NA", "-", "--", ""):
            valid_hs.append(hs_stripped)
    if valid_hs:
        result.related_hs_codes = "; ".join(valid_hs)

    # ── product_relevance_level & score ──
    level, score = _calc_product_relevance(
        product_keywords=product_keywords,
        trade_products=imp.trade_products,
        trade_descriptions=imp.trade_product_descriptions,
        trade_hs_codes=imp.trade_hs_codes,
        top_products_json=result.top_products_json,
    )
    result.product_relevance_level = level
    result.product_relevance_score = str(score)

    # ── matched_product_keywords: 提取匹配到的关键词片段 ──
    if product_keywords:
        matched_kws = []
        kw_lower = product_keywords.lower()
        # 检查输入关键词是否在贸易产品/描述中出现
        for kw in kw_lower.split(";"):
            kw = kw.strip()
            if not kw:
                continue
            for p in all_products:
                if kw in p.lower():
                    matched_kws.append(kw)
                    break
        if matched_kws:
            result.matched_product_keywords = "; ".join(matched_kws)


def _calc_product_relevance(
    product_keywords: str,
    trade_products: list,
    trade_descriptions: list,
    trade_hs_codes: list,
    top_products_json: str,
) -> tuple[str, int]:
    """计算产品相关性等级和分数。

    high: 不锈钢/钢管/法兰/304/316/不锈钢管/钢卷等核心产品
    medium: 碳钢/铸铁等一般金属制品
    low: 消费品、电子、服装等明显不相关产品
    unknown: 无任何产品/HS/描述信息时
    """
    import json as _json

    high_kws = [
        "不锈钢", "steel pipe", "steel tube", "seamless", "welded pipe",
        "flange", "pipe fitting", "管", "法兰", "弯头", "三通",
        "304", "316", "316l", "304l", "stainless", "钢卷", "steel coil",
        "steel plate", "steel sheet", "不锈钢管", "无缝钢管", "焊接钢管",
    ]
    medium_kws = [
        "carbon steel", "casting", "forging", "碳钢", "铸铁",
        "steel structure", "steel bar", "steel rod", "steel wire",
        "metal product", "金属制品", "型钢",
    ]
    low_kws = [
        "electronics", "electronic", "服装", "clothing", "garment",
        "food", "食品", "toy", "玩具", "furniture", "家具",
        "cosmetic", "化妆品", "plastic product", "塑料制品",
        "textile", "纺织", "shoe", "鞋", "bag", "包",
    ]

    # 合并所有产品文本
    all_text = " ".join(
        [str(p) for p in trade_products]
        + [str(d) for d in trade_descriptions]
        + [str(h) for h in trade_hs_codes]
    ).lower()

    # 也检查 top_products_json
    if top_products_json:
        try:
            top_products = _json.loads(top_products_json)
            if isinstance(top_products, list):
                for tp in top_products:
                    if isinstance(tp, dict):
                        for v in tp.values():
                            all_text += " " + str(v).lower()
                    else:
                        all_text += " " + str(tp).lower()
        except Exception:
            pass

    if not all_text.strip() and not product_keywords:
        return "unknown", 0

    # 检查输入的产品关键词是否在贸易数据中出现
    input_kw_signal = False
    if product_keywords:
        for kw in product_keywords.split(";"):
            kw = kw.strip().lower()
            if kw and kw in all_text:
                input_kw_signal = True
                break

    # 分级打分
    high_count = sum(1 for kw in high_kws if kw in all_text)
    medium_count = sum(1 for kw in medium_kws if kw in all_text)
    low_count = sum(1 for kw in low_kws if kw in all_text)

    if high_count >= 2 or (high_count >= 1 and input_kw_signal):
        score = min(80 + high_count * 5, 95)
        return "high", score
    elif high_count >= 1:
        return "high", 75
    elif medium_count >= 1 and low_count == 0:
        score = 50 + medium_count * 5
        return "medium", min(score, 65)
    elif low_count >= 2 and high_count == 0:
        return "low", max(10, 30 - low_count * 5)
    elif low_count >= 1 and high_count == 0:
        return "low", 30
    elif input_kw_signal:
        return "medium", 55
    else:
        return "unknown", 0


def determine_action(status: str, import_active: str) -> str:
    """根据 match_status 和进口活跃度推荐行动。"""
    if status == "confirmed" and import_active == "active":
        return "建议优先跟进"
    if status == "confirmed":
        return "建议继续跟进"
    if status == "likely_match":
        return "待人工复核后跟进"
    if status in ("unconfirmed", "conflict"):
        return "待人工复核"
    if status in ("no_result", "detail_page_failed"):
        return "转官网/LinkedIn核验"
    return "转官网/LinkedIn核验"


def build_summary(detail: CompanyDetail, imp: ImportAnalysis) -> str:
    """生成 business_summary 一句话摘要。"""
    parts = []
    if detail.standard_name:
        parts.append(detail.standard_name)
    if detail.company_status != "unknown":
        parts.append(f"状态: {detail.company_status}")
    if detail.website:
        parts.append(f"官网: {detail.website}")
    if imp.latest_import_date:
        parts.append(f"最近进口: {imp.latest_import_date}")
    if imp.total_records > 0:
        parts.append(f"贸易记录: {imp.total_records} 次")
    if imp.product_hs:
        parts.append(f"产品: {imp.product_hs[:30]}")
    return "，".join(parts) if parts else ""


def build_import_summary(imp: ImportAnalysis) -> str:
    """生成 import_activity_summary。"""
    if not imp.latest_import_date and imp.total_records == 0:
        return ""
    return f"最近一次进口时间为 {imp.latest_import_date}，页面展示 {imp.total_records} 次贸易记录。"


def build_evidence_excerpt(detail: CompanyDetail, imp: ImportAnalysis) -> str:
    """生成 evidence_excerpt 关键证据摘录。"""
    parts = []
    if imp.latest_import_date:
        parts.append(f"进口日期: {imp.latest_import_date}")
    if detail.website:
        parts.append(f"官网: {detail.website}")
    if imp.total_records > 0:
        parts.append(f"贸易记录: {imp.total_records} 次")
    if imp.product_hs:
        parts.append(f"产品: {imp.product_hs[:30]}")
    if detail.country:
        parts.append(f"国家: {detail.country}")
    return " | ".join(parts) if parts else ""


# ============================================================================
# 主入口函数（被 run_batch.py 调用）
# ============================================================================

# 全局 scraper 实例，复用浏览器连接
_scraper: TendataScraper | None = None

# HS 搜索额外结果列表（enrich_one_customer 返回 top1，其余存入此处）
_hs_extra_results: list[EnrichmentRow] = []


def get_and_clear_hs_extra_results() -> list[EnrichmentRow]:
    """取出并清空 HS 搜索的额外结果。供调用方获取完整公司列表。"""
    global _hs_extra_results
    results = _hs_extra_results
    _hs_extra_results = []
    return results


def self_check_before_batch(headless: bool = False) -> dict:
    """运行前自检，确保环境和腾道登录态就绪。

    Returns:
        dict with keys: ok (bool), messages (list[str])
    """
    messages = []
    ok = True

    # 1. 检查 Playwright 是否可用
    if not HAS_PLAYWRIGHT:
        messages.append("[FAIL] 缺少浏览器驱动，请运行: pip install playwright && playwright install chromium")
        return {"ok": False, "messages": messages}
    messages.append("[OK] 浏览器驱动已就绪")

    # 2. 尝试连接浏览器
    test_scraper = TendataScraper(headless=headless)
    try:
        test_scraper.connect()
        messages.append("[OK] 腾道助手窗口已连接")
    except RuntimeError:
        messages.append("[FAIL] 无法连接到腾道助手窗口")
        messages.append("")
        messages.append("请按以下步骤操作：")
        messages.append("  1. 双击运行 start_tendata_helper.bat 启动腾道助手")
        messages.append("  2. 等待腾道首页在腾道助手中打开")
        messages.append("  3. 重新运行抓取脚本")
        return {"ok": False, "messages": messages}

    # 3. 检查登录态
    try:
        logged_in = test_scraper.check_login()
        if logged_in:
            messages.append("[OK] 腾道登录态正常")
        else:
            messages.append("[FAIL] 腾道未登录或登录已过期")
            messages.append("")
            messages.append("请在腾道助手窗口中登录腾道，然后重试。")
            ok = False
    except Exception as e:
        messages.append(f"[FAIL] 登录态检查异常: {e}")
        ok = False

    # 关闭测试连接
    test_scraper.close()

    # 4. 关闭后需要重置全局 _scraper，否则后续 enrich 会复用已关闭的连接
    global _scraper
    _scraper = None

    return {"ok": ok, "messages": messages}


def _get_scraper(headless: bool = False) -> TendataScraper:
    global _scraper
    if _scraper is None:
        _scraper = TendataScraper(headless=headless)
        _scraper.connect()
    return _scraper


def _reset_browser_pages(scraper: TendataScraper):
    """重置浏览器状态：关闭多余标签页 + 强制回到干净搜索页。

    解决多公司批次中：
    1) 浏览器标签页持续累积
    2) 下一家任务从上一家的结果页/详情页起跑（search#/mailsou 等旧态）

    V5 新增：
    3) 检查浏览器健康状态
    4) 处理 pages=0 情况
    5) 抛出 BrowserContextClosedError 当浏览器不可恢复时
    """
    print(f"  [浏览器] _reset_browser_pages: 开始")

    # 【V5 新增】先进行健康检查
    health = check_browser_health(scraper)
    if not health["healthy"]:
        print(f"  [浏览器] 健康检查失败: {health['error']}")
        # 尝试恢复
        if recover_browser_page(scraper):
            print(f"  [浏览器] 页面恢复成功")
        else:
            print(f"  [浏览器] 页面恢复失败，抛出 BrowserContextClosedError")
            raise BrowserContextClosedError(f"浏览器上下文不可用: {health['error']}")

    if not scraper.context:
        print(f"  [浏览器] _reset_browser_pages: context 为空")
        raise BrowserContextClosedError("context 为空")

    # 安全读取 URL（防止 page 对象卡死）
    try:
        current_url = scraper.page.url
        print(f"  [浏览器] _reset_browser_pages: 当前 URL = {current_url}")
    except Exception as e:
        print(f"  [浏览器] _reset_browser_pages: 读取 URL 异常: {e}")
        # 尝试恢复
        if recover_browser_page(scraper):
            print(f"  [浏览器] 页面恢复成功")
            current_url = scraper.page.url
        else:
            raise BrowserContextClosedError(f"读取 URL 异常且恢复失败: {e}")

    try:
        pages = scraper.context.pages
        print(f"  [浏览器] _reset_browser_pages: pages 数量 = {len(pages)}")
    except Exception as e:
        print(f"  [浏览器] _reset_browser_pages: 读取 pages 异常: {e}")
        raise BrowserContextClosedError(f"读取 pages 异常: {e}")

    # 【V5 新增】pages=0 检查
    if len(pages) == 0:
        print(f"  [浏览器] pages 数量 = 0，尝试恢复...")
        if recover_browser_page(scraper):
            print(f"  [浏览器] 页面恢复成功")
            pages = scraper.context.pages
        else:
            raise BrowserContextClosedError("pages=0 且恢复失败")

    # 选出业务页作为 scraper.page，不关闭 chrome:// 内部页（会导致 CDP 连接断开）
    biz_page = None
    for p in pages:
        try:
            p_url = p.url
            if "bizr.tendata.cn/search" in p_url:
                biz_page = p
                break
        except Exception:
            pass
    if biz_page is None:
        for p in pages:
            try:
                p_url = p.url
                if "account.tendata.cn" in p_url:
                    biz_page = p
                    break
            except Exception:
                pass
    if biz_page is None:
        # 只保留最后一张非 chrome:// 的页面
        for p in reversed(pages):
            try:
                p_url = p.url
                if not p_url.startswith("chrome://"):
                    biz_page = p
                    break
            except Exception:
                pass
    if biz_page is not None:
        scraper.page = biz_page
        print(f"  [浏览器] _reset_browser_pages: 已选择业务页 URL={biz_page.url}")
    else:
        print(f"  [浏览器] _reset_browser_pages: 未找到业务页，尝试创建...")
        if recover_browser_page(scraper):
            print(f"  [浏览器] 页面创建成功")
        else:
            raise BrowserContextClosedError("未找到业务页且创建失败")

    # 强制导航回干净搜索页，不复用上一家的结果页
    search_url = "https://bizr.tendata.cn/search#/index"
    try:
        current_url = scraper.page.url
        if current_url != search_url and not current_url.endswith("#/index"):
            print(f"  [浏览器] 当前页不在搜索首页 ({current_url})，强制回到 {search_url}")
            scraper.page.goto(search_url, timeout=15000)
            scraper.page.wait_for_load_state("domcontentloaded", timeout=10000)
            scraper.page.wait_for_timeout(1000)
    except Exception as e:
        err_str = str(e).lower()
        if "target page" in err_str or "context" in err_str or "browser" in err_str or "closed" in err_str:
            print(f"  [浏览器] 导航时浏览器上下文关闭: {e}")
            raise BrowserContextClosedError(f"导航时浏览器上下文关闭: {e}")
        print(f"  [浏览器] 导航回搜索页异常: {e}")

    # 确保当前页在搜索页，不在就导航回去
    try:
        current_url = scraper.page.url
        if "bizr.tendata.cn/search" not in current_url:
            print(f"  [浏览器] 当前页不在搜索页，导航回搜索页")
            scraper.page.goto("https://bizr.tendata.cn/search#/index", timeout=15000)
            scraper.page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception as e:
        err_str = str(e).lower()
        if "target page" in err_str or "context" in err_str or "browser" in err_str or "closed" in err_str:
            print(f"  [浏览器] 导航时浏览器上下文关闭: {e}")
            raise BrowserContextClosedError(f"导航时浏览器上下文关闭: {e}")
        print(f"  [浏览器] 导航回搜索页异常: {e}")


def _close_scraper():
    global _scraper
    if _scraper:
        _scraper.close()
        _scraper = None


def enrich_one_customer(
    customer_name: str,
    country_region: str = "",
    website: str = "",
    email_domain: str = "",
    product_keywords: str = "",
    internal_customer_id: str = "",
    has_country: bool = True,
    headless: bool = False,
    batch_id: str = "",
    search_keyword: str = "",
    fallback_keyword: str = "",
    search_variants: str = "",
    used_search_variant: str = "",
) -> EnrichmentRow:
    """
    对单条客户执行腾道检索和信息抽取。

    Returns:
        EnrichmentRow 结果对象
    """
    t_start = time.monotonic()
    result = EnrichmentRow(
        customer_name=customer_name,
        country_region=country_region,
        website_input=website,
        email_domain=email_domain,
        product_keywords=product_keywords,
        internal_customer_id=internal_customer_id,
        search_keyword=search_keyword or customer_name,
        search_variants=search_variants,
        used_search_variant=used_search_variant,
        source_capture_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        source_search_keyword=search_keyword or customer_name,
        run_batch_id=batch_id,
    )

    # ---- 内部记录过滤：检测非客户记录 ----
    internal_record_keywords = [
        "费用", "部门费用", "总经办", "内销部门", "供应链部门", "线材部门",
        "内部", "测试", "财务部", "行政部", "人事部", "采购部",
        "销售部", "市场部", "研发部", "技术部", "生产部", "仓储部",
        "物流部", "质量部", "质检部", "售后部", "客服部", "运营部",
        "管理部", "综合部", "办公室", "办事处", "分公司",
    ]
    # 排除可能的误伤：如果公司名包含完整公司后缀，则不算内部记录
    company_suffixes = ["有限公司", "有限责任公司", "股份公司", "股份有限公司",
                        "公司", "CORP", "INC", "LTD", "LLC", "GMBH", "CO.", "CO.,LTD"]
    customer_name_lower = customer_name.lower().strip()

    # 检查是否包含公司后缀
    has_company_suffix = any(suffix.lower() in customer_name_lower for suffix in company_suffixes)

    # 检查是否匹配内部记录关键词
    is_internal_record = False
    matched_keyword = ""
    if not has_company_suffix:  # 只有不含公司后缀时才检测内部记录
        for keyword in internal_record_keywords:
            if keyword in customer_name:
                is_internal_record = True
                matched_keyword = keyword
                break

    if is_internal_record:
        print(f"  [过滤] 检测到内部记录关键词 '{matched_keyword}'，跳过腾道查询")
        result.match_status = "excluded_internal_record"
        result.match_confidence = 0
        result.manual_review_flag = "no"
        result.manual_review_reason = f"非客户记录/内部费用记录（关键词: {matched_keyword}）"
        result.recommended_action = "不纳入客户排查"
        return result

    scraper = _get_scraper(headless=headless)

    # ---- 浏览器状态重置：关闭多余标签页，确保从干净状态开始 ----
    _reset_browser_pages(scraper)
    try:

        # ---- 登录检查 ----
        if not scraper.check_login():
            result.match_status = "unconfirmed"
            result.match_confidence = 0
            result.manual_review_flag = "yes"
            result.manual_review_reason = "腾道未登录或登录态失效"
            result.recommended_action = "待人工复核"
            return result

        # 登录检查通过后，确认页面类型
        page_type = classify_page(scraper.page)
        if page_type == "login":
            # check_login 已拦截，这里不会到达
            result.match_status = "unconfirmed"
            result.match_confidence = 0
            result.manual_review_flag = "yes"
            result.manual_review_reason = "腾道未登录"
            result.recommended_action = "待人工复核"
            return result
        if page_type == "learning":
            result.match_status = "unconfirmed"
            result.match_confidence = 0
            result.manual_review_flag = "yes"
            result.manual_review_reason = "已登录腾道，但当前处于学习中心（knowledge.tendata.cn）。请先进入商情发现页面（bizr.tendata.cn）后再重试"
            result.recommended_action = "待人工复核"
            return result

        # ---- 搜索 ----
        # 判断是否为 HS 编码搜索模式
        is_hs_search = False
        cleaned = re.sub(r"[^\d]", "", product_keywords) if product_keywords else ""
        if len(cleaned) == 6:
            is_hs_search = True
            print(f"  [HS模式] 检测到 product_keywords='{product_keywords}' 为 6 位 HS 编码，走 HS 批量搜索路径")

        if is_hs_search:
            # HS 编码默认走快速搜索路径：只提取卡片摘要，不进入详情页
            # 如需深度挖掘指定公司，使用 hs_enrich_selected()
            cards = hs_quick_search(
                hs_code=cleaned,
                country_filter=country_region,
                max_companies=20,
                headless=headless,
                batch_id=batch_id,
                scraper=scraper,
            )
            if not cards:
                result.match_status = "no_result"
                result.match_confidence = 0
                result.manual_review_reason = "HS 编码搜索无结果"
                result.recommended_action = "转官网/LinkedIn核验"
                return result

            # 将 top1 卡片摘要转为 EnrichmentRow 返回
            top1 = cards[0]
            result.customer_name = top1["company_name"]
            result.hs_product = top1["hs_product_desc"]
            if top1["hs_trade_count"] > 0:
                result.total_import_volume = str(top1["hs_trade_count"])
            result.match_status = "matched"
            result.match_confidence = 50
            result.source_page_title = "HS 搜索结果页"
            result.source_candidate_rank = top1.get("rank", 1)
            result.source_page_url = top1.get("page_url", "")
            result.recommended_action = "待人工选择"
            result.manual_review_reason = f"quick_search 返回 {len(cards)} 家卡片摘要，请使用 hs_enrich_selected() 展开指定公司详情"

            # 全部卡片存入全局缓存，供调用方通过 get_and_clear_hs_extra_results() 获取
            _hs_extra_results.clear()
            for card in cards[1:]:
                extra_row = EnrichmentRow(
                    customer_name=card["company_name"],
                    product_keywords=cleaned,
                    hs_product=card["hs_product_desc"],
                    total_import_volume=str(card["hs_trade_count"]) if card["hs_trade_count"] > 0 else "",
                    match_status="matched",
                    match_confidence=50,
                    source_page_title="HS 搜索结果页",
                    source_candidate_rank=card.get("rank", 0),
                    source_page_url=card.get("page_url", ""),
                    run_batch_id=batch_id,
                )
                _hs_extra_results.append(extra_row)

            print(f"  [HS模式] quick_search 返回 {len(cards)} 家卡片摘要（未进详情页），返回 top1: '{top1['company_name'][:60]}'")
            return result
        else:
            # 公司名搜索路径
            if search_keyword:
                # 调用方已提供清洗后的搜索词
                raw_kw = search_keyword
            else:
                raw_kw = customer_name
            search_keyword = _normalize_company_name(raw_kw)
            if search_keyword != raw_kw:
                print(f"  [标准化] 公司名: '{raw_kw}' → '{search_keyword}'")
            if not scraper.search_company(search_keyword):
                result.match_status = "no_result"
                result.match_confidence = 0
                result.manual_review_reason = "搜索页打开失败或搜索超时"
                result.recommended_action = "转官网/LinkedIn核验"
                return result

        # ---- 提取搜索结果 ----
        candidates = scraper.extract_search_results()
        if not candidates:
            if fallback_keyword:
                print(f"  [备用搜索] 主搜索无结果，尝试备用词: '{fallback_keyword}'")
                fb_kw_normalized = _normalize_company_name(fallback_keyword)
                top1, candidates = _search_and_select(
                    scraper, fb_kw_normalized, customer_name, country_region, 60
                )
                if top1:
                    print(f"  [备用搜索] 成功选中: '{top1.company_name[:60]}'")
                    result.source_search_keyword = fallback_keyword
                else:
                    print(f"  [备用搜索] 备用词搜索也无结果")
                    result.match_status = "no_result"
                    result.match_confidence = 0
                    result.recommended_action = "转官网/LinkedIn核验"
                    return result
            else:
                result.match_status = "no_result"
                result.match_confidence = 0
                result.recommended_action = "转官网/LinkedIn核验"
                return result

        # ---- 搜索结果选择 ----
        if is_hs_search:
            # HS 搜索：直接取第一个结果，不做名称相似度打分
            top1 = candidates[0]
            result.match_status = "matched"
            result.match_confidence = 50  # HS 搜索结果，置信度固定
            print(f"  [HS模式] 取搜索结果第 1 家: '{top1.company_name[:50]}'")
        else:
            # 公司名搜索：多维度打分 + 国家匹配优先
            scored = []
            for c in candidates:
                s, d = _score_search_candidate(c, customer_name, country_region)
                scored.append((s, d, c))
            scored.sort(key=lambda x: x[0], reverse=True)

            print(f"  [候选打分] 输入: '{customer_name}', 国家: '{country_region or '未提供'}'")
            for s, d, c in scored[:5]:
                name_sim_str = f"sim={d.get('name_similarity', 0):.2f}" if 'name_similarity' in d else ""
                print(f"  [候选打分] 分数={s} {name_sim_str}  候选: '{c.company_name[:50]}' 地区: '{(getattr(c, 'country', '') or '')[:30]}' 详情: {d}")

            best_score, best_details, top1 = scored[0]
            score_threshold = 60

            # 新规则：名称高度匹配时降低阈值
            # 如果 name_similarity >= 0.70 或 name_match_level = high，降低阈值到 40
            best_name_sim = best_details.get("name_similarity", 0)
            best_name_level = best_details.get("name_match_level", "low")
            if best_name_sim >= 0.70 or best_name_level == "high":
                score_threshold = 40  # 降低阈值，允许高分名称匹配候选进入详情页
                print(f"  [候选打分] 名称高度匹配 (sim={best_name_sim:.2f}, level={best_name_level})，降低阈值到 {score_threshold}")

            # 新规则：exact/near-exact 匹配时直接进入详情页（不受阈值限制）
            if best_name_sim >= 0.85:
                print(f"  [候选打分] 检测到 exact/near-exact 名称匹配 (sim={best_name_sim:.2f})，直接进入详情页")
                # 跳过阈值检查，直接进入详情页
            elif best_score < score_threshold:
                print(f"  [候选打分] 最高分 {best_score} < 阈值 {score_threshold}，候选分数不足")

                # 新规则：分层处理 candidate_found_not_entered
                # 1. 如果 name_match_level = high 或 name_similarity >= 0.80，仍尝试进入详情页
                # 2. 如果是弱候选（candidate_score < 60 且 name_similarity < 0.70），标记为 weak_candidate_ignored，不需要人工复核
                # 3. 如果候选国家未知但名称有一定匹配（name_similarity >= 0.70），标注为 candidate_country_unknown

                is_high_name_match = best_name_level == "high" or best_name_sim >= 0.80

                if is_high_name_match:
                    # 高名称匹配但分数不够（可能是国家缺失导致），仍尝试进入详情页
                    print(f"  [候选打分] 名称高度匹配 (level={best_name_level}, sim={best_name_sim:.2f})，即使分数不足也尝试进入详情页")
                    # 继续到详情页逻辑，不在此返回
                elif candidates and len(candidates) > 0:
                    # 判断候选类型
                    is_weak_candidate = best_score < 60 and best_name_sim < 0.70
                    is_country_unknown = best_details.get("country_match", "").startswith("unknown") and best_name_sim >= 0.70

                    if is_weak_candidate:
                        # 弱候选：分数不足且名称匹配度低，不需要人工复核
                        print(f"  [候选打分] 弱候选 (score={best_score}, sim={best_name_sim:.2f})，标记为 weak_candidate_ignored")
                        result.match_status = "candidate_found_not_entered"
                        result.match_confidence = 0
                        result.manual_review_flag = "no"
                        result.matched_company_name = top1.company_name
                        result.manual_review_reason = f"weak_candidate_ignored: 候选分数低且名称不够匹配（score={best_score} < 60, name_sim={best_name_sim:.2f} < 0.70）"
                        result.recommended_action = "转官网/LinkedIn核验"

                    elif is_country_unknown:
                        # 国家未知但名称有一定匹配，需要人工复核
                        print(f"  [候选打分] 国家未知但名称有一定匹配 (sim={best_name_sim:.2f})，标记为 candidate_country_unknown")
                        result.match_status = "candidate_found_not_entered"
                        result.match_confidence = max(int(best_score * 100 // 100), 0)
                        result.manual_review_flag = "yes"
                        result.matched_company_name = top1.company_name

                        detail_parts = []
                        if "country_match" in best_details:
                            detail_parts.append(best_details["country_match"])
                        if "name_body_match" in best_details:
                            detail_parts.append(best_details["name_body_match"])

                        result.manual_review_reason = f"candidate_country_unknown: 候选国家未解析但名称有一定匹配（最高分={best_score}, name_sim={best_name_sim:.2f}, {'; '.join(detail_parts)}）"
                        result.recommended_action = "待人工复核"

                    else:
                        # 其他情况：分数不够但有中等匹配，标记为需要人工复核
                        print(f"  [候选打分] 搜索页有 {len(candidates)} 个候选但分数不足，标记为 candidate_found_not_entered")
                        result.match_status = "candidate_found_not_entered"
                        result.match_confidence = max(int(best_score * 100 // 100), 0)
                        result.manual_review_flag = "yes"
                        result.matched_company_name = top1.company_name

                        detail_parts = []
                        if "country_match" in best_details:
                            detail_parts.append(best_details["country_match"])
                        if "name_body_match" in best_details:
                            detail_parts.append(best_details["name_body_match"])

                        result.manual_review_reason = f"候选分数不足（最高分={best_score} < 阈值{score_threshold}, name_sim={best_name_sim:.2f}, {'; '.join(detail_parts)}）"
                        result.recommended_action = "待人工复核"

                    # 保留候选摘要信息
                    result.candidate_score = best_score
                    result.name_match_level = best_details.get("name_match_level", "unknown")
                    result.country_match = best_details.get("country_match", "unknown")
                    result.source_search_keyword = search_keyword or customer_name
                    result.used_search_variant = search_keyword or customer_name

                    # 保留 top 3 候选摘要
                    candidate_summary = []
                    for i, (s, d, c) in enumerate(scored[:3]):
                        summary_entry = {
                            "rank": i + 1,
                            "company_name": c.company_name,
                            "country": getattr(c, 'country', '') or '',
                            "score": s,
                            "name_similarity": d.get("name_similarity", 0),
                            "name_match_level": d.get("name_match_level", "unknown"),
                            "country_match": d.get("country_match", "unknown"),
                        }
                        candidate_summary.append(summary_entry)
                    if candidate_summary:
                        import json as _json
                        result.candidate_summary_json = _json.dumps(candidate_summary, ensure_ascii=False)

                    return result

                # 尝试备用搜索词
                if fallback_keyword:
                    print(f"  [备用搜索] 主搜索词无高匹配候选，尝试备用词: '{fallback_keyword}'")
                    fb_kw_normalized = _normalize_company_name(fallback_keyword)
                    top1, retry_candidates = _search_and_select(
                        scraper, fb_kw_normalized, customer_name, country_region, score_threshold
                    )
                    if top1:
                        print(f"  [备用搜索] 成功选中: '{top1.company_name[:60]}'")
                        result.source_search_keyword = fallback_keyword
                        result.source_candidate_rank = top1.rank
                        result.source_page_url = top1.page_url
                        candidates = retry_candidates
                        # 检查多候选接近
                        scored = [(s, d, c) for s, d, c in zip(
                            [_score_search_candidate(c, customer_name, country_region)[0] for c in retry_candidates[:5]],
                            [_score_search_candidate(c, customer_name, country_region)[1] for c in retry_candidates[:5]],
                            retry_candidates[:5],
                        )]
                        scored.sort(key=lambda x: x[0], reverse=True)
                        if len(scored) >= 2 and scored[0][0] - scored[1][0] < 15:
                            result.manual_review_flag = "yes"
                            result.manual_review_reason = f"多候选接近（top1={scored[0][0]}, top2={scored[1][0]}）"
                        # 继续到详情页
                    else:
                        print(f"  [备用搜索] 备用词搜索也未找到高匹配候选")
                        # 新规则：备用搜索也无果，但如果有候选，仍保留候选摘要
                        result.match_status = "no_result"
                        result.match_confidence = max(int(best_score * 100 // 100), 0)
                        result.manual_review_flag = "yes"
                        detail_parts = []
                        if "country_match" in best_details:
                            detail_parts.append(best_details["country_match"])
                        if "name_body_match" in best_details:
                            detail_parts.append(best_details["name_body_match"])
                        result.manual_review_reason = f"搜索结果无高匹配候选（最高分={best_score}, {'; '.join(detail_parts)}），备用搜索也无果"
                        result.recommended_action = "转官网/LinkedIn核验"

                        # 保留候选摘要
                        result.candidate_score = best_score
                        result.name_match_level = best_details.get("name_match_level", "unknown")
                        result.country_match = best_details.get("country_match", "unknown")
                        result.matched_company_name = top1.company_name if top1 else ""
                        result.source_search_keyword = search_keyword or customer_name
                        result.used_search_variant = search_keyword or customer_name

                        return result
                else:
                    # 无备用搜索词，但有候选
                    result.match_status = "no_result"
                    result.match_confidence = max(int(best_score * 100 // 100), 0)
                    result.manual_review_flag = "yes"
                    detail_parts = []
                    if "country_match" in best_details:
                        detail_parts.append(best_details["country_match"])
                    if "name_body_match" in best_details:
                        detail_parts.append(best_details["name_body_match"])
                    result.manual_review_reason = f"搜索结果无高匹配候选（最高分={best_score}, {'; '.join(detail_parts)}）"
                    result.recommended_action = "转官网/LinkedIn核验"

                    # 保留候选摘要
                    result.candidate_score = best_score
                    result.name_match_level = best_details.get("name_match_level", "unknown")
                    result.country_match = best_details.get("country_match", "unknown")
                    result.matched_company_name = top1.company_name if top1 else ""
                    result.source_search_keyword = search_keyword or customer_name
                    result.used_search_variant = search_keyword or customer_name

                    return result

            # 检查国家不匹配的最高分候选
            has_country_input = bool(country_region and country_region.strip())
            if has_country_input:
                cand_country = getattr(top1, 'country', '') or ''
                # 只对国家名看起来有效的候选做国家匹配检查
                is_valid_cand = bool(
                    cand_country
                    and len(cand_country) >= 2
                    and not cand_country.strip().isdigit()
                )
                if is_valid_cand:
                    norm_input = _normalize_country(country_region)
                    norm_cand = _normalize_country(cand_country)
                    if norm_input and norm_cand and norm_input != norm_cand:
                        print(f"  [候选选择] 最高分候选国家不匹配（输入={country_region}, 候选={cand_country}），检查是否有国家匹配候选")
                        # 寻找国家匹配的候选
                        country_matched = None
                        for s, d, c in scored:
                            cc = getattr(c, 'country', '') or ''
                            cc_valid = bool(cc and len(cc) >= 2 and not cc.strip().isdigit())
                            if cc_valid and _normalize_country(cc) == norm_input:
                                if s >= score_threshold:
                                    country_matched = (s, d, c)
                                    break
                        if country_matched:
                            print(f"  [候选选择] 改用国家匹配候选: '{country_matched[2].company_name[:50]}' (分数={country_matched[0]})")
                            best_score, best_details, top1 = country_matched
                        else:
                            print(f"  [候选选择] 无国家匹配候选，标记 conflict")
                            result.match_status = "conflict"
                            result.match_confidence = max(int(best_score // 2), 10)
                            result.manual_review_flag = "yes"
                            result.manual_review_reason = f"国家不匹配：输入={country_region}，搜索结果={cand_country}"
                            result.recommended_action = "待人工复核"
                            return result

            # 检查多候选接近
            if len(scored) >= 2:
                second_score = scored[1][0]
                if best_score - second_score < 15:
                    result.manual_review_flag = "yes"
                    result.manual_review_reason = f"多候选接近（top1={best_score}, top2={second_score}）"

        # ---- 进入详情页 ----
        best_name_sim = best_details.get("name_similarity", 0)
        if not scraper.go_to_detail(top1, name_similarity=best_name_sim):
            result.matched_company_name = top1.company_name
            result.match_status = "detail_page_failed"  # 新状态：详情页进入失败
            result.match_confidence = max(int(_name_similarity(customer_name, top1.company_name) * 100), 30)
            result.source_page_title = "贸易数据搜索结果页"
            result.manual_review_flag = "yes"
            result.manual_review_reason = "详情页进入失败（多种方式尝试后仍未能跳转到详情页）"
            result.recommended_action = "待人工复核"

            # 保留候选摘要信息
            result.candidate_score = best_score
            result.name_match_level = best_details.get("name_match_level", "unknown")
            result.country_match = best_details.get("country_match", "unknown")
            result.source_search_keyword = search_keyword or customer_name
            result.used_search_variant = search_keyword or customer_name

            # 保留 top 3 候选摘要
            candidate_summary = []
            for i, (s, d, c) in enumerate(scored[:3]):
                summary_entry = {
                    "rank": i + 1,
                    "company_name": c.company_name,
                    "country": getattr(c, 'country', '') or '',
                    "score": s,
                    "name_similarity": d.get("name_similarity", 0),
                    "name_match_level": d.get("name_match_level", "unknown"),
                    "country_match": d.get("country_match", "unknown"),
                }
                candidate_summary.append(summary_entry)
            if candidate_summary:
                import json as _json
                result.candidate_summary_json = _json.dumps(candidate_summary, ensure_ascii=False)

            current_url_after_fail = scraper.page.url
            total_seconds = round(time.monotonic() - t_start, 2)
            print(f"\n  [错误] 详情页进入失败，跳过该客户")
            print(f"  [错误] result.page_url: '{top1.page_url}'")
            print(f"  [错误] current_url: {current_url_after_fail}")
            print(f"  [错误] total_seconds: {total_seconds}s")
            return result

        # 详情页已确认进入
        print(f"  [流程] 详情页识别成功，开始提取详情字段")
        t_detail_start = time.monotonic()
        detail = scraper.extract_company_detail()
        detail_page_seconds = round(time.monotonic() - t_detail_start, 2)
        result.matched_company_name = detail.standard_name or top1.company_name
        result.matched_country = detail.country
        result.website_result = detail.website
        result.company_status = detail.company_status
        result.contact_name = detail.contact_name
        result.phone = detail.phone
        result.email = detail.email
        result.address = detail.address
        result.location = detail.location
        result.whatsapp = detail.whatsapp
        result.linkedin = detail.linkedin

        # 记录实际使用的搜索词
        result.used_search_variant = search_keyword or customer_name

        # ---- 产品信息页 ----
        t_product_start = time.monotonic()
        if scraper.go_to_product_info_tab():
            products = scraper.extract_top_products(max_items=3)
            if products:
                import json as _json
                result.top_products_json = _json.dumps(products, ensure_ascii=False)
                print(f"  [流程] 产品信息: 提取到 {len(products)} 个产品")
            else:
                print(f"  [流程] 产品信息: 无产品数据")
        product_page_seconds = round(time.monotonic() - t_product_start, 2)

        # ---- 进口分析页 ----
        t_import_start = time.monotonic()
        target_hs_codes = _parse_hs_codes(product_keywords)

        imp = scraper.go_to_import_analysis()
        if imp.analysis_entry_status == "entered_confirmed":
            imp = scraper.extract_import_analysis(imp, target_hs_codes=target_hs_codes)
        else:
            print(f"  [流程] 未确认进入进口分析页 (entry_status={imp.analysis_entry_status})")

        # 如果进口分析页数据不完整，尝试标记更具体的状态
        if imp.analysis_entry_status == "entered_confirmed":
            if imp.analysis_data_status != "has_data":
                # 检查是否有部分数据
                has_partial_data = bool(imp.latest_import_date or imp.trade_dates or imp.total_records > 0)
                if has_partial_data:
                    imp.analysis_data_status = "partial_data"
                    print(f"  [流程] 进口分析页数据部分提取 (analysis_data_status=partial_data)")
                else:
                    imp.analysis_data_status = "no_import_analysis_data"
                    print(f"  [流程] 进口分析页无数据 (analysis_data_status=no_import_analysis_data)")

        import_analysis_seconds = round(time.monotonic() - t_import_start, 2)

        # 将分析状态写入结果行
        result.analysis_entry_status = imp.analysis_entry_status
        result.analysis_data_status = imp.analysis_data_status

        if imp.analysis_entry_status == "entered_confirmed" and imp.analysis_data_status == "has_data":
            print(f"  [流程] 进口分析页进入成功，检测到进口数据")
            result.source_page_title = "进口分析页"
        elif imp.analysis_entry_status == "entered_confirmed":
            print(f"  [流程] 进入进口分析页成功，数据状态: {imp.analysis_data_status}")
            result.source_page_title = "进口分析页"
        elif imp.analysis_entry_status == "clicked_not_confirmed":
            print(f"  [流程] 已点击进口分析入口但未确认进入")
            # 不再强制标记为需要人工复核，因为 base-info 可能已经足够确认
            result.source_page_title = "企业详情页"
        else:
            print(f"  [流程] 未找到进口分析入口 (entry_status={imp.analysis_entry_status})")
            # 标记为 partial_base_info_only
            imp.analysis_data_status = "partial_base_info_only"
            if detail.standard_name:
                result.source_page_title = "企业详情页"
            else:
                result.source_page_title = "贸易数据搜索结果页"

        result.latest_import_date = imp.latest_import_date

        # ── 供应商相关字段 ──
        _populate_supplier_fields(result, imp)

        # ── total_shipment_count: 优先用统计卡片「贸易次数」，其次用贸易明细表行数 ──
        if imp.total_records > 0:
            result.total_shipment_count = str(imp.total_records)
        elif imp.trade_dates:
            result.total_shipment_count = str(len(imp.trade_dates))
        elif result.total_import_volume:
            result.total_shipment_count = result.total_import_volume

        # ── last_12m/24m/36m_import_count: 仅从贸易明细表「日期」列按当前日期倒推计算 ──
        _populate_import_counts_from_dates(result, imp)

        # ── import_active_status ──
        result.import_active_status = determine_import_active_new(
            imp.latest_import_date,
            result.match_status,
        )

        # ── import_frequency_level ──
        result.import_frequency_level = determine_import_frequency_new(
            result.last_12m_import_count,
            result.last_24m_import_count,
            result.last_36m_import_count,
        )

        # ── import_activity_summary: 不出现矛盾 ──
        result.import_activity_summary = build_import_summary_v2(imp)

        # ── 产品相关字段 ──
        _populate_product_fields(result, imp, product_keywords)

        # ── 体量字段 ──
        result.estimated_trade_volume_level = _estimate_trade_volume(
            total_records=imp.total_records,
            amount_json=imp.target_hs_amount_json,
        )
        result.buyer_activity_level = result.import_active_status

        # ---- 计算匹配状态 ----
        status, conf = compute_match_status(
            input_name=customer_name,
            matched_name=detail.standard_name or top1.company_name,
            input_country=country_region,
            page_country=detail.country,
            input_website=website,
            page_website=detail.website,
            has_country=has_country,
        )
        result.match_status = status
        result.match_confidence = conf

        # 名称匹配等级
        name_sim_val = _name_similarity(customer_name, detail.standard_name or top1.company_name)
        if name_sim_val >= 0.85:
            result.name_match_level = "high"
        elif name_sim_val >= 0.6:
            result.name_match_level = "medium"
        elif name_sim_val >= 0.4:
            result.name_match_level = "low"
        else:
            result.name_match_level = "none"

        # 候选分数
        result.candidate_score = conf

        # country_match 字段
        if has_country and country_region and detail.country:
            norm_input_c = _normalize_country(country_region)
            norm_page_c = _normalize_country(detail.country)
            if norm_input_c and norm_page_c:
                result.country_match = "yes" if norm_input_c == norm_page_c else "no"
            else:
                result.country_match = "unknown"
        elif has_country and country_region:
            result.country_match = "no_page_country"
        else:
            result.country_match = "no_input_country"

        # ---- 国家/行业不匹配安全规则 ----
        if has_country and country_region and detail.country:
            norm_input = _normalize_country(country_region)
            norm_page = _normalize_country(detail.country)
            if norm_input and norm_page and norm_input != norm_page:
                # 国家明显不一致，禁止 confirmed
                result.manual_review_flag = "yes"
                reason = f"国家不匹配：输入={country_region}，搜索结果={detail.country}"
                if result.manual_review_reason:
                    result.manual_review_reason = result.manual_review_reason + "; " + reason
                else:
                    result.manual_review_reason = reason
                if result.match_status == "confirmed":
                    result.match_status = "unconfirmed"
                    result.match_confidence = min(result.match_confidence, 59)
                elif result.match_status == "likely_match":
                    result.match_confidence = min(result.match_confidence, 59)

        # ---- 明确冲突检测：国家+官网/邮箱双不匹配 → conflict ----
        is_conflict, conflict_reasons = _detect_conflict(
            input_country=country_region,
            page_country=detail.country,
            input_website=website,
            page_website=detail.website,
            input_email_domain=email_domain,
            page_email=detail.email,
            top_products_json=result.top_products_json,
        )
        if is_conflict:
            # 保存原始候选日期（冲突时不得作为目标客户有效数据）
            result.raw_candidate_latest_import_date = imp.latest_import_date or ""
            # 覆盖 match_status
            result.match_status = "conflict"
            result.match_confidence = max(result.match_confidence, 10)
            result.manual_review_flag = "yes"
            result.conflict_reason = "; ".join(conflict_reasons)
            if result.manual_review_reason:
                result.manual_review_reason = result.manual_review_reason + "; " + "; ".join(conflict_reasons)
            else:
                result.manual_review_reason = "; ".join(conflict_reasons)
            # 清除目标客户所有进口活跃度相关字段（V5：防止污染后续评分）
            result.latest_import_date = ""
            result.last_12m_import_count = ""
            result.last_24m_import_count = ""
            result.last_36m_import_count = ""
            result.total_shipment_count = ""
            result.supplier_count = ""
            result.top_import_products = ""
            result.related_hs_codes = ""
            result.product_relevance_level = "unknown"
            result.product_relevance_score = "0"
            result.import_frequency_level = "unknown"
            result.import_active_status = "invalid_for_target"
            result.recommended_action = "待人工复核"
            print(f"  [冲突检测] 明确冲突 — {'; '.join(conflict_reasons)}")

        # 记录 domain_match 和 country_match 详情
        try:
            if website and detail.website:
                result.domain_match = "yes" if _domain_match(website, detail.website) else "no"
            elif website:
                result.domain_match = "no_website_result"
            else:
                result.domain_match = "no_input"
        except Exception:
            result.domain_match = "unknown"

        # ---- 多候选时降置信度 ----
        if result.manual_review_flag == "yes" and result.match_confidence > 60:
            result.match_confidence = 60
            if result.match_status == "confirmed":
                result.match_status = "likely_match"

        # ---- 摘要字段 ----
        result.business_summary = build_summary(detail, imp)
        result.evidence_excerpt = build_evidence_excerpt(detail, imp)

        # ---- 推荐行动 ----
        result.recommended_action = determine_action(result.match_status, result.import_active_status)

        # ---- V5: confirmed/likely_match 但无进口分析数据 → 调整推荐 ----
        if result.match_status in ("confirmed", "likely_match"):
            has_import_data = bool(
                result.total_shipment_count
                or result.top_import_products
                or result.related_hs_codes
                or result.supplier_count
            )
            # 有 latest_import_date 但无贸易次数 → partial_import_signal
            has_latest_date = bool(result.latest_import_date)
            has_trade_counts = bool(result.last_12m_import_count or result.last_24m_import_count or result.last_36m_import_count)
            if has_latest_date and not has_trade_counts:
                result.analysis_data_status = "partial_import_signal"
                result.import_frequency_level = "unknown"
                print(f"  [V5规则] 有进口日期但无贸易次数，标记 partial_import_signal")
            elif not has_import_data:
                # 主体匹配但无腾道采购数据
                if result.analysis_data_status in ("unknown", "has_data"):
                    result.analysis_data_status = "no_import_analysis_data"
                result.import_active_status = "unknown"
                result.import_frequency_level = "unknown"
                result.recommended_action = "转官网/LinkedIn核验"
                print(f"  [V5规则] confirmed/likely_match 但无进口数据，调整推荐: {result.recommended_action}")

        # ---- V5: 目录站/网站候选检测 → 不算企业主体 ----
        matched_name = result.matched_company_name or ""
        matched_name_lower = matched_name.lower()
        domain_patterns = [r'\.com', r'\.org', r'\.net', r'\.io', r'\.co\.', r'\.tr', r'\.com\.tr', r'\.co\.uk']
        is_domain_candidate = False
        for pattern in domain_patterns:
            if re.search(pattern, matched_name_lower):
                is_domain_candidate = True
                break
        if is_domain_candidate:
            # 检查是否有公司后缀（有则可能是公司名包含域名）
            has_company_suffix = any(suffix in matched_name_lower for suffix in [
                'ltd', 'llc', 'inc', 'corp', 'gmbh', 'srl', 'spa', 'co.', 'limited', 'company'
            ])
            if not has_company_suffix:
                # 纯域名候选，不是企业主体
                result.match_status = "unconfirmed"
                result.match_confidence = min(result.match_confidence, 30)
                result.manual_review_flag = "no"
                result.manual_review_reason = "命中目录/网站候选，非企业主体"
                result.recommended_action = "转官网/LinkedIn核验"
                # 清除进口活跃度字段
                result.latest_import_date = ""
                result.import_active_status = "unknown"
                result.import_frequency_level = "unknown"
                result.total_shipment_count = ""
                result.supplier_count = ""
                result.top_import_products = ""
                result.related_hs_codes = ""
                result.product_relevance_level = "unknown"
                result.product_relevance_score = "0"
                print(f"  [V5规则] 命中目录/网站候选 '{matched_name[:50]}'，标记为 unconfirmed")

        # ---- 诊断汇总输出 ----
        total_seconds = round(time.monotonic() - t_start, 2)
        result.elapsed_seconds = str(total_seconds)
        result.current_url = scraper.page.url if scraper.page else ""
        tp = result.top_products_json
        ts = result.top_suppliers_json
        tp_str = f"'{tp[:100]}...'" if tp else "(空)"
        ts_str = f"'{ts[:100]}...'" if ts else "(空)"
        print(f"\n  === 诊断汇总 ===")
        print(f"  result.page_url       : '{top1.page_url}'")
        print(f"  matched_company_name  : '{result.matched_company_name}'")
        print(f"  location              : '{result.location}'")
        print(f"  website_result        : '{result.website_result}'")
        print(f"  phone                 : '{result.phone}'")
        print(f"  address               : '{result.address}'")
        print(f"  whatsapp              : '{result.whatsapp}'")
        print(f"  linkedin              : '{result.linkedin}'")
        print(f"  company_status        : '{result.company_status}'")
        print(f"  top_products_json     : {tp_str}")
        print(f"  target_hs_amount_json : '{result.target_hs_amount_json}'")
        print(f"  top_suppliers_json    : {ts_str}")
        print(f"  analysis_entry_status : '{result.analysis_entry_status}'")
        print(f"  analysis_data_status  : '{result.analysis_data_status}'")
        print(f"  match_status          : '{result.match_status}' (confidence={result.match_confidence})")
        print(f"  recommended_action    : '{result.recommended_action}'")
        print(f"  --- 耗时 ---")
        print(f"  detail_page_seconds   : {detail_page_seconds}s")
        print(f"  product_page_seconds  : {product_page_seconds}s")
        print(f"  import_analysis_seconds: {import_analysis_seconds}s")
        print(f"  total_seconds         : {total_seconds}s")
        print(f"  =======================\n")

    finally:
        cleanup_info = scraper._cleanup_tabs()
        page_count = cleanup_info["remaining_count"]
        closed = cleanup_info["closed_count"]
        main_url = cleanup_info["main_page_url"]

        print(f"  [清理] 当前标签页数量: {page_count}")
        print(f"  [清理] 已关闭临时标签页: {closed}")
        print(f"  [清理] 保留主标签页: {main_url}")

        if page_count > 5:
            print(f"  [清理] 警告: 清理后仍有 {page_count} 个标签页，超过安全阈值 5")
            forced = scraper._force_cleanup_tabs()
            page_count = forced["remaining_count"]
            if page_count > 5:
                raise RuntimeError(
                    f"浏览器标签页异常: 清理后仍有 {page_count} 个标签页 (阈值=5)，"
                    f"暂停批次以防止浏览器卡死。请检查浏览器状态后重启。"
                )

    return result


def enrich_hs_search(
    hs_code: str,
    country_filter: str = "",
    max_companies: int = 20,
    headless: bool = False,
    batch_id: str = "",
    scraper: "TendataScraper | None" = None,
) -> list[EnrichmentRow]:
    """HS 编码批量找客户。

    执行一次 HS 搜索，提取前 max_companies 家公司，逐家抓取详情+进口分析。

    Args:
        hs_code: 6位 HS 编码（如 "730723"）
        country_filter: 可选，目标国家过滤（如 "CA"），为空不过滤
        max_companies: 最大返回公司数，默认 20
        headless: 是否无头模式
        batch_id: 批次 ID，用于日志
        scraper: 可选，传入已有 scraper 实例（避免重复创建）

    Returns:
        list[EnrichmentRow] — 每家一行，空列表表示无结果
    """
    t_start = time.monotonic()
    rows: list[EnrichmentRow] = []

    own_scraper = False
    if scraper is None:
        scraper = _get_scraper(headless=headless)
        own_scraper = True
        _reset_browser_pages(scraper)

    # ---- 登录检查 ----
    if not scraper.check_login():
        print(f"  [HS批量] 腾道未登录或登录态失效")
        return rows

    # ---- HS 搜索 ----
    try:
        hs_ok = scraper.search_by_hs_code(hs_code, country_filter=country_filter)
    except RuntimeError as e:
        print(f"  [HS批量] HS tab 切换失败: {e}")
        return rows

    if not hs_ok:
        print(f"  [HS批量] HS 搜索执行失败")
        return rows

    # ---- 提取所有搜索结果 ----
    candidates = scraper.extract_search_results()
    if not candidates:
        print(f"  [HS批量] 未搜索到任何结果")
        return rows

    print(f"  [HS批量] 共找到 {len(candidates)} 家公司，计划处理前 {min(len(candidates), max_companies)} 家")

    # 截取前 max_companies 家
    candidates = candidates[:max_companies]

    # 保存搜索页 URL（用于每家处理后返回）
    search_url = scraper.page.url
    print(f"  [HS批量] 搜索页 URL: {search_url}")

    # ---- 逐家抓取详情 ----
    for idx, candidate in enumerate(candidates):
        company_start = time.monotonic()
        print(f"\n  {'='*50}")
        print(f"  [HS批量] 处理第 {idx+1}/{len(candidates)} 家: '{candidate.company_name[:60]}'")
        print(f"  {'='*50}")

        # 如果不是第一家，先回到搜索页
        if idx > 0:
            print(f"  [HS批量] 返回搜索页...")
            card_count = scraper.restore_search_page(search_url, country_filter=country_filter)
            if card_count == 0:
                print(f"  [HS批量] 返回搜索页失败，所有恢复策略均无卡片")
                row = EnrichmentRow(
                    customer_name=candidate.company_name,
                    product_keywords=hs_code,
                    source_capture_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source_search_keyword=hs_code,
                    source_candidate_rank=candidate.rank,
                    source_page_url=candidate.page_url,
                    run_batch_id=batch_id,
                    match_status="unconfirmed",
                    match_confidence=0,
                    manual_review_flag="yes",
                    manual_review_reason="返回搜索页失败",
                    recommended_action="待人工复核",
                )
                rows.append(row)
                continue

        row = EnrichmentRow(
            customer_name=candidate.company_name,
            product_keywords=hs_code,
            source_capture_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            source_search_keyword=hs_code,
            source_candidate_rank=candidate.rank,
            source_page_url=candidate.page_url,
            run_batch_id=batch_id,
        )

        # 写入 HS 结果卡片上的额外字段
        if candidate.hs_product_desc:
            row.hs_product = candidate.hs_product_desc
        if candidate.hs_trade_count > 0:
            row.total_import_volume = str(candidate.hs_trade_count)

        # HS 搜索固定 match_status
        row.match_status = "matched"
        row.match_confidence = 50

        # ---- 进入详情页 ----
        if not scraper.go_to_detail(candidate, expected_company_name=candidate.company_name, name_similarity=0.85):
            row.match_status = "unconfirmed"
            row.manual_review_flag = "yes"
            row.manual_review_reason = "未进入企业详情页"
            row.recommended_action = "待人工复核"
            rows.append(row)
            print(f"  [HS批量] 第 {idx+1} 家：详情页进入失败，跳过")
            # 清理可能打开的 enterprise 标签
            scraper.close_detail_tab(search_url)
            continue

        # ---- 提取公司详情 ----
        detail = scraper.extract_company_detail()
        row.matched_company_name = detail.standard_name or candidate.company_name
        row.website_result = detail.website
        row.company_status = detail.company_status
        row.contact_name = detail.contact_name
        row.phone = detail.phone
        row.email = detail.email
        row.address = detail.address
        row.location = detail.location
        row.whatsapp = detail.whatsapp
        row.linkedin = detail.linkedin

        # ---- 进口分析页 ----
        imp = scraper.go_to_import_analysis()
        if imp.analysis_entry_status == "entered_confirmed":
            imp = scraper.extract_import_analysis(imp, target_hs_codes=[hs_code])

        row.analysis_entry_status = imp.analysis_entry_status
        row.analysis_data_status = imp.analysis_data_status
        row.latest_import_date = imp.latest_import_date
        row.import_active_status = determine_import_active(imp.latest_import_date)
        row.import_activity_summary = build_import_summary(imp)
        row.target_hs_amount_json = imp.target_hs_amount_json
        row.top_suppliers_json = imp.top_suppliers_json
        row.top_3_import_countries_json = imp.top_3_import_countries_json

        if imp.analysis_entry_status == "entered_confirmed" and imp.analysis_data_status == "has_data":
            row.source_page_title = "进口分析页"
        elif imp.analysis_entry_status == "entered_confirmed":
            row.source_page_title = "进口分析页（无数据）"
        else:
            row.source_page_title = "企业详情页"

        # ---- 产品页 ----
        if scraper.go_to_product_info_tab():
            products = scraper.extract_top_products(max_items=3)
            if products:
                import json as _json
                row.top_products_json = _json.dumps(products, ensure_ascii=False)

        # ---- 关闭详情页标签，切回搜索页 ----
        scraper.close_detail_tab(search_url)

        # ---- 计算匹配状态（HS 搜索简化处理） ----
        row.match_status = "matched"
        row.match_confidence = 50
        row.recommended_action = determine_action(row.match_status, row.import_active_status)

        company_seconds = round(time.monotonic() - company_start, 2)
        import json as _json
        print(f"  [HS批量] 第 {idx+1} 家完成 ({company_seconds}s): "
              f"公司='{row.matched_company_name[:40]}', "
              f"进口状态='{row.import_active_status}', "
              f"供应商数={len(_json.loads(row.top_suppliers_json)) if row.top_suppliers_json else 0}")

        rows.append(row)

    total_seconds = round(time.monotonic() - t_start, 2)
    print(f"\n  [HS批量] 全部完成 — 共处理 {len(rows)} 家公司，总耗时 {total_seconds}s")

    return rows


def hs_quick_search(
    hs_code: str,
    country_filter: str = "",
    max_companies: int = 20,
    headless: bool = False,
    batch_id: str = "",
    scraper: "TendataScraper | None" = None,
) -> list[dict]:
    """HS 编码快速搜索：只提取搜索结果卡片摘要，不进入详情页。

    返回前 max_companies 家公司的卡片摘要，包含：
    card_index, company_name, hs_product_desc, hs_trade_count,
    hs_supplier_count, recent_trade_date, summary, page_url

    Args:
        hs_code: 6位 HS 编码
        country_filter: 可选，国家过滤（中文名如"加拿大"或英文名如"Canada"）
        max_companies: 最大返回公司数，默认 20
        headless: 是否无头模式
        batch_id: 批次 ID
        scraper: 可选，传入已有 scraper 实例

    Returns:
        list[dict] — 每家公司的卡片摘要，空列表表示无结果
    """
    t_start = time.monotonic()
    cards_out: list[dict] = []

    own_scraper = False
    if scraper is None:
        scraper = _get_scraper(headless=headless)
        own_scraper = True
        _reset_browser_pages(scraper)

    # 登录检查
    if not scraper.check_login():
        print(f"  [HS快速] 腾道未登录或登录态失效")
        return cards_out

    # HS 搜索
    try:
        hs_ok = scraper.search_by_hs_code(hs_code, country_filter=country_filter)
    except RuntimeError as e:
        print(f"  [HS快速] HS tab 切换失败: {e}")
        return cards_out

    if not hs_ok:
        print(f"  [HS快速] HS 搜索执行失败")
        return cards_out

    # 提取搜索结果卡片
    candidates = scraper.extract_search_results()
    if not candidates:
        print(f"  [HS快速] 未搜索到任何结果")
        return cards_out

    candidates = candidates[:max_companies]
    result_page_url = scraper.page.url  # 保存搜索结果页 URL，供 hs_enrich_selected 复用
    print(f"  [HS快速] 共找到 {len(candidates)} 家公司（前 {len(candidates)} 家），总耗时 {round(time.monotonic() - t_start, 2)}s")

    for idx, c in enumerate(candidates):
        card = {
            "card_index": idx + 1,
            "company_name": c.company_name,
            "hs_product_desc": c.hs_product_desc or "",
            "hs_trade_count": c.hs_trade_count,
            "hs_supplier_count": c.hs_supplier_count,
            "recent_trade_date": c.recent_trade_date or "",
            "summary": getattr(c, "company_brief", "") or "",
            "page_url": c.page_url,
            "rank": c.rank,
            # 上下文信息，供 hs_enrich_selected 重建搜索页
            "_result_page_url": result_page_url,
            "_hs_code": hs_code,
            "_country_filter": country_filter,
        }
        cards_out.append(card)
        print(f"  [HS快速] #{idx+1} {c.company_name[:60]} | 贸易:{c.hs_trade_count}次 | 供应商:{c.hs_supplier_count}家 | {c.recent_trade_date or '无日期'}")

    print(f"  [HS快速] quick_search 完成 — 返回 {len(cards_out)} 家卡片摘要")
    return cards_out


def hs_enrich_selected(
    quick_results: list[dict],
    selections: list[int] | list[str] | None = None,
    hs_code: str = "",
    country_filter: str = "",
    headless: bool = False,
    batch_id: str = "",
    scraper: "TendataScraper | None" = None,
) -> list[EnrichmentRow]:
    """对 quick_search 结果中选定的公司进行深度挖掘。

    原则：
    - 优先复用 quick_search 结束时的搜索结果页，不跳回 search#/index
    - 只在结果页上下文丢失时，才用传入参数重建搜索
    - 重建时直接用 hs_code / country_filter，不依赖当前 URL 解析

    selection 支持三种形式：
    - 序号列表：[1, 3, 5] → 展开第1/3/5家
    - 公司名称列表：["FIDELITY PAC", "ABC Corp"] → 按名称匹配
    - None / 空列表 → 全部展开

    Args:
        quick_results: hs_quick_search 返回的卡片摘要列表
        selections: 选择条件，序号或公司名列表；为空时全部展开
        hs_code: HS 编码（用于结果行标记和重建搜索）
        country_filter: 国家过滤
        headless: 是否无头模式
        batch_id: 批次 ID
        scraper: 可选，传入已有 scraper 实例

    Returns:
        list[EnrichmentRow] — 选中公司的深度挖掘结果
    """
    t_start = time.monotonic()
    rows: list[EnrichmentRow] = []

    if not quick_results:
        print(f"  [HS深挖] quick_results 为空，无公司可深挖")
        return rows

    # 解析选择
    if selections is None or len(selections) == 0:
        selected_indices = list(range(len(quick_results)))
        print(f"  [HS深挖] 未指定选择，默认全部展开 ({len(quick_results)} 家)")
    elif isinstance(selections[0], int):
        selected_indices = []
        for idx in selections:
            if 1 <= idx <= len(quick_results):
                selected_indices.append(idx - 1)
            else:
                print(f"  [HS深挖] 序号 {idx} 超出范围 (1-{len(quick_results)})，跳过")
        print(f"  [HS深挖] 按序号选择: {[i+1 for i in selected_indices]}")
    else:
        selected_indices = []
        for sel_name in selections:
            sel_lower = sel_name.lower().strip()
            found = False
            for i, card in enumerate(quick_results):
                if card["company_name"].lower().strip() == sel_lower:
                    if i not in selected_indices:
                        selected_indices.append(i)
                    found = True
                    break
            if not found:
                for i, card in enumerate(quick_results):
                    if sel_lower in card["company_name"].lower():
                        if i not in selected_indices:
                            selected_indices.append(i)
                        found = True
                        break
            if not found:
                print(f"  [HS深挖] 未匹配到公司: '{sel_name}'")
        print(f"  [HS深挖] 按名称选择: {[quick_results[i]['company_name'][:50] for i in selected_indices]}")

    if not selected_indices:
        print(f"  [HS深挖] 无有效选择，跳过深挖")
        return rows

    # 确保 scraper 可用（不重置页面，保留 quick_search 的结果页）
    if scraper is None:
        scraper = _get_scraper(headless=headless)

    if not scraper.check_login():
        print(f"  [HS深挖] 腾道未登录或登录态失效")
        return rows

    # ── 确定搜索结果页 URL ──
    # 优先从 quick_search 卡片中取（每张卡片都保存了 _result_page_url）
    result_page_url = quick_results[0].get("_result_page_url", "")
    # 从卡片中提取 hs_code / country_filter（用于重建搜索）
    card_hs_code = quick_results[0].get("_hs_code", "")
    card_country = quick_results[0].get("_country_filter", "")
    effective_hs_code = hs_code or card_hs_code
    effective_country = country_filter or card_country

    current_url = scraper.page.url
    is_on_result_page = (
        result_page_url and current_url == result_page_url
    ) or (
        "search" in current_url and "mode=hs" in current_url and "trade" in current_url
    )

    if is_on_result_page:
        search_url = current_url
        print(f"  [HS深挖] 复用当前搜索结果页: {search_url[:100]}")
        # 验证卡片数量
        card_count = scraper.page.evaluate(
            r"""() => document.querySelectorAll('div[class*="reocrdItem--tradeRecordItem"]').length"""
        )
        print(f"  [HS深挖] 当前页卡片数: {card_count}")
    else:
        # 不在结果页上，需要用参数重建
        if not effective_hs_code:
            print(f"  [HS深挖] 当前不在搜索结果页，且无法获取 HS 编码，无法重建")
            return rows
        print(f"  [HS深挖] 当前不在搜索结果页，重新执行 HS 搜索: hs_code='{effective_hs_code}', country='{effective_country}'")
        try:
            ok = scraper.search_by_hs_code(effective_hs_code, country_filter=effective_country)
        except RuntimeError as e:
            print(f"  [HS深挖] HS 搜索失败: {e}")
            return rows
        if not ok:
            print(f"  [HS深挖] HS 搜索执行失败")
            return rows
        search_url = scraper.page.url
        print(f"  [HS深挖] 搜索完成，结果页 URL: {search_url[:100]}")

    # 逐个深挖
    for pos, ci in enumerate(selected_indices):
        card = quick_results[ci]
        company_start = time.monotonic()
        print(f"\n  {'='*50}")
        print(f"  [HS深挖] 第 {pos+1}/{len(selected_indices)} 家 (卡片#{ci+1}): '{card['company_name'][:60]}'")
        print(f"  {'='*50}")

        # 打印当前浏览器标签状态
        try:
            ctx = scraper.page.context
            tab_urls = []
            for p in ctx.pages:
                try:
                    tab_urls.append(p.url[:80])
                except Exception:
                    tab_urls.append("(无法读取)")
            print(f"  [HS深挖] 当前浏览器标签: {len(ctx.pages)} 个 | 前3个: {tab_urls[:3]}")
        except Exception:
            pass

        # 如果不是第一家，回到搜索页
        if pos > 0:
            print(f"  [HS深挖] 返回搜索页...")
            card_count = scraper.restore_search_page(search_url, country_filter=effective_country)
            if card_count == 0:
                print(f"  [HS深挖] 返回搜索页失败，跳过剩余公司")
                break

        # ★ 在点击卡片进入详情页前，清理所有遗留的 enterprise 标签
        # 确保 _wait_for_detail_tab 只找到本次点击打开的新标签
        scraper._close_all_enterprise_tabs()

        row = EnrichmentRow(
            customer_name=card["company_name"],
            product_keywords=effective_hs_code,
            source_capture_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            source_search_keyword=effective_hs_code,
            source_candidate_rank=card.get("rank", 0),
            source_page_url=card.get("page_url", ""),
            run_batch_id=batch_id,
        )
        row.hs_product = card["hs_product_desc"]
        if card["hs_trade_count"] > 0:
            row.total_import_volume = str(card["hs_trade_count"])
        row.match_status = "matched"
        row.match_confidence = 50

        # 构建 SearchResult 用于 go_to_detail
        candidate = SearchResult(
            company_name=card["company_name"],
            rank=card.get("rank", ci + 1),
            recent_trade_date=card["recent_trade_date"],
            page_url=card["page_url"],
            hs_trade_count=card["hs_trade_count"],
            hs_supplier_count=card["hs_supplier_count"],
            hs_product_desc=card["hs_product_desc"],
            card_index=ci + 1,
        )

        # 进入详情页
        if not scraper.go_to_detail(candidate, expected_company_name=candidate.company_name, name_similarity=0.85):
            row.match_status = "unconfirmed"
            row.manual_review_flag = "yes"
            row.manual_review_reason = "未进入企业详情页"
            row.recommended_action = "待人工复核"
            rows.append(row)
            print(f"  [HS深挖] 第 {pos+1} 家：详情页进入失败，跳过")
            scraper.close_detail_tab(search_url)
            continue

        # 提取详情
        detail = scraper.extract_company_detail()
        row.matched_company_name = detail.standard_name or candidate.company_name
        row.website_result = detail.website
        row.company_status = detail.company_status
        row.contact_name = detail.contact_name
        row.phone = detail.phone
        row.email = detail.email
        row.address = detail.address
        row.location = detail.location
        row.whatsapp = detail.whatsapp
        row.linkedin = detail.linkedin

        # 进口分析
        imp = scraper.go_to_import_analysis()
        if imp.analysis_entry_status == "entered_confirmed":
            imp = scraper.extract_import_analysis(imp, target_hs_codes=[effective_hs_code] if effective_hs_code else [])

        row.analysis_entry_status = imp.analysis_entry_status
        row.analysis_data_status = imp.analysis_data_status
        row.latest_import_date = imp.latest_import_date
        row.import_active_status = determine_import_active(imp.latest_import_date)
        row.import_activity_summary = build_import_summary(imp)
        row.target_hs_amount_json = imp.target_hs_amount_json
        row.top_suppliers_json = imp.top_suppliers_json
        row.top_3_import_countries_json = imp.top_3_import_countries_json

        if imp.analysis_entry_status == "entered_confirmed" and imp.analysis_data_status == "has_data":
            row.source_page_title = "进口分析页"
        elif imp.analysis_entry_status == "entered_confirmed":
            row.source_page_title = "进口分析页（无数据）"
        else:
            row.source_page_title = "企业详情页"

        # 产品页
        if scraper.go_to_product_info_tab():
            products = scraper.extract_top_products(max_items=3)
            if products:
                import json as _json
                row.top_products_json = _json.dumps(products, ensure_ascii=False)

        # 关闭详情页，回到搜索页
        scraper.close_detail_tab(search_url)

        row.match_status = "matched"
        row.match_confidence = 50
        row.recommended_action = determine_action(row.match_status, row.import_active_status)

        company_seconds = round(time.monotonic() - company_start, 2)
        import json as _json
        print(f"  [HS深挖] 第 {pos+1} 家完成 ({company_seconds}s): "
              f"公司='{row.matched_company_name[:40]}', "
              f"进口状态='{row.import_active_status}'")

        rows.append(row)

    total_seconds = round(time.monotonic() - t_start, 2)
    print(f"\n  [HS深挖] 全部完成 — 共处理 {len(rows)}/{len(selected_indices)} 家，总耗时 {total_seconds}s")

    return rows


def create_result_row(
    customer_name: str,
    country_region: str,
    website_input: str,
    email_domain: str,
    product_keywords: str,
    internal_customer_id: str,
    status: str,
    confidence: int,
    reason: str,
    batch_id: str,
    has_country: bool = True,
    search_keyword: str = "",
    search_variants: str = "",
) -> EnrichmentRow:
    """创建一条错误/跳过结果行。"""
    row = EnrichmentRow(
        customer_name=customer_name,
        country_region=country_region,
        website_input=website_input,
        email_domain=email_domain,
        product_keywords=product_keywords,
        internal_customer_id=internal_customer_id,
        search_keyword=search_keyword or customer_name,
        search_variants=search_variants,
        match_status=status,
        match_confidence=confidence,
        source_capture_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        source_search_keyword=search_keyword or customer_name,
        run_batch_id=batch_id,
        error_message=reason,
    )
    if status == "no_result":
        row.recommended_action = "转官网/LinkedIn核验"
    else:
        row.manual_review_flag = "yes"
        row.manual_review_reason = reason
        row.recommended_action = "待人工复核"
    return row


# ============================================================================
# CLI 测试入口
# ============================================================================

if __name__ == "__main__":
    import sys as _sys
    import argparse as _argparse

    parser = _argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="运行匹配逻辑测试")
    parser.add_argument("--single", type=str, help="测试单条公司搜索（需要浏览器运行）")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    if args.test:
        tests = [
            ("ABC Corp", "ABC Corporation", "United States", "United States", "abc.com", "https://www.abc.com", True),
            ("XYZ Ltd", "XYZ Industries", "Germany", "France", "", "", True),
            ("No Match Company", "", "", "", "", "", False),
            ("华为技术", "华为技术有限公司", "China", "China", "huawei.com", "https://huawei.com", True),
        ]
        for i, (iname, mname, icountry, pcountry, iweb, pweb, hc) in enumerate(tests, 1):
            status, conf = compute_match_status(iname, mname, icountry, pcountry, iweb, pweb, hc)
            print(f"  Test {i}: name_sim={_name_similarity(iname, mname):.2f} → {status} ({conf})")
        print("匹配逻辑测试完成")

    elif args.single:
        print(f"测试单条搜索: {args.single}")
        try:
            result = enrich_one_customer(
                customer_name=args.single,
                country_region="",
                website="",
                headless=args.headless,
                batch_id="TEST-001",
            )
            print(f"  匹配公司: {result.matched_company_name}")
            print(f"  状态: {result.match_status} ({result.match_confidence})")
            print(f"  官网: {result.website_result}")
            print(f"  进口: {result.latest_import_date}")
        finally:
            _close_scraper()
