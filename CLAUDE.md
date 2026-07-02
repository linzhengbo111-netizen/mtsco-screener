# tendata-customer-enricher

腾道海关数据抓取与客户富化系统。从腾道平台抓取海外买家海关数据，匹配客户信息，生成分析报告。

## 项目当前状态（2025-06-29）

- 核心抓取流程已跑通：`scripts/run_batch.py` + Chrome CDP (端口 9222)
- HTTP API 服务：`scripts/task_server.py --port 8080`
- HS 编码两段式搜索已实现：quick_search → enrich_selected
- Agent 配置：`agents/openai.yaml`（tendata-customer-enrich）
- Skill 配置：`skill/tendata-batch-query/`
- **记忆体系已建立**：CLAUDE.md + memory/ 目录，每次会话自动读取，无需手动传文件
- **用户已理解**：Claude Code 是文件驱动的，不是对话驱动的。传文件=新会话，但记忆从硬盘自动加载

## 运行前必须

1. 启动 Chrome 调试模式：双击 `start_tendata_helper.bat`
2. 在浏览器中手动登录腾道（账号密码不存，验证码需人工）
3. 确认 Chrome CDP 在 http://localhost:9222 可用

## 关键文件

- 技术文档：`README.md`
- 业务操作指南：`README_business_user.md`
- 完整 Skill 说明：`SKILL.md`
- Agent 定义：`agents/openai.yaml`
- 详细提示词：`prompts/` 目录
- 业务规则参考：`references/` 目录

## 日常操作

```bash
# HTTP API 模式（推荐）
python scripts/task_server.py --port 8080

# 批处理模式
python scripts/run_batch.py --input 客户名单.xlsx

# HS 编码搜索
python scripts/run_hs_search.py auto --hs-code 730723 --country 加拿大 --max 5
```
