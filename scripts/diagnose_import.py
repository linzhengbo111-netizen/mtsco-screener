"""
diagnose_import.py — 进口分析页结构诊断工具

用法:
    python scripts/diagnose_import.py --company "SCOPE METALS GROUP LTD."

功能:
    1. 连接已登录的 Chrome (CDP 9222-9225)
    2. 导航到腾道商情发现搜索页
    3. 搜索指定公司
    4. 进入详情页
    5. 点击"进口分析"
    6. 系统诊断页面结构：iframe/shadow root/滚动容器/懒加载/tab切换
    7. 逐步尝试触发表格渲染，每步后重新统计 tbody/data-row
"""

from __future__ import annotations

import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] Playwright 未安装: pip install playwright && playwright install chromium")
    sys.exit(1)


# 复用 extract_tendata_fields.py 中的分类逻辑
def classify_page(page):
    url = page.url.lower()
    if "knowledge.tendata.cn" in url:
        return "learning"
    if "bizr.tendata.cn/search" in url:
        return "biz_search"
    if "bizr.tendata.cn" in url or "account.tendata.cn" in url:
        return "biz_home"
    if "login.tendata.cn" in url or "/login" in url:
        return "login"
    return "unknown"


def is_internal_page(url, title):
    """判断是否为浏览器内部页。"""
    if not url or url.startswith("chrome://"):
        return True
    if url.startswith("devtools://"):
        return True
    if url.startswith("edge://"):
        return True
    if url.startswith("about:blank"):
        return True
    if "Omnibox Popup" in title:
        return True
    return False


def is_tendata_business(url):
    return any(kw in url for kw in ["account.tendata.cn", "bizr.tendata.cn", "knowledge.tendata.cn", "login.tendata.cn"])


def connect_browser(pw):
    """连接 Chrome，尝试 CDP 9222-9225，失败则持久化上下文。"""
    for port in [9222, 9223, 9224, 9225]:
        try:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            print(f"  [连接] CDP 端口 {port} 成功")

            # 选择腾道业务页（过滤 chrome:// 等内部页）
            all_pages = list(ctx.pages) if ctx.pages else []
            print(f"  [连接] CDP pages total: {len(all_pages)}")
            candidates = []
            filtered_internal = 0
            for pg in all_pages:
                url = pg.url or "(empty)"
                title = pg.title() or ""
                if is_internal_page(url, title):
                    filtered_internal += 1
                    print(f"  [连接] 过滤内部页: {url}")
                    continue
                if is_tendata_business(url):
                    priority = 5
                    if "account.tendata.cn/#/index" in url or "account.tendata.cn/#/home" in url:
                        priority = 1
                    elif "bizr.tendata.cn/search#/index" in url:
                        priority = 2
                    elif "bizr.tendata.cn/enterprise#" in url:
                        priority = 3
                    elif "bizr.tendata.cn" in url:
                        priority = 4
                    candidates.append((pg, url, title, priority))

            print(f"  [连接] filtered_internal_pages: {filtered_internal}")
            print(f"  [连接] business_candidate_pages: {len(candidates)}")

            if not candidates:
                raise RuntimeError(f"已连接 Chrome 但无业务页 (total={len(all_pages)}, filtered={filtered_internal})")

            candidates.sort(key=lambda x: x[3])
            selected_pg, selected_url, selected_title, _ = candidates[0]
            print(f"  [连接] selected_page_url: {selected_url}")
            print(f"  [连接] selected_page_title: {selected_title}")
            return browser, ctx, selected_pg, False
        except RuntimeError:
            raise
        except Exception as e:
            print(f"  [连接] 端口 {port} 失败: {e}")

    # 持久化上下文兜底
    user_dir = str(Path(__file__).parent.parent / ".tendata-chrome-profile")
    ctx = pw.chromium.launch_persistent_context(user_dir, locale="zh-CN")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    print(f"  [连接] 持久化上下文成功")
    return ctx, ctx, pg, True


