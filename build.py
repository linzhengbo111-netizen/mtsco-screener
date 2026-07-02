"""
build.py - Build script for tendata-customer-enricher

Generates:
  dist/skill.zip                          -> ClawHub upload package
  dist/tendata-customer-enricher-user.zip -> Business user run package

Usage: python build.py
"""

import zipfile, os, pathlib, shutil

ROOT = pathlib.Path(__file__).parent
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)

# ── Pre-build cleanup ──────────────────────────────────────────────

def pre_build_cleanup():
    """Remove runtime artifacts before packaging."""
    cleaned = 0

    # 1. Delete .tendata-chrome-profile/
    profile = ROOT / ".tendata-chrome-profile"
    if profile.exists():
        try:
            shutil.rmtree(profile)
            print(f"  Cleaned: {profile}")
            cleaned += 1
        except PermissionError:
            print(f"  WARNING: {profile} is locked — Chrome may still be running.")

    # 2. Remove __pycache__/ and .pyc
    for root, dirs, files in os.walk(ROOT):
        rp = pathlib.Path(root)
        if rp.relative_to(ROOT).parts[:1] in {("dist",), (".git",)}:
            dirs.clear()
            continue
        for d in dirs:
            if d == "__pycache__":
                try:
                    shutil.rmtree(rp / d)
                    cleaned += 1
                except Exception:
                    pass
        for f in files:
            if f.endswith(".pyc"):
                try:
                    (rp / f).unlink()
                    cleaned += 1
                except Exception:
                    pass

    # 3. Remove temporary result Excel files (single_test_result_*.xlsx, tendata_result_*.xlsx)
    for pat in ["result_*.xlsx", "single_test_result*.xlsx", "tendata_result_*.xlsx"]:
        for f in ROOT.glob(pat):
            try:
                f.unlink()
                cleaned += 1
            except Exception:
                pass

    # 4. Remove output/ and logs/ directories (runtime artifacts)
    for d in ["output", "logs"]:
        dp = ROOT / d
        if dp.exists():
            try:
                shutil.rmtree(dp)
                cleaned += 1
            except Exception:
                pass

    if cleaned:
        print(f"  Pre-build cleanup: removed {cleaned} item(s)\n")
    else:
        print("  Pre-build: no runtime artifacts found\n")

pre_build_cleanup()

# ── Packaging ──────────────────────────────────────────────────────

exclude_exts = {".pyc", ".pyo"}

def make_zip(name, file_list):
    out = DIST / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in file_list:
            path = ROOT / fp
            if not path.exists():
                print(f"  SKIP (not found): {fp}")
                continue
            for ext in exclude_exts:
                if str(fp).endswith(ext):
                    print(f"  SKIP (excluded): {fp}")
                    break
            else:
                zf.write(path, fp)
    sz = out.stat().st_size
    print(f"  -> {out.name}: {sz:,} bytes ({sz/1024:.1f} KB)")
    return out

print("=== Building skill.zip ===")
make_zip("skill.zip", [
    "SKILL.md",
    "README.md",
    "README_business_user.md",
    "agents/openai.yaml",
    "references/architecture.md",
    "references/field-schema.md",
    "references/input-template.md",
    "references/io-contract.md",
    "references/matching-rules.md",
    "references/page-flow.md",
    "references/report-template.md",
    "references/task-lifecycle.md",
    "references/external-integration.md",
    "references/integration-manual.md",
    "references/troubleshooting.md",
    "references/implementation-checklist.md",
    "scripts/callback.py",
    "scripts/export_results.py",
    "scripts/extract_tendata_fields.py",
    "scripts/generate_report.py",
    "scripts/models.py",
    "scripts/normalize_input.py",
    "scripts/queue_worker.py",
    "scripts/run_batch.py",
    "scripts/run_task.py",
    "scripts/task_server.py",
    "scripts/task_store.py",
    "scripts/test_external_api.py",
    "sample_input.xlsx",
    "sample_input_chinese_headers.xlsx",
])

print("=== Building tendata-customer-enricher-user.zip ===")
make_zip("tendata-customer-enricher-user.zip", [
    "start_tendata_helper.bat",
    "run_tendata_batch.bat",
    "start_services.bat",
    "README_business_user.md",
    "scripts/export_results.py",
    "scripts/extract_tendata_fields.py",
    "scripts/generate_report.py",
    "scripts/models.py",
    "scripts/normalize_input.py",
    "scripts/queue_worker.py",
    "scripts/run_batch.py",
    "scripts/run_task.py",
    "scripts/task_server.py",
    "scripts/task_store.py",
    "scripts/callback.py",
    "scripts/check_health.py",
    "sample_input.xlsx",
    "sample_input_chinese_headers.xlsx",
])

print("\n=== Verification ===")
for name in ["skill.zip", "tendata-customer-enricher-user.zip"]:
    path = DIST / name
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        checks = {
            "inner zip": any(n.endswith(".zip") for n in names),
            ".pyc": any(n.endswith(".pyc") for n in names),
            "__pycache__": any("__pycache__" in n for n in names),
            "chrome-profile": any(".tendata" in n for n in names),
            "non-ascii": any(not n.isascii() for n in names),
        }
        status = "OK" if not any(checks.values()) else "FAIL"
        print(f"  {name}: {status} ({len(names)} files)")
        for k, v in checks.items():
            if v: print(f"    WARNING: contains {k}")
