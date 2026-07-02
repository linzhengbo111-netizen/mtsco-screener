"""
app.py — 迈拓 B2B客户智能筛选系统 (Streamlit 主界面)

三源交叉验证：Google + LinkedIn + 海关数据 → 七维度打分 → A/B/C/D分级排序
"""

import streamlit as st
import pandas as pd
import sys
import os
import time
from pathlib import Path
from datetime import datetime

# 确保项目脚本可导入
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

# =============================================================================
# 页面配置
# =============================================================================
st.set_page_config(
    page_title="迈拓 B2B客户智能筛选",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# 初始化 Session State
# =============================================================================
DEFAULTS = {
    "results": [],           # list[LeadQualificationResult]
    "is_processing": False,
    "processing_progress": 0,
    "processing_total": 0,
    "processing_log": [],
    "uploaded_file_name": None,
    "uploaded_df": None,
    "chrome_status": "unknown",
}

for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =============================================================================
# 工具函数
# =============================================================================

def check_chrome_cdp():
    """检查 Chrome CDP 是否可用"""
    import urllib.request
    import json as _json
    try:
        req = urllib.request.urlopen("http://localhost:9222/json/version", timeout=3)
        data = _json.loads(req.read())
        return True, data.get("Browser", "Chrome")
    except Exception:
        return False, "未连接"


def load_excel_preview(uploaded_file) -> pd.DataFrame:
    """读取上传的 Excel 文件"""
    df = pd.read_excel(uploaded_file)
    # 标准化列名（中文 → 英文映射）
    COLUMN_MAP = {
        "公司名称": "customer_name", "公司名": "customer_name", "客户名称": "customer_name",
        "国家": "country_region", "国家地区": "country_region", "所在国家": "country_region",
        "网站": "website", "公司网站": "website", "网址": "website",
        "关键词": "product_keywords", "产品关键词": "product_keywords",
        "邮箱": "email_domain", "邮箱域名": "email_domain",
        "客户ID": "internal_customer_id", "内部ID": "internal_customer_id",
    }
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})
    return df


def validate_input_columns(df: pd.DataFrame) -> list:
    """检查必填列，返回缺失列表"""
    required = ["customer_name"]
    missing = [c for c in required if c not in df.columns]
    return missing


def color_tier(val):
    """根据 tier 着色"""
    colors = {"A": "background-color: #d4edda; color: #155724",
              "B": "background-color: #d1ecf1; color: #0c5460",
              "C": "background-color: #fff3cd; color: #856404",
              "D": "background-color: #f8d7da; color: #721c24"}
    return colors.get(val, "")


def log(msg):
    """添加处理日志"""
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.processing_log.append(f"[{ts}] {msg}")


