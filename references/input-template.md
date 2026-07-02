# 输入文件规范

## 输入载体

- 文件格式：`.xlsx`
- 编码：UTF-8（Excel 默认）
- 首行为表头行

## 最小输入字段

| 归一化字段名 | 是否必填 | 说明 |
|---|---|---|
| customer_name | **必填** | 公司/客户名称，用于腾道搜索关键词 |
| country_region | 建议必填 | 国家/地区，用于匹配校验 |
| website | 可空 | 官网网址，辅助匹配 |
| email_domain | 可空 | 邮箱域名，辅助匹配 |
| product_keywords | 可空 | 产品关键词，辅助匹配 |
| internal_customer_id | 可空 | 内部客户 ID，结果表透传 |

## 表头自动识别

程序读取第一行表头后做以下标准化：

1. 去首尾空格
2. 全角转半角
3. 英文转小写
4. 去掉连续空格
5. 下划线 `_` 和空格视为等价

### 精确英文表头

`customer_name`、`country_region`、`website`、`email_domain`、`product_keywords`、`internal_customer_id`

### 最小中文兼容映射

| 输入表头（归一化后） | 映射为 |
|---|---|
| 客户名 / 客户名称 / 公司名 / 公司名称 | customer_name |
| 国家 / 国家地区 / 国家地区 / 国家/地区 | country_region |
| 官网 / 网址 / 网站 | website |
| 邮箱域名 / 域名 | email_domain |
| 产品关键词 / 产品 / 品类 | product_keywords |
| 客户id / 原始客户id / 内部客户id | internal_customer_id |

## 输入校验

- 未识别到 `customer_name`：**整批报错，不开始执行**
- 未识别到 `country_region`：允许执行，但结果默认降置信度
- 其他字段缺失：允许为空，不影响执行
