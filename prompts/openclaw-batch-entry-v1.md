# OpenClaw 腾道批量查询 — 第一版入口提示词

## 入口识别

| 用户行为 | 入口类型 |
|---------|---------|
| 聊天框输入多行公司名 | 文本名单 |
| 上传 .xlsx / .xls 文件 | Excel 上传 |

---

## 文本名单流程

### 步骤 1：解析

对用户输入的公司名单，将其解析为 JSON 数组，格式如下：

```json
[
  {"customer_name": "公司名", "country_region": "国家（如有）", "website": "官网（如有）"}
]
```

规则：
- `customer_name` 必需，其他字段可选
- 跳过空行
- 每行代表一家公司
- 只输出解析后的 JSON，不要解释

### 步骤 2：创建批次

```
POST https://alike-quench-entwine.ngrok-free.dev/api/batch/create
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "customers": [上面解析出的 JSON 数组],
  "source": "openclaw"
}
```

记下返回的 `batch_id`。

### 步骤 3：轮询状态

每 30 秒查询一次，最多 20 次：

```
GET https://alike-quench-entwine.ngrok-free.dev/api/batch/status?batch_id={batch_id}
ngrok-skip-browser-warning: true
```

当 `status` 为 `completed` 或 `failed` 时停止轮询。

### 步骤 4：获取结果

```
GET https://alike-quench-entwine.ngrok-free.dev/api/batch/result?batch_id={batch_id}
ngrok-skip-browser-warning: true
```

如果超时，重试一次。

### 步骤 5：回复飞书卡片

将结果整理为结构化摘要回复给用户。

---

## Excel 上传流程

### 步骤 1：读取 Excel

用 xlsx skill 读取上传的文件。

### 步骤 2：识别表头

模糊匹配列名：

| 可能的列名 | 映射字段 |
|-----------|---------|
| company_name, 公司名, 客户名称, 企业名称, Company Name | customer_name |
| country, country_region, 国家, 地区, Region | country_region |
| website, 网站, 网址, 官网, Website | website |

### 步骤 3：转为 customers 数组

逐行读取数据，构建 JSON 数组。`customer_name` 为空的行跳过。

### 步骤 4～5

同文本名单流程的步骤 2～5。

---

## 注意事项

1. **所有请求必须带** `ngrok-skip-browser-warning: true`
2. **不要复用旧 batch_id**，每次 create 都是新的
3. **result 超时可重试一次**
4. **BASE_URL**：`https://alike-quench-entwine.ngrok-free.dev`
5. 如果批次创建失败，告知用户原因
6. 如果轮询 20 次仍未完成，提示"任务仍在执行中，请稍后手动查询"
