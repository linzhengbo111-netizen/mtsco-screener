"""
task_store.py — 统一任务仓库（SQLite 持久化）

所有任务状态操作的唯一入口：创建、查询、更新、出队。
task_server.py、queue_worker.py、run_task.py 必须通过本模块交互，
不能再各自维护内存任务对象。

用法：
    from task_store import TaskStore, task_store

    # 创建任务（自动写入 SQLite）
    task_store.create(task)

    # 查询
    task_store.get("TASK-XXX")

    # 更新状态
    task_store.update_status("TASK-XXX", "running", started_at=...)

    # 出队（原子操作：QUEUED → RUNNING）
    task_store.dequeue()
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import (
    Task, TaskStatus, CustomerInput, EnrichmentResult, BatchTask,
    QUEUE_DB_PATH, OUTPUT_DIR, DATA_DIR, INPUT_DIR,
)


def _ensure_dirs():
    """确保数据目录存在。"""
    QUEUE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)


def _init_db(conn: sqlite3.Connection):
    """初始化数据库表结构。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id        TEXT PRIMARY KEY,
            source         TEXT NOT NULL DEFAULT 'manual',
            status         TEXT NOT NULL DEFAULT 'queued',
            customers_json TEXT NOT NULL,
            options_json   TEXT NOT NULL DEFAULT '{}',
            callback_json  TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL,
            started_at     TEXT,
            finished_at    TEXT,
            results_json   TEXT NOT NULL DEFAULT '[]',
            excel_path     TEXT NOT NULL DEFAULT '',
            json_path      TEXT NOT NULL DEFAULT '',
            report_path    TEXT NOT NULL DEFAULT '',
            error_code     TEXT NOT NULL DEFAULT '',
            error_message  TEXT NOT NULL DEFAULT '',
            parent_batch_id TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(parent_batch_id);

        CREATE TABLE IF NOT EXISTS batches (
            batch_id       TEXT PRIMARY KEY,
            source         TEXT NOT NULL DEFAULT 'manual',
            customer_inputs_json TEXT NOT NULL,
            sub_task_ids_json TEXT NOT NULL DEFAULT '[]',
            status         TEXT NOT NULL DEFAULT 'pending',
            callback_json  TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL,
            finished_at    TEXT,
            error_code     TEXT NOT NULL DEFAULT '',
            error_message  TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
        CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at);
    """)

    # 旧库迁移：为已有 tasks 表添加 parent_batch_id 列
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_batch_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # 列已存在


class _DbPool:
    """线程安全的 SQLite 连接池（单例）。"""

    def __init__(self, db_path: Path):
        _ensure_dirs()
        self._db_path = str(db_path)
        self._local = threading.local()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（延迟初始化）。"""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            _init_db(conn)
            self._local.conn = conn
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._get_conn()
        return conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        conn = self._get_conn()
        return conn.executemany(sql, params_list)

    def commit(self):
        conn = self._get_conn()
        conn.commit()

    def row_factory(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """返回单行结果或 None。"""
        cur = self.execute(sql, params)
        return cur.fetchone()

    def rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """返回多行结果。"""
        cur = self.execute(sql, params)
        return cur.fetchall()

    def close(self):
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn


# ── 序列化 / 反序列化 ────────────────────────────────────────────────

def _serialize_task(task: Task) -> dict:
    """将 Task 对象转为数据库行字典。"""
    return {
        "task_id": task.task_id,
        "source": task.source,
        "status": task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
        "customers_json": json.dumps([
            {"customer_name": c.customer_name, "country_region": c.country_region,
             "website": c.website, "email_domain": c.email_domain,
             "product_keywords": c.product_keywords, "internal_customer_id": c.internal_customer_id}
            for c in task.customers
        ], ensure_ascii=False),
        "options_json": json.dumps({
            "generate_report": task.generate_report,
            "report_format": task.report_format,
            "batch_size": task.batch_size,
        }, ensure_ascii=False),
        "callback_json": json.dumps({
            "submitted_by": task.submitted_by,
            "callback_mode": task.callback_mode,
            "callback_target": task.callback_target,
            "delivery_status": task.delivery_status,
            "delivered_at": task.delivered_at,
        }, ensure_ascii=False),
        "created_at": task.created_at,
        "started_at": task.started_at or None,
        "finished_at": task.finished_at or None,
        "results_json": json.dumps([r.to_dict() for r in task.results], ensure_ascii=False),
        "excel_path": task.excel_path,
        "json_path": task.json_path,
        "report_path": task.report_path,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "parent_batch_id": task.parent_batch_id,
    }


def _deserialize_task(row: sqlite3.Row) -> Task:
    """从数据库行重建 Task 对象。"""
    customers = [
        CustomerInput(**c) for c in json.loads(row["customers_json"])
    ]
    options = json.loads(row["options_json"])
    callback = json.loads(row["callback_json"])

    # 兼容旧数据库（无 parent_batch_id 列）
    try:
        pbi = row["parent_batch_id"]
    except IndexError:
        pbi = ""

    task = Task(
        task_id=row["task_id"],
        source=row["source"],
        customers=customers,
        status=TaskStatus(row["status"]),
        generate_report=options.get("generate_report", True),
        report_format=options.get("report_format", "markdown"),
        batch_size=options.get("batch_size", 10),
        submitted_by=callback.get("submitted_by", ""),
        callback_mode=callback.get("callback_mode", ""),
        callback_target=callback.get("callback_target", ""),
        delivery_status=callback.get("delivery_status", ""),
        delivered_at=callback.get("delivered_at", ""),
        created_at=row["created_at"],
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"] or "",
        results=[EnrichmentResult(**r) for r in json.loads(row["results_json"])],
        excel_path=row["excel_path"],
        json_path=row["json_path"],
        report_path=row["report_path"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        parent_batch_id=pbi,
    )
    return task


# ── 批次序列化 / 反序列化 ────────────────────────────────────────────

def _serialize_batch(batch: BatchTask) -> dict:
    return {
        "batch_id": batch.batch_id,
        "source": batch.source,
        "customer_inputs_json": json.dumps([
            {"customer_name": c.customer_name, "country_region": c.country_region,
             "website": c.website, "email_domain": c.email_domain,
             "product_keywords": c.product_keywords, "internal_customer_id": c.internal_customer_id}
            for c in batch.customer_inputs
        ], ensure_ascii=False),
        "sub_task_ids_json": json.dumps(batch.sub_task_ids, ensure_ascii=False),
        "status": batch.status.value if isinstance(batch.status, TaskStatus) else str(batch.status),
        "callback_json": json.dumps({
            "submitted_by": batch.submitted_by,
            "callback_mode": batch.callback_mode,
            "callback_target": batch.callback_target,
        }, ensure_ascii=False),
        "created_at": batch.created_at,
        "finished_at": batch.finished_at or None,
        "error_code": batch.error_code,
        "error_message": batch.error_message,
    }


def _deserialize_batch(row: sqlite3.Row) -> BatchTask:
    return BatchTask(
        batch_id=row["batch_id"],
        source=row["source"],
        customer_inputs=[
            CustomerInput(**c) for c in json.loads(row["customer_inputs_json"])
        ],
        sub_task_ids=json.loads(row["sub_task_ids_json"]),
        status=TaskStatus(row["status"]),
        callback_mode=json.loads(row["callback_json"]).get("callback_mode", ""),
        callback_target=json.loads(row["callback_json"]).get("callback_target", ""),
        submitted_by=json.loads(row["callback_json"]).get("submitted_by", ""),
        created_at=row["created_at"],
        finished_at=row["finished_at"] or "",
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


# ── TaskStore ────────────────────────────────────────────────────────

class TaskStore:
    """统一任务仓库。所有任务操作的唯一入口。"""

    def __init__(self, db_path: Path = QUEUE_DB_PATH):
        self._pool = _DbPool(db_path)

    # ── 创建 ──

    def create(self, task: Task, max_queue_size: int = 50) -> str:
        """创建任务并写入 SQLite。返回 task_id。

        任务初始状态为 QUEUED，自动进入队列等待 worker 消费。

        Args:
            task: 任务对象
            max_queue_size: 队列容量上限。超过此限制时抛出 ValueError。
        """
        # 重复 task_id 保护
        existing = self.get(task.task_id)
        if existing is not None:
            raise ValueError(f"task_id 已存在: {task.task_id}，请勿重复提交同一任务")

        if task.status == TaskStatus.REJECTED:
            task.created_at = datetime.now().isoformat()
            self._persist(task)
            return task.task_id

        # 检查队列容量
        queued_count = self._queued_count()
        if queued_count >= max_queue_size:
            raise ValueError(f"队列已满 ({queued_count}/{max_queue_size})，请稍后重试")

        task.status = TaskStatus.QUEUED
        if not task.created_at:
            task.created_at = datetime.now().isoformat()
        self._persist(task)
        return task.task_id

    def _queued_count(self) -> int:
        """返回当前 QUEUED 任务数。"""
        row = self._pool.execute("SELECT COUNT(*) as c FROM tasks WHERE status = 'queued'").fetchone()
        return row["c"] if row else 0

    def _persist(self, task: Task):
        """将任务持久化到 SQLite（纯 INSERT，task_id 冲突由调用方负责检查）。"""
        d = _serialize_task(task)
        self._pool.execute("""
            INSERT INTO tasks (
                task_id, source, status, customers_json, options_json,
                callback_json, created_at, started_at, finished_at,
                results_json, excel_path, json_path, report_path,
                error_code, error_message, parent_batch_id
            ) VALUES (
                :task_id, :source, :status, :customers_json, :options_json,
                :callback_json, :created_at, :started_at, :finished_at,
                :results_json, :excel_path, :json_path, :report_path,
                :error_code, :error_message, :parent_batch_id
            )
        """, d)
        self._pool.commit()

    # ── 查询 ──

    def get(self, task_id: str) -> Optional[Task]:
        """按 task_id 查询任务。不存在时返回 None。"""
        row = self._pool.row_factory("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if row is None:
            return None
        return _deserialize_task(row)

    def list(self, status: Optional[str] = None, limit: int = 50) -> list[Task]:
        """列出任务，可选按状态过滤。"""
        if status:
            rows = self._pool.rows(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self._pool.rows(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [_deserialize_task(r) for r in rows]

    def stats(self) -> dict:
        """返回各状态任务计数。"""
        rows = self._pool.rows(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        )
        result = {
            "total": 0,
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        for r in rows:
            s = r["status"]
            c = r["cnt"]
            result["total"] += c
            if s == "queued": result["queued"] = c
            elif s == "running": result["running"] = c
            elif s in ("completed", "partial_failed"): result["completed"] += c
            elif s == "failed": result["failed"] = c
        return result

    # ── 出队（原子操作） ──

    def dequeue(self) -> Optional[Task]:
        """原子出队：将一条 QUEUED 任务改为 RUNNING，返回该 Task。

        使用 SQLite RETURNING 子句（3.35+），在一次原子 UPDATE 中
        同时完成状态更新和行返回，不依赖任何二次查询。
        这确保返回的行就是刚刚被出队的那一行，无论表中有几条 RUNNING 任务。
        """
        now = datetime.now().isoformat()

        # 原子 UPDATE + RETURNING：一次操作完成出队并返回完整行
        cur = self._pool.execute("""
            UPDATE tasks SET status = 'running', started_at = ?
            WHERE task_id = (
                SELECT task_id FROM tasks
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING *
        """, (now,))

        row = cur.fetchone()
        self._pool.commit()

        if row is None:
            return None
        return _deserialize_task(row)

    def get_by_status(self, status: str) -> Optional[Task]:
        """获取一条指定状态的任务。"""
        row = self._pool.row_factory(
            "SELECT * FROM tasks WHERE status = ? LIMIT 1",
            (status,),
        )
        if row is None:
            return None
        return _deserialize_task(row)

    def recover_stale_running(self, timeout_seconds: int = 1800) -> list[str]:
        """回收异常退出的 RUNNING 任务。

        当 worker 进程崩溃时，RUNNING 任务会永远停留在 running 状态。
        此方法将 started_at 超过 timeout_seconds 的 RUNNING 任务
        重置为 QUEUED，使其重新进入队列。

        Returns:
            被回收的 task_id 列表
        """
        import time
        cutoff = datetime.fromtimestamp(time.time() - timeout_seconds).isoformat()

        rows = self._pool.rows(
            "SELECT task_id FROM tasks WHERE status = 'running' AND started_at < ?",
            (cutoff,),
        )
        task_ids = [r["task_id"] for r in rows]
        if task_ids:
            placeholders = ",".join("?" * len(task_ids))
            self._pool.execute(
                f"UPDATE tasks SET status = 'queued', started_at = NULL WHERE task_id IN ({placeholders})",
                tuple(task_ids),
            )
            self._pool.commit()
            print(f"[task_store] 回收 {len(task_ids)} 个僵死 RUNNING 任务: {task_ids}")
        return task_ids

    # ── 状态更新 ──

    def update_status(self, task_id: str, status: str, **fields):
        """更新任务状态及可选附加字段。

        支持的附加字段：started_at, finished_at, error_code, error_message,
        excel_path, json_path, report_path, results_json, callback_json.
        """
        set_parts = ["status = ?"]
        params = [status]

        if "started_at" in fields:
            set_parts.append("started_at = ?")
            params.append(fields["started_at"])
        if "finished_at" in fields:
            set_parts.append("finished_at = ?")
            params.append(fields["finished_at"])
        if "error_code" in fields:
            set_parts.append("error_code = ?")
            params.append(fields["error_code"])
        if "error_message" in fields:
            set_parts.append("error_message = ?")
            params.append(fields["error_message"][:2000])
        if "excel_path" in fields:
            set_parts.append("excel_path = ?")
            params.append(fields["excel_path"])
        if "json_path" in fields:
            set_parts.append("json_path = ?")
            params.append(fields["json_path"])
        if "report_path" in fields:
            set_parts.append("report_path = ?")
            params.append(fields["report_path"])
        if "results_json" in fields:
            set_parts.append("results_json = ?")
            params.append(fields["results_json"])
        if "callback_json" in fields:
            set_parts.append("callback_json = ?")
            params.append(fields["callback_json"])

        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(set_parts)} WHERE task_id = ?"
        self._pool.execute(sql, tuple(params))
        self._pool.commit()

    # ── 取消 ──

    def cancel(self, task_id: str) -> tuple[bool, str]:
        """取消任务。

        仅允许取消 PENDING / QUEUED 状态的任务。
        RUNNING / COMPLETED / FAILED 状态不可取消。

        Returns:
            (success, message)
        """
        task = self.get(task_id)
        if task is None:
            return False, f"任务 {task_id} 不存在"

        cancellable_states = {TaskStatus.PENDING.value, TaskStatus.QUEUED.value,
                              "pending", "queued"}
        current = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
        if current not in cancellable_states:
            return False, f"任务处于 {current} 状态，无法取消（仅 PENDING/QUEUED 可取消）"

        self.update_status(task_id, TaskStatus.CANCELLED.value,
                          finished_at=datetime.now().isoformat())
        return True, f"任务 {task_id} 已取消"

    # ── 批次 ──

    def create_batch(self, batch: BatchTask, max_queue_size: int = 50) -> BatchTask:
        """创建批次：将多家公司拆分为单公司子任务，逐个入队。

        返回完整的 BatchTask，包含所有子任务 ID。
        """
        # 重复 batch_id 保护
        existing = self._pool.row_factory(
            "SELECT batch_id FROM batches WHERE batch_id = ?", (batch.batch_id,)
        )
        if existing:
            raise ValueError(f"batch_id 已存在: {batch.batch_id}，请勿重复提交")

        sub_task_ids = []
        for i, cust in enumerate(batch.customer_inputs):
            sub_id = f"{batch.batch_id}-C{i+1:03d}"
            sub_task = Task(
                task_id=sub_id,
                source=batch.source,
                customers=[cust],
                parent_batch_id=batch.batch_id,
                generate_report=True,
                submitted_by=batch.submitted_by,
                callback_mode="",  # 子任务不回传，由批次统一回传
            )
            # 子任务直接复用 create（写入 tasks 表，入队）
            self.create(sub_task, max_queue_size=max_queue_size)
            sub_task_ids.append(sub_id)

        batch.sub_task_ids = sub_task_ids
        batch.status = TaskStatus.QUEUED

        # 写入 batches 表
        self._persist_batch(batch)
        return batch

    def get_batch(self, batch_id: str) -> Optional[BatchTask]:
        """按 batch_id 查询批次。不存在时返回 None。"""
        row = self._pool.row_factory(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        )
        if row is None:
            return None
        return _deserialize_batch(row)

    def get_batch_results(self, batch_id: str) -> Optional[dict]:
        """获取批次所有子任务的结果汇总。

        返回：
        {
            "batch_id": ...,
            "total": 5,
            "completed": 3,
            "running": 1,
            "queued": 1,     # 排队中
            "results": [...],  # 已完成子任务的结果
        }
        """
        batch = self.get_batch(batch_id)
        if batch is None:
            return None

        completed = 0
        running = 0
        queued = 0
        results = []

        for sub_id in batch.sub_task_ids:
            task = self.get(sub_id)
            if task is None:
                queued += 1
                continue
            s = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
            if s in ("completed", "partial_failed"):
                completed += 1
                results.extend([r.to_dict() for r in task.results])
            elif s == "running":
                running += 1
            else:
                # queued / pending / cancelled / failed
                queued += 1

        return {
            "batch_id": batch_id,
            "total": batch.total,
            "completed": completed,
            "running": running,
            "queued": queued,
            "status": batch.status.value if isinstance(batch.status, TaskStatus) else batch.status,
            "results": results,
        }

    def update_batch_status(self, batch_id: str, status: str, **fields):
        """更新批次状态。"""
        set_parts = ["status = ?"]
        params = [status]

        if "finished_at" in fields:
            set_parts.append("finished_at = ?")
            params.append(fields["finished_at"])
        if "error_code" in fields:
            set_parts.append("error_code = ?")
            params.append(fields["error_code"])
        if "error_message" in fields:
            set_parts.append("error_message = ?")
            params.append(fields["error_message"][:2000])

        params.append(batch_id)
        sql = f"UPDATE batches SET {', '.join(set_parts)} WHERE batch_id = ?"
        self._pool.execute(sql, tuple(params))
        self._pool.commit()

    def list_batches(self, limit: int = 50) -> list[BatchTask]:
        """列出批次，按创建时间倒序。"""
        rows = self._pool.rows(
            "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_deserialize_batch(r) for r in rows]

    def _persist_batch(self, batch: BatchTask):
        """将批次持久化到 batches 表。"""
        self._pool.execute("""
            INSERT INTO batches (
                batch_id, source, customer_inputs_json, sub_task_ids_json,
                status, callback_json, created_at, finished_at,
                error_code, error_message
            ) VALUES (
                :batch_id, :source, :customer_inputs_json, :sub_task_ids_json,
                :status, :callback_json, :created_at, :finished_at,
                :error_code, :error_message
            )
        """, _serialize_batch(batch))
        self._pool.commit()

    # ── 关闭 ──

    def close(self):
        """关闭当前线程的数据库连接。"""
        self._pool.close()


# ── 全局单例 ──────────────────────────────────────────────────────────

task_store = TaskStore()
