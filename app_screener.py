#!/usr/bin/env python3
"""
MTSCO 客户背调系统 — Streamlit Web 界面
Batch Customer Screener for MTSCO

部署:
    streamlit run app_screener.py

或部署到 Streamlit Cloud:
    GitHub仓库 + requirements.txt + 本文件
"""

import sys
import io
import time
from pathlib import Path
from datetime import datetime

# 确保能导入 scripts 目录
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

import streamlit as st
import pandas as pd

from batch_screener import (
    BatchScreener,
    CompanyResult,
    results_to_excel,
    create_sample_excel,
    BATCH_SIZE,
)

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MTSCO 客户背调系统",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
APP_PASSWORD = "555888"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Session State 初始化
# ---------------------------------------------------------------------------
DEFAULTS = {
    "authenticated": False,
    "api_key": "",
    "model": "deepseek-chat",
    "theme": "dark",              # dark | light
    "results": [],                 # list[CompanyResult]
    "all_companies": [],           # 标准化后的输入公司列表
    "analysis_running": False,
    "analysis_stopped": False,
    "failed_indices": [],          # 分析失败的索引
    "current_batch_info": {},      # 当前批次进度
    "uploaded_df": None,           # 原始上传DataFrame
    "validated_df": None,          # 校验通过的标准DataFrame
    "history_duplicates": [],      # 去重掉的公司名
    "cost_estimate": {},           # 成本估算
    "sample_file_created": False,
}

for key, default in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# 主题 CSS
# ---------------------------------------------------------------------------
def get_theme_css(dark: bool) -> str:
    if dark:
        return """
        <style>
        .stApp { background-color: #0F172A; color: #E2E8F0; }
        .stButton > button {
            background-color: #1E40AF; color: white; border-radius: 8px;
            border: none; padding: 8px 20px; font-weight: 600;
        }
        .stButton > button:hover { background-color: #2563EB; }
        .stButton > button:disabled { background-color: #475569; color: #94A3B8; }
        .stProgress > div > div { background-color: #2563EB; }
        .stTextInput > div > div > input, .stSelectbox > div > div {
            background-color: #1E293B; color: #E2E8F0; border-color: #334155;
        }
        .result-card {
            background-color: #1E293B; border: 1px solid #334155;
            border-radius: 8px; padding: 12px 16px; margin: 4px 0;
        }
        .result-card:hover { border-color: #475569; }
        .match-high { color: #34D399; }
        .match-medium { color: #FBBF24; }
        .match-low { color: #F87171; }
        .info-box {
            background-color: #1E293B; border: 1px solid #334155;
            border-radius: 8px; padding: 16px; margin: 12px 0;
        }
        hr { border-color: #334155; }
        .detail-box {
            background-color: #0F172A; border: 1px solid #475569;
            border-radius: 8px; padding: 16px; max-height: 500px; overflow-y: auto;
            font-family: 'Courier New', monospace; font-size: 13px; white-space: pre-wrap;
        }
        .step-hint {
            background-color: #1E3A5F; border: 1px solid #2563EB;
            border-radius: 8px; padding: 10px 16px; margin-bottom: 16px;
            font-size: 14px; color: #93C5FD;
        }
        .error-box {
            background-color: #7F1D1D; border: 1px solid #DC2626;
            border-radius: 8px; padding: 12px 16px; margin: 8px 0;
        }
        .success-box {
            background-color: #064E3B; border: 1px solid #059669;
            border-radius: 8px; padding: 12px 16px; margin: 8px 0;
        }
        </style>
        """
    else:
        return """
        <style>
        .stApp { background-color: #FFFFFF; color: #1F2937; }
        .stButton > button {
            background-color: #2563EB; color: white; border-radius: 8px;
            border: none; padding: 8px 20px; font-weight: 600;
        }
        .stButton > button:hover { background-color: #1D4ED8; }
        .stButton > button:disabled { background-color: #D1D5DB; color: #9CA3AF; }
        .stProgress > div > div { background-color: #2563EB; }
        .result-card {
            background-color: #F9FAFB; border: 1px solid #E5E7EB;
            border-radius: 8px; padding: 12px 16px; margin: 4px 0;
        }
        .result-card:hover { border-color: #9CA3AF; }
        .match-high { color: #059669; }
        .match-medium { color: #D97706; }
        .match-low { color: #DC2626; }
        .info-box {
            background-color: #F3F4F6; border: 1px solid #E5E7EB;
            border-radius: 8px; padding: 16px; margin: 12px 0;
        }
        hr { border-color: #E5E7EB; }
        .detail-box {
            background-color: #F9FAFB; border: 1px solid #D1D5DB;
            border-radius: 8px; padding: 16px; max-height: 500px; overflow-y: auto;
            font-family: 'Courier New', monospace; font-size: 13px; white-space: pre-wrap;
        }
        .step-hint {
            background-color: #EFF6FF; border: 1px solid #93C5FD;
            border-radius: 8px; padding: 10px 16px; margin-bottom: 16px;
            font-size: 14px; color: #1E40AF;
        }
        .error-box {
            background-color: #FEE2E2; border: 1px solid #F87171;
            border-radius: 8px; padding: 12px 16px; margin: 8px 0;
        }
        .success-box {
            background-color: #D1FAE5; border: 1px solid #6EE7B7;
            border-radius: 8px; padding: 12px 16px; margin: 8px 0;
        }
        </style>
        """

