# MTSCO 客户背调系统 — 技术文档

## 概述

MTSCO 客户背调系统是一个 Streamlit Web 应用，用于批量筛选海外潜在客户。用户上传含公司名+国家的 Excel 文件，系统自动通过 DuckDuckGo 搜索公司信息，调用 DeepSeek API 分析匹配度，输出带颜色标记的 Excel 结果。

**网址**: 部署后通过 Streamlit Cloud 访问
**技术栈**: Python 3.10+ / Streamlit / DeepSeek API / DuckDuckGo Search / pandas / openpyxl

---

## 文件结构

```
tendata-customer-enricher/
├── app_screener.py                    # Streamlit 主界面（密码保护、上传、进度、下载、详情）
├── scripts/
│   ├── batch_screener.py              # 核心逻辑：搜索、分析、校验、去重、导出
│   └── config/
│       └── screener_prompt.yaml       # DeepSeek 提示词（MTSCO产品线、匹配规则、JSON输出格式）
├── output/                            # 输出目录（结果Excel、示例文件）
├── requirements_screener.txt          # Python 依赖
└── README_SCREENER.md                 # 本文件
```

---

## 核心架构

```
用户上传 Excel
    ↓
格式校验 + 列名自动识别（中英文变体）
    ↓
国家名统一转英文（韩国→South Korea）
    ↓
历史去重（严格名称匹配，跳过已出现公司）
    ↓
成本估算（公司数、批次数、预计时间、API费用）
    ↓
20家/批 分批处理
    ↓
每家: DuckDuckGo搜索（3角度 + LinkedIn + 简称回退）
         ↓
       抓取前2个网页内容
         ↓
       DeepSeek API 分析（JSON输出）
         ↓
       汇总结果（按匹配度排序）
    ↓
输出Excel（9列、带颜色、文件名带日期）
```

---

## 模块详解

### 1. `app_screener.py` — Streamlit 界面

**功能**:
- 密码保护（默认密码: `555888`，输错提示联系 15666314715）
- 暗色/亮色切换（默认暗色）
- API Key 输入（浏览器 session_state 记住，不存服务器）
- 模型选择（deepseek-chat / deepseek-reasoner）
- Excel 上传（支持 .xlsx / .xls / .csv）
- 历史文件上传去重
- 数据预览（前5行）+ 成本估算
- 开始/停止按钮
- 进度条 + 逐条结果实时展示
- 全部跑完后统一下载（中途停止也可下载已完成部分）
- 失败项重试按钮
- 结果表格（按匹配度排序，3列固定+展开）
- 详情查询（打字搜索或列表选择，展示原始搜索材料）
- 评判标准可展开
- 示例文件下载
- 清空重置
- 离开页面确认（防止误关）

**Session State 变量**:
| 变量 | 类型 | 说明 |
|------|------|------|
| `authenticated` | bool | 密码是否通过 |
| `api_key` | str | DeepSeek API Key |
| `model` | str | 模型名称 |
| `theme` | str | "dark" / "light" |
| `results` | list[CompanyResult] | 分析结果列表 |
| `all_companies` | list[dict] | 标准化后的输入公司 |
| `analysis_running` | bool | 是否正在分析 |
| `analysis_stopped` | bool | 是否手动停止 |
| `failed_indices` | list[int] | 失败公司索引 |
| `validated_df` | DataFrame | 校验通过的数据 |

### 2. `scripts/batch_screener.py` — 核心逻辑

#### BatchScreener 类

