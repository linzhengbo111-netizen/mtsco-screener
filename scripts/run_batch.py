"""
run_batch.py — 腾道客户数据抓取主流程

用法:
    python scripts/run_batch.py --input <客户名单.xlsx> [--output <结果.xlsx>] [--headless]

功能:
    1. 读取 Excel 客户名单
    2. 归一化表头
    3. 取前 10 行
    4. 逐条调用腾道抓取逻辑
    5. 输出结果 Excel
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from datetime import datetime

# 确保能导入同级模块
sys.path.insert(0, str(Path(__file__).parent))

from normalize_input import load_and_normalize, validate
from extract_tendata_fields import (
    enrich_one_customer, create_result_row, _close_scraper,
    self_check_before_batch, get_and_clear_hs_extra_results,
    clean_search_keyword, get_fallback_keyword,
    BrowserContextClosedError,
)
from export_results import export_results

BATCH_LIMIT = 10


def _extract_email_domain(email: str) -> str:
    """从邮箱提取 @ 后面的域名。"""
    if not email or "@" not in email:
        return ""
    return email.split("@")[-1].strip()


def _safe_str(val, default: str = "") -> str:
    """安全转换 pandas 值，处理 NaN → default。"""
    import pandas as pd
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    s = str(val).strip()
    return s if s else default


def main():
    parser = argparse.ArgumentParser(description="腾道客户数据抓取 — 批处理主流程")
    parser.add_argument("--input", required=True, help="输入 Excel 文件路径 (.xlsx)")
    parser.add_argument("--output", default=None, help="输出 Excel 文件路径 (.xlsx)")
    parser.add_argument("--headless", action="store_true", help="无头模式运行浏览器")
    parser.add_argument("--batch-id", default=None, help="运行批次 ID（默认自动生成）")
    parser.add_argument("--batch-limit", type=int, default=0, help="最多处理行数（0=不限制，默认全部）")
    args = parser.parse_args()

    input_path = args.input
    if not Path(input_path).exists():
        print(f"[ERROR] 输入文件不存在: {input_path}")
        sys.exit(1)

    batch_id = args.batch_id or f"BATCH-{uuid.uuid4().hex[:8].upper()}"

    # ---- 1. 读取并归一化 ----
    print(f"[1/4] 读取输入文件: {input_path}")
    df = load_and_normalize(input_path)
    errors = validate(df)
    for e in errors:
        if "customer_name" in e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        else:
            print(f"[WARN] {e}")

    print(f"  识别列: {list(df.columns)}")
    print(f"  总行数: {len(df)}")

    # ---- 2. 截断（可选） ----
    limit = args.batch_limit if args.batch_limit and args.batch_limit > 0 else len(df)
    if limit < len(df):
        print(f"[INFO] 设置了 --batch-limit={limit}，仅处理前 {limit} 行")
        df = df.head(limit).reset_index(drop=True)
    else:
        print(f"  将处理全部 {len(df)} 行")

    # ---- 3. 自检 ----
    print(f"[2/5] 运行环境自检...")
    check = self_check_before_batch(headless=args.headless)
    for msg in check["messages"]:
        print(f"  {msg}")
    if not check["ok"]:
        print("")
        print("[提示] 自检未通过，请按提示操作后重试")
        sys.exit(1)
    print("  自检通过，开始抓取...")

    # ---- 4. 逐条抓取 ----
    print(f"[3/5] 启动浏览器，逐条处理 {len(df)} 家客户...")
    results = []
    has_country = "country_region" in df.columns
    consecutive_failures = 0  # 连续系统级失败计数
    max_consecutive_failures = 2  # 【V5】降低阈值，更快中止
    browser_context_closed_count = 0  # 【V5】浏览器上下文关闭计数
    batch_aborted = False  # 【V5】批次中止标记

    for i, row in df.iterrows():
        customer_name = str(row.get("customer_name", "")).strip()
        if not customer_name:
            print(f"  [{i+1}/{len(df)}] customer_name 为空，跳过")
            email_val = str(row.get("email", ""))
            email_domain_val = str(row.get("email_domain", ""))
            if not email_domain_val and email_val and "@" in email_val:
                email_domain_val = _extract_email_domain(email_val)
            results.append(create_result_row(
                customer_name="",
                country_region=str(row.get("country_region", "")),
                website_input=str(row.get("website_input", "") or row.get("website", "")),
                email_domain=email_domain_val,
                product_keywords=str(row.get("product_keywords", "")),
                internal_customer_id=str(row.get("internal_customer_id", "")),
                status="no_result",
                confidence=0,
                reason="customer_name 为空",
                batch_id=batch_id,
                has_country=has_country,
                search_keyword="",
                search_variants="",
            ))
            continue

        country = str(row.get("country_region", "")) if has_country else ""
        website = str(row.get("website_input", "") or row.get("website", ""))
        email = _safe_str(row.get("email", ""))
        email_domain = _safe_str(row.get("email_domain", ""))
        if not email_domain and email and "@" in email:
            email_domain = _extract_email_domain(email)
        product_keywords = _safe_str(row.get("product_keywords", ""))
        internal_id = _safe_str(row.get("internal_customer_id", ""))
        company_short_name = _safe_str(row.get("company_short_name", ""))
        search_variants = _safe_str(row.get("search_variants", ""))

        # 搜索词清洗：优先用公司简称，否则用公司名称；清理业务备注后缀
        search_kw = clean_search_keyword(customer_name, company_short_name)

        print(f"  [{i+1}/{len(df)}] 处理: {customer_name} (搜索词: {search_kw}, 国家: {country or '未提供'})")

        try:
            fallback_kw = get_fallback_keyword(company_short_name)
            result = enrich_one_customer(
                customer_name=customer_name,
                country_region=country,
                website=website,
                email_domain=email_domain,
                product_keywords=product_keywords,
                internal_customer_id=internal_id,
                search_keyword=search_kw,
                fallback_keyword=fallback_kw,
                search_variants=search_variants,
                has_country=has_country,
                headless=args.headless,
                batch_id=batch_id,
            )
            results.append(asdict(result))
            print(f"    → {result.match_status} (置信度: {result.match_confidence})")
            # 成功：重置连续失败计数
            consecutive_failures = 0
            browser_context_closed_count = 0  # 【V5】重置浏览器关闭计数

        except KeyboardInterrupt:
            print(f"\n  [中断] 用户手动中断")
            break

        except BrowserContextClosedError as e:
            # 【V5 新增】专门处理浏览器上下文关闭错误
            print(f"    → [严重] 浏览器上下文关闭: {e}")
            browser_context_closed_count += 1

            if browser_context_closed_count >= 2:
                # 连续 2 次 browser_context_closed，中止批次
                print(f"\n  [严重] 连续 {browser_context_closed_count} 次浏览器上下文关闭，暂停批次")
                print(f"  [严重] 已完成 {len(results)} 个客户，剩余客户请重跑")
                # 当前客户写入 system_error
                err_row = create_result_row(
                    customer_name=customer_name,
                    country_region=country,
                    website_input=website,
                    email_domain=email_domain,
                    product_keywords=product_keywords,
                    internal_customer_id=internal_id,
                    status="system_error",
                    confidence=0,
                    reason="browser_context_closed",
                    batch_id=batch_id,
                    has_country=has_country,
                    search_keyword=search_kw,
                    search_variants=search_variants,
                )
                results.append(asdict(err_row))
                batch_aborted = True
                break
            else:
                # 第一次，尝试恢复并重试当前客户
                print(f"    → 尝试恢复浏览器并重试...")
                # 写入当前失败记录
                err_row = create_result_row(
                    customer_name=customer_name,
                    country_region=country,
                    website_input=website,
                    email_domain=email_domain,
                    product_keywords=product_keywords,
                    internal_customer_id=internal_id,
                    status="system_error",
                    confidence=0,
                    reason="browser_context_closed (将重试)",
                    batch_id=batch_id,
                    has_country=has_country,
                    search_keyword=search_kw,
                    search_variants=search_variants,
                )
                results.append(asdict(err_row))

        except Exception as e:
            err_reason = str(e)[:200]
            print(f"    → 异常: {err_reason}")

            # 判断是否为系统级失败（登录失效、浏览器断开等）
            err_lower = err_reason.lower()
            is_system_failure = any(
                kw in err_lower for kw in
                ["login", "登录", "helper", "disconnected", "connection refused",
                 "browser", "chrome", "cdp", "websocket", "page closed",
                 "target closed", "session closed", "not connected",
                 "腾道未登录", "腾道登录态", "context", "pages"]
            )

            if is_system_failure:
                consecutive_failures += 1
                browser_context_closed_count += 1  # 也计入浏览器关闭计数
                if consecutive_failures >= max_consecutive_failures or browser_context_closed_count >= 2:
                    print(f"\n  [系统错误] 连续系统级失败，中止整批")
                    # 先写入当前失败客户的结果
                    err_row = create_result_row(
                        customer_name=customer_name,
                        country_region=country,
                        website_input=website,
                        email_domain=email_domain,
                        product_keywords=product_keywords,
                        internal_customer_id=internal_id,
                        status="system_error",
                        confidence=0,
                        reason=f"系统级异常: {err_reason[:80]}",
                        batch_id=batch_id,
                        has_country=has_country,
                        search_keyword=search_kw,
                        search_variants=search_variants,
                    )
                    results.append(asdict(err_row))
                    batch_aborted = True
                    break
            else:
                # 单客户级失败，重置连续计数，继续处理下一个
                consecutive_failures = 0
                browser_context_closed_count = 0

            err_row = create_result_row(
                customer_name=customer_name,
                country_region=country,
                website_input=website,
                email_domain=email_domain,
                product_keywords=product_keywords,
                internal_customer_id=internal_id,
                status="detail_page_failed",
                confidence=0,
                reason=f"抓取异常: {err_reason[:80]}",
                batch_id=batch_id,
                has_country=has_country,
                search_keyword=search_kw,
                search_variants=search_variants,
            )
            results.append(asdict(err_row))

    # ---- 5. 关闭浏览器 ----
    print("[4/5] 关闭浏览器连接...")
    _close_scraper()

    # ---- 6. 导出结果 ----
    print(f"[5/5] 导出结果...")
    output_path = export_results(results, output_path=args.output, batch_id=batch_id)
    print(f"  结果已保存: {output_path}")

    # ---- 统计 ----
    status_counts = {}
    for r in results:
        s = r.get("match_status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
    print("  状态统计:")
    for s, c in status_counts.items():
        print(f"    {s}: {c} 条")
    print(f"  批次 ID: {batch_id}")


def run_batch_for_task(task) -> list:
    """供 queue_worker 调用的抓取入口。

    从 Task 对象中提取客户列表，逐条调用抓取内核，
    返回 EnrichmentResult 列表（不导出 Excel，由上层处理）。

    Args:
        task: Task 对象（来自 models.py）

    Returns:
        list[EnrichmentResult]
    """
    from models import CustomerInput, EnrichmentResult

    batch_id = task.task_id
    customers = task.customers[:task.batch_size]
    has_country = any(c.country_region for c in customers)

    print(f"  [内核] 批次 {batch_id}, 客户数 {len(customers)}")

    # 自检
    print(f"  [内核] 环境自检...")
    check = self_check_before_batch(headless=False)
    for msg in check["messages"]:
        print(f"    {msg}")
    if not check["ok"]:
        print("  [内核] 自检未通过，中止")
        task.error_code = "TEN_LOGIN_REQUIRED"
        task.error_message = "腾道登录态无效"
        return []

    results = []
    consecutive_failures = 0  # 连续系统级失败计数
    max_consecutive_failures = 3
    for i, c in enumerate(customers):
        print(f"  [内核] [{i+1}/{len(customers)}] 处理: {c.customer_name}")
        try:
            er = enrich_one_customer(
                customer_name=c.customer_name,
                country_region=c.country_region,
                website=c.website,
                email_domain=c.email_domain,
                product_keywords=c.product_keywords,
                internal_customer_id=c.internal_customer_id,
                has_country=has_country,
                headless=False,
                batch_id=batch_id,
            )
            # 转换为 EnrichmentResult
            from dataclasses import asdict
            rd = asdict(er)
            result = EnrichmentResult(**{k: rd.get(k, "") for k in EnrichmentResult.__dataclass_fields__})
            results.append(result)

            # HS 搜索额外结果：enrich_one_customer 返回 top1，其余存入全局缓存
            extra = get_and_clear_hs_extra_results()
            for extra_er in extra:
                extra_rd = asdict(extra_er)
                extra_result = EnrichmentResult(**{k: extra_rd.get(k, "") for k in EnrichmentResult.__dataclass_fields__})
                results.append(extra_result)
                print(f"  [内核]   + HS 额外公司: '{extra_result.customer_name[:50]}'")

            # 成功：重置连续失败计数
            consecutive_failures = 0

        except KeyboardInterrupt:
            print(f"\n  [内核] 用户手动中断")
            break
        except Exception as e:
            err_reason = str(e)[:200]
            print(f"  [内核] [{i+1}/{len(customers)}] 异常: {err_reason}")

            # 判断是否为系统级失败
            err_lower = err_reason.lower()
            is_system_failure = any(
                kw in err_lower for kw in
                ["login", "登录", "helper", "disconnected", "connection refused",
                 "browser", "chrome", "cdp", "websocket", "page closed",
                 "target closed", "session closed", "not connected",
                 "腾道未登录", "腾道登录态"]
            )

            if is_system_failure:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    print(f"\n  [内核] 连续 {consecutive_failures} 次系统级失败，中止整批")
                    results.append(EnrichmentResult(
                        customer_name=c.customer_name,
                        country_region=c.country_region,
                        internal_customer_id=c.internal_customer_id,
                        match_status="detail_page_failed",
                        error_message=f"系统级异常: {err_reason[:100]}",
                    ))
                    break
            else:
                consecutive_failures = 0

            results.append(EnrichmentResult(
                customer_name=c.customer_name,
                country_region=c.country_region,
                internal_customer_id=c.internal_customer_id,
                match_status="detail_page_failed",
                error_message=f"抓取异常: {err_reason[:100]}",
            ))

    _close_scraper()
    return results


if __name__ == "__main__":
    main()
