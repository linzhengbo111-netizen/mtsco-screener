"""
task_server.py — HTTP 任务接口

提供 RESTful API，供 OpenClaw / 影刀 / 上游系统调用。
所有任务操作通过 task_store（SQLite）持久化。

端点：
  POST /api/task/create   — 创建并提交任务（写入 SQLite，进入队列）
  GET  /api/task/status   — 查询任务状态
  GET  /api/task/result   — 获取任务结果
  GET  /api/task/list     — 列出任务
  GET  /api/health        — 健康检查（含队列统计）
  POST /api/task/cancel   — 取消任务

用法：
  python scripts/task_server.py [--port 8080]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uuid
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from models import Task, TaskStatus, CustomerInput, BatchTask
from task_store import task_store


# ── HS 输出格式化 ────────────────────────────────────────────────────

def _format_quick_summary(c: dict) -> str:
    """生成自然语言摘要：进口次数 X / 供应商 Y / 最近进口 DATE / 产品: DESC"""
    parts = []
    trade = c.get("hs_trade_count", 0)
    supplier = c.get("hs_supplier_count", 0)
    date = c.get("recent_trade_date", "")
    product = c.get("hs_product_desc", "")
    parts.append(f"进口次数 {trade}")
    parts.append(f"供应商 {supplier}")
    parts.append(f"最近进口 {date}" if date else "暂无进口日期")
    parts.append(f"产品: {product}" if product else "")
    return " / ".join(p for p in parts if p)


def _format_quick_text_preview(c: dict) -> str:
    """生成 200-300 字符的卡片摘要预览。"""
    name = c.get("company_name", "")
    product = c.get("hs_product_desc", "")
    trade = c.get("hs_trade_count", 0)
    supplier = c.get("hs_supplier_count", 0)
    date = c.get("recent_trade_date", "")
    summary = c.get("summary", "")
    preview = f"{name}"
    if product:
        preview += f" — 主营 {product}"
    preview += f"。贸易 {trade} 次，供应商 {supplier} 家"
    if date:
        preview += f"，最近进口 {date}"
    if summary:
        preview += f"。{summary}"
    # 截断到 200-300 字符
    if len(preview) > 280:
        preview = preview[:277] + "..."
    return preview


def _format_enrich_comments(r: dict) -> str:
    """从导入数据生成自然语言备注：进口次数 X / 供应商 Y / 主要进口国"""
    parts = []
    # 进口总次数
    vol = r.get("total_import_volume", "")
    if vol:
        parts.append(f"进口次数 {vol}")
    # 最近进口日期
    latest = r.get("latest_import_date", "")
    if latest:
        parts.append(f"最近进口 {latest}")
    # 主要进口国家（从 top_3_import_countries_json 解析）
    countries_json = r.get("top_3_import_countries_json", "")
    if countries_json:
        try:
            countries = json.loads(countries_json)
            if countries:
                country_names = [c.get("country", c) if isinstance(c, dict) else c for c in countries[:3]]
                parts.append(" / ".join(country_names))
        except (json.JSONDecodeError, AttributeError):
            pass
    # 如果没有有效数据，使用 import_activity_summary
    if not parts:
        summary = r.get("import_activity_summary", "")
        if summary:
            return summary[:200]
        return "暂无进口活动记录"
    return " / ".join(parts)


def _format_enrich_product_list(r: dict) -> list:
    """从 top_products_json 解析产品列表。"""
    products_json = r.get("top_products_json", "")
    if products_json:
        try:
            products = json.loads(products_json)
            result = []
            for p in products[:10]:  # 最多 10 个
                if isinstance(p, dict):
                    result.append(p.get("product_name", p.get("name", p.get("desc", ""))))
                else:
                    result.append(str(p))
            return [p for p in result if p]
        except (json.JSONDecodeError, AttributeError):
            pass
    return []


def _format_quick_search_output(cards: list[dict]) -> list[dict]:
    """格式化 quick_search 输出为固定字段顺序的业务友好格式。"""
    formatted = []
    for c in cards:
        formatted.append({
            "card_index": c.get("card_index"),
            "company_name": c.get("company_name"),
            "hs_product_desc": c.get("hs_product_desc", ""),
            "hs_trade_count": c.get("hs_trade_count", 0),
            "hs_supplier_count": c.get("hs_supplier_count", 0),
            "recent_trade_date": c.get("recent_trade_date", ""),
            "page_url": c.get("page_url", ""),
            "text_preview": _format_quick_text_preview(c),
            "summary": _format_quick_summary(c),
        })
    return formatted


def _format_enrich_output(results: list[dict]) -> list[dict]:
    """格式化 enrich_selected 输出为固定字段顺序的业务友好格式。"""
    formatted = []
    for r in results:
        formatted.append({
            "card_index": r.get("card_index"),
            "company_name": r.get("customer_name", ""),
            "website": r.get("website_result", ""),
            "phone": r.get("phone", ""),
            "address": r.get("address", ""),
            "LinkedIn": r.get("linkedin", ""),
            "import_status": r.get("import_active_status", "unknown"),
            "product_list": _format_enrich_product_list(r),
            "last_import_date": r.get("latest_import_date", ""),
            "comments": _format_enrich_comments(r),
        })
    return formatted


# ── HS 端点 ──────────────────────────────────────────────────────────

def _hs_quick_search_endpoint(data: dict) -> tuple[int, dict]:
    """执行 HS 快速搜索，返回候选列表。

    注意：不关闭浏览器，保留搜索结果页上下文供 hs_enrich_selected 复用。
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent))
    from extract_tendata_fields import hs_quick_search

    hs_code = data.get("hs_code", "").strip()
    if not hs_code:
        return 400, {"error": "hs_code is required"}

    country_filter = data.get("country_filter", "").strip()
    max_companies = min(data.get("max_companies", 20), 20)
    task_id = data.get("task_id", f"HS-QUICK-{uuid.uuid4().hex[:8].upper()}")

    print(f"  [HS快速API] task_id={task_id}, hs_code={hs_code}, country={country_filter}")

    cards = hs_quick_search(
        hs_code=hs_code,
        country_filter=country_filter,
        max_companies=max_companies,
        headless=False,
        batch_id=task_id,
    )

    # ★ 不关闭浏览器！保留搜索结果页上下文，供 hs_enrich_selected 直接复用
    # 避免 enrich_selected 重复执行 HS 搜索

    # 格式化输出（固定字段顺序 + 自然语言摘要）
    clean_candidates = _format_quick_search_output(cards)

    # 保存原始候选列表到 task_store（含内部上下文字段）
    candidates_json = json.dumps(cards, ensure_ascii=False)
    task = Task(
        task_id=task_id,
        source=data.get("source", "api"),
        customers=[],  # HS 搜索不使用传统 customers
        enrich_mode="hs_manual_select",
        generate_report=False,
    )
    task.status = TaskStatus.COMPLETED
    task.hs_candidates_json = candidates_json
    task_store.create(task)

    return 200, {
        "task_id": task_id,
        "status": "completed",
        "mode": "hs_quick_search",
        "total_candidates": len(clean_candidates),
        "candidates": clean_candidates,
        "browser_session": "active — call /api/task/hs_cancel to close",
    }