| 方法 | 说明 |
|------|------|
| `__init__(api_key, model)` | 初始化 DeepSeek client (OpenAI SDK兼容) + 加载提示词 |
| `validate_api_key()` | 验证 Key 有效性（发送最小请求，不产生费用） |
| `search_company(name, country)` | 3角度搜索 + 抓取前2个网页 |
| `analyze_company(name, country, website, search_data)` | 调用 DeepSeek 分析，返回 CompanyResult |
| `validate_excel(df)` | 列名识别 + 格式校验 + 国家名统一 |
| `deduplicate(df, history_df)` | 严格名称匹配去重 |
| `estimate_cost(count)` | 估算耗时和费用 |
| `run_batch(companies, callback)` | 分批处理主循环 |
| `stop()` | 设置停止标志 |
| `get_company_detail(results, name)` | 模糊匹配查找详情 |

#### 搜索策略

```
角度1: "{name}" procurement OR supplier OR import
角度2: "{name}" {country} company profile
角度3: site:linkedin.com/company "{name}"

无结果 → 自动去除 Co., Ltd., Inc. 等后缀 → 用简称重搜
```

#### 网页抓取

- User-Agent 伪装 Chrome 131
- 自动编码检测（Content-Type header → apparent_encoding → utf-8 fallback）
- 去除 script/style/nav/footer/header/aside 标签
- 每页截取前 5000 字符
- 抓取间隔 1 秒（避免被封）
- 响应码、超时、连接错误均有独立处理

#### 重试机制

- API 调用: 3 次重试，间隔 3 秒
- DuckDuckGo 限流: 检测到 rate limit → 暂停 30 秒 → 重试
- 网页超时: 30 秒超时，抓取失败标记 "[超时: 30s]"
- API 余额不足: 立即停止，保留已完成结果

#### 列名自动识别

| 功能列 | 支持的别名 |
|------|------|
| 公司名 | 公司名称、公司名、Company、Company Name、Name、客户名称、客户、企业名称、Customer、Organization 等 |
| 国家 | 国家、Country、Nation、地区、Region、Country/Region 等 |
| 网站 | 网站、Website、Web、官网、URL、Site 等 |

#### 国家名统一

支持中/英/本地语言 → 统一转英文缩写或标准名，例如:
- `韩国` / `Korea` / `KOR` / `한국` → `South Korea`
- 覆盖约 40 个常见贸易国家

### 3. `scripts/config/screener_prompt.yaml` — 提示词

包含:
- MTSCO 完整产品线（无缝管、焊管、管件法兰、盘管、丝材、各材质）
- MTSCO 资质和标杆项目
- 公司类型定义（7种: end_user / epc / subcontractor / trader / stockist / manufacturer / uncertain）
- 目标行业（13个）和非目标行业（12个）
- 三级匹配规则（高/中/低，含详细判断标准）
- 置信度标注规则
- 严格 JSON 输出格式（12个字段）

---

## 数据流

```
输入Excel → validate_excel() → CompanyInput列表
                ↓
            deduplicate() (可选)
                ↓
            estimate_cost()
                ↓
            run_batch() ───→ 逐家:
                ├── search_company() → {results, texts, keywords}
                └── analyze_company() → CompanyResult
                ↓
            results_to_excel() → 带颜色Excel
```

---

## 输出 Excel 格式

| 列 | 内容 | 示例 |
|------|------|------|
| 公司名 | 原始公司名 | SK Engineering & Construction Co., Ltd. |
| 国家 | 统一后的国家名 | South Korea |
| 网站 | 找到的官网URL | www.skec.com |
| 公司类型 | 分类结果 | epc |
| 行业 | 行业分类 | oil_gas |
| 海关记录 | 海关数据摘要 | 未查到海关数据（免费渠道限制） |
| 匹配度 | 🟢高/🟡中/🔴低 | 🟢 高匹配 |
| 理由 | 2-4句中文分析 | SK E&C是韩国顶级EPC承包商... |
| 结论 | 发开发信/不发/需人工核实 | 发开发信 |

- 按匹配度降序排列（高→中→低）
- 匹配度和结论列带条件颜色
- 文件名: `背调结果_20260702.xlsx`

---

## 部署指南

### 方式一: 本地运行

