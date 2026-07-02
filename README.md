# tendata-customer-enricher

腾道（TenData）客户数据抓取 Skill — 技术文档。

## 功能

读取 Excel 客户名单 -> 自动识别表头 -> 在已登录腾道前提下逐条检索 -> 抽取企业信息 -> 输出结构化结果表。

## 前置条件

### Python 依赖

```bash
pip install playwright pandas openpyxl
playwright install chromium
```

需要 Python 3.9+。

### 腾道登录

本 skill **不做自动登录**。用户必须先手动登录腾道，skill 只复用已登录的浏览器会话。

- 不输入账号密码
- 不处理验证码
- 不尝试自动过验证码

### 浏览器启动方式

**方式 A：双击 start_tendata_helper.bat（推荐）**

自动在专用 Chrome 实例上启动调试模式，打开 bizr.tendata.cn，使用独立 profile。
启动后用户需在腾道助手中登录腾道。

**方式 B：手动启动**

```bash
chrome.exe --remote-debugging-port=9222 --user-data-dir="<profile_dir>" "https://bizr.tendata.cn/search#/index"
```

登录后保持该窗口打开，运行抓取脚本即可。

## 架构

```
start_tendata_helper.bat  -->  启动 Chrome (port 9222)
                                 ↓
                          用户手动登录腾道
                                 ↓
run_tendata_batch.bat  -->  检查 port 9222 在线
                                 ↓
scripts/run_batch.py  -->  自检 -> 逐条抓取 -> 导出
                                 ↓
                          result_xxxx.xlsx
```

### 浏览器连接

- 通过 CDP (Chrome DevTools Protocol) 连接已有 Chrome 实例
- 支持端口 9222-9225 自动尝试
- `connect_over_cdp` 返回 Browser，通过 `browser.contexts[0]` 获取上下文
- 备选：持久化上下文 (`launch_persistent_context`)

### 登录态检测

不主动导航，而是检查当前活动标签页的状态：
- 打印当前 URL、title、页面分类（biz_search/biz_home/learning/login/unknown）
- biz_search（bizr.tendata.cn/search）：直接接管
- biz_home（account.tendata.cn 等）：已登录，自动跳转到搜索页
- learning（knowledge.tendata.cn）：已登录但页面不对，跳转到搜索页
- login：判断为未登录
- unknown：扫描 DOM 特征元素判断

### 搜索/抓取流程

```
当前活动标签页 -> 页面分类
  biz_search (bizr.tendata.cn/search) -> 直接接管 -> 输入关键词搜索
  biz_home (account.tendata.cn)      -> 跳转到搜索页 -> 输入关键词搜索
  learning (knowledge.tendata.cn)    -> 跳转到搜索页 -> 输入关键词搜索
  unknown                             -> 点击"商情发现"菜单 -> 搜索页
  login                               -> 判断为未登录 -> 中止
```

### 匹配逻辑

- 公司名标准化：统一后缀 (co/ltd/llc/corp/inc/gmbh 等)
- Jaccard 字符级相似度 + 子串加分
- 辅助信号：官网域名匹配、国家/地区一致
- 置信度 0-100，状态：confirmed / likely_match / unconfirmed / no_result

## 运行

```bash
# 方式一：双击启动器
# 1. start_tendata_helper.bat -> 登录腾道
# 2. run_tendata_batch.bat customer_list.xlsx

# 方式二：命令行
python scripts/run_batch.py --input <客户名单.xlsx> [--output <结果.xlsx>] [--headless] [--batch-id BATCH-001]
```

### 参数

| 参数 | 说明 |
|---|---|
| `--input` | 输入 Excel 路径（必填） |
| `--output` | 输出 Excel 路径（可选，默认自动生成） |
| `--headless` | 无头模式运行浏览器 |
| `--batch-id` | 批次 ID（可选，默认自动生成） |

### 自检

运行前自动执行环境自检：
1. Playwright 是否已安装
2. Chrome 调试端口是否在线
3. 腾道登录态是否有效

### 单条测试

```bash
# 确保已在腾道助手中登录并处于商情发现页面
python scripts/extract_tendata_fields.py --single "Maxvalue Industries"

# 或使用 single_test.xlsx 文件测试
python scripts/run_batch.py --input single_test.xlsx --output single_test_result.xlsx
```

如果 single_test.xlsx 不存在，可运行 `make_single_test.bat` 或 `python scripts/make_single_test.py` 自动生成。

### 匹配逻辑测试

```bash
python scripts/extract_tendata_fields.py --test
```

### 清理运行时产物

测试完成后，如需打包或传输，请先运行：

```bash
clean_runtime_artifacts.bat
```

或：

```bash
python scripts/clean_runtime_artifacts.py
```

清理范围：`.tendata-chrome-profile/`、`__pycache__/`、`*.pyc`、临时结果 Excel。

### 打包

运行 `python build.py` 生成到 `dist/` 目录：
- `dist/skill.zip` — ClawHub 上传包
- `dist/tendata-customer-enricher-user.zip` — 业务员运行包

## 目录结构

```
tendata-customer-enricher/
├── SKILL.md                          # 技能入口
├── README.md                         # 技术文档（本文件）
├── README_business_user.md           # 业务员操作指南
├── start_tendata_helper.bat          # 启动 Chrome 腾道助手
├── run_tendata_batch.bat             # 运行批处理抓取
├── clean_runtime_artifacts.bat       # 清理运行时缓存
├── make_single_test.bat              # 生成单条测试 Excel
├── build.py                          # 打包脚本（生成到 dist/）
├── single_test.xlsx                  # 单条测试样例
├── sample_input.xlsx                 # 批量测试样例（英文表头）
├── sample_input_chinese_headers.xlsx # 批量测试样例（中文表头）
├── agents/
│   └── openai.yaml                   # Agent 配置
├── references/
│   ├── field-schema.md               # 字段定义
│   ├── page-flow.md                  # 页面链路
│   ├── matching-rules.md             # 匹配规则
│   └── input-template.md             # 输入规范
├── scripts/
│   ├── normalize_input.py            # 表头归一化
│   ├── extract_tendata_fields.py     # 浏览器抓取 + 匹配计算
│   ├── export_results.py             # 结果导出
│   ├── run_batch.py                  # 主流程编排（含自检）
│   ├── make_single_test.py           # 生成单条测试 Excel
│   └── clean_runtime_artifacts.py    # 清理运行时缓存
└── dist/                             # 打包产物（自动排除在包外）
    ├── skill.zip
    └── tendata-customer-enricher-user.zip
```

## 选择器配置

腾道页面 CSS 选择器在 `extract_tendata_fields.py` 的 `TENDATA_CONFIG` 中定义。如页面结构变化，需更新以下选择器：

- `search_input`: 搜索输入框
- `search_button`: 搜索按钮
- `search_result_item`: 搜索结果项
- `detail_company_name`: 详情页公司名
- `detail_website`: 官网链接
- `import_analysis_tab`: 进口分析 Tab

## 异常处理

- 单条失败不中断整批
- 无结果 -> no_result + confidence=0
- 多结果冲突 -> 输出 top1 + 降置信度 + manual_review_flag=yes
- 页面打开失败 -> unconfirmed + manual_review_flag=yes
- 字段缺失时留空，**不编造**
- 每批最多处理 10 家

## 打包

生成 skill.zip，排除以下文件：
- `.tendata-chrome-profile/`（运行时自动创建）
- `__pycache__/` 和 `*.pyc`

## 非技术用户指南

业务员操作指南请见 [README_business_user.md](README_business_user.md)，内容仅包含双击启动到查看结果的步骤，不涉及 Python / Playwright / 命令行。