def _hs_enrich_selected_endpoint(data: dict) -> tuple[int, dict]:
    """对选定的 HS 候选公司进行深度挖掘。

    优先复用 quick_search 保留的浏览器搜索结果页，不重复触发 HS 搜索。
    仅在页面上下文丢失时才重建搜索。

    参数 keep_browser（默认 False）：
      True  = 深挖完成后保留浏览器窗口，供后续操作继续使用
      False = 深挖完成后自动关闭浏览器（默认行为，释放资源）
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent))
    from extract_tendata_fields import hs_enrich_selected, _close_scraper

    task_id = data.get("task_id", "").strip()
    if not task_id:
        return 400, {"error": "task_id is required (from hs_quick_search)"}

    # 从 task_store 获取 quick_search 缓存（含内部上下文字段）
    task = task_store.get(task_id)
    if not task:
        return 404, {"error": f"task {task_id} not found, run hs_quick_search first"}
    if not task.hs_candidates_json:
        return 400, {"error": f"task {task_id} has no cached candidates"}

    candidates = json.loads(task.hs_candidates_json)
    selections = data.get("selections")  # None = 全部
    keep_browser = data.get("keep_browser", False)

    # 从卡片中提取 hs_code / country_filter 供重建搜索使用
    hs_code = candidates[0].get("_hs_code", "")
    country_filter = candidates[0].get("_country_filter", "")

    print(f"  [HS深挖API] task_id={task_id}, selections={selections}, keep_browser={keep_browser}, hs_code={hs_code}, country={country_filter}")
    print(f"  [HS深挖API] 候选 {len(candidates)} 家，将深挖 {len(selections) if selections else len(candidates)} 家")

    try:
        rows = hs_enrich_selected(
            quick_results=candidates,
            selections=selections,
            hs_code=hs_code,
            country_filter=country_filter,
            batch_id=task_id,
        )
    except Exception as e:
        _close_scraper()  # 异常时始终清理浏览器，确保安全
        raise

    # ★ 根据 keep_browser 参数决定是否关闭浏览器
    if keep_browser:
        print(f"  [HS深挖API] keep_browser=True，保留浏览器窗口")
        browser_status = "active — call /api/task/hs_cancel to close"
    else:
        _close_scraper()
        browser_status = "closed"

    # 更新 task_store 结果（保存完整原始数据）
    raw_results = []
    for r in rows:
        raw_results.append(r.to_dict())

    results_json = json.dumps(raw_results, ensure_ascii=False)
    task_store.update_status(
        task_id,
        TaskStatus.COMPLETED.value,
        finished_at=datetime.now().isoformat(),
        results_json=results_json,
    )

    # 格式化输出为业务友好格式（固定字段顺序）
    business_results = _format_enrich_output(raw_results)

    return 200, {
        "task_id": task_id,
        "status": "completed",
        "total_enriched": len(business_results),
        "results": business_results,
        "browser_session": browser_status,
    }


def _json_response(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _parse_query(path: str) -> dict:
    """解析 URL query 参数。"""
    return urllib.parse.parse_qs(urllib.parse.urlparse(path).query)


def _task_from_json(data: dict) -> Task:
    """从请求 JSON 构建 Task。"""
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
        source=data.get("source", "api"),
        customers=customers,
        generate_report=options.get("generate_report", True),
        report_format=options.get("report_format", "markdown"),
        batch_size=min(options.get("batch_size", 10), 10),
        submitted_by=callback.get("submitted_by", ""),
        callback_mode=callback.get("callback_mode", ""),
        callback_target=callback.get("callback_target", ""),
    )
    return task


def _validate_task(task: Task) -> str | None:
    """校验任务，返回错误信息或 None。"""
    valid_customers = [c for c in task.customers if c.customer_name.strip()]
    if not valid_customers:
        return "no valid customers (customer_name required)"
    if len(task.customers) > 50:
        return "too many customers (max 50)"
    return None


class TaskHandler(BaseHTTPRequestHandler):
    """处理 HTTP 请求。"""

    def log_message(self, format, *args):
        pass

    def _send(self, code: int, body: str, content_type: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        # ── 健康检查 ──
        if self.path == "/api/health":
            stats = task_store.stats()
            self._send(200, _json_response({"status": "ok", "queue": stats}))
            return

        # ── 查询状态 ──
        if self.path.startswith("/api/task/status"):
            params = _parse_query(self.path)
            task_id = params.get("task_id", [None])[0]
            if not task_id:
                self._send(400, _json_response({"error": "missing task_id"}))
                return
            task = task_store.get(task_id)
            if not task:
                self._send(404, _json_response({"error": "task not found"}))
                return
            self._send(200, _json_response(task.to_dict()))
            return

        # ── 查询结果 ──
        if self.path.startswith("/api/task/result"):
            params = _parse_query(self.path)
            task_id = params.get("task_id", [None])[0]
            if not task_id:
                self._send(400, _json_response({"error": "missing task_id"}))
                return
            task = task_store.get(task_id)
            if not task:
                self._send(404, _json_response({"error": "task not found"}))
                return
            if task.status not in (TaskStatus.COMPLETED, TaskStatus.PARTIAL_FAILED):
                self._send(200, _json_response({
                    "task_id": task_id,
                    "status": task.status.value if isinstance(task.status, TaskStatus) else task.status,
                    "message": "task not yet completed",
                }))
                return
            self._send(200, _json_response({
                "task_id": task.task_id,
                "status": "completed",
                "results": [r.to_dict() for r in task.results],
                "artifacts": {
                    "excel_path": task.excel_path,
                    "json_path": task.json_path,
                    "report_path": task.report_path,
                },
            }))
            return

        # ── 列出任务 ──
        if self.path.startswith("/api/task/list"):
            params = _parse_query(self.path)
            status = params.get("status", [None])[0]
            tasks = task_store.list(status=status)
            self._send(200, _json_response({
                "tasks": [t.to_dict() for t in tasks],
                "count": len(tasks),
            }))
            return

        # ── 批次状态 ──
        if self.path.startswith("/api/batch/status"):
            params = _parse_query(self.path)
            batch_id = params.get("batch_id", [None])[0]
            if not batch_id:
                self._send(400, _json_response({"error": "missing batch_id"}))
                return
            batch = task_store.get_batch(batch_id)
            if not batch:
                self._send(404, _json_response({"error": "batch not found"}))
                return
            resp = batch.to_dict()
            # 附加子任务状态概览
            sub_statuses = {}
            for sub_id in batch.sub_task_ids:
                t = task_store.get(sub_id)
                if t:
                    s = t.status.value if isinstance(t.status, TaskStatus) else str(t.status)
                    sub_statuses[sub_id] = s
            resp["sub_tasks"] = sub_statuses
            self._send(200, _json_response(resp))
            return

        # ── 批次结果 ──
        if self.path.startswith("/api/batch/result"):
            params = _parse_query(self.path)
            batch_id = params.get("batch_id", [None])[0]
            if not batch_id:
                self._send(400, _json_response({"error": "missing batch_id"}))
                return
            result = task_store.get_batch_results(batch_id)
            if result is None:
                self._send(404, _json_response({"error": "batch not found"}))
                return
            self._send(200, _json_response(result))
            return

        self._send(404, _json_response({"error": "not found"}))

    def do_POST(self):
        # ── 创建任务 ──
        if self.path == "/api/task/create":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send(400, _json_response({"error": "invalid JSON"}))
                return

            task = _task_from_json(data)

            # 校验
            err = _validate_task(task)
            if err:
                task.status = TaskStatus.REJECTED
                task_store.create(task)
                self._send(400, _json_response({
                    "task_id": task.task_id,
                    "status": "rejected",
                    "error": err,
                }))
                return

            # 创建并进入队列（写入 SQLite）
            try:
                task_store.create(task)
            except ValueError as e:
                task.status = TaskStatus.REJECTED
                task_store.create(task)
                self._send(429, _json_response({
                    "task_id": task.task_id,
                    "status": "rejected",
                    "error": str(e),
                }))
                return

            self._send(201, _json_response({
                "task_id": task.task_id,
                "status": "queued",
                "message": "task created and enqueued",
            }))
            return

        # ── 批次创建 ──
        if self.path == "/api/batch/create":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send(400, _json_response({"error": "invalid JSON"}))
                return

            batch_id = data.get("batch_id", f"BATCH-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}")

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

            if not customers:
                self._send(400, _json_response({"error": "no valid customers (customer_name required)"}))
                return
            if len(customers) > 50:
                self._send(400, _json_response({"error": "too many customers (max 50)"}))
                return

            callback = data.get("callback", {})
            batch = BatchTask(
                batch_id=batch_id,
                source=data.get("source", "api"),
                customer_inputs=customers,
                callback_mode=callback.get("callback_mode", ""),
                callback_target=callback.get("callback_target", ""),
                submitted_by=callback.get("submitted_by", ""),
            )

            try:
                batch = task_store.create_batch(batch)
            except ValueError as e:
                self._send(429, _json_response({
                    "batch_id": batch_id,
                    "status": "rejected",
                    "error": str(e),
                }))
                return

            self._send(201, _json_response({
                "batch_id": batch.batch_id,
                "status": "queued",
                "total": batch.total,
                "sub_task_ids": batch.sub_task_ids,
                "message": f"batch created with {batch.total} sub-tasks enqueued",
            }))
            return

        # ── 取消任务 ──
        if self.path == "/api/task/cancel":
            try:
                params = _parse_query(self.path)
                task_id = params.get("task_id", [None])[0]
                if not task_id:
                    self._send(400, _json_response({"error": "missing task_id"}))
                    return
                ok, msg = task_store.cancel(task_id)
                if ok:
                    self._send(200, _json_response({"task_id": task_id, "status": "cancelled"}))
                else:
                    # 任务不存在或状态不允许取消
                    task = task_store.get(task_id)
                    if task is None:
                        self._send(404, _json_response({"error": msg}))
                    else:
                        self._send(400, _json_response({"error": msg}))
            except Exception as e:
                self._send(500, _json_response({"error": f"internal error: {e}"}))
            return

        # ── HS 快速搜索 ──
        if self.path == "/api/task/hs_quick_search":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send(400, _json_response({"error": "invalid JSON"}))
                return
            code, resp = _hs_quick_search_endpoint(data)
            self._send(code, _json_response(resp))
            return

        # ── HS 深挖选定 ──
        if self.path == "/api/task/hs_enrich_selected":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send(400, _json_response({"error": "invalid JSON"}))
                return
            code, resp = _hs_enrich_selected_endpoint(data)
            self._send(code, _json_response(resp))
            return

        # ── HS 关闭浏览器（放弃两段式流程） ──
        if self.path == "/api/task/hs_cancel":
            import sys as _sys
            from pathlib import Path as _Path
            _sys.path.insert(0, str(_Path(__file__).parent))
            from extract_tendata_fields import _close_scraper
            _close_scraper()
            self._send(200, _json_response({"status": "ok", "message": "browser session closed"}))
            return

        self._send(404, _json_response({"error": "not found"}))


def main():
    parser = argparse.ArgumentParser(description="tendata-customer-enricher 任务服务")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 端口（默认 8080）")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), TaskHandler)
    print(f"Task server started on port {args.port}")
    print(f"  POST /api/batch/create          — 创建批次任务（拆单+入队）")
    print(f"  GET  /api/batch/status          — 查询批次状态")
    print(f"  GET  /api/batch/result          — 获取批次结果")
    print(f"  POST /api/task/create           — 创建任务（写入 SQLite）")
    print(f"  GET  /api/task/status           — 查询状态")
    print(f"  GET  /api/task/result           — 获取结果")
    print(f"  GET  /api/task/list             — 列出任务")
    print(f"  POST /api/task/cancel           — 取消任务")
    print(f"  POST /api/task/hs_quick_search  — HS 快速搜索（浏览器保持活跃）")
    print(f"  POST /api/task/hs_enrich_selected — HS 深挖选定（复用搜索页，完成后关闭浏览器）")
    print(f"  POST /api/task/hs_cancel        — 关闭浏览器（放弃 HS 两段式流程）")
    print(f"  GET  /api/health                — 健康检查")
    print("Press Ctrl+C to stop.\n")
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
        print("Server stopped.")
    except Exception as e:
        print(f"\nServer error: {e}", file=sys.stderr)
        server.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
