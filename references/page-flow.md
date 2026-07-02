# 腾道页面链路

## 站点信息

- **主站（营销首页）**：`https://www.tendata.cn`
- **业务首页/工作台**：`https://account.tendata.cn/#/index`
- **商情发现搜索页**：`https://bizr.tendata.cn/search#/index`
- **学习中心**：`https://knowledge.tendata.cn`
- **登录页**：`https://login.tendata.cn/login#/`

## 页面分类

| 页面类型 | URL 特征 | DOM 特征 | 处理方式 |
|---|---|---|---|
| biz_search | bizr.tendata.cn/search | 商情发现、搜索Tab（公司名称/产品/产品描述/HS编码） | 直接接管 |
| biz_home | account.tendata.cn 或其他 bizr.tendata.cn 非搜索页 | 工作台元素 | 跳转到搜索页 |
| learning | knowledge.tendata.cn | 学习相关元素 | 跳转到搜索页 |
| login | login.tendata.cn | 登录表单 | 判断为未登录 |
| unknown | 其他 | 扫描 LOGIN_INDICATORS | 点击菜单或跳转 |

## 页面 0：接管已登录浏览器

- **入口**：用户已在 Chrome 中手动登录腾道
- **操作**：skill 通过 CDP 连接 Chrome，检查当前活动标签页
- **页面分类策略**：
  1. URL 包含 `knowledge.tendata.cn` → learning
  2. URL 包含 `bizr.tendata.cn/search` → biz_search
  3. URL 包含 `account.tendata.cn` 或其他 `bizr.tendata.cn` → biz_home
  4. URL 包含 `login.tendata.cn` 或 `/login` → login
  5. 通过 DOM 特征判断 → biz_search 或 unknown
- **登录态检查**：不依赖 URL/title，而是检查页面中是否包含已登录特征元素：
  - 左侧菜单"商情发现"
  - 搜索区域 Tab（公司名称/产品/产品描述/HS编码）
  - "商情洞察"、"数据通"等功能入口
  - "退出登录"、"个人中心"等用户区域文字

## 页面 1：商情发现搜索页

- **入口**：`https://bizr.tendata.cn/search#/index`
- **操作**：选择"公司名称"检索模式，输入 `customer_name` 发起查询
- **提取信息**：
  - 搜索关键词记录
  - 搜索结果总数

## 页面 2：搜索结果页 / 贸易数据结果页

- **操作**：浏览候选公司列表
- **提取信息**：
  - 候选公司名称列表
  - 最近贸易时间
  - 联系人数
  - 公司简介
  - 每个候选的排名（rank）
- **决策**：选取 top1 候选，结合 `country_region` 判断合理性

## 页面 3：企业详情页 / 公司信息页

- **入口**：点击搜索结果中 top1 候选进入
- **提取信息**：
  - 标准公司名
  - 公司网址（website）
  - 公司运营状态（company_status）
  - 联系方式摘要
  - 企业基础信息
- **页面打开失败处理**：记录 failure → unconfirmed + manual_review_flag=yes

## 页面 4：进口分析页

- **入口**：从企业详情页导航至进口分析
- **提取信息**：
  - 最近进口日期（latest_import_date）
  - 贸易次数
  - 金额
  - 重量
  - 产品/HS 线索
  - 供应国/贸易方向
  - 贸易记录总数
- **页面打开失败处理**：记录 failure → unconfirmed + manual_review_flag=yes

## 页面访问顺序

```
当前活动标签页（接管）
    -> 页面分类
        biz_search: 直接接管
        biz_home/learning: 跳转到 bizr.tendata.cn/search#/index
        unknown: 点击"商情发现"或跳转
        login: 中止
    -> 商情发现搜索页
        -> 输入 customer_name 搜索
        -> 搜索结果页
            -> 选取 top1 + 国家校验
            -> 企业详情页
                -> 提取公司信息
                -> 进口分析页
                    -> 提取进口记录
                    -> 计算 match_status/confidence
                    -> 生成结果行
```

## 标签页管理

- 优先复用当前活动标签页
- 不打开无关标签页（如学习中心、通知页）
- 如果系统自动弹出学习中心/通知页，忽略并回到 biz_search 页继续

## 不做自动登录

- 不输入账号密码
- 不处理验证码
- 不尝试自动过验证码
- 只复用用户已经登录好的浏览器会话