# =============================================================================
# 侧边栏
# =============================================================================

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/search.png", width=64)
    st.title("迈拓客户筛选")
    st.caption("B2B Lead Qualification System v1.0")

    st.divider()

    # ── 文件上传 ──
    st.subheader("📤 上传客户名单")
    uploaded_file = st.file_uploader(
        "选择 Excel 文件",
        type=["xlsx", "xls"],
        help="必含列: 公司名称。可选列: 国家, 网站, 产品关键词",
    )

    if uploaded_file:
        if uploaded_file.name != st.session_state.uploaded_file_name:
            st.session_state.uploaded_file_name = uploaded_file.name
            st.session_state.results = []
        st.success(f"已加载: {uploaded_file.name}")

    st.divider()

    # ── 数据源开关 ──
    st.subheader("⚙️ 数据源")
    use_google = st.checkbox("Google 验证", value=True, help="DuckDuckGo 搜索 + 网站验证")
    use_linkedin = st.checkbox("LinkedIn 信息", value=True, help="LinkedIn 公司页面抓取")
    use_tendata = st.checkbox("海关数据", value=True, help="Tendata 进口记录匹配")

    st.divider()

    # ── 筛选条件 ──
    st.subheader("🎯 筛选条件")
    min_score = st.slider("最低分数", 0, 100, 40, 5)
    show_tiers = st.multiselect(
        "显示级别",
        ["A (优先跟进)", "B (列入跟进)", "C (人工复核)", "D (暂不跟进)"],
        default=["A (优先跟进)", "B (列入跟进)", "C (人工复核)"],
    )

    st.divider()

    # ── 导出 ──
    if st.session_state.results:
        st.subheader("📥 导出结果")
        col_export1, col_export2 = st.columns(2)
        with col_export1:
            from scripts.lead_report import export_to_excel
            import io
            excel_path = export_to_excel(st.session_state.results)
            with open(excel_path, "rb") as f:
                st.download_button(
                    label="📥 下载 Excel",
                    data=f,
                    file_name=excel_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
        with col_export2:
            from scripts.lead_report import export_to_json
            json_path = export_to_json(st.session_state.results)
            with open(json_path, "rb") as f:
                st.download_button(
                    label="📥 下载 JSON",
                    data=f,
                    file_name=json_path.name,
                    mime="application/json",
                    use_container_width=True,
                )

    st.divider()

    # ── 系统状态 ──
    st.subheader("🖥 系统状态")
    chrome_ok, chrome_ver = check_chrome_cdp()
    if chrome_ok:
        st.success(f"Chrome CDP ✓ ({chrome_ver})")
    else:
        st.error("Chrome CDP ✗ (需先启动)")

    # 数据源状态
    col_st1, col_st2, col_st3 = st.columns(3)
    with col_st1:
        st.caption(f"Google: {'✅' if use_google else '⏸'}")
    with col_st2:
        st.caption(f"LinkedIn: {'✅' if use_linkedin else '⏸'}")
    with col_st3:
        st.caption(f"海关: {'✅' if use_tendata else '⏸'}")

    if st.session_state.uploaded_df is not None:
        st.caption(f"已加载: {len(st.session_state.uploaded_df)} 家公司")
    if st.session_state.results:
        st.caption(f"已处理: {len(st.session_state.results)} 家")


# =============================================================================
# 主区域
# =============================================================================

st.title("🔍 迈拓 B2B客户智能筛选系统")
st.caption("上传客户名单 → 三源交叉验证（Google + LinkedIn + 海关数据）→ 智能打分排序 → 筛选高价值潜在客户")

st.divider()

# ── 状态 1: 未上传文件 ──
if uploaded_file is None:
    col_intro1, col_intro2, col_intro3 = st.columns(3)

    with col_intro1:
        st.info("""
        ### 📋 Step 1: 准备 Excel
        创建包含以下列的 Excel 文件：
        - **公司名称**（必填）
        - 国家/地区
        - 公司网站
        - 产品关键词

        [下载模板](#)
        """)

    with col_intro2:
        st.success("""
        ### 🌐 Step 2: 三源验证
        系统自动从三个数据源交叉验证：
        - **Google** — 公司官网、行业分类
        - **LinkedIn** — 公司规模、核心联系人
        - **海关数据** — 进口活跃度、产品匹配
        """)

    with col_intro3:
        st.warning("""
        ### 🎯 Step 3: 智能筛选
        七维度打分 → A/B/C/D 分级：
        - **A级 (75+)** → 优先跟进
        - **B级 (60-74)** → 列入跟进
        - **C级 (40-59)** → 人工复核
        - **D级 (<40)** → 暂不跟进
        """)

    st.divider()

    # 演示数据预览
    with st.expander("📊 查看示例数据格式", expanded=False):
        demo_data = {
            "公司名称": ["LAM RESEARCH MANUFACTURING KOREA", "TEXON CO LTD", "YOUNGJIN FLEX CO LTD"],
            "国家": ["韩国", "韩国", "韩国"],
            "网站": ["www.lamresearch.com", "texon.co.kr", "youngjinflex.com.vn"],
            "产品关键词": ["semiconductor, steel pipe", "semiconductor wire, harness", "stainless steel, flexible hose"],
        }
        st.dataframe(pd.DataFrame(demo_data), use_container_width=True)

# ── 状态 2: 已上传文件 ──
else:
    # 读取文件
    if st.session_state.uploaded_df is None:
        df = load_excel_preview(uploaded_file)
        st.session_state.uploaded_df = df
    else:
        df = st.session_state.uploaded_df

    missing = validate_input_columns(df)
    if missing:
        st.error(f"❌ Excel 缺少必填列: {', '.join(missing)}。请确保包含「公司名称」列。")
    else:
        # ── 数据预览 ──
        col_preview1, col_preview2 = st.columns([3, 1])
        with col_preview1:
            st.subheader(f"📋 客户名单 ({len(df)} 家)")
        with col_preview2:
            st.caption(f"文件: {uploaded_file.name}")

        st.dataframe(df.head(10), use_container_width=True)
        if len(df) > 10:
            st.caption(f"仅显示前 10 行，共 {len(df)} 行")

        # ── 开始处理按钮 ──
        st.divider()
        col_btn1, col_btn2, _ = st.columns([2, 2, 6])
        with col_btn1:
            start_btn = st.button(
                "🚀 开始智能筛选",
                type="primary",
                disabled=st.session_state.is_processing,
                use_container_width=True,
            )
        with col_btn2:
            reset_btn = st.button(
                "🔄 重置结果",
                disabled=st.session_state.is_processing,
                use_container_width=True,
            )

        if reset_btn:
            st.session_state.results = []
            st.session_state.processing_log = []
            st.rerun()

        # ── 进度显示 ──
        if st.session_state.is_processing:
            progress_bar = st.progress(st.session_state.processing_progress)
            status_text = st.empty()
            status_text.text(
                f"处理中... {st.session_state.processing_progress}/{st.session_state.processing_total}"
            )

        # ── 处理日志 ──
        if st.session_state.processing_log:
            with st.expander("📝 处理日志", expanded=st.session_state.is_processing):
                log_container = st.empty()
                log_container.code(
                    "\n".join(st.session_state.processing_log[-20:]),
                    language="log",
                )

        # ── 结果表格 ──
        if st.session_state.results:
            st.divider()
            st.subheader(f"📊 筛选结果")

            # 过滤
            tier_map = {
                "A (优先跟进)": "A", "B (列入跟进)": "B",
                "C (人工复核)": "C", "D (暂不跟进)": "D",
            }
            allowed_tiers = [tier_map[t] for t in show_tiers]

            results_to_show = [
                r for r in st.session_state.results
                if r.final_score >= min_score and r.tier in allowed_tiers
            ]

            if not results_to_show:
                st.info("当前筛选条件下无匹配结果，请调整筛选条件。")
            else:
                # 构建 DataFrame
                rows = [r.to_summary_dict() for r in results_to_show]
                result_df = pd.DataFrame(rows)

                # 选择显示列
                display_cols = [
                    "customer_name", "country_region", "final_score", "tier",
                    "recommended_action", "product_fit_score", "purchase_intent_score",
                    "legitimacy_score", "google_found", "linkedin_found", "tendata_found",
                    "linkedin_industry", "tendata_products", "elapsed_seconds",
                ]
                display_cols = [c for c in display_cols if c in result_df.columns]

                # 使用 column_config 美化
                column_config = {
                    "customer_name": st.column_config.TextColumn("公司名称", width="large"),
                    "country_region": st.column_config.TextColumn("国家", width="small"),
                    "final_score": st.column_config.NumberColumn("总分", help="0-100"),
                    "tier": st.column_config.TextColumn("级别", width="small"),
                    "recommended_action": st.column_config.TextColumn("建议", width="small"),
                    "product_fit_score": st.column_config.NumberColumn("产品匹配"),
                    "purchase_intent_score": st.column_config.NumberColumn("采购意愿"),
                    "legitimacy_score": st.column_config.NumberColumn("可信度"),
                    "google_found": st.column_config.CheckboxColumn("Google"),
                    "linkedin_found": st.column_config.CheckboxColumn("LinkedIn"),
                    "tendata_found": st.column_config.CheckboxColumn("海关"),
                    "linkedin_industry": st.column_config.TextColumn("行业"),
                    "tendata_products": st.column_config.TextColumn("主要进口产品"),
                    "elapsed_seconds": st.column_config.NumberColumn("耗时(s)", format="%.1f"),
                }
                # 只用存在的列
                column_config = {k: v for k, v in column_config.items() if k in display_cols}

                st.dataframe(
                    result_df[display_cols],
                    column_config=column_config,
                    use_container_width=True,
                    hide_index=True,
                )

                # ── 统计 ──
                col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
                with col_stat1:
                    a_count = sum(1 for r in results_to_show if r.tier == "A")
                    st.metric("A级 (优先跟进)", a_count)
                with col_stat2:
                    b_count = sum(1 for r in results_to_show if r.tier == "B")
                    st.metric("B级 (列入跟进)", b_count)
                with col_stat3:
                    c_count = sum(1 for r in results_to_show if r.tier == "C")
                    st.metric("C级 (人工复核)", c_count)
                with col_stat4:
                    d_count = sum(1 for r in results_to_show if r.tier == "D")
                    st.metric("D级 (暂不跟进)", d_count)

                # ── 公司详情面板 ──
                st.divider()
                st.subheader("🔎 公司详情")
                for r in results_to_show:
                    with st.expander(
                        f"{r.tier}级 | {r.customer_name} | 总分: {r.final_score} | {r.tier_label}"
                    ):
                        col_det1, col_det2, col_det3 = st.columns(3)

                        with col_det1:
                            st.markdown("**Google 验证**")
                            st.write(f"网站: {r.google.official_website or '未找到'}")
                            st.write(f"行业类型: {r.google.business_type or '未知'}")
                            st.write(f"产品关键词: {', '.join(r.google.product_keywords_found[:5]) or '无'}")
                            st.progress(r.google.confidence / 100, text=f"置信度: {r.google.confidence}")

                        with col_det2:
                            st.markdown("**LinkedIn 信息**")
                            st.write(f"公司主页: {'✅' if r.linkedin.company_page_found else '❌'}")
                            st.write(f"员工规模: {r.linkedin.employee_count_range or '未知'}")
                            st.write(f"行业标签: {', '.join(r.linkedin.industry_tags) or '无'}")
                            if r.linkedin.key_contacts:
                                for c in r.linkedin.key_contacts[:3]:
                                    st.caption(f"👤 {c.get('name','')} — {c.get('title','')}")

                        with col_det3:
                            st.markdown("**海关数据**")
                            st.write(f"匹配状态: {r.tendata.match_status}")
                            st.write(f"进口活跃: {'✅' if r.tendata.import_active else '❌'}")
                            st.write(f"12月进口次数: {r.tendata.total_shipments_12m}")
                            st.write(f"中国供应商: {'✅' if r.tendata.has_chinese_supplier else '❌'}")
                            if r.tendata.top_products:
                                for p in r.tendata.top_products[:3]:
                                    st.caption(f"📦 {p.get('product_name','')[:60]}")

                        # 打分拆解
                        st.markdown("---")
                        st.markdown("**📊 打分拆解**")
                        cols = st.columns(7)
                        dims = [
                            ("源一致性", r.source_agreement_score, 15),
                            ("产品匹配", r.product_fit_score, 25),
                            ("采购意愿", r.purchase_intent_score, 25),
                            ("可信度", r.legitimacy_score, 15),
                            ("联系方式", r.contact_score, 10),
                            ("行业规模", r.industry_scale_score, 5),
                            ("风险扣分", r.risk_penalty, -20),
                        ]
                        for i, (label, score, max_val) in enumerate(dims):
                            with cols[i]:
                                if max_val > 0:
                                    ratio = min(score / max_val, 1.0)
                                else:
                                    ratio = min(abs(score) / abs(max_val), 1.0) if max_val != 0 else 0
                                color = "inverse" if max_val < 0 else "normal"
                                st.metric(label, f"{score}/{max_val}")

                        if r.risk_notes:
                            st.warning(f"⚠️ 风险提醒: {r.risk_notes}")
                        if r.error:
                            st.error(f"错误: {r.error}")


# =============================================================================
# 处理入口 — 真实流水线
# =============================================================================

# 初始化触发器状态
if "start_triggered" not in st.session_state:
    st.session_state.start_triggered = False


def start_processing():
    """按钮回调：设置处理标志"""
    st.session_state.start_triggered = True
    st.session_state.is_processing = True
    st.session_state.processing_progress = 0
    st.session_state.processing_log = []


# 在侧边栏内定义开始按钮
# 按钮在文件上传后渲染（直接检查 uploaded_file，不用等 uploaded_df）
st.sidebar.divider()
st.sidebar.subheader("🚀 执行")

if uploaded_file is not None:
    st.sidebar.button(
        "▶️ 开始智能筛选",
        type="primary",
        disabled=st.session_state.is_processing,
        on_click=start_processing,
        use_container_width=True,
    )
else:
    st.sidebar.button(
        "▶️ 开始智能筛选",
        disabled=True,
        use_container_width=True,
        help="请先上传 Excel 文件",
    )


# 如果正在处理中，运行真实流水线
if st.session_state.is_processing and st.session_state.start_triggered:
    df = st.session_state.uploaded_df

    if df is None:
        st.error("请先上传文件")
        st.session_state.is_processing = False
        st.session_state.start_triggered = False
    else:
        from scripts.models import CustomerInput

        # 构建客户列表
        customers = []
        for _, row in df.iterrows():
            customers.append(CustomerInput(
                customer_name=str(row.get("customer_name", "")),
                country_region=str(row.get("country_region", "")),
                website=str(row.get("website", "")),
                product_keywords=str(row.get("product_keywords", "")),
                email_domain=str(row.get("email_domain", "")),
                internal_customer_id=str(row.get("internal_customer_id", "")),
            ))

        total = len(customers)
        st.session_state.processing_total = total

        # 进度显示
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_area = st.empty()

        # 进度回调
        def update_progress(msg):
            st.session_state.processing_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        results = []

        for i, customer in enumerate(customers):
            status_text.text(f"⏳ 处理中... {i+1}/{total}: {customer.customer_name}")

            try:
                from scripts.lead_pipeline import process_one

                result = process_one(
                    company_name=customer.customer_name,
                    country=customer.country_region,
                    website=customer.website,
                    product_keywords=customer.product_keywords,
                    use_google=use_google,
                    use_linkedin=use_linkedin,
                    use_tendata=use_tendata,
                    progress_callback=update_progress,
                )
                result.rank = i + 1
                results.append(result)

            except Exception as e:
                update_progress(f"❌ 处理失败: {customer.customer_name} — {str(e)[:100]}")
                from scripts.models import LeadQualificationResult
                results.append(LeadQualificationResult(
                    customer_name=customer.customer_name,
                    country_region=customer.country_region,
                    error=str(e)[:200],
                ))

            st.session_state.processing_progress = i + 1
            progress_bar.progress((i + 1) / total)

            # 实时更新日志
            log_area.code(
                "\n".join(st.session_state.processing_log[-15:]),
                language="log",
            )

        # 排序
        results.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        st.session_state.results = results
        st.session_state.is_processing = False
        st.session_state.start_triggered = False
        st.session_state.processing_progress = total

        update_progress(f"✅ 处理完成! 共 {total} 家, A级 {sum(1 for r in results if r.tier=='A')} 家")
        st.rerun()
