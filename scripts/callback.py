"""
callback.py — 任务结果回传模块

queue_worker 完成抓取后，根据任务的 callback_mode 将结果推送给外部系统。
支持三种回传模式：webhook、feishu、poll（默认）。

用法：
    from callback import send_callback
    send_callback(task)  # 根据 task.callback_mode 自动选择回传方式
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from models import Task, TaskStatus, BatchTask

logger = logging.getLogger("tendata.callback")


def send_callback(task: Task) -> bool:
    """根据任务的 callback_mode 发送结果回传。

    Args:
        task: 已完成的任务对象

    Returns:
        True 表示回传成功或无需回传（poll 模式），False 表示回传失败
    """
    mode = task.callback_mode or "poll"

    if mode == "poll":
        # 轮询模式：外部系统自行通过 API 查询结果，无需主动推送
        logger.info(f"[callback] 任务 {task.task_id} 为 poll 模式，无需推送")
        return True

    if mode == "webhook":
        return _send_webhook(task)

    if mode == "feishu":
        return _send_feishu(task)

    logger.warning(f"[callback] 未知的 callback_mode: {mode}")
    return False


def _update_delivery(task: Task, status: str, message: str = ""):
    """更新任务的回传状态到 task_store。"""
    from task_store import task_store

    callback_data = {
        "submitted_by": task.submitted_by,
        "callback_mode": task.callback_mode,
        "callback_target": task.callback_target,
        "delivery_status": status,
        "delivered_at": datetime.now().isoformat(),
    }
    try:
        import json as _json
        task_store.update_status(
            task.task_id,
            task.status.value if isinstance(task.status, TaskStatus) else task.status,
            callback_json=_json.dumps(callback_data, ensure_ascii=False),
        )
    except Exception as e:
        logger.error(f"[callback] 更新回传状态失败: {e}")


def _send_webhook(task: Task) -> bool:
    """通用 webhook 回传。

    POST JSON 到 task.callback_target 指定的 URL。
    """
    target = task.callback_target
    if not target:
        logger.warning(f"[callback] webhook 模式缺少 callback_target")
        _update_delivery(task, "failed", "missing callback_target")
        return False

    payload = {
        "task_id": task.task_id,
        "source": task.source,
        "status": task.status.value if isinstance(task.status, TaskStatus) else task.status,
        "submitted_by": task.submitted_by,
        "customer_count": len(task.customers),
        "result_count": len(task.results),
        "results": [r.to_dict() for r in task.results],
        "artifacts": {
            "excel_path": task.excel_path,
            "json_path": task.json_path,
            "report_path": task.report_path,
        },
        "finished_at": task.finished_at,
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201, 202, 204):
                logger.info(f"[callback] webhook 推送成功 → {target}")
                _update_delivery(task, "sent")
                return True
            else:
                logger.error(f"[callback] webhook 返回 {resp.status}")
                _update_delivery(task, "failed", f"HTTP {resp.status}")
                return False
    except urllib.error.HTTPError as e:
        logger.error(f"[callback] webhook HTTP 错误: {e.code} {e.reason}")
        _update_delivery(task, "failed", f"HTTP {e.code}")
        return False
    except urllib.error.URLError as e:
        logger.error(f"[callback] webhook 连接失败: {e.reason}")
        _update_delivery(task, "failed", f"connection: {e.reason}")
        return False
    except Exception as e:
        logger.error(f"[callback] webhook 未知错误: {e}")
        _update_delivery(task, "failed", str(e)[:200])
        return False


def _send_feishu(task: Task) -> bool:
    """飞书消息回传。

    通过飞书机器人 Webhook 发送富文本卡片消息。
    callback_target 格式：https://open.feishu.cn/open-apis/bot/v2/hook/<token>
    """
    target = task.callback_target
    if not target:
        logger.warning(f"[callback] feishu 模式缺少 callback_target")
        _update_delivery(task, "failed", "missing callback_target")
        return False

    # 构建飞书卡片消息
    status = task.status.value if isinstance(task.status, TaskStatus) else task.status
    summary_lines = []
    for r in task.results[:5]:  # 最多展示 5 条
        name = r.matched_company_name or r.customer_name or "未知"
        ms = r.match_status
        mc = r.match_confidence
        summary_lines.append(f"{name} | {ms} ({mc})")

    if len(task.results) > 5:
        summary_lines.append(f"... 还有 {len(task.results) - 5} 条")

    content = "\n".join(summary_lines) if summary_lines else "无结果"

    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"腾道数据报告 — {task.task_id}",
                },
                "template": "green" if status == "completed" else "red",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**任务状态**: {status}\n"
                            f"**客户数**: {len(task.customers)}\n"
                            f"**结果数**: {len(task.results)}\n"
                            f"**报告**: {task.report_path}\n"
                            f"**JSON**: {task.json_path}\n"
                            "---\n"
                            f"{content}"
                        ),
                    },
                },
            ],
        },
    }

    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8")
            if resp.status == 200:
                data = json.loads(resp_body)
                if data.get("code") == 0:
                    logger.info(f"[callback] 飞书推送成功 → {target}")
                    _update_delivery(task, "sent")
                    return True
                else:
                    logger.error(f"[callback] 飞书返回错误: {data}")
                    _update_delivery(task, "failed", f"feishu: {data.get('msg', '')}")
                    return False
            else:
                logger.error(f"[callback] 飞书 HTTP {resp.status}")
                _update_delivery(task, "failed", f"HTTP {resp.status}")
                return False
    except Exception as e:
        logger.error(f"[callback] 飞书推送失败: {e}")
        _update_delivery(task, "failed", str(e)[:200])
        return False


def send_batch_callback(batch: BatchTask) -> bool:
    """批次完成后的统一回传。

    仅支持 webhook 和 feishu 模式，poll 模式无需推送。

    Args:
        batch: 已完成的批次对象

    Returns:
        True 表示回传成功或无需回传，False 表示回传失败
    """
    mode = batch.callback_mode or "poll"

    if mode == "poll":
        logger.info(f"[callback] 批次 {batch.batch_id} 为 poll 模式，无需推送")
        return True

    if mode == "webhook":
        return _send_batch_webhook(batch)

    if mode == "feishu":
        return _send_batch_feishu(batch)

    logger.warning(f"[callback] 未知的 batch callback_mode: {mode}")
    return False


def _send_batch_webhook(batch: BatchTask) -> bool:
    """批次 webhook 回传。"""
    target = batch.callback_target
    if not target:
        logger.warning(f"[callback] batch webhook 模式缺少 callback_target")
        return False

    from task_store import task_store
    result = task_store.get_batch_results(batch.batch_id)

    payload = {
        "batch_id": batch.batch_id,
        "source": batch.source,
        "status": batch.status.value if isinstance(batch.status, TaskStatus) else batch.status,
        "total": batch.total,
        "result_count": len(result["results"]) if result else 0,
        "results": result["results"] if result else [],
        "finished_at": batch.finished_at,
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201, 202, 204):
                logger.info(f"[callback] batch webhook 推送成功 → {target}")
                return True
            else:
                logger.error(f"[callback] batch webhook 返回 {resp.status}")
                return False
    except Exception as e:
        logger.error(f"[callback] batch webhook 错误: {e}")
        return False


def _send_batch_feishu(batch: BatchTask) -> bool:
    """批次飞书回传。"""
    target = batch.callback_target
    if not target:
        logger.warning(f"[callback] batch feishu 模式缺少 callback_target")
        return False

    from task_store import task_store
    result = task_store.get_batch_results(batch.batch_id)

    completed = result["completed"] if result else 0
    total = batch.total
    summary_lines = [f"**批次 {batch.batch_id}** 全部完成 ({completed}/{total} 成功)"]

    if result:
        for r in result["results"][:5]:
            name = r.get("matched_company_name", r.get("customer_name", "未知"))
            ms = r.get("match_status", "")
            mc = r.get("match_confidence", 0)
            summary_lines.append(f"{name} | {ms} ({mc})")
        if len(result["results"]) > 5:
            summary_lines.append(f"... 还有 {len(result['results']) - 5} 条")

    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"腾道批次报告 — {batch.batch_id}"},
                "template": "green" if batch.status == TaskStatus.COMPLETED else "orange",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "\n".join(summary_lines),
                    },
                },
            ],
        },
    }

    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8")
            if resp.status == 200:
                data = json.loads(resp_body)
                if data.get("code") == 0:
                    logger.info(f"[callback] 批次飞书推送成功 → {target}")
                    return True
                else:
                    logger.error(f"[callback] 批次飞书返回错误: {data}")
                    return False
            else:
                logger.error(f"[callback] 批次飞书 HTTP {resp.status}")
                return False
    except Exception as e:
        logger.error(f"[callback] 批次飞书推送失败: {e}")
        return False
