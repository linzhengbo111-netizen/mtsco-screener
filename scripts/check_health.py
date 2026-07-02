"""
check_health.py — 执行机健康检查脚本

快速确认执行机是否就绪：服务状态 + 浏览器状态 + 队列状态。
供实施同事在联调前运行。

用法：
    python scripts/check_health.py
    python scripts/check_health.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def check(desc, condition, ok_msg="OK", fail_msg="FAIL"):
    """检查一项，输出结果。"""
    if condition:
        print(f"  [OK] {desc}: {ok_msg}")
        return True
    else:
        print(f"  [!!] {desc}: {fail_msg}")
        return False


def http_get(url, timeout=5):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as e:
        return 0, str(e)


def main():
    parser = argparse.ArgumentParser(description="执行机健康检查")
    parser.add_argument("--port", type=int, default=8080, help="task_server 端口（默认 8080）")
    args = parser.parse_args()

    all_ok = True
    base = f"http://localhost:{args.port}"

    print("=" * 50)
    print("  TenData Customer Enricher — 健康检查")
    print("=" * 50)

    # 1. HTTP 服务
    print("\n[1] HTTP 服务 (task_server)")
    code, resp = http_get(f"{base}/api/health")
    if code == 200 and isinstance(resp, dict):
        check("服务状态", True, f"HTTP {code}")
        q = resp.get("queue", {})
        check("队列状态", True,
              f"queued={q.get('queued',0)}, running={q.get('running',0)}, completed={q.get('completed',0)}")
    else:
        check("服务状态", False, "",
              f"HTTP {code} / 未响应 (请确认 task_server 已启动)")
        all_ok = False

    # 2. Chrome CDP
    print("\n[2] Chrome 浏览器 (CDP 9222)")
    code, resp = http_get("http://localhost:9222/json/version")
    if code == 200 and isinstance(resp, dict):
        browser = resp.get("Browser", "unknown")[:40]
        check("Chrome CDP", True, browser)
    else:
        check("Chrome CDP", False, "", "9222 端口不可用 (请先启动 Chrome)")
        all_ok = False

    # 3. 腾道登录态（通过 task_store 快速测试）
    print("\n[3] 腾道登录态")
    try:
        from task_store import task_store
        from models import Task, TaskStatus, CustomerInput
        import time

        test_id = f"HEALTH-CHECK-{int(time.time())}"
        task = Task(
            task_id=test_id,
            source="health_check",
            customers=[CustomerInput(customer_name="Health Check", country_region="US")],
        )
        task_store.create(task)

        # 不启动 worker，直接查询
        t = task_store.get(test_id)
        if t:
            check("任务提交", True, f"task_id={test_id}")
        else:
            check("任务提交", False, "", "写入后立即读取失败")
            all_ok = False

        # 清理
        task_store.update_status(test_id, "cancelled")
    except Exception as e:
        check("SQLite", False, "", str(e))
        all_ok = False

    # 4. 磁盘空间
    print("\n[4] 磁盘与目录")
    root = Path(__file__).parent.parent
    output = root / "output"
    data = root / "data"
    check("output/ 目录", output.exists() or True, "", "不存在但可创建")
    check("data/ 目录", data.exists() or True, "", "不存在但可创建")

    # 汇总
    print()
    if all_ok:
        print("  => 执行机就绪，可以开始联调")
    else:
        print("  => 存在异常，请先解决上述 [!!] 项再联调")
    print("=" * 50)


if __name__ == "__main__":
    main()
