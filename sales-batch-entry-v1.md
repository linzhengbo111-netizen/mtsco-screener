# 业务员批量查询 — 第一版入口用法

> 验证日期：2026-04-28
> 状态：已验证通过

---

## 当前可用 BASE_URL

```
https://alike-quench-entwine.ngrok-free.dev

```

---

## 使用顺序

**create -> status -> result**（严格按此顺序）

1. **create** 创建批次，拿到 `batch_id`
2. **status** 轮询执行进度，直到 `completed`
3. **result** 拉取结果（超时可重试一次）

所有请求必须带请求头：`ngrok-skip-browser-warning: true`

---

## 一、文本名单入口

### 1. 解析输入

用户在聊天框输入多行公司名，每行代表一家公司，空行跳过。

示例输入：
```
公司A
公司B US
公司C http://example.com
```

解析为 JSON 数组：
```json
[
  {"customer_name": "公司A", "country_region": "", "website": ""},
  {"customer_name": "公司B", "country_region": "US", "website": ""},
  {"customer_name": "公司C", "country_region": "", "website": "http://example.com"}
]
```

规则：`customer_name` 必需，`country_region` 和 `website` 可选。

### 2. 创建批次

```
POST {BASE_URL}/api/batch/create
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "customers": [...],
  "source": "openclaw"
}
```

记下返回的 `batch_id`。

### 3. 轮询状态

每 30 秒查询一次，最多 20 次：

```
GET {BASE_URL}/api/batch/status?batch_id={batch_id}
ngrok-skip-browser-warning: true
```

当 `status` 为 `completed` 或 `failed` 时停止轮询。

### 4. 获取结果

```
GET {BASE_URL}/api/batch/result?batch_id={batch_id}
ngrok-skip-browser-warning: true
```

如果超时，重试一次。

### 5. 回复用户

将结果整理为结构化摘要回复给用户。

---

## 二、Excel 上传入口

### 1. 读取 Excel

用 xlsx skill 读取用户上传的 `.xlsx` / `.xls` 文件。

### 2. 表头映射规则（标准模板优先 + 自动识别）

#### 2.1 第一优先：标准模板列名

如果列名完全匹配以下标准字段名，直接映射：

| 标准列名 | 映射字段 |
|---------|---------|
| `company_name` | customer_name |
| `country` | country_region |
| `website` | website |

即 `sales_batch_template.xlsx` 模板格式，无需任何转换。

#### 2.2 第二优先：常见别名自动识别

如果列名不是标准字段名，按以下别名表做**忽略大小写 + 去空格的模糊匹配**：

| 目标字段 | 兼容别名（不分大小写） |
|---------|----------------------|
| **customer_name** | 公司名, 客户名称, 企业名称, 公司名称, 公司, 客户, Company Name, Customer Name, companyname, customername |
| **country_region** | 国家, 国家地区, 地区, Country, Region, countryregion, 国家/地区, 国别 |
| **website** | 官网, 网站, 网址, Website, URL, 公司网址, 公司网址/官网, companywebsite |

匹配规则：
- 先精确匹配（忽略大小写、去前后空格）
- 再包含匹配（列名包含别名关键词，如"客户名称(中文)"匹配"客户名称"）
- 不需要业务员手动转换表头

#### 2.3 无法识别时：提示人工确认

以下情况**不能自动跑**，必须提示用户确认：

| 情况 | 处理方式 |
|------|---------|
| 找不到任何类似公司名的列 | 提示"未识别到公司名列，请确认哪一列是公司名称" |
| 有两列都像公司名 | 提示"发现两列都可能公司名，请确认用哪一列" |
| Excel 无表头（第一行就是数据） | 提示"请确认第一行是否为表头，或提供列号对应关系" |

提示后，等待用户回复确认映射关系，再继续。

### 3. 转为 customers 数组

逐行读取数据，构建 JSON 数组。`customer_name` 为空的行跳过。

格式：
```json
[
  {"customer_name": "公司名", "country_region": "国家（如有）", "website": "官网（如有）"}
]
```

### 4. 创建批次 / 轮询 / 获取结果

同文本名单入口的步骤 2 ~ 4。

### 5. 回复用户

将结果整理为结构化摘要回复给用户。

---

## 三、Excel 模板

### 标准模板文件

项目根目录下提供：`sales_batch_template.xlsx`

| company_name | country | website |
|-------------|---------|---------|
| 上海某某进出口贸易有限公司 | CN | https://www.example1.com |
| ABC Trading LLC | US | https://www.abc-trading.com |

规则：
- `company_name` 列必须存在，值为空则跳过该行
- `country` 和 `website` 列可选
- 表头行不计入数据

### 也兼容业务员自有 Excel

如果业务员上传自己的 Excel（非标准模板），系统会自动识别表头（见第二节 2.2）。识别失败才提示人工确认。

---

## 四、OpenClaw 入口调用说明

### 入口识别

| 用户行为 | 入口类型 |
|---------|---------|
| 聊天框输入多行公司名 | 文本名单 |
| 上传 `.xlsx` / `.xls` 文件 | Excel 上传 |

### 注意事项

1. 所有请求必须带 `ngrok-skip-browser-warning: true`
2. 不要复用旧 `batch_id`，每次 create 都是新的
3. `result` 超时可重试一次，不要频繁轮询
4. `source` 字段必须为 `"openclaw"`
5. 如果轮询 20 次仍未完成，提示"任务仍在执行中，请稍后手动查询"
6. 如果批次创建失败，告知用户原因

---

## 五、已验证通过的说明

- **文本多公司入口**：已通过 create / status / result 全链路测试
- **Excel 上传入口**：已通过 create / status / result 全链路测试
- 两个 OpenClaw 实例均已验证通过
