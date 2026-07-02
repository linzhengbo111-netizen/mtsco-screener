"""
queue_worker.py — 任务队列消费器

核心约束：同一时间只允许 1 个腾道任务运行。
通过 task_store（SQLite）出队并持久化结果。

职责：
  - 轮询 task_store.dequeue() 获取 QUEUED 任务
  - 执行抓取 + 报告生成
  - 将结果和最终状态写回 task_store

用法：
  python scripts/queue_worker.py
  # 或作为后台进程运行
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from datetime import datetime
import json

# 确保能导入同级模块
sys.path.insert(0, str(Path(__file__).parent))

from models import TaskStatus, OUTPUT_DIR
from task_store import task_store


def execute_task(task_id: str):
    """从 task_store 加载任务、执行、写回结果。

    所有状态更新通过 task_store 持久化。
    """
    # 先加载任务判断模式
    task = task_store.get(task_id)
    if task is None:
        print(f"[QueueWorker] 任务 {task_id} 不存在")
        task_store.update_status(task_id, TaskStatus.FAILED.value,
                                 error_code="TASK_NOT_FOUND",
                                 error_message="task not found in store")
        return

    # HS 自动全量模式：quick_search → enrich_selected(all)
    if getattr(task, 'enrich_mode', 'company_name') == "hs_auto_enrich":
        _execute_hs_auto_enrich(task_id, task)
        return

    # 默认：公司名称搜索模式
    from run_batch import run_batch_for_task
    from generate_report import generate_task_report, generate_customer_report
    from models import Task, CustomerInput

    # 1. 从数据库加载任务
    task = task_store.get(task_id)
    if task is None:
        print(f"[QueueWorker] 任务 {task_id} 不存在")
        task_store.update_status(task_id, TaskStatus.FAILED.value,
                                 error_code="TASK_NOT_FOUND",
                                 error_message="task not found in store")
        return

    print(f"\n[QueueWorker] 开始执行任务 {task.task_id} ({len(task.customers)} 家客户)")

    try:
        # 2. 调用抓取内核
        results = run_batch_for_task(task)
        task.results = results

        # 序列化结果
        results_json = json.dumps([r.to_dict() for r in results], ensure_ascii=False)

        # 3. 生成报告
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = ""
        report_path = ""
        excel_path = ""

        if task.generate_report:
            json_path, md_path = generate_task_report(task, OUTPUT_DIR)
            task.json_path = json_path
            task.report_path = md_path
            print(f"  [报告] JSON: {json_path}")
            print(f"  [报告] Markdown: {md_path}")

        # 为每条结果生成独立 Markdown 报告
        for r in results:
            if r.matched_company_name:
                try:
                    rp = generate_customer_report(r, OUTPUT_DIR)
                    print(f"  [报告] 客户报告: {rp}")
                except Exception as e:
                    print(f"  [报告] 生成失败: {e}")

        # 4. 更新最终状态
        # 只要抓取内核未抛异常，子任务就算 COMPLETED。
        # 匹配置信度由 result.match_status 体现，不影响子任务状态。
        final_status = TaskStatus.COMPLETED.value

        task_store.update_status(
            task_id,
            final_status,
            finished_at=datetime.now().isoformat(),
            results_json=results_json,
            json_path=json_path,
            report_path=task.report_path,
            excel_path=excel_path,
        )

        print(f"[QueueWorker] 任务 {task_id} 完成 — {final_status}")

        # 5. 结果回传（webhook / feishu / poll）
        task = task_store.get(task_id)
        if task and task.callback_mode:
            try:
                from callback import send_callback
                ok = send_callback(task)
                print(f"  [回传] {'成功' if ok else '失败'} (mode={task.callback_mode})")
            except Exception as e:
                print(f"  [回传] 异常: {e}")

        # 6. 批次完成检查（如果子任务属于某批次）
        if task and task.parent_batch_id:
            _check_batch_completion(task.parent_batch_id)

    except Exception as e:
        task_store.update_status(
            task_id,
            TaskStatus.FAILED.value,
            finished_at=datetime.now().isoformat(),
            error_code="TEN_SINGLE_FAIL",
            error_message=str(e)[:500],
        )
        print(f"[QueueWorker] 任务 {task_id} 失败: {e}")

        # 批次完成检查（如果子任务属于某批次）
        task = task_store.get(task_id)
        if task and task.parent_batch_id:
            _check_batch_completion(task.parent_batch_id)


def _execute_hs_auto_enrich(task_id: str, task):
    """HS 编码自动全量模式：quick_search → enrich_selected(all)。

    直接从 task 的 customers 中提取 hs_code 和 country，
    执行 quick_search + enrich_selected 全流程。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from extract_tendata_fields import hs_quick_search, hs_enrich_selected, _close_scraper
    from models import TaskStatus
    from generate_report import generate_task_report, generate_customer_report

    print(f"\n[QueueWorker] HS 自动模式 {task_id}")

    # 从 customers 取 HS 编码和国家
    hs_code = ""
    country = ""
    if task.customers:
        c = task.customers[0]
        hs_code = c.product_keywords or c.customer_name  # 优先 product_keywords
        country = c.country_region

    if not hs_code:
        task_store.update_status(
            task_id, TaskStatus.FAILED.value,
            finished_at=datetime.now().isoformat(),
            error_code="INPUT_INVALID",
            error_message="hs_code required (set via product_keywords or customer_name)",
        )
        return

    print(f"  [HS自动] hs_code={hs_code}, country={country}")

    try:
        # 阶段 1：快速搜索
        cards = hs_quick_search(
            hs_code=hs_code,
            country_filter=country,
            max_companies=task.batch_size,
            headless=False,
            batch_id=task_id,
        )

        if not cards:
            task_store.update_status(
                task_id, TaskStatus.PARTIAL_FAILED.value,
                finished_at=datetime.now().isoformat(),
                results_json="[]",
            )
            _close_scraper()
            return

        print(f"  [HS自动] quick_search 返回 {len(cards)} 家，开始全量深挖")

        # 阶段 2：全量深挖
        rows = hs_enrich_selected(
            quick_results=cards,
            selections=None,  # 全部展开
            batch_id=task_id,
        )

        results_json = json.dumps([r.to_dict() for r in rows], ensure_ascii=False)

        # 生成报告
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        json_path = ""
        report_path = ""
        excel_path = ""

        if task.generate_report:
            json_path, md_path = generate_task_report(task, OUTPUT_DIR)
            task.json_path = json_path
            task.report_path = md_path

        for r in rows:
            if r.matched_company_name:
                try:
                    rp = generate_customer_report(r, OUTPUT_DIR)
                    print(f"  [报告] 客户报告: {rp}")
                except Exception as e:
                    print(f"  [报告] 生成失败: {e}")

        task_store.update_status(
            task_id,
            TaskStatus.COMPLETED.value,
            finished_at=datetime.now().isoformat(),
            results_json=results_json,
            json_path=json_path,
            report_path=task.report_path,
            excel_path=excel_path,
        )

        print(f"[QueueWorker] HS 自动模式 {task_id} 完成 — {len(rows)} 家")

        # 回传
        task = task_store.get(task_id)
        if task and task.callback_mode:
            try:
                from callback import send_callback
                ok = send_callback(task)
                print(f"  [回传] {'成功' if ok else '失败'}")
            except Exception as e:
                print(f"  [回传] 异常: {e}")

        if task and task.parent_batch_id:
            _check_batch_completion(task.parent_batch_id)

        _close_scraper()

    except Exception as e:
        task_store.update_status(
            task_id, TaskStatus.FAILED.value,
            finished_at=datetime.now().isoformat(),
            error_code="TEN_SINGLE_FAIL",
            error_message=str(e)[:500],
        )
        print(f"[QueueWorker] HS 自动模式 {task_id} 失败: {e}")
        _close_scraper()