```bash
# 1. 安装依赖
pip install streamlit pandas openpyxl duckduckgo-search beautifulsoup4 requests openai pyyaml

# 2. 启动
streamlit run app_screener.py

# 3. 打开浏览器 http://localhost:8501
```

### 方式二: Streamlit Cloud（免费）

```bash
# 1. 将项目推送到 GitHub 公开仓库
# 2. 在 requirements.txt 中列出依赖（见下方）
# 3. 登录 share.streamlit.io → 连接 GitHub → 选择仓库 → 部署
# 4. 获得网址 https://xxx.streamlit.app
```

requirements.txt:
```
streamlit>=1.28.0
pandas>=2.0.0
openpyxl>=3.1.0
duckduckgo-search>=6.0.0
beautifulsoup4>=4.12.0
requests>=2.31.0
openai>=1.0.0
pyyaml>=6.0
```

### 初始化账号（给别人用）

1. 对方打开网站 → 输入密码 `555888`
2. 去 [platform.deepseek.com](https://platform.deepseek.com) 注册 → 充值 10 元 → 创建 API Key
3. 粘贴 Key 到网站 → 上传 Excel → 开始
4. 费用: 背调 100 家公司约 ¥0.4-0.6

---

## 常见问题排查

### Q: 上传 Excel 提示"未识别到公司名列"
**原因**: 列名不在自动识别列表中
**解决**: 确保公司名列标题为 `公司名`、`Company`、`公司名称` 等常见写法。下载示例文件查看标准格式。

### Q: 一直卡在"正在搜索"
**原因**: DuckDuckGo 被限流（免费搜索偶尔会限）
**解决**: 系统会自动检测限流并暂停 30 秒后重试。如果持续卡住，关闭网页等 5 分钟后重新打开。

### Q: 提示"API余额不足"
**原因**: DeepSeek 账户余额用完
**解决**: 登录 platform.deepseek.com 充值。充值后点击「重试失败项」继续。

### Q: 分析结果不准确
**原因**: 免费搜索渠道信息有限，部分小公司搜不到
**解决**: 
- 在 Excel 中填写网站列，可以显著提高准确率
- 标记为"需人工核实"的公司手动去腾道查
- 查看详情了解 AI 看到了什么材料

### Q: 中途关闭了网页
**原因**: Streamlit 不支持后台运行
**解决**: 重新打开网页，重新上传 Excel 并上传上次的结果文件去重，避免重复分析已完成的。

### Q: 想把网站搬到其他平台
**解决**: 代码是标准的 Streamlit 应用，可部署到:
- Hugging Face Spaces (免费)
- 阿里云/腾讯云轻量服务器 (~¥34/月)
- 自己的 VPS

---

## 本地调试

```bash
# 测试核心模块
cd scripts
python3 -c "
from batch_screener import BatchScreener
s = BatchScreener(api_key='sk-test')
print('Module loaded OK')
"

# 测试搜索（不需要 API Key）
python3 -c "
from batch_screener import BatchScreener
s = BatchScreener(api_key='sk-dummy')
data = s.search_company('SK Engineering Construction', 'South Korea')
print(f'Found {len(data[\"search_results\"])} results')
print(f'Fetched {len(data[\"page_texts\"])} pages')
"

# 启动 Streamlit
streamlit run app_screener.py --server.port 8501
```

---

## 已知限制

1. **搜索质量**: DuckDuckGo 免费搜索不如 Google，部分公司可能搜不到
2. **海关数据**: 免费渠道无法系统获取海关进口记录，结论依赖公开信息推断
3. **准确率**: 预计 75-85%，取决于公司知名度和你是否提供了网站
4. **运行时间**: 每家公司约 15-20 秒，60 家约 15-20 分钟
5. **并发**: 不支持并行分析（避免 DuckDuckGo 限流和 API 限速）

---

**相关问题**: 睿贝ERP数据提取 [[erp-data-extraction]] | 客户开发SOP [[customer-development-sop]]
