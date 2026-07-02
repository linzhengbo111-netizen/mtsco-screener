"""
run_task.py — 任务编排入口

供 OpenClaw / 影刀 / 人工直接调用，将输入转换为任务，
提交到 task_store（SQLite 队列），由 queue_worker 消费。

职责：
  - 解析输入（JSON / Excel / 命令行参数）
  - 校验并构建 Task 对象
  - 提交到 task_store（task_store.create → 写入 SQLite）
  - 不直接执行！执行由 queue_worker 负责

用法：
  # 方式一：从 JSON 文件创建任务
  python scripts/run_task.py --input task.json

  # 方式二：从 Excel 文件创建任务
  python scripts/run_task.py --input customers.xlsx --source manual

  # 方式三：直接指定客户名（快速测试）
  python scripts/run_task.py --name "SCOPE METALS GROUP LTD" --country Israel

  # 方式四：仅提交不等待（提交后返回，由 queue_worker 异步消费）
  python scripts/run_task.py --input task.json --submit-only

  # 方式五：同步等待执行完成（仅本地调试使用，启动内嵌 worker 线程）
  python scripts/run_task.py --input task.json --wait
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime

# 确保能导入同级模块
sys.path.insert(0, str(Path(__file__).parent))

from models import Task, TaskStatus, CustomerInput
from task_store import task_store


def task_from_json(path: str) -> Task:
    """从 JSON 文件创建任务。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    customers = []
    for c in data.get("customers", []):
        customers.append(CustomerInput(
            customer_name=c.get("customer_name", ""),
            country_region=c.get("country_region", ""),
            website=c.get("website", ""),
            email_domain=c.get("email_domain", ""),
            product_keywords=c.get("product_keywords", ""),
            internal_customer_id=c.get("internal_customer_id", ""),
        ))

    options = data.get("options", {})
    callback = data.get("callback", {})

    task = Task(
        task_id=data.get("task_id", f"TASK-{uuid.uuid4().hex[:8].upper()}"),
        source=data.get("source", "manual"),
        customers=customers,
        generate_report=options.get("generate_report", True),
        report_format=options.get("report_format", "markdown"),
        batch_size=min(options.get("batch_size", 10), 10),
        submitted_by=callback.get("submitted_by", ""),
        callback_mode=callback.get("callback_mode", ""),
        callback_target=callback.get("callback_target", ""),
    )
    return task


def task_from_excel(path: str, source: str = "manual") -> Task:
    """从 Excel 文件创建任务。"""
    from normalize_input import load_and_normalize

    df = load_and_normalize(path)
    customers = []
    for _, row in df.iterrows():
        customers.append(CustomerInput(
            customer_name=str(row.get("customer_name", "")),
            country_region=str(row.get("country_region", "")),
            website=str(row.get("website", "")),
            email_domain=str(row.get("email_domain", "")),
            product_keywords=str(row.get("product_keywords", "")),
            internal_customer_id=str(row.get("internal_customer_id", "")),
        ))

    task = Task(
        task_id=f"TASK-{uuid.uuid4().hex[:8].upper()}",
        source=source,
        customers=customers,
    )
    return task


def task_from_name(name: str, country: str = "", **kwargs) -> Task:
    """从单个客户名创建任务（快速测试用）。"""
    customer = CustomerInput(customer_name=name, country_region=country)
    task = Task(
        task_id=f"TASK-{uuid.uuid4().hex[:8].upper()}",
        source="manual",
        customers=[customer],
    )
    return task


def submit_task(task: Task) -> str:
    """提交任务到 task_store（SQLite 队列）。

    不做任何执行！任务进入 QUEUED 状态，等待 queue_worker 消费。

    Returns:
        task_id
    """
    # 校验
    valid_customers = [c for c in task.customers if c.customer_name.strip()]
    if not valid_customers:
        print("[ERROR] 没有有效的客户名")
        task.status = TaskStatus.REJECTED
        task_store.create(task)
        sys.exit(1)
    task.customers = valid_customers

    # 写入 SQLite，状态自动设为 QUEUED
    task_id = task_store.create(task)

    print(f"[提交] task_id: {task_id}")
    print(f"[提交] 状态: queued")
    print(f"[提交] 客户数: {len(task.customers)}")
    print(f"[提交] 等待 queue_worker 消费")
    print(f"[提交] 启动 worker: python scripts/queue_worker.py")
    print(f"[提交] 查询状态: python scripts/run_task.py --status {task_id}")

    return task_id


