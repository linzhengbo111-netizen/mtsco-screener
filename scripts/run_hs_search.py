"""
run_hs_search.py — HS 编码两段式搜索 CLI 入口

用法：
  # 阶段 1：快速搜索（返回候选列表）
  python scripts/run_hs_search.py quick --hs-code 730723 --country 加拿大

  # 阶段 2：深挖选定公司
  python scripts/run_hs_search.py enrich --task-id HS-001 --select 1,2,3

  # 一步到位：自动全量（quick_search + enrich_all）
  python scripts/run_hs_search.py auto --hs-code 730723 --country 加拿大 --max 5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from extract_tendata_fields import (
    hs_quick_search,
    hs_enrich_selected,
    _close_scraper,
)
from task_store import task_store
from models import Task, TaskStatus


def cmd_quick(args):
    """阶段 1：HS 快速搜索。"""
    cards = hs_quick_search(
        hs_code=args.hs_code,
        country_filter=args.country or "",
        max_companies=args.max or 20,
        headless=args.headless,
        batch_id=args.task_id or "",
    )

    _close_scraper()

    if not cards:
        print("\n[结果] 未搜索到任何公司")
        return

    print(f"\n[结果] 共 {len(cards)} 家候选：\n")
    for i, c in enumerate(cards):
        print(f"  #{c['card_index']} {c['company_name'][:60]}")
        print(f"     产品: {c.get('hs_product_desc', '')[:80] or '(空)'}")
        print(f"     贸易: {c.get('hs_trade_count', 0)}次 | 供应商: {c.get('hs_supplier_count', 0)}家 | 日期: {c.get('recent_trade_date', '(无)')}")
        print()

    # 保存到 task_store
    task_id = args.task_id or f"HS-QUICK-{Path(__file__).stem}"
    task = Task(
        task_id=task_id,
        source="cli",
        customers=[],
        enrich_mode="hs_manual_select",
        generate_report=False,
    )
    task.status = TaskStatus.COMPLETED
    task.hs_candidates_json = json.dumps(cards, ensure_ascii=False)
    task_store.create(task)

    print(f"[保存] 候选列表已缓存到 task_store (task_id={task_id})")
    print(f"[下一步] python scripts/run_hs_search.py enrich --task-id {task_id} --select 1,2,3")


def cmd_enrich(args):
    """阶段 2：深挖选定公司。"""
    task = task_store.get(args.task_id)
    if not task:
        print(f"[错误] 任务 {args.task_id} 不存在，请先运行 quick 搜索")
        sys.exit(1)
    if not task.hs_candidates_json:
        print(f"[错误] 任务 {args.task_id} 没有缓存的候选列表")
        sys.exit(1)

    candidates = json.loads(task.hs_candidates_json)

    # 解析选择
    if args.select:
        selections = [int(x.strip()) for x in args.select.split(",")]
    else:
        selections = None  # 全部

    keep_browser = getattr(args, "keep_browser", False)

    print(f"[深挖] 从 {len(candidates)} 家中选择 {selections or '全部'} 家...")
    if keep_browser:
        print(f"[深挖] --keep-browser 已启用，深挖后保留浏览器窗口\n")

    rows = hs_enrich_selected(
        quick_results=candidates,
        selections=selections,
        batch_id=args.task_id,
    )

    if keep_browser:
        print(f"[深挖] keep_browser=True，不关闭浏览器窗口")
    else:
        _close_scraper()

    if not rows:
        print("\n[结果] 未深挖到任何公司")
        return

    print(f"\n[结果] 共深挖 {len(rows)} 家：\n")
    for i, r in enumerate(rows):
        print(f"  #{i+1} {r.customer_name[:60]}")
        print(f"     匹配: {r.matched_company_name[:60]}")
        print(f"     网站: {r.website_result or '(空)'}")
        print(f"     电话: {r.phone or '(空)'}")
        print(f"     进口: {r.import_active_status} | 状态: {r.match_status}")
        print()

    # 更新 task_store
    results_json = json.dumps([r.to_dict() for r in rows], ensure_ascii=False)
    task_store.update_status(
        args.task_id,
        TaskStatus.COMPLETED.value,
        finished_at=__import__("datetime").datetime.now().isoformat(),
        results_json=results_json,
    )

    print(f"[保存] 结果已写入 task_store (task_id={args.task_id})")


def cmd_auto(args):
    """一步到位：快速搜索 + 全量深挖。"""
    hs_code = args.hs_code
    country = args.country or ""
    max_companies = args.max or 20

    print(f"[HS自动] hs_code={hs_code}, country={country}, max={max_companies}\n")

    # 阶段 1
    cards = hs_quick_search(
        hs_code=hs_code,
        country_filter=country,
        max_companies=max_companies,
        headless=args.headless,
        batch_id=args.task_id or "",
    )

    if not cards:
        _close_scraper()
        print("\n[结果] 未搜索到任何公司")
        return

    print(f"\n[HS自动] quick_search 返回 {len(cards)} 家，开始全量深挖...\n")

    # 阶段 2
    rows = hs_enrich_selected(
        quick_results=cards,
        selections=None,  # 全部
        batch_id=args.task_id or "",
    )

    _close_scraper()

    print(f"\n[结果] 共深挖 {len(rows)} 家：\n")
    for i, r in enumerate(rows):
        print(f"  #{i+1} {r.customer_name[:60]}")
        print(f"     匹配: {r.matched_company_name[:60]}")
        print(f"     网站: {r.website_result or '(空)'}")
        print(f"     进口: {r.import_active_status}")
        print()


def main():
    parser = argparse.ArgumentParser(description="HS 编码两段式搜索")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # quick
    p_quick = subparsers.add_parser("quick", help="HS 快速搜索（返回候选列表）")
    p_quick.add_argument("--hs-code", required=True, help="HS 编码（如 730723）")
    p_quick.add_argument("--country", default="", help="国家过滤（如 加拿大）")
    p_quick.add_argument("--max", type=int, default=20, help="最大候选数")
    p_quick.add_argument("--task-id", default="", help="任务 ID")
    p_quick.add_argument("--headless", action="store_true", help="无头模式")
    p_quick.set_defaults(func=cmd_quick)

    # enrich
    p_enrich = subparsers.add_parser("enrich", help="深挖选定公司")
    p_enrich.add_argument("--task-id", required=True, help="quick 搜索的 task_id")
    p_enrich.add_argument("--select", default="", help="选择序号，逗号分隔（如 1,2,3，为空则全部）")
    p_enrich.add_argument("--keep-browser", action="store_true", help="深挖后保留浏览器窗口，不关闭")
    p_enrich.set_defaults(func=cmd_enrich)

    # auto
    p_auto = subparsers.add_parser("auto", help="一步到位（快速搜索 + 全量深挖）")
    p_auto.add_argument("--hs-code", required=True, help="HS 编码")
    p_auto.add_argument("--country", default="", help="国家过滤")
    p_auto.add_argument("--max", type=int, default=20, help="最大公司数")
    p_auto.add_argument("--task-id", default="", help="任务 ID")
    p_auto.add_argument("--headless", action="store_true", help="无头模式")
    p_auto.set_defaults(func=cmd_auto)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
