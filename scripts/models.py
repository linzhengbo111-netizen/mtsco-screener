"""
models.py — tendata-customer-enricher 数据模型

定义任务、客户、结果的核心数据结构，供任务层与抓取内核共享。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


# ── 枚举 ─────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class MatchStatus(str, Enum):
    CONFIRMED = "confirmed"
    LIKELY_MATCH = "likely_match"
    UNCONFIRMED = "unconfirmed"
    NO_RESULT = "no_result"
    CONFLICT = "conflict"
    DETAIL_PAGE_FAILED = "detail_page_failed"
    CANDIDATE_FOUND_NOT_ENTERED = "candidate_found_not_entered"
    EXCLUDED_INTERNAL_RECORD = "excluded_internal_record"


class CompanyStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class RecommendedAction(str, Enum):
    FOLLOW_UP = "建议继续跟进"
    MANUAL_REVIEW = "待人工复核"
    SKIP = "暂不跟进"


# ── 客户输入 ──────────────────────────────────────────────────────────

@dataclass
class CustomerInput:
    customer_name: str
    country_region: str = ""
    website: str = ""
    email_domain: str = ""
    product_keywords: str = ""
    internal_customer_id: str = ""


# ── 单条抓取结果 ──────────────────────────────────────────────────────

@dataclass
class EnrichmentResult:
    """单条客户抓取结果，与 extract_tendata_fields.py 中的 EnrichmentRow 对齐。"""
    customer_name: str = ""
    country_region: str = ""
    website_input: str = ""
    email_domain: str = ""
    product_keywords: str = ""
    internal_customer_id: str = ""

    matched_company_name: str = ""
    match_status: str = "no_result"
    match_confidence: int = 0
    website_result: str = ""
    company_status: str = "unknown"
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    location: str = ""
    whatsapp: str = ""
    linkedin: str = ""

    latest_import_date: str = ""
    import_active_status: str = "unknown"
    analysis_entry_status: str = "unknown"
    analysis_data_status: str = "unknown"

    top_products_json: str = ""
    target_hs_amount_json: str = ""
    top_suppliers_json: str = ""
    top_3_import_countries_json: str = ""

    # HS 搜索额外字段
    hs_product: str = ""
    total_import_volume: str = ""

    # 摘要字段
    import_activity_summary: str = ""
    business_summary: str = ""
    evidence_excerpt: str = ""
    source_page_title: str = ""
    source_candidate_rank: int = 0
    source_page_url: str = ""

    recommended_action: str = "暂不跟进"
    manual_review_flag: str = "no"
    manual_review_reason: str = ""

    source_capture_time: str = ""
    source_search_keyword: str = ""
    run_batch_id: str = ""

    # 错误字段
    error_message: str = ""

    # 候选摘要（用于 no_result/unconfirmed/detail_page_failed 时保留候选信息）
    candidate_summary_json: str = ""  # JSON 字符串，包含 top 3 候选摘要

    @property
    def top_products(self) -> list[dict]:
        if self.top_products_json:
            return json.loads(self.top_products_json)
        return []

    @property
    def target_hs_amounts(self) -> list[dict]:
        if self.target_hs_amount_json:
            return json.loads(self.target_hs_amount_json)
        return []

    @property
    def top_suppliers(self) -> list[dict]:
        if self.top_suppliers_json:
            return json.loads(self.top_suppliers_json)
        return []

    def to_dict(self) -> dict:
        return asdict(self)


# ── 任务 ──────────────────────────────────────────────────────────────

@dataclass
class Task:
    """一个完整的抓取任务，包含一个或多个客户。"""
    task_id: str
    source: str = "manual"  # openclaw / shadowbot / manual
    customers: list[CustomerInput] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING

    # 选项
    generate_report: bool = True
    report_format: str = "markdown"
    batch_size: int = 10

    # 模式
    enrich_mode: str = "company_name"  # company_name / hs_auto_enrich / hs_manual_select

    # 回传预留字段（OpenClaw / 飞书推送）
    submitted_by: str = ""       # 提交者标识（如 OpenClaw session ID）
    callback_mode: str = ""      # 回传方式: feishu / webhook / poll
    callback_target: str = ""    # 回传目标（飞书 chat_id 或 webhook URL）
    delivery_status: str = ""    # 回传状态: pending / sent / failed
    delivered_at: str = ""       # 回传时间

    # 运行时
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    results: list[EnrichmentResult] = field(default_factory=list)

    # 产物路径
    excel_path: str = ""
    json_path: str = ""
    report_path: str = ""

    # HS 搜索缓存（quick_search 阶段保存候选列表）
    hs_candidates_json: str = ""  # JSON 字符串，hs_quick_search 返回的候选摘要

    # 错误
    error_code: str = ""
    error_message: str = ""

    # 批次归属（串行多公司查询）
    parent_batch_id: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "source": self.source,
            "enrich_mode": self.enrich_mode,
            "customers": [asdict(c) for c in self.customers],
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "options": {
                "generate_report": self.generate_report,
                "report_format": self.report_format,
                "batch_size": self.batch_size,
            },
            "callback": {
                "submitted_by": self.submitted_by,
                "callback_mode": self.callback_mode,
                "callback_target": self.callback_target,
                "delivery_status": self.delivery_status,
                "delivered_at": self.delivered_at,
            },
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [r.to_dict() for r in self.results],
            "artifacts": {
                "excel_path": self.excel_path,
                "json_path": self.json_path,
                "report_path": self.report_path,
            },
            "error": {
                "code": self.error_code,
                "message": self.error_message,
            } if self.error_code else None,
            "parent_batch_id": self.parent_batch_id or None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ── 批次任务 ──────────────────────────────────────────────────────────

@dataclass
class BatchTask:
    """多公司批量查询批次。一个批次拆分为多个单公司子任务串行执行。"""
    batch_id: str
    source: str = "manual"  # openclaw / shadowbot / manual
    customer_inputs: list[CustomerInput] = field(default_factory=list)
    sub_task_ids: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    callback_mode: str = ""
    callback_target: str = ""
    submitted_by: str = ""
    created_at: str = ""
    finished_at: str = ""
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def total(self) -> int:
        return len(self.sub_task_ids)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "source": self.source,
            "total": self.total,
            "sub_task_ids": self.sub_task_ids,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "callback": {
                "mode": self.callback_mode,
                "target": self.callback_target,
                "submitted_by": self.submitted_by,
            },
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": {
                "code": self.error_code,
                "message": self.error_message,
            } if self.error_code else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ── 多源筛选模型（新增 Lead Qualification）─────────────────────────────


@dataclass
class GoogleSourceSignals:
    """Google/DuckDuckGo 搜索验证信号"""
    company_found: bool = False
    official_website: str = ""
    website_title: str = ""
    website_match_confidence: float = 0.0  # 0-1
    industry_keywords_found: list = field(default_factory=list)
    business_type: str = ""  # manufacturer / distributor / trader / unknown
    product_keywords_found: list = field(default_factory=list)
    contact_email: str = ""
    contact_phone: str = ""
    evidence_urls: list = field(default_factory=list)
    search_snippet: str = ""
    company_status: str = "unknown"  # active / inactive / unknown
    confidence: int = 0  # 0-100
    error: str = ""


@dataclass
class LinkedInSourceSignals:
    """LinkedIn 公司页面提取信号"""
    company_page_found: bool = False
    company_url: str = ""
    company_name_on_li: str = ""
    name_match_status: str = "no_match"  # confirmed / likely_match / no_match
    industry_tags: list = field(default_factory=list)
    employee_count_range: str = ""  # e.g. "11-50", "51-200"
    employee_count_estimate: int = 0
    key_contacts: list = field(default_factory=list)  # [{name, title, url}]
    country_match: bool = False
    specialties: list = field(default_factory=list)
    company_description: str = ""
    founded_year: str = ""
    confidence: int = 0  # 0-100
    error: str = ""


@dataclass
class TendataSourceSignals:
    """海关数据信号（封装现有 EnrichmentResult）"""
    found: bool = False
    match_status: str = "no_result"
    match_confidence: int = 0
    matched_company_name: str = ""
    import_active: bool = False
    latest_import_date: str = ""
    total_shipments_12m: int = 0
    related_hs_codes: list = field(default_factory=list)
    top_products: list = field(default_factory=list)
    top_suppliers: list = field(default_factory=list)
    has_chinese_supplier: bool = False
    product_relevance_level: str = "unknown"  # high / medium / low / unknown
    error: str = ""


@dataclass
class LeadQualificationResult:
    """单家公司多源筛选最终结果"""
    customer_name: str = ""
    country_region: str = ""
    internal_customer_id: str = ""

    # 三源信号
    google: GoogleSourceSignals = field(default_factory=GoogleSourceSignals)
    linkedin: LinkedInSourceSignals = field(default_factory=LinkedInSourceSignals)
    tendata: TendataSourceSignals = field(default_factory=TendataSourceSignals)

    # 打分拆解
    final_score: int = 0
    source_agreement_score: int = 0
    product_fit_score: int = 0
    purchase_intent_score: int = 0
    legitimacy_score: int = 0
    contact_score: int = 0
    industry_scale_score: int = 0
    risk_penalty: int = 0

    # 分级
    tier: str = "D"  # A / B / C / D
    tier_label: str = "暂不跟进"
    rank: int = 0

    # 分析文本
    product_fit_analysis: str = ""
    purchase_signals_analysis: str = ""
    recommended_action: str = "暂不跟进"
    risk_notes: str = ""

    # 处理元数据
    elapsed_seconds: float = 0.0
    google_elapsed: float = 0.0
    linkedin_elapsed: float = 0.0
    tendata_elapsed: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_summary_dict(self) -> dict:
        """精简版字典，适合 Streamlit 表格显示"""
        return {
            "customer_name": self.customer_name,
            "country_region": self.country_region,
            "final_score": self.final_score,
            "tier": self.tier,
            "recommended_action": self.tier_label,
            "product_fit_score": self.product_fit_score,
            "purchase_intent_score": self.purchase_intent_score,
            "legitimacy_score": self.legitimacy_score,
            "contact_score": self.contact_score,
            "source_agreement_score": self.source_agreement_score,
            "industry_scale_score": self.industry_scale_score,
            "risk_penalty": self.risk_penalty,
            # Google 摘要
            "google_found": self.google.company_found,
            "google_website": self.google.official_website,
            "google_business_type": self.google.business_type,
            # LinkedIn 摘要
            "linkedin_found": self.linkedin.company_page_found,
            "linkedin_employees": self.linkedin.employee_count_range,
            "linkedin_industry": ", ".join(self.linkedin.industry_tags[:3]),
            # Tendata 摘要
            "tendata_found": self.tendata.found,
            "tendata_match_status": self.tendata.match_status,
            "tendata_import_active": self.tendata.import_active,
            "tendata_shipments": self.tendata.total_shipments_12m,
            "tendata_products": ", ".join(p.get("product_name", "")[:40] for p in self.tendata.top_products[:3]),
            # 分析
            "product_fit_analysis": self.product_fit_analysis,
            "purchase_signals_analysis": self.purchase_signals_analysis,
            "risk_notes": self.risk_notes,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "error": self.error,
        }


# ── 路径常量 ──────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
QUEUE_DB_PATH = DATA_DIR / "tasks.db"
OUTPUT_DIR = ROOT_DIR / "output"
INPUT_DIR = DATA_DIR / "input"
