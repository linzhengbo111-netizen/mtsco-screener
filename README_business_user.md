# 腾道客户数据抓取 — 业务员操作指南

> 按步骤操作即可，无需了解技术细节。

---

## 一、首次准备（只需做一次）

### 1. 准备客户名单 Excel

文件格式为 `.xlsx`，第一行为表头。

| 必填 | 字段 | 说明 |
|---|---|---|
| 必填 | customer_name | 客户公司名 |
| 建议 | country_region | 国家/地区，提高匹配准确度 |
| 可选 | website | 公司官网 |
| 可选 | product_keywords | 产品关键词 |
| 可选 | internal_customer_id | 内部客户编号 |

示例：

| customer_name | country_region |
|---|---|
| ABC Corporation | United States |
| 华为技术 | China |

中文表头和英文表头都支持，系统会自动识别。

---

## 二、运行步骤

### 第 1 步：双击 start_tendata_helper.bat

这个脚本会打开一个专用的"腾道助手"窗口。

- 如果之前运行过，会直接打开腾道首页
- 如果是第一次运行，会自动创建浏览器配置

### 第 2 步：在"腾道助手"窗口中登录

1. 在打开的腾道助手窗口中登录你的腾道账号（含验证码）
2. 登录后确认左侧能看到"商情发现"等菜单
3. **登录完成后保持该窗口打开，不要关闭**

> 注意：必须在腾道助手窗口中登录，不要在普通 Chrome 浏览器中登录，否则工具无法读取登录状态。

### 第 3 步：双击 run_tendata_batch.bat

双击后会提示你输入客户名单文件的路径。输入后回车即可开始。

如果你熟悉命令行，也可以直接运行：

```
run_tendata_batch.bat 客户名单.xlsx
```

### 第 4 步：等待结果

- 运行过程中会在屏幕上显示进度
- 每批最多处理 10 家公司
- 完成后会自动生成结果文件（文件名格式：`result_日期时间.xlsx`）

---

## 三、查看结果

结果 Excel 包含以下主要列：

| 列名 | 说明 |
|---|---|
| customer_name | 你输入的客户名 |
| matched_company_name | 腾道上匹配到的公司名 |
| match_status | confirmed（确认）/ likely_match（可能匹配）/ unconfirmed（待确认）/ no_result（无结果） |
| match_confidence | 匹配置信度 0-100 |
| website_result | 腾道上的公司官网 |
| company_status | 公司运营状态 |
| contact_name | 联系人姓名 |
| phone | 联系电话 |
| email | 联系邮箱 |
| address | 公司地址 |
| latest_import_date | 最近进口日期 |
| import_active_status | 活跃 / 不活跃 / 未知 |
| import_activity_summary | 进口活动一句话摘要 |
| analysis_entry_status | 进口分析页进入状态 |
| analysis_data_status | 进口数据状态 |
| recommended_action | 建议继续跟进 / 待人工复核 / 暂不跟进 |
| manual_review_flag | 是否需要人工复核（yes/no） |

---

## 四、结果解读

| 状态 | 含义 | 建议 |
|---|---|---|
| confirmed | 公司名高度匹配，国家一致 | 继续跟进 |
| likely_match | 公司名相似但证据不足 | 人工复核 |
| unconfirmed | 有结果但证据冲突 | 人工复核 |
| no_result | 搜索无结果 | 暂不跟进 |

---

## 五、常见问题

### Q1: 双击 start_tendata_helper.bat 后没有弹出窗口

**可能原因**：未安装 Google Chrome

**解决**：请先安装 Google Chrome 浏览器。

### Q2: 运行 run_tendata_batch.bat 时提示"腾道助手未运行"

**原因**：start_tendata_helper.bat 启动的窗口被关闭了

**解决**：
1. 重新双击 start_tendata_helper.bat
2. 确认腾道已登录
3. 保持窗口打开，再运行 run_tendata_batch.bat

### Q3: 提示"腾道未登录或登录已过期"

**解决**：在腾道助手窗口中重新登录，然后重试。

### Q4: 一批最多处理多少家？

最多 10 家。如需处理更多客户，请分批运行。

### Q5: 每次运行会覆盖之前的结果吗？

不会。每次运行会生成新的结果文件。

---

## 六、注意事项

1. 本工具**不会**：自动输入账号密码、处理验证码、修改腾道账号信息、编造任何数据
2. 每批最多处理 **10 家**客户
3. 每次运行生成**新的结果文件**，不会覆盖历史数据
4. 如果腾道页面结构发生变化，可能需要更新工具配置
