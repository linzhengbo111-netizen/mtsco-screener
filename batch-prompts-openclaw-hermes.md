# tendata-customer-enricher 批次 API 提示词

## 一、本地联调用（localhost:8080）

### OpenClaw

**创建批次**
```
POST http://localhost:8080/api/batch/create
Content-Type: application/json

{
  "customers": [
    {"customer_name": "公司A", "country_region": "US", "website": "example.com"},
    {"customer_name": "公司B", "country_region": "UK"}
  ],
  "source": "openclaw"
}
```

**查询状态**
```
GET http://localhost:8080/api/batch/status?batch_id={{batch_id}}
```

**获取结果**
```
GET http://localhost:8080/api/batch/result?batch_id={{batch_id}}
```

### Hermes

**创建批次**
```
POST http://localhost:8080/api/batch/create
Content-Type: application/json

{
  "customers": [
    {"customer_name": "公司A", "country_region": "US", "website": "example.com"},
    {"customer_name": "公司B", "country_region": "UK"}
  ],
  "source": "shadowbot"
}
```

**查询状态**
```
GET http://localhost:8080/api/batch/status?batch_id={{batch_id}}
```

**获取结果**
```
GET http://localhost:8080/api/batch/result?batch_id={{batch_id}}
```

---

## 二、远程执行机/正式实例（{{BASE_URL}}）

### OpenClaw

**创建批次**
```
POST {{BASE_URL}}/api/batch/create
Content-Type: application/json

{
  "customers": [
    {"customer_name": "公司A", "country_region": "US", "website": "example.com"},
    {"customer_name": "公司B", "country_region": "UK"}
  ],
  "source": "openclaw"
}
```

**查询状态**
```
GET {{BASE_URL}}/api/batch/status?batch_id={{batch_id}}
```

**获取结果**
```
GET {{BASE_URL}}/api/batch/result?batch_id={{batch_id}}
```

### Hermes

**创建批次**
```
POST {{BASE_URL}}/api/batch/create
Content-Type: application/json

{
  "customers": [
    {"customer_name": "公司A", "country_region": "US", "website": "example.com"},
    {"customer_name": "公司B", "country_region": "UK"}
  ],
  "source": "shadowbot"
}
```

**查询状态**
```
GET {{BASE_URL}}/api/batch/status?batch_id={{batch_id}}
```

**获取结果**
```
GET {{BASE_URL}}/api/batch/result?batch_id={{batch_id}}
```

---

## 三、最短通用版（查状态 / 取结果）

**查状态**
```
GET {{BASE_URL}}/api/batch/status?batch_id={{batch_id}}
```

**取结果**
```
GET {{BASE_URL}}/api/batch/result?batch_id={{batch_id}}
```