def dump_page_structure(page, label="诊断"):
    """核心诊断：输出页面结构全景信息。"""
    print(f"\n{'='*70}")
    print(f"  [{label}] 页面结构诊断")
    print(f"{'='*70}")
    print(f"  URL: {page.url}")
    print(f"  Title: {page.title()}")

    diag = page.evaluate(r"""() => {
        // ── 1. iframe ──
        const iframes = document.querySelectorAll('iframe');
        const iframeInfo = Array.from(iframes).map(f => ({
            src: f.src || '(no src)',
            width: f.getBoundingClientRect().width,
            height: f.getBoundingClientRect().height,
        }));

        // ── 2. shadow root ──
        let shadowRootCount = 0;
        const shadowHosts = [];
        function scanShadows(el) {
            if (el.shadowRoot) {
                shadowRootCount++;
                shadowHosts.push({
                    tag: el.tagName,
                    className: (el.className || '').substring(0, 60),
                    shadowHTMLPreview: (el.shadowRoot.innerHTML || '').substring(0, 200),
                });
                scanShadows(el.shadowRoot);
            }
            for (const child of Array.from(el.children || [])) {
                scanShadows(child);
            }
        }
        scanShadows(document.documentElement);

        // ── 3. 滚动容器 ──
        const scrollContainers = [];
        const allEls = document.querySelectorAll('*');
        for (const el of allEls) {
            const cs = getComputedStyle(el);
            if (cs.overflow === 'auto' || cs.overflow === 'scroll' || cs.overflowY === 'auto' || cs.overflowY === 'scroll') {
                const r = el.getBoundingClientRect();
                if (r.width > 100 && r.height > 100) {
                    scrollContainers.push({
                        tag: el.tagName,
                        className: (el.className || '').substring(0, 80),
                        scrollHeight: el.scrollHeight,
                        clientHeight: el.clientHeight,
                        overflow: cs.overflow || cs.overflowY,
                    });
                }
            }
        }

        // ── 4. 可点击元素（含 HS/供应商等关键词） ──
        const keywords = ['HS', '编码', '供应商', '出口商', '进口商', '记录', '明细', '表格', '海关', '排行', 'top', '采购'];
        const clickableCandidates = [];
        const clickableTags = ['button', 'a', 'li', 'span', 'div', 'td', 'th'];
        for (const tag of clickableTags) {
            for (const el of document.querySelectorAll(tag)) {
                const text = (el.innerText || '').trim();
                if (text.length > 0 && text.length < 100) {
                    for (const kw of keywords) {
                        if (text.includes(kw) || text.toLowerCase().includes(kw.toLowerCase())) {
                            const r = el.getBoundingClientRect();
                            clickableCandidates.push({
                                tag,
                                text: text.substring(0, 80),
                                visible: r.width > 0 && r.height > 0,
                                x: Math.round(r.x),
                                y: Math.round(r.y),
                            });
                            break;
                        }
                    }
                }
            }
        }

        // ── 5. table / tbody / tr[data-row-key] ──
        let tableCount = 0;
        let tbodyCount = 0;
        let tendataTbodyCount = 0;
        let dataRowCount = 0;
        const tablePreviews = [];

        for (const table of document.querySelectorAll('table')) {
            tableCount++;
            const rows = table.querySelectorAll('tr');
            const bodies = table.querySelectorAll('tbody');
            tbodyCount += bodies.length;

            // 检查是否有 tendata-ui-table-tbody
            for (const body of bodies) {
                if (body.classList.contains('tendata-ui-table-tbody')) {
                    tendataTbodyCount++;
                }
                const dataRows = body.querySelectorAll('tr[data-row-key]');
                dataRowCount += dataRows.length;
            }

            // 所有 tr[data-row-key]（不限 tbody class）
            const allDataRows = table.querySelectorAll('tr[data-row-key]');
            if (allDataRows.length > 0) {
                dataRowCount += allDataRows.length;
                // 预览第一行
                const firstRow = allDataRows[0];
                const cells = firstRow.querySelectorAll('td, th');
                const cellTexts = Array.from(cells).slice(0, 8).map(c => c.innerText.trim().substring(0, 60));
                tablePreviews.push({
                    rowKey: firstRow.getAttribute('data-row-key'),
                    cellCount: cells.length,
                    firstCells: cellTexts,
                    totalDataRows: allDataRows.length,
                });
            }

            // 如果 table 有文本但无 tbody，预览其文本
            if (bodies.length === 0) {
                const txt = table.innerText.trim().substring(0, 200);
                if (txt.length > 20) {
                    tablePreviews.push({
                        note: 'table 无 tbody，文本预览',
                        preview: txt.substring(0, 200),
                    });
                }
            }
        }

        // 不在 table 内的 tr[data-row-key]
        const orphanRows = [];
        for (const tr of document.querySelectorAll('tr[data-row-key]')) {
            if (!tr.closest('table')) {
                orphanRows.push({
                    rowKey: tr.getAttribute('data-row-key'),
                    textPreview: tr.innerText.trim().substring(0, 100),
                });
            }
        }

        // ── 6. 模块标题 ──
        const moduleTitles = [];
        for (const el of document.querySelectorAll('h1, h2, h3, h4, h5, [class*="title"], [class*="Title"], [class*="header"], [class*="Header"]')) {
            const t = (el.innerText || '').trim();
            const r = el.getBoundingClientRect();
            if (t.length > 1 && t.length < 100 && r.width > 20) {
                moduleTitles.push(t);
            }
        }

        // ── 7. 所有 class 中包含 table/row/cell 的元素 ──
        const tableLikeElements = [];
        for (const el of document.querySelectorAll('[class*="table"], [class*="Table"], [class*="row"], [class*="Row"], [class*="cell"], [class*="Cell"]')) {
            const tag = el.tagName;
            if (tag !== 'TABLE' && tag !== 'TR' && tag !== 'TD' && tag !== 'TH' && tag !== 'TBODY' && tag !== 'THEAD') {
                const cls = (el.className || '').toString().substring(0, 80);
                const t = (el.innerText || '').trim().substring(0, 80);
                const r = el.getBoundingClientRect();
                if (r.width > 50 && r.height > 20) {
                    tableLikeElements.push({ tag, className: cls, textPreview: t });
                    if (tableLikeElements.length >= 30) break;
                }
            }
        }

        // ── 8. 虚拟列表 / 懒加载特征 ──
        const virtualListIndicators = [];
        for (const kw of ['virtual', 'Virtual', 'lazy', 'Lazy', 'infinite', 'Infinite', 'List', 'list', 'ant-table-body', 'rc-virtual-list']) {
            const found = document.querySelectorAll(`[class*="${kw}"]`);
            if (found.length > 0) {
                virtualListIndicators.push({ keyword: kw, count: found.length });
            }
        }

        // ── 9. SVG/canvas 图表 ──
        const svgCount = document.querySelectorAll('svg').length;
        const canvasCount = document.querySelectorAll('canvas').length;

        // ── 10. 页面整体文本中是否有 HS/供应商关键词 ──
        const bodyText = document.body.innerText || '';
        const hasHSKeyword = /HS编码|海关编码|HS Code/.test(bodyText);
        const hasSupplierKeyword = /供应商|出口商|采购商|top.*supplier/i.test(bodyText);
        const hasImportData = /进口.*数据|进口.*记录|进口.*金额/.test(bodyText);

        return {
            iframe_count: iframeInfo.length,
            iframes: iframeInfo,
            shadow_root_count: shadowRootCount,
            shadow_hosts: shadowHosts,
            scroll_container_count: scrollContainers.length,
            scroll_containers: scrollContainers.slice(0, 10),
            clickable_candidates_count: clickableCandidates.length,
            clickable_candidates: clickableCandidates.slice(0, 50),
            table_count: tableCount,
            tbody_count: tbodyCount,
            tendata_tbody_count: tendataTbodyCount,
            data_row_count: dataRowCount,
            table_previews: tablePreviews,
            orphan_rows: orphanRows,
            visible_module_titles: [...new Set(moduleTitles)].slice(0, 30),
            table_like_elements: tableLikeElements,
            virtual_list_indicators: virtualListIndicators,
            svg_count: svgCount,
            canvas_count: canvasCount,
            body_has_hs: hasHSKeyword,
            body_has_supplier: hasSupplierKeyword,
            body_has_import_data: hasImportData,
        };
    }""")

    # ── 打印诊断结果 ──
    print(f"\n  [1] iframe 数量: {diag['iframe_count']}")
    for ifr in diag['iframes']:
        print(f"      -> src='{ifr['src']}' size={ifr['width']}x{ifr['height']}")

    print(f"  [2] shadow root 数量: {diag['shadow_root_count']}")
    for sh in diag['shadow_hosts'][:5]:
        print(f"      -> host={sh['tag']} class='{sh['className']}' preview='{sh['shadowHTMLPreview'][:100]}'")

    print(f"  [3] 滚动容器数量: {diag['scroll_container_count']}")
    for sc in diag['scroll_containers'][:5]:
        print(f"      -> {sc['tag']} class='{sc['className']}' scrollH={sc['scrollHeight']} clientH={sc['clientHeight']} overflow={sc['overflow']}")

    print(f"  [4] 可点击候选元素: {diag['clickable_candidates_count']} 个")
    for cc in diag['clickable_candidates'][:30]:
        vis = '可见' if cc['visible'] else '隐藏'
        print(f"      -> [{cc['tag']}] ({vis}) @({cc['x']},{cc['y']}) '{cc['text']}'")

    print(f"  [5] table 总数: {diag['table_count']}")
    print(f"      tbody 总数: {diag['tbody_count']}")
    print(f"      tbody.tendata-ui-table-tbody 数量: {diag['tendata_tbody_count']}")
    print(f"      tr[data-row-key] 总数: {diag['data_row_count']}")

    for tp in diag['table_previews'][:10]:
        if 'note' in tp:
            print(f"      -> {tp['note']}: {tp['preview'][:120]}")
        else:
            print(f"      -> data-row-key='{tp['rowKey']}' cells={tp['cellCount']} rows={tp['totalDataRows']} firstCells={tp['firstCells']}")

    if diag['orphan_rows']:
        print(f"  [5+] 不在 table 内的 tr[data-row-key]: {len(diag['orphan_rows'])} 行")
        for orow in diag['orphan_rows'][:5]:
            print(f"      -> key='{orow['rowKey']}' text='{orow['textPreview'][:80]}'")

    print(f"  [6] 模块标题 ({len(diag['visible_module_titles'])} 个):")
    for mt in diag['visible_module_titles']:
        print(f"      -> {mt}")

    print(f"  [7] 类表格结构元素 (非 <table> 标签): {len(diag['table_like_elements'])} 个")
    for te in diag['table_like_elements'][:10]:
        print(f"      -> {te['tag']} class='{te['className']}' text='{te['textPreview'][:60]}'")

    print(f"  [8] 虚拟列表/懒加载特征: {diag['virtual_list_indicators']}")
    print(f"  [9] SVG: {diag['svg_count']} 个, Canvas: {diag['canvas_count']} 个")
    print(f"  [10] 页面文本关键词: HS={diag['body_has_hs']}, 供应商={diag['body_has_supplier']}, 进口数据={diag['body_has_import_data']}")

    return diag


