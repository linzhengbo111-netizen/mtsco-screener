# OpenClaw 批次 API 最终用法

> 验证日期：2026-04-27
> 状态：已验证通过

---

## 当前可用 BASE_URL

```
https://alike-quench-entwine.ngrok-free.dev
```

---

## 使用顺序

**create → status → result**（严格按此顺序）

1. **create** 创建批次，拿到 `batch_id`
2. **status** 轮询执行进度，直到 `completed`
3. **result** 拉取结果（超时可重试一次）

---

## 一、创建批次（create）

```
POST https://alike-quench-entwine.ngrok-free.dev/api/batch/create
Content-Type: application/json
ngrok-skip-browser-warning: true

{
  "customers": [
    {"customer_name": "公司A", "country_region": "US", "website": "example.com"},
    {"customer_name": "公司B", "country_region": "UK"}
  ],
  "source": "openclaw"
}
```

**响应示例：**
```json
{
  "batch_id": "BATCH-xxx",
  "total": 2,
  "status": "pending"
}
```

---

## 二、查询状态（status）

```
GET https://alike-quench-entwine.ngrok-free.dev/api/batch/status?batch_id=BATCH-xxx
ngrok-skip-browser-warning: true
```

**响应示例：**
```json
{
  "batch_id": "BATCH-xxx",
  "status": "completed",
  "total": 2,
  "done": 2
}
```

---

## 三、获取结果（result）

```
GET https://alike-quench-entwine.ngrok-free.dev/api/batch/result?batch_id=BATCH-xxx
ngrok-skip-browser-warning: true
```

---

## 注意事项

1. **必须带 `ngrok-skip-browser-warning: true` 请求头**，否则会被 ngrok 拦截
2. **result 请求如果超时可重试一次**，不要频繁轮询
3. **不要复用旧 batch_id**，每次 create 都会生成新的 batch_id，旧的不需要
4. `source` 字段必须为 `"openclaw"`（Hermes 版本用 `"shadowbot"`，但本次未验证）

---

## 验证结论

- OpenClaw 实例一：已通过 create / status / result 全链路测试
- OpenClaw 实例二：已通过 create / status / result 全链路测试
- 两个 OpenClaw 实例均已验证通过
