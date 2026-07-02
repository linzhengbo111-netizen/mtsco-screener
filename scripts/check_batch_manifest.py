"""
check_batch_manifest.py — 批次核对与结果清单生成

扫描输入批次和结果文件，匹配并判断哪些结果可合并。
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd


def scan_input_batches(input_dir: str) -> list[dict]:
    """扫描输入批次文件。"""
    input_path = Path(input_dir)
    batch_files = sorted(input_path.glob("tendata_all_batch_*.xlsx"))

    results = []
    for f in batch_files:
        try:
            df = pd.read_excel(f)
            batch_no = re.search(r"batch_(\d+)", f.name)
            batch_no = batch_no.group(1) if batch_no else "unknown"

            # 获取客户信息
            customer_ids = df.get("internal_customer_id", pd.Series()).dropna().astype(str).tolist()
            customer_names = df.get("customer_name", pd.Series()).dropna().astype(str).tolist()

            results.append({
                "input_batch_file": f.name,
                "input_batch_no": batch_no,
                "input_row_count": len(df),
                "input_unique_customer_count": len(set(customer_names)),
                "first_customer_id": customer_ids[0] if customer_ids else "",
                "last_customer_id": customer_ids[-1] if customer_ids else "",
                "first_customer_name": customer_names[0][:50] if customer_names else "",
                "last_customer_name": customer_names[-1][:50] if customer_names else "",
                "customer_id_set": set(customer_ids),
                "customer_name_set": set(customer_names),
            })
        except Exception as e:
            print(f"[WARN] 读取输入批次 {f.name} 失败: {e}")

    return results


def scan_result_files(root_dir: str) -> list[dict]:
    """扫描结果文件。"""
    root_path = Path(root_dir)
    # 扫描两种格式：tendata_result_BATCH-*.xlsx 和 tendata_all_batch_*.xlsx (结果格式)
    result_files = sorted(root_path.glob("tendata_result_BATCH-*.xlsx"))
    # 也检查根目录下的 tendata_all_batch_*.xlsx 是否是结果文件
    for f in root_path.glob("tendata_all_batch_*.xlsx"):
        if f not in result_files:
            # 检查是否包含结果字段
            try:
                df = pd.read_excel(f)
                if "match_status" in df.columns:
                    result_files.append(f)
            except Exception:
                pass

    results = []
    for f in result_files:
        try:
            # 获取文件修改时间
            mtime = datetime.fromtimestamp(f.stat().st_mtime)

            df = pd.read_excel(f)

            # 获取客户信息
            customer_ids = df.get("internal_customer_id", pd.Series()).dropna().astype(str).tolist()
            customer_names = df.get("customer_name", pd.Series()).dropna().astype(str).tolist()

            # match_status 分布
            status_col = "match_status"
            status_counts = df[status_col].value_counts().to_dict() if status_col in df.columns else {}

            # 检查错误信息
            error_col = "error_message"
            error_messages = ""
            if error_col in df.columns:
                error_messages = " ".join(df[error_col].dropna().astype(str).tolist())

            reason_col = "manual_review_reason"
            reason_messages = ""
            if reason_col in df.columns:
                reason_messages = " ".join(df[reason_col].dropna().astype(str).tolist())

            # 合并所有文本用于检查
            all_text = error_messages + " " + reason_messages

            # 检查各种错误标记
            has_browser_context_closed = "browser_context_closed" in all_text.lower()
            has_target_page_closed = "target page" in all_text.lower() or "context or browser has been closed" in all_text.lower()
            has_pages_zero = "pages=0" in all_text.lower() or "未找到业务页" in all_text or "保留主标签页：(none)" in all_text

            # 检查连续 system_error
            system_error_count = status_counts.get("system_error", 0)
            has_consecutive_system_error = system_error_count >= 2

            results.append({
                "result_file": f.name,
                "modified_time": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                "result_row_count": len(df),
                "result_unique_customer_count": len(set(customer_names)),
                "confirmed_count": status_counts.get("confirmed", 0),
                "likely_match_count": status_counts.get("likely_match", 0),
                "unconfirmed_count": status_counts.get("unconfirmed", 0),
                "candidate_found_not_entered_count": status_counts.get("candidate_found_not_entered", 0),
                "no_result_count": status_counts.get("no_result", 0),
                "conflict_count": status_counts.get("conflict", 0),
                "excluded_internal_record_count": status_counts.get("excluded_internal_record", 0),
                "system_error_count": system_error_count,
                "detail_page_failed_count": status_counts.get("detail_page_failed", 0),
                "has_browser_context_closed": has_browser_context_closed,
                "has_target_page_closed": has_target_page_closed,
                "has_pages_zero": has_pages_zero,
                "has_consecutive_system_error": has_consecutive_system_error,
                "customer_id_set": set(customer_ids),
                "customer_name_set": set(customer_names),
                "status_counts_str": str(status_counts),
            })
        except Exception as e:
            print(f"[WARN] 读取结果文件 {f.name} 失败: {e}")

    return results


def match_results_to_inputs(input_batches: list[dict], result_files: list[dict]) -> list[dict]:
    """将结果文件匹配到输入批次。"""
    mappings = []

    for inp in input_batches:
        inp_customers = inp["customer_name_set"]
        inp_ids = inp["customer_id_set"]

        best_match = None
        best_overlap_rate = 0
        best_overlap_count = 0

        matching_results = []

        for res in result_files:
            res_customers = res["customer_name_set"]
            res_ids = res["customer_id_set"]

            # 计算重合（优先用名称，因为ID可能缺失）
            overlap_by_name = inp_customers & res_customers
            overlap_by_id = inp_ids & res_ids

            overlap_count = max(len(overlap_by_name), len(overlap_by_id))

            if inp["input_row_count"] > 0:
                overlap_rate = overlap_count / inp["input_row_count"]
            else:
                overlap_rate = 0

            if overlap_rate >= 0.5:  # 可能匹配
                matching_results.append({
                    "result_file": res["result_file"],
                    "overlap_count": overlap_count,
                    "overlap_rate": overlap_rate,
                    "modified_time": res["modified_time"],
                    "has_errors": res["has_browser_context_closed"] or res["has_target_page_closed"] or res["has_pages_zero"],
                })

            if overlap_rate > best_overlap_rate:
                best_overlap_rate = overlap_rate
                best_overlap_count = overlap_count
                best_match = res

        # 判断匹配状态
        is_missing = best_overlap_rate < 0.95
        is_duplicate = len([m for m in matching_results if m["overlap_rate"] >= 0.95]) > 1

        # 选择最终采用的结果文件
        selected_result = ""
        if matching_results:
            # 按时间排序，选择最新的无错误文件
            valid_results = [m for m in matching_results if m["overlap_rate"] >= 0.95 and not m["has_errors"]]
            if valid_results:
                valid_results.sort(key=lambda x: x["modified_time"], reverse=True)
                selected_result = valid_results[0]["result_file"]
            elif matching_results:
                # 没有无错误的，选择最新的
                matching_results.sort(key=lambda x: x["modified_time"], reverse=True)
                selected_result = matching_results[0]["result_file"]

        mapping = {
            "input_batch_file": inp["input_batch_file"],
            "input_batch_no": inp["input_batch_no"],
            "input_row_count": inp["input_row_count"],
            "matched_result_file": best_match["result_file"] if best_match else "",
            "match_overlap_count": best_overlap_count,
            "match_overlap_rate": round(best_overlap_rate, 4),
            "all_matching_results": "; ".join([m["result_file"] for m in matching_results]),
            "is_missing_result": is_missing,
            "is_duplicate_result": is_duplicate,
            "selected_result_file": selected_result,
        }

        mappings.append(mapping)

    return mappings


def check_orphan_results(input_batches: list[dict], result_files: list[dict], mappings: list[dict]) -> list[dict]:
    """检查孤儿结果文件（无法匹配任何输入批次）。"""
    matched_files = set()
    for m in mappings:
        if m["matched_result_file"]:
            matched_files.add(m["matched_result_file"])
        if m["all_matching_results"]:
            for f in m["all_matching_results"].split("; "):
                if f:
                    matched_files.add(f)

    orphan_files = []
    for res in result_files:
        if res["result_file"] not in matched_files:
            orphan_files.append({
                "result_file": res["result_file"],
                "result_row_count": res["result_row_count"],
                "is_orphan": True,
                "orphan_reason": "无法匹配任何输入批次",
            })

    return orphan_files


def determine_merge_validity(result_files: list[dict], mappings: list[dict]) -> list[dict]:
    """判断每个结果文件是否可合并。"""
    # 构建选择结果文件集合
    selected_files = set()
    for m in mappings:
        if m["selected_result_file"]:
            selected_files.add(m["selected_result_file"])

    decisions = []
    for res in result_files:
        file_name = res["result_file"]
        is_valid = True
        reasons = []

        # 检查是否能匹配到输入批次
        matched_input = None
        for m in mappings:
            if m["matched_result_file"] == file_name or file_name in (m["all_matching_results"] or ""):
                matched_input = m
                break

        if not matched_input:
            is_valid = False
            reasons.append("orphan_result_file")

        # 检查各种错误
        if res["has_browser_context_closed"]:
            is_valid = False
            reasons.append("browser_context_closed")

        if res["has_target_page_closed"]:
            is_valid = False
            reasons.append("target_page_closed")

        if res["has_pages_zero"]:
            is_valid = False
            reasons.append("pages_zero_error")

        if res["has_consecutive_system_error"]:
            is_valid = False
            reasons.append("consecutive_system_error")

        # 检查行数是否足够
        if matched_input and res["result_row_count"] < matched_input["input_row_count"]:
            is_valid = False
            reasons.append(f"row_count_insufficient({res['result_row_count']}/{matched_input['input_row_count']})")

        # 检查重合率
        if matched_input and matched_input["match_overlap_rate"] < 0.95:
            is_valid = False
            reasons.append(f"overlap_rate_low({matched_input['match_overlap_rate']})")

        # 检查是否是被淘汰的重复文件
        if file_name not in selected_files and matched_input and matched_input["is_duplicate_result"]:
            is_valid = False
            reasons.append("duplicate_old_file")

        decisions.append({
            "result_file": file_name,
            "is_valid_for_merge": "yes" if is_valid else "no",
            "invalid_reason": "; ".join(reasons) if reasons else "",
            "result_row_count": res["result_row_count"],
            "system_error_count": res["system_error_count"],
            "has_browser_context_closed": res["has_browser_context_closed"],
            "has_target_page_closed": res["has_target_page_closed"],
            "has_pages_zero": res["has_pages_zero"],
        })

    return decisions


def generate_summary(input_batches: list[dict], result_files: list[dict],
                     mappings: list[dict], merge_decisions: list[dict]) -> dict:
    """生成汇总信息。"""
    total_input_customers = sum(b["input_row_count"] for b in input_batches)

    valid_results = [d for d in merge_decisions if d["is_valid_for_merge"] == "yes"]
    valid_result_files = [d["result_file"] for d in valid_results]

    missing_batches = [m for m in mappings if m["is_missing_result"]]
    duplicate_batches = [m for m in mappings if m["is_duplicate_result"]]

    invalid_decisions = [d for d in merge_decisions if d["is_valid_for_merge"] == "no"]

    # 计算可合并客户数
    mergeable_customers = 0
    for m in mappings:
        if m["selected_result_file"] in valid_result_files:
            mergeable_customers += m["input_row_count"]

    # 需要重跑的批次
    rerun_batches = []
    for m in mappings:
        if m["is_missing_result"] or m["selected_result_file"] not in valid_result_files:
            rerun_batches.append(m["input_batch_file"])

    return {
        "input_batch_count": len(input_batches),
        "input_customer_total": total_input_customers,
        "result_file_count": len(result_files),
        "valid_result_file_count": len(valid_result_files),
        "missing_batch_count": len(missing_batches),
        "duplicate_batch_count": len(duplicate_batches),
        "invalid_result_file_count": len(invalid_decisions),
        "mergeable_customer_count": mergeable_customers,
        "rerun_batch_files": "; ".join(rerun_batches) if rerun_batches else "无",
    }


def main():
    root_dir = Path(__file__).parent.parent
    input_dir = root_dir / "input"

    print("=" * 60)
    print("批次核对与结果清单生成")
    print("=" * 60)

    # Step 1: 扫描输入批次
    print("\n[1/4] 扫描输入批次...")
    input_batches = scan_input_batches(input_dir)
    print(f"  找到 {len(input_batches)} 个输入批次文件")

    # Step 2: 扫描结果文件
    print("\n[2/4] 扫描结果文件...")
    result_files = scan_result_files(root_dir)
    print(f"  找到 {len(result_files)} 个结果文件")

    # Step 3: 匹配
    print("\n[3/4] 匹配结果文件到输入批次...")
    mappings = match_results_to_inputs(input_batches, result_files)

    # Step 4: 判断可合并性
    print("\n[4/4] 判断哪些结果可合并...")
    merge_decisions = determine_merge_validity(result_files, mappings)

    # 生成汇总
    summary = generate_summary(input_batches, result_files, mappings, merge_decisions)

    # 输出到 Excel
    output_path = root_dir / "tendata_result_manifest.xlsx"
    print(f"\n生成清单文件: {output_path}")

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Sheet 1: Input_Batches
        input_df = pd.DataFrame([{k: v for k, v in b.items() if k not in ("customer_id_set", "customer_name_set")}
                                  for b in input_batches])
        input_df.to_excel(writer, sheet_name="Input_Batches", index=False)

        # Sheet 2: Result_Files
        result_df = pd.DataFrame([{k: v for k, v in r.items() if k not in ("customer_id_set", "customer_name_set")}
                                   for r in result_files])
        result_df.to_excel(writer, sheet_name="Result_Files", index=False)

        # Sheet 3: Batch_Result_Mapping
        mapping_df = pd.DataFrame(mappings)
        mapping_df.to_excel(writer, sheet_name="Batch_Result_Mapping", index=False)

        # Sheet 4: Merge_Decision
        decision_df = pd.DataFrame(merge_decisions)
        decision_df.to_excel(writer, sheet_name="Merge_Decision", index=False)

        # Sheet 5: Summary
        summary_df = pd.DataFrame([summary])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    # 打印汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    print(f"输入批次数: {summary['input_batch_count']}")
    print(f"输入客户总数: {summary['input_customer_total']}")
    print(f"结果文件数: {summary['result_file_count']}")
    print(f"有效结果文件数: {summary['valid_result_file_count']}")
    print(f"缺失批次数: {summary['missing_batch_count']}")
    print(f"重复批次数: {summary['duplicate_batch_count']}")
    print(f"异常结果文件数: {summary['invalid_result_file_count']}")
    print(f"最终可合并客户数: {summary['mergeable_customer_count']}")
    print(f"\n需要重跑的输入批次文件:")
    for f in summary['rerun_batch_files'].split("; "):
        if f and f != "无":
            print(f"  - {f}")

    # 打印异常文件详情
    print("\n" + "=" * 60)
    print("异常结果文件详情")
    print("=" * 60)
    for d in merge_decisions:
        if d["is_valid_for_merge"] == "no":
            print(f"\n{d['result_file']}:")
            print(f"  原因: {d['invalid_reason']}")
            print(f"  行数: {d['result_row_count']}")
            print(f"  系统错误数: {d['system_error_count']}")
            if d["has_browser_context_closed"]:
                print(f"  包含 browser_context_closed: 是")
            if d["has_target_page_closed"]:
                print(f"  包含 target_page_closed: 是")
            if d["has_pages_zero"]:
                print(f"  包含 pages_zero: 是")

    print(f"\n清单已保存: {output_path}")


if __name__ == "__main__":
    main()