# ---------------------------------------------------------------------------
# 密码页
# ---------------------------------------------------------------------------
def render_password_page():
    st.markdown("<h2 style='text-align:center;margin-top:80px;'>🔐 MTSCO 客户背调系统</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:#94A3B8;'>请输入密码以继续</p>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        password = st.text_input("密码", type="password", key="pwd_input", label_visibility="collapsed")
        if st.button("进入", use_container_width=True):
            if password == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密码错误，请联系 15666314715 重置密码")

    st.markdown("<p style='text-align:center;color:#64748B;font-size:12px;margin-top:40px;'>MTSCO Customer Screener v" + APP_VERSION + "</p>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
def render_main_app():
    dark = st.session_state.theme == "dark"
    st.markdown(get_theme_css(dark), unsafe_allow_html=True)

    # --- 顶栏 ---
    col_title, col_theme = st.columns([5, 1])
    with col_title:
        st.markdown("## 🔧 MTSCO 客户背调系统")
    with col_theme:
        theme_label = "☀️ 亮色" if dark else "🌙 暗色"
        if st.button(theme_label, key="theme_toggle", use_container_width=True):
            st.session_state.theme = "light" if dark else "dark"
            st.rerun()

    # --- 步骤提示 ---
    st.markdown(
        '<div class="step-hint">📋 1. 填入 DeepSeek API Key → 2. 上传 Excel（含公司名+国家）→ 3. 点击开始分析 → 4. 下载结果</div>',
        unsafe_allow_html=True,
    )

    # --- 侧边栏: 配置 ---
    with st.sidebar:
        st.markdown("### ⚙️ 配置")

        # API Key
        saved_key = st.session_state.get("api_key", "")
        api_key = st.text_input(
            "🔑 DeepSeek API Key",
            type="password",
            value=saved_key,
            placeholder="sk-...",
            help="从 platform.deepseek.com 获取。Key 仅保存在浏览器本地。",
            key="api_key_input",
        )
        if api_key:
            st.session_state.api_key = api_key

        # 模型
        model = st.selectbox(
            "🧠 模型",
            options=["deepseek-chat", "deepseek-reasoner"],
            index=0 if st.session_state.model == "deepseek-chat" else 1,
            help="deepseek-chat (V3): 快速便宜 / deepseek-reasoner (R1): 推理更强但慢",
            key="model_select",
        )
        st.session_state.model = model

        st.divider()

        # 评判标准
        with st.expander("📂 评判标准"):
            st.markdown("""
            **匹配度说明：**
            - 🟢 **高匹配** — 行业对口 + 业务需不锈钢管材 → 发开发信
            - 🟡 **中等** — 信息不足或需确认 → 人工核实
            - 🔴 **不匹配** — 行业不对口或无采购需求 → 不发

            **公司类型：**
            - End User — 工厂/设施运营商
            - EPC — 工程总承包商
            - Subcontractor — 安装分包商
            - Trader — 贸易商
            - Stockist — 库存商
            - Manufacturer — 制造商（可能需母管原料）

            **数据来源：** DuckDuckGo搜索 + LinkedIn + 官网抓取。
            免费渠道限制，海关数据可能不全。
            """)

        st.divider()

        # 示例下载
        if st.button("📥 下载示例 Excel", use_container_width=True):
            sample_path = Path(__file__).parent / "output" / "sample_input.xlsx"
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            create_sample_excel(str(sample_path))
            with open(sample_path, "rb") as f:
                st.download_button(
                    label="点击下载示例文件",
                    data=f,
                    file_name="背调模板_示例.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            st.caption("包含3家公司示例：公司名称 + 国家 + 网站")

        st.divider()

        # 清空
        if st.button("🗑️ 清空重置", use_container_width=True):
            for key in DEFAULTS:
                if key != "authenticated" and key != "theme":
                    st.session_state[key] = DEFAULTS[key]
            st.rerun()

        st.caption(f"v{APP_VERSION}")

    # =======================================================================
    # 主区域
    # =======================================================================

    # --- 文件上传区 ---
    col_upload, col_history = st.columns([3, 2])

    with col_upload:
        uploaded_file = st.file_uploader(
            "📎 上传 Excel（公司名+国家+网站）",
            type=["xlsx", "xls", "csv"],
            help="支持 .xlsx / .xls / .csv。列名可自动识别。",
            key="file_uploader",
            disabled=st.session_state.analysis_running,
        )

    with col_history:
        history_file = st.file_uploader(
            "📎 历史结果去重（选填）",
            type=["xlsx", "xls", "csv"],
            help="上传之前的结果Excel，自动排除已出现的公司",
            key="history_uploader",
            disabled=st.session_state.analysis_running,
        )

    # --- 处理上传 ---
    if uploaded_file is not None and (
        st.session_state.get("_last_uploaded") != uploaded_file.name
    ):
        st.session_state._last_uploaded = uploaded_file.name
        try:
            if uploaded_file.name.endswith(".csv"):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)

            # 校验
            screener = BatchScreener(api_key="dummy")  # 仅用于校验
            is_valid, errors, validated = screener.validate_excel(df)

            if is_valid:
                st.session_state.validated_df = validated
                st.session_state.uploaded_df = df
                st.session_state.results = []
                st.session_state.all_companies = []
                st.session_state.failed_indices = []
                st.session_state._upload_errors = None

                # 去重
                history_df = None
                if history_file is not None:
                    try:
                        if history_file.name.endswith(".csv"):
                            history_df = pd.read_csv(history_file)
                        else:
                            history_df = pd.read_excel(history_file)
                    except Exception:
                        history_df = None

                if history_df is not None and not history_df.empty:
                    deduped, duplicates = BatchScreener.deduplicate(validated, history_df)
                    st.session_state.validated_df = deduped
                    st.session_state.history_duplicates = duplicates
                else:
                    st.session_state.history_duplicates = []

                # 成本估算
                count = len(st.session_state.validated_df)
                st.session_state.cost_estimate = BatchScreener.estimate_cost(count)
                # 清除旧的估算，等会重新显示
                st.session_state._show_estimate = True

            else:
                st.session_state._upload_errors = errors
                st.session_state.validated_df = None
                st.session_state.cost_estimate = {}
                st.session_state._show_estimate = False

        except Exception as e:
            st.session_state._upload_errors = [f"文件读取失败: {str(e)[:200]}"]
            st.session_state.validated_df = None
            st.session_state.cost_estimate = {}
            st.session_state._show_estimate = False

    # --- 显示上传结果 ---
    if hasattr(st.session_state, "_upload_errors") and st.session_state._upload_errors:
        st.markdown('<div class="error-box">', unsafe_allow_html=True)
        st.error("❌ Excel 格式有问题，请修改后重新上传:")
        for err in st.session_state._upload_errors:
            st.write(f"• {err}")
        st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.history_duplicates:
        dup_count = len(st.session_state.history_duplicates)
        st.info(f"🔍 去重: 跳过 {dup_count} 家已出现过的公司（如要重新分析请先移除历史文件）")

    # --- 预览 + 成本估算 ---
    if st.session_state.validated_df is not None:
        df_v = st.session_state.validated_df
        total = len(df_v)

        st.markdown("#### 📊 数据预览")
        preview_n = min(5, total)
        preview_df = df_v.head(preview_n)[["name", "country", "website"]].copy()
        preview_df.columns = ["公司名", "国家", "网站"]
        preview_df.insert(0, "#", range(1, preview_n + 1))
        st.dataframe(preview_df, hide_index=True, use_container_width=True)

        if total > preview_n:
            st.caption(f"...共 {total} 家公司（显示前{preview_n}家）")

        # 成本估算
        if st.session_state.cost_estimate:
            est = st.session_state.cost_estimate
            st.markdown(f"""
            <div class="info-box">
            📈 共 <strong>{est['companies']}</strong> 家 ·
            <strong>{est['batches']}</strong> 批 ·
            预计耗时 ~<strong>{est['estimated_minutes']}</strong> 分钟 ·
            预估 API 费用 ¥<strong>{est['estimated_cost_rmb']}</strong>
            &nbsp;&nbsp;<span style="font-size:12px;color:#94A3B8;">（从你的 DeepSeek 账户扣除）</span>
            </div>
            """, unsafe_allow_html=True)

    # --- 分析按钮 ---
    col_start, col_stop, col_space = st.columns([2, 2, 6])

    with col_start:
        can_start = (
            st.session_state.validated_df is not None
            and st.session_state.api_key
            and not st.session_state.analysis_running
        )
        start_clicked = st.button(
            "▶ 开始分析",
            type="primary",
            use_container_width=True,
            disabled=not can_start,
        )

    with col_stop:
        stop_clicked = st.button(
            "🛑 停止分析",
            use_container_width=True,
            disabled=not st.session_state.analysis_running,
        )

    if stop_clicked:
        st.session_state.analysis_running = False
        st.session_state.analysis_stopped = True
        st.rerun()

    # --- 执行分析 ---
    if start_clicked and can_start:
        st.session_state.analysis_running = True
        st.session_state.analysis_stopped = False
        st.session_state.results = []
        st.session_state.failed_indices = []
        st.session_state.all_companies = st.session_state.validated_df.to_dict("records")
        st.rerun()

    # --- 分析运行中 ---
    if st.session_state.analysis_running and st.session_state.all_companies:
        _run_analysis()

    # --- 显示结果 ---
    if st.session_state.results:
        _render_results()

    # --- 详情查询 ---
    if st.session_state.results:
        st.divider()
        st.markdown("#### 🔍 查询详情（原始搜索材料）")
        _render_detail_search()


# ---------------------------------------------------------------------------
# 分析执行（在 st.rerun 循环中逐家处理）
# ---------------------------------------------------------------------------
def _run_analysis():
    companies = st.session_state.all_companies
    total = len(companies)
    completed = len(st.session_state.results)

    if completed >= total:
        st.session_state.analysis_running = False
        st.rerun()
        return

    # 初始化 screener（仅首次）
    if "_screener_instance" not in st.session_state:
        try:
            screener = BatchScreener(
                api_key=st.session_state.api_key,
                model=st.session_state.model,
            )
            # 验证 API Key
            is_valid, msg = screener.validate_api_key()
            if not is_valid:
                st.error(f"❌ API Key 验证失败: {msg}")
                st.session_state.analysis_running = False
                st.rerun()
                return
            st.success(f"✅ {msg}")
            st.session_state._screener_instance = screener
        except Exception as e:
            st.error(f"❌ 初始化失败: {str(e)[:200]}")
            st.session_state.analysis_running = False
            st.rerun()
            return

    screener: BatchScreener = st.session_state._screener_instance

    # 处理下一家
    idx = completed
    company = companies[idx]
    name = company["name"]
    country = company["country"]
    website = company.get("website", "")

    batch_num = idx // BATCH_SIZE + 1
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    # --- 进度条 ---
    progress = completed / total
    progress_text = f"第 {batch_num}/{total_batches} 批 ({completed}/{total})"
    st.progress(progress, text=progress_text)

    # --- 搜索 ---
    search_placeholder = st.empty()
    search_placeholder.info(f"🔍 正在搜索: **{name}** ({country}) ...")

    try:
        search_data = screener.search_company(name, country)
        search_ok = True
        results_count = len(search_data.get("search_results", []))
        pages_count = len(search_data.get("page_texts", []))
        search_placeholder.success(
            f"🔍 {name} — 搜索到 {results_count} 个结果，抓取 {pages_count} 个页面"
        )
    except RuntimeError as e:
        msg = str(e)
        if "DDG_RATELIMIT" in msg:
            search_placeholder.warning("⚠️ DuckDuckGo 限流，暂停30秒...")
            time.sleep(30)
            try:
                search_data = screener.search_company(name, country)
                search_ok = True
                search_placeholder.success(f"🔍 {name} — 搜索已恢复")
            except RuntimeError:
                search_data = None
                search_ok = False
                search_placeholder.error(f"⚠️ {name} — 搜索超时，跳过搜索直接用AI判断")
        else:
            search_data = None
            search_ok = False
            search_placeholder.error(f"⚠️ {name} — 搜索失败")

    # --- AI 分析 ---
    analyze_placeholder = st.empty()
    analyze_placeholder.info(f"🤖 正在分析: **{name}** ...")

    try:
        result = screener.analyze_company(name, country, website, search_data)
    except RuntimeError as e:
        msg = str(e)
        if "BALANCE_INSUFFICIENT" in msg:
            st.error("💸 **API 余额不足！** 请充值后点击下方「重试失败项」继续。")
            result = CompanyResult(
                company_name=name, country=country, website=website,
                error_message="API余额不足",
                match_level="medium", conclusion="需人工核实",
                match_reason="分析中断: API余额不足",
                confidence="low",
            )
            st.session_state.results.append(result)
            st.session_state.analysis_running = False
            st.rerun()
            return
        else:
            result = CompanyResult(
                company_name=name, country=country, website=website,
                error_message=f"API异常: {msg[:200]}",
                match_level="medium", conclusion="需人工核实",
                match_reason=f"分析失败: {msg[:200]}",
                confidence="low",
            )
            st.session_state.failed_indices.append(completed)

    result.analysis_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.results.append(result)

    # 显示当前结果
    match_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(result.match_level, "⚪")
    if result.error_message and "余额不足" in result.error_message:
        analyze_placeholder.error(f"💸 {name} — 余额不足，分析中断")
    elif result.error_message:
        analyze_placeholder.warning(f"❌ {name} — {result.error_message[:100]}")
    else:
        analyze_placeholder.success(
            f"{match_emoji} **{name}** — {result.match_level.upper()} — "
            f"{result.company_type} / {result.industry} — {result.conclusion}"
        )

    # 自动 rerun 处理下一家
    time.sleep(0.2)
    st.rerun()


# ---------------------------------------------------------------------------
# 结果展示
# ---------------------------------------------------------------------------
def _render_results():
    results = st.session_state.results
    total_expected = len(st.session_state.all_companies)

    st.divider()
    st.markdown("### 📊 分析结果")

    # 统计摘要
    high_count = sum(1 for r in results if r.match_level == "high")
    med_count = sum(1 for r in results if r.match_level == "medium")
    low_count = sum(1 for r in results if r.match_level == "low")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("已完成", f"{len(results)}/{total_expected}")
    with col2:
        st.metric("🟢 高匹配", high_count)
    with col3:
        st.metric("🟡 中等", med_count)
    with col4:
        st.metric("🔴 不匹配", low_count)

    # 失败项
    if st.session_state.failed_indices:
        failed_names = [st.session_state.all_companies[i]["name"] for i in st.session_state.failed_indices]
        st.warning(f"⚠️ {len(st.session_state.failed_indices)} 家分析失败: {', '.join(failed_names[:5])}"
                   f"{'...' if len(st.session_state.failed_indices) > 5 else ''}")

        if st.button("🔄 重试失败项"):
            # 重试：移除失败的结果，重新设置
            for idx in sorted(st.session_state.failed_indices, reverse=True):
                if idx < len(st.session_state.results):
                    st.session_state.results.pop(idx)
            st.session_state.failed_indices = []
            st.session_state.analysis_running = True
            st.session_state.analysis_stopped = False
            # 清除 screener 缓存以便重试
            if "_screener_instance" in st.session_state:
                del st.session_state._screener_instance
            st.rerun()

    # 下载按钮
    all_done = len(results) >= total_expected and not st.session_state.analysis_running
    stopped_early = st.session_state.analysis_stopped and len(results) > 0

    if all_done or stopped_early:
        # 生成 Excel
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        excel_path = output_dir / f"背调结果_{date_str}.xlsx"
        results_to_excel(results, str(excel_path))

        with open(excel_path, "rb") as f:
            excel_data = f.read()

        st.download_button(
            label=f"📥 下载结果 Excel（{len(results)}家）",
            data=excel_data,
            file_name=f"背调结果_{date_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    # --- 结果表格 ---
    if results:
        # 按匹配度排序
        match_order = {"high": 0, "medium": 1, "low": 2}
        sorted_results = sorted(results, key=lambda r: match_order.get(r.match_level, 99))

        # 构建展示数据
        table_data = []
        for r in sorted_results:
            emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(r.match_level, "⚪")
            table_data.append({
                "公司名": r.company_name,
                "匹配度": f"{emoji} {r.match_level}",
                "结论": r.conclusion,
                "国家": r.country,
                "网站": r.website,
                "公司类型": r.company_type,
                "行业": r.industry,
                "海关记录": r.customs_summary or "未查到",
                "理由": r.match_reason,
            })

        display_df = pd.DataFrame(table_data)

        # 固定列 + 展开列
        fixed_cols = ["公司名", "匹配度", "结论"]
        other_cols = [c for c in display_df.columns if c not in fixed_cols]

        st.markdown("##### 结果列表（按匹配度排序）")
        st.dataframe(
            display_df,
            column_config={
                "公司名": st.column_config.TextColumn("公司名", width="large"),
                "匹配度": st.column_config.TextColumn("匹配度", width="small"),
                "结论": st.column_config.TextColumn("结论", width="small"),
                "理由": st.column_config.TextColumn("理由", width="large"),
            },
            hide_index=True,
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# 详情查询
# ---------------------------------------------------------------------------
def _render_detail_search():
    results = st.session_state.results
    if not results:
        return

    company_names = [r.company_name for r in results]

    col_input, col_btn = st.columns([4, 1])
    with col_input:
        # 支持打字搜索 + 下拉选择
        search_term = st.text_input(
            "输入公司名搜索",
            placeholder="输入公司名或从列表选择...",
            key="detail_search_input",
            label_visibility="collapsed",
        )

    query_name = search_term.strip() if search_term else ""

    if query_name:
        detail = BatchScreener.get_company_detail(results, query_name)
        if detail:
            with st.expander(f"📄 {detail['公司名']} — 原始材料", expanded=True):
                st.markdown("**搜索关键词:**")
                st.code(detail.get("搜索关键词", "无"))

                st.markdown("**搜索结果摘要:**")
                st.code(detail.get("搜索结果摘要", "无")[:3000] or "无")

                st.markdown("**抓取页面内容:**")
                st.code(detail.get("抓取页面", "无")[:3000] or "无")

                st.markdown("**AI 原始响应:**")
                st.code(detail.get("AI原始响应", "无")[:2000] or "无")

                if detail.get("错误信息"):
                    st.warning(f"错误: {detail['错误信息']}")
        else:
            st.info(f"未找到「{query_name}」，请检查公司名拼写")

    # 快捷选择
    if not query_name:
        with st.expander("📋 从已分析公司列表选择"):
            for name in company_names:
                if st.button(name, key=f"detail_btn_{name[:30]}"):
                    st.session_state.detail_search_input = name
                    st.rerun()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not st.session_state.authenticated:
        render_password_page()
    else:
        render_main_app()


if __name__ == "__main__":
    main()