def _check_batch_completion(batch_id: str):
    """检查批次是否所有子任务都已完成，如是则更新批次状态并回传。"""
    try:
        batch = task_store.get_batch(batch_id)
        if not batch:
            return

        completed = 0
        failed = 0
        for sub_id in batch.sub_task_ids:
            t = task_store.get(sub_id)
            if t is None:
                continue  # 子任务还未创建，跳过
            s = t.status.value if isinstance(t.status, TaskStatus) else str(t.status)
            if s in ("completed", "partial_failed"):
                completed += 1
            elif s == "failed":
                failed += 1

        if completed + failed == batch.total:
            final = TaskStatus.COMPLETED.value if failed == 0 else TaskStatus.PARTIAL_FAILED.value
            task_store.update_batch_status(
                batch_id, final, finished_at=datetime.now().isoformat()
            )
            print(f"[QueueWorker] 批次 {batch_id} 全部完成 — {final} ({completed} 成功, {failed} 失败)")

            # 批次统一回传
            if batch.callback_mode:
                try:
                    from callback import send_batch_callback
                    ok = send_batch_callback(batch)
                    print(f"  [批次回传] {'成功' if ok else '失败'} (batch={batch_id})")
                except Exception as e:
                    print(f"  [批次回传] 异常: {e}")
    except Exception as e:
        print(f"[QueueWorker] 批次完成检查异常 (batch={batch_id}): {e}")


def worker_loop():
    """队列消费主循环。

    从 task_store 原子出队 → 执行 → 写回结果。
    循环持续运行，直到进程被终止。
    """
    _running = True
    print("[QueueWorker] 启动，轮询 task_store 等待任务...")

    while _running:
        # 原子出队：SQLite 事务确保同一任务只被一个 worker 取出
        task = task_store.dequeue()
        if task is None:
            time.sleep(2)
            continue

        print(f"[QueueWorker] 取出任务 {task.task_id}")
        execute_task(task.task_id)

    print("[QueueWorker] 已停止")


def start_worker(daemon: bool = True):
    """在后台启动队列消费线程。"""
    t = threading.Thread(target=worker_loop, daemon=daemon)
    t.start()
    return t


if __name__ == "__main__":
    worker_loop()