def navigate_to_search_page(page):
    """导航到腾道商情发现搜索页。"""
    from extract_tendata_fields import classify_page as classify
    url = page.url.lower()
    if "bizr.tendata.cn/search" in url:
        print("  [导航] 已在搜索页")
        return True
    if "bizr.tendata.cn" in url or "account.tendata.cn" in url:
        print("  [导航] 从业务首页跳转到搜索页")
        page.goto("https://bizr.tendata.cn/search#/index", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        return True
    if "knowledge.tendata.cn" in url:
        print("  [导航] 从学习中心跳转到搜索页")
        page.goto("https://bizr.tendata.cn/search#/index", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        return True

    # unknown 页，尝试点击"商情发现"
    sq = page.query_selector("text=商情发现")
    if sq and sq.is_visible():
        sq.click()
        time.sleep(3)
        if "bizr.tendata.cn/search" in page.url.lower():
            return True
        page.goto("https://bizr.tendata.cn/search#/index", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        return True

    # 兜底
    page.goto("https://bizr.tendata.cn/search#/index", timeout=30000, wait_until="domcontentloaded")
    time.sleep(2)
    return "bizr.tendata.cn/search" in page.url.lower()


def search_company(page, company_name):
    """在搜索页搜索公司。"""
    print(f"  [搜索] 搜索: {company_name}")

    # 点击"公司名称"Tab
    try:
        tab = page.wait_for_selector("text=公司名称", timeout=3000)
        if tab and tab.is_visible():
            tab.click()
            time.sleep(0.5)
    except PlaywrightTimeout:
        pass

    # 定位输入框
    input_el = None
    for ph in ["请输入公司名称", "公司名称", "搜索公司"]:
        try:
            el = page.query_selector(f"input[placeholder*='{ph}']")
            if el and el.is_visible():
                input_el = el
                break
        except Exception:
            pass

    if not input_el:
        inputs = page.query_selector_all("input")
        for inp in inputs:
            try:
                if inp.is_visible():
                    box = inp.bounding_box()
                    if box and box["width"] > 20:
                        input_el = inp
                        break
            except Exception:
                continue

    if not input_el:
        print("  [搜索] 未找到输入框，使用 page.goto 直接跳转搜索结果")
        # 尝试直接 URL 搜索
        encoded = company_name.replace(" ", "%20")
        page.goto(f"https://bizr.tendata.cn/search#/index?keyword={encoded}", timeout=30000)
        time.sleep(3)
        return

    input_el.click()
    time.sleep(0.2)
    input_el.press("Control+a")
    time.sleep(0.1)
    input_el.press("Backspace")
    time.sleep(0.1)
    input_el.fill(company_name)
    time.sleep(0.5)

    # 点击搜索按钮
    btn = page.query_selector("button:has-text('搜索')")
    if not btn:
        btn = page.query_selector("button:has-text('查询')")
    if btn and btn.is_visible():
        btn.click()
    else:
        input_el.press("Enter")

    print("  [搜索] 等待结果加载...")
    try:
        page.wait_for_function(
            "() => document.body.innerText.includes('共搜索到') || document.body.innerText.includes('条结果')",
            timeout=15000,
        )
    except PlaywrightTimeout:
        pass
    time.sleep(2)


def click_first_result(page):
    """点击搜索结果第一个公司名链接。"""
    print("  [结果] 尝试点击第一个搜索结果...")

    # 尝试找公司名链接
    for sel in [
        "a[class*='companyName']",
        "a:has-text('')",
        ".company-name a",
        "table tbody tr:first-child a",
        "[class*='resultItem'] a",
    ]:
        try:
            els = page.query_selector_all(sel)
            if els:
                for el in els:
                    try:
                        if el.is_visible() and el.get_attribute("href"):
                            el.click()
                            time.sleep(3)
                            print(f"  [结果] 已点击链接: {sel}")
                            return True
                    except Exception:
                        continue
        except Exception:
            pass

    # JS 方式：找第一个包含公司名特征的可见链接
    try:
        clicked = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            for (const a of links) {
                const r = a.getBoundingClientRect();
                if (r.width < 20 || r.height < 10) continue;
                if (!a.href || a.href === '#' || a.href === 'javascript:void(0)') continue;
                const text = a.innerText.trim();
                if (text.length > 3 && !text.includes('登录') && !text.includes('注册')) {
                    a.click();
                    return { clicked: true, text, href: a.href };
                }
            }
            return { clicked: false };
        }""")
        if clicked.get("clicked"):
            print(f"  [结果] JS 点击了: {clicked['text']} -> {clicked['href']}")
            time.sleep(3)
            return True
    except Exception:
        pass

    print("  [结果] 未找到可点击的结果链接")
    return False


def click_import_tab(page):
    """在详情页点击进口分析 tab。"""
    print("  [进口] 查找并点击进口分析...")

    # 尝试各种选择器
    for sel in [
        "a:has-text('进口分析')",
        "a:has-text('进口数据')",
        "text=进口分析",
        "[data-tab='import']",
        "li:has-text('进口') a",
        ".nav-item:has-text('进口分析')",
    ]:
        try:
            el = page.wait_for_selector(sel, timeout=3000)
            if el and el.is_visible():
                el.click()
                time.sleep(2)
                print(f"  [进口] 已点击: {sel}")
                return True
        except PlaywrightTimeout:
            continue

    print("  [进口] 未找到进口分析入口")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="搜索的公司名称")
    args = parser.parse_args()

    print(f"{'='*70}")
    print(f"  进口分析页诊断工具")
    print(f"  目标公司: {args.company}")
    print(f"{'='*70}")

    pw = sync_playwright().start()
    browser = None
    try:
        browser, ctx, page, is_persistent = connect_browser(pw)

        # 0. 检查登录态
        page_type = classify_page(page)
        print(f"  [状态] 当前页面类型: {page_type}, URL: {page.url}")

        # 1. 导航到搜索页
        if page_type not in ("biz_search", "biz_home"):
            print(f"  [状态] 需要导航到搜索页...")
            navigate_to_search_page(page)

        # 2. 搜索公司
        search_company(page, args.company)

        # 3. 诊断搜索结果页
        dump_page_structure(page, label="搜索结果页")

        # 4. 点击第一个结果
        click_first_result(page)

        # 5. 诊断详情页
        dump_page_structure(page, label="企业详情页")

        # 6. 点击进口分析
        click_import_tab(page)
        time.sleep(2)

        # 7. 诊断进口分析页（初始状态）
        dump_page_structure(page, label="进口分析页-初始")

        # 8. 尝试触发表格渲染的一系列操作
        print(f"\n{'='*70}")
        print(f"  开始尝试触发表格渲染...")
        print(f"{'='*70}")

        # 8a. 滚动到底部
        print("\n  [动作] 滚动到底部...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
        dump_page_structure(page, label="滚动到底部后")

        # 8b. 滚回中间
        print("\n  [动作] 滚回中部...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(2)
        dump_page_structure(page, label="滚回中部后")

        # 8c. 等待 5s
        print("\n  [动作] 等待 5 秒...")
        time.sleep(5)
        dump_page_structure(page, label="等待5秒后")

        # 8d. 等待 8s
        print("\n  [动作] 再等待 8 秒...")
        time.sleep(8)
        dump_page_structure(page, label="等待8秒后")

        # 8e. 点击所有包含"全部HS"或"HS"的可点击元素
        print("\n  [动作] 点击包含'全部HS'/'HS编码'/'HS'的元素...")
        hs_clicks = page.evaluate("""() => {
            const clicked = [];
            for (const text of ['全部HS编码', 'HS编码', 'HS', '全部海关编码']) {
                const el = document.querySelector(`text=${text}`);
                if (el) {
                    // find visible clickable ancestor
                    let target = el;
                    for (let i = 0; i < 5; i++) {
                        const r = target.getBoundingClientRect();
                        if (r.width > 10 && r.height > 10) {
                            target.click();
                            clicked.push({ text: target.innerText.trim().substring(0, 60), tag: target.tagName });
                            break;
                        }
                        if (target.parentElement) target = target.parentElement;
                        else break;
                    }
                }
            }
            return clicked;
        }""")
        print(f"  [动作] 点击了 {len(hs_clicks)} 个元素: {hs_clicks}")
        time.sleep(3)
        dump_page_structure(page, label="点击HS相关后")

        # 8f. 点击包含"全部供应商"/"供应商"的元素
        print("\n  [动作] 点击包含'全部供应商'/'供应商'/'top供应商'的元素...")
        sup_clicks = page.evaluate("""() => {
            const clicked = [];
            for (const text of ['全部供应商', '供应商', 'TOP供应商', '采购商', '出口商']) {
                const allEls = Array.from(document.querySelectorAll('*'));
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    if (t === text || t.startsWith(text)) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 10 && r.height > 10) {
                            // 找可点击的祖先
                            let target = el;
                            for (let i = 0; i < 5; i++) {
                                const tr = target.getBoundingClientRect();
                                if (tr.width > 10 && tr.height > 10) {
                                    target.click();
                                    clicked.push({ text: target.innerText.trim().substring(0, 60), tag: target.tagName });
                                    break;
                                }
                                if (target.parentElement) target = target.parentElement;
                                else break;
                            }
                            break;
                        }
                    }
                }
            }
            return clicked;
        }""")
        print(f"  [动作] 点击了 {len(sup_clicks)} 个元素: {sup_clicks}")
        time.sleep(3)
        dump_page_structure(page, label="点击供应商相关后")

        # 8g. 点击所有可能切换视图的 tab
        print("\n  [动作] 点击所有可能的 tab/segment 切换...")
        tab_clicks = page.evaluate("""() => {
            const clicked = [];
            const tabKeywords = ['产品', '产品分析', '采购产品', '进口', '记录', '明细', '列表', '表格', '趋势', '趋势分析'];
            const allEls = Array.from(document.querySelectorAll('li, a, button, [role="tab"], [class*="tab"], [class*="segment"]'));
            for (const el of allEls) {
                const t = (el.innerText || '').trim();
                if (t.length > 0 && t.length < 30) {
                    for (const kw of tabKeywords) {
                        if (t.includes(kw)) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 10 && r.height > 10) {
                                el.click();
                                clicked.push(t);
                                break;
                            }
                        }
                    }
                }
                if (clicked.length >= 10) break;
            }
            return clicked;
        }""")
        print(f"  [动作] 点击了 tab: {tab_clicks}")
        time.sleep(3)
        dump_page_structure(page, label="点击Tab切换后")

        # 8h. 如果有 iframe，切换到 iframe 内诊断
        diag = dump_page_structure(page, label="最终诊断")
        if diag["iframe_count"] > 0:
            print(f"\n  [诊断] 检测到 {diag['iframe_count']} 个 iframe，切换到 iframe 内重新诊断...")
            # 需要 Playwright 的 frame 操作，这里打印 iframe 信息供手动分析
            for i, ifr in enumerate(diag["iframes"]):
                print(f"  iframe #{i+1}: src='{ifr['src']}' size={ifr['width']}x{ifr['height']}")

        # 8i. 滚动容器内操作
        if diag["scroll_container_count"] > 0 and diag["tendata_tbody_count"] == 0:
            print(f"\n  [诊断] 检测到 {diag['scroll_container_count']} 个滚动容器，但无 tbody.tendata-ui-table-tbody")
            print(f"  尝试在滚动容器内模拟滚动加载...")
            for i in range(3):
                page.evaluate(f"""() => {{
                    const containers = Array.from(document.querySelectorAll('*')).filter(el => {{
                        const cs = getComputedStyle(el);
                        return cs.overflow === 'auto' || cs.overflow === 'scroll' || cs.overflowY === 'auto' || cs.overflowY === 'scroll';
                    }});
                    const c = containers[{i}];
                    if (c) {{ c.scrollTop = c.scrollHeight; return true; }}
                    return false;
                }}""")
                time.sleep(2)
                print(f"  [动作] 在滚动容器 #{i+1} 内滚动到底，等待 2s...")
                dump_page_structure(page, label=f"滚动容器#{i+1}加载后")

        print(f"\n{'='*70}")
        print(f"  诊断完成")
        print(f"{'='*70}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if browser:
            try:
                if not is_persistent:
                    browser.close()
                else:
                    ctx.close()
            except Exception:
                pass
        pw.stop()


if __name__ == "__main__":
    main()
