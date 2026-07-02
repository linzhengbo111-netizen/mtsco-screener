#!/usr/bin/env python3
"""
开发信生成器 — Streamlit Web 界面
MTSCO Cold Email Generator

用法:
    streamlit run app_email.py
"""

import sys
from pathlib import Path

# 确保能导入 scripts 目录下的模块
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

import streamlit as st
import pandas as pd
from datetime import datetime

from cold_email_generator import (
    CompanyResearcher,
    EmailGenerator,
    EmailResult,
    ResearchResult,
    save_to_markdown,
)

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="开发信生成器 — MTSCO",
    page_icon="📧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .email-box {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 8px;
        padding: 24px 28px;
        font-family: 'Georgia', 'Times New Roman', serif;
        font-size: 15px;
        line-height: 1.7;
        color: #212529;
        white-space: pre-wrap;
    }
    .email-subject {
        font-weight: 700;
        font-size: 16px;
        color: #0d6efd;
        margin-bottom: 12px;
        border-bottom: 1px solid #dee2e6;
        padding-bottom: 8px;
    }
    .company-type-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 4px;
        font-size: 13px;
        font-weight: 600;
    }
    .badge-epc { background-color: #cfe2ff; color: #084298; }
    .badge-end_user { background-color: #d1e7dd; color: #0a3622; }
    .badge-subcontractor { background-color: #fff3cd; color: #664d03; }
    .badge-trader { background-color: #f8d7da; color: #6a1a21; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 初始化 Session State
# ---------------------------------------------------------------------------
if "history" not in st.session_state:
    st.session_state.history = []  # 每个元素: {name, country, type, subject, body, time}

if "current_email" not in st.session_state:
    st.session_state.current_email = None

# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🔧 开发信生成器")
    st.caption("MTSCO Cold Email Generator")
    st.divider()

    # API Key
    st.subheader("🔑 API 配置")
    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        value=st.session_state.get("api_key", ""),
        placeholder="sk-ant-...",
        help="输入你的 Anthropic API Key。不会存储在磁盘上。",
    )
    if api_key:
        st.session_state.api_key = api_key

    # Model 选择
    model = st.selectbox(
        "模型",
        options=[
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
        index=0,
        help="Sonnet: 质量更高; Haiku: 更快更便宜",
    )

    st.divider()

    # 快速参考
    with st.expander("📋 邮件格式参考"):
        st.markdown("""
        **固定格式：**
        1. **Subject** — 具体、有吸引力
        2. **Dear sir, Good day.**
        3. **我是谁** — Bubba from MTSCO
        4. **哪里知道你的** — 引用实际业务
        5. **能带来什么价值** — 长管减焊接、降污染、加速施工
        6. **Call to action** — Shall I share...

        **规则：简短、具体、不编造**
        """)

    st.divider()

    # 历史记录
    st.subheader("📚 历史记录")
    if st.session_state.history:
        for i, h in enumerate(reversed(st.session_state.history[-10:])):
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(f"📧 {h['name'][:30]}", key=f"hist_{i}", use_container_width=True):
                    st.session_state.current_email = {
                        "company_name": h["name"],
                        "country": h.get("country", ""),
                        "company_type": h.get("type", ""),
                        "subject": h["subject"],
                        "body": h["body"],
                        "generated_at": h["time"],
                    }
                    st.rerun()
            with col2:
                st.caption(h["time"][:10])
    else:
        st.caption("还没有生成记录")

    if st.session_state.history:
        if st.button("🗑️ 清空历史", use_container_width=True):
            st.session_state.history = []
            st.session_state.current_email = None
            st.rerun()

# ---------------------------------------------------------------------------
# 主区域
# ---------------------------------------------------------------------------
st.title("📧 开发信生成器")
st.caption("输入海外客户公司名和国家，自动研究、分类、生成开发信")

st.divider()

# --- 输入表单 ---
col1, col2, col3 = st.columns([3, 2, 2])

with col1:
    company_name = st.text_input(
        "公司名称 *",
        placeholder="例如: SK Engineering & Construction Co., Ltd.",
        help="输入完整的公司英文名称",
    )

with col2:
    country = st.text_input(
        "国家 *",
        placeholder="例如: 韩国 / South Korea",
        help="公司总部所在国家",
    )

with col3:
    website = st.text_input(
        "网站（选填）",
        placeholder="例如: skec.com",
        help="如果知道公司官网，可以直接填入以加速搜索",
    )

col4, col5 = st.columns([2, 2])

with col4:
    company_type_override = st.selectbox(
        "公司类型",
        options=["自动判断", "End User (终端用户)", "EPC (工程总包)",
                  "Subcontractor (分包商)", "Trader (贸易商)"],
        index=0,
        help="默认自动判断。如果你明确知道类型可以手动选择。",
    )

with col5:
    st.write("")  # spacer
    st.write("")
    generate_btn = st.button(
        "🔍 搜索并生成",
        type="primary",
        use_container_width=True,
        disabled=not (company_name and country and api_key),
    )

# 状态提示
if not api_key:
    st.warning("⚠️ 请在左侧边栏输入 Anthropic API Key")
elif not company_name or not country:
    st.info("💡 请输入公司名称和国家后点击生成")

st.divider()

# --- 生成逻辑 ---
if generate_btn and company_name and country and api_key:
    # 解析公司类型覆盖
    type_map = {
        "End User (终端用户)": "end_user",
        "EPC (工程总包)": "epc",
        "Subcontractor (分包商)": "subcontractor",
        "Trader (贸易商)": "trader",
    }
    type_hint = type_map.get(company_type_override, "")

    with st.spinner(f"🔍 正在搜索 {company_name} 的信息..."):
        researcher = CompanyResearcher()
        research = researcher.research(
            company_name=company_name,
            country=country,
            website=website,
        )

    if research.combined_text:
        st.success(f"✅ 研究完成 — 搜索到 {len(research.search_results)} 个结果，抓取 {len(research.page_texts)} 个页面")
    else:
        st.warning("⚠️ 未获取到详细研究数据，将基于公司名和国家生成邮件")

    with st.spinner(f"🤖 正在调用 Claude 分析公司并生成开发信..."):
        generator = EmailGenerator(api_key=api_key, model=model)
        email_result = generator.generate(
            company_name=company_name,
            country=country,
            research_data=research.combined_text,
            website=website,
            company_type_hint=type_hint,
        )

    # 存入 session state
    st.session_state.current_email = {
        "company_name": company_name,
        "country": country,
        "company_type": email_result.company_type,
        "classification_reason": email_result.classification_reason,
        "subject": email_result.subject,
        "body": email_result.body,
        "generated_at": email_result.generated_at,
    }

    # 添加到历史
    st.session_state.history.append({
        "name": company_name,
        "country": country,
        "type": email_result.company_type,
        "subject": email_result.subject,
        "body": email_result.body,
        "time": email_result.generated_at,
    })

    st.rerun()

# --- 显示结果 ---
if st.session_state.current_email:
    email = st.session_state.current_email

    st.subheader("📊 分析结果")

    # 公司类型徽章
    badge_class = f"badge-{email['company_type']}" if email['company_type'] in ["end_user", "epc", "subcontractor", "trader"] else ""
    type_labels = {
        "end_user": "End User (终端用户)",
        "epc": "EPC (工程总包)",
        "subcontractor": "Subcontractor (分包商)",
        "trader": "Trader (贸易商)",
    }
    type_label = type_labels.get(email["company_type"], email["company_type"])

    col_type, col_reason = st.columns([1, 3])
    with col_type:
        st.markdown(f'<span class="company-type-badge {badge_class}">{type_label}</span>', unsafe_allow_html=True)
    with col_reason:
        if email.get("classification_reason"):
            st.caption(f"分类理由: {email['classification_reason']}")

    st.divider()

    st.subheader("📧 开发信")

    # 邮件展示
    email_html = f"""
    <div class="email-box">
        <div class="email-subject">Subject: {email['subject']}</div>
        {email['body']}
    </div>
    """
    st.markdown(email_html, unsafe_allow_html=True)

    # 操作按钮
    st.divider()
    col_action1, col_action2, col_action3 = st.columns([1, 1, 3])

    with col_action1:
        # 复制按钮
        full_text = f"Subject: {email['subject']}\n\n{email['body']}"
        st.code(full_text, language="text", line_numbers=False)
        st.caption("👆 选中上方文本后 Cmd+C 复制")

    with col_action2:
        # 下载按钮
        safe_name = email['company_name'].replace(" ", "_")[:30]
        download_filename = f"cold_email_{safe_name}.md"
        download_content = f"""# 开发信 — {email['company_name']}

**国家**: {email.get('country', '')}
**公司类型**: {type_label}
**生成时间**: {email['generated_at']}

---

## 邮件

**Subject: {email['subject']}**

{email['body']}
"""
        st.download_button(
            label="📥 下载 Markdown",
            data=download_content,
            file_name=download_filename,
            mime="text/markdown",
            use_container_width=True,
        )

    with col_action3:
        pass

# ---------------------------------------------------------------------------
# 底部
# ---------------------------------------------------------------------------
st.divider()
st.caption("MTSCO Cold Email Generator — 迈拓不锈钢管材 · 半导体EPC项目专业供应商")