def wait_for_completion(task_id: str, timeout: int = 1800) -> Task:
    """轮询等待任务完成（仅本地调试使用）。

    启动内嵌 worker 线程并阻塞等待结果。
    生产环境应使用 queue_worker 后台进程。

    Returns:
        完成后的 Task 对象
    """
    # 启动内嵌 worker（后台线程）
    from queue_worker import start_worker
    worker_thread = start_worker(daemon=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        task = task_store.get(task_id)
        if task is None:
            print(f"[等待] 任务 {task_id} 不存在")
            return None
        if task.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL_FAILED,
                           TaskStatus.FAILED, TaskStatus.CANCELLED):
            return task
        time.sleep(2)

    print(f"[等待] 超时 ({timeout}s)")
    return None


def main():
    parser = argparse.ArgumentParser(description="腾道客户数据抓取 — 任务编排")
    parser.add_argument("--input", help="输入文件路径（JSON 或 Excel）")
    parser.add_argument("--source", default="manual", help="任务来源标识")
    parser.add_argument("--name", help="客户公司名（快速测试，与 --input 互斥）")
    parser.add_argument("--country", default="", help="国家/地区（与 --name 配合）")
    parser.add_argument("--submit-only", action="store_true",
                        help="仅提交任务，不等待执行（默认行为）")
    parser.add_argument("--wait", action="store_true",
                        help="提交后等待执行完成（仅本地调试使用，启动内嵌 worker 线程）")
    parser.add_argument("--status", help="查询指定任务状态（独立模式）")
    parser.add_argument("--list", action="store_true", help="列出所有任务")
    args = parser.parse_args()

    # ── 查询模式 ──
    if args.status:
        task = task_store.get(args.status)
        if not task:
            print(f"[查询] 任务 {args.status} 不存在")
            sys.exit(1)
        print(f"[查询] task_id: {task.task_id}")
        print(f"[查询] 状态: {task.status.value if isinstance(task.status, TaskStatus) else task.status}")
        print(f"[查询] 来源: {task.source}")
        print(f"[查询] 创建时间: {task.created_at}")
        print(f"[查询] 开始时间: {task.started_at or '—'}")
        print(f"[查询] 完成时间: {task.finished_at or '—'}")
        if task.results:
            for r in task.results:
                name = r.matched_company_name or r.customer_name
                print(f"  → {name}: {r.match_status} ({r.match_confidence})")
        if task.error_code:
            print(f"[查询] 错误: {task.error_code} — {task.error_message}")
        return

    # ── 列表模式 ──
    if args.list:
        tasks = task_store.list(limit=20)
        if not tasks:
            print("[列表] 无任务")
            return
        for t in tasks:
            status = t.status.value if isinstance(t.status, TaskStatus) else str(t.status)
            n = len(t.customers)
            print(f"  {t.task_id:30s} {status:15s} {n} customers  {t.created_at}")
        return

    # ── 创建任务 ──
    if args.input:
        ext = Path(args.input).suffix.lower()
        if ext == ".json":
            task = task_from_json(args.input)
        elif ext in (".xlsx", ".xls"):
            task = task_from_excel(args.input, source=args.source)
        else:
            print(f"[ERROR] 不支持的文件格式: {ext}")
            sys.exit(1)
    elif args.name:
        task = task_from_name(args.name, args.country)
    else:
        print("[ERROR] 请指定 --input 或 --name 参数")
        parser.print_help()
        sys.exit(1)

    # ── 提交到 task_store ──
    task_id = submit_task(task)

    # ── 可选：等待完成 ──
    if args.wait:
        print(f"\n[等待] 启动内嵌 worker，等待任务 {task_id} 完成...")
        result = wait_for_completion(task_id)
        if result:
            print(f"\n[结果] 任务状态: {result.status.value if isinstance(result.status, TaskStatus) else result.status}")
            print(f"[结果] Excel: {result.excel_path}")
            print(f"[结果] JSON: {result.json_path}")
            print(f"[结果] 报告: {result.report_path}")
            for r in result.results:
                name = r.matched_company_name or r.customer_name
                print(f"  → {name}: {r.match_status} ({r.match_confidence})")
        else:
            print(f"\n[结果] 任务未完成或超时")


if __name__ == "__main__":
    main()
