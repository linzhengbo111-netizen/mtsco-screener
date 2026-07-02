# -*- coding: utf-8 -*-
"""诊断腾道搜索页 HS 编码 tab 的 DOM 结构。

用法：
1. 确保 Chrome 已启动（debug port 9222）
2. 确保腾道已登录
3. python scripts/diagnose_hs_tab.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def main():
    with sync_playwright() as pw:
        print("[连接] 正在连接 Chrome (port 9222)...")
        try:
            browser = pw.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            print(f"[错误] 连接失败: {e}")
            print("请先启动 Chrome: start_tendata_stack.bat")
            return

        contexts = browser.contexts
        if not contexts:
            print("[错误] 没有已打开的浏览器上下文")
            return

        # 找第一个有页面的 context
        ctx = None
        for c in contexts:
            if c.pages:
                ctx = c
                break

        if not ctx:
            print("[错误] 没有已打开的标签页")
            return

        print(f"[连接] 找到 {len(ctx.pages)} 个标签页")

        # 优先找 bizr.tendata.cn 的页面
        page = None
        for p in ctx.pages:
            if "bizr.tendata.cn" in p.url:
                page = p
                break

        if not page:
            page = ctx.pages[0]

        print(f"[连接] 使用页面: {page.url}")
        print(f"[连接] 页面标题: {page.title()}")
        print()

        # 等待页面加载
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # === 1. 扫描所有可能作为 tab 的元素 ===
        print("=" * 60)
        print("1. 扫描所有可能作为搜索模式 tab 的元素")
        print("=" * 60)

        tabs_info = page.evaluate(r"""() => {
            const results = [];

            // 策略 A: 扫描所有可见元素，查找包含特定关键词的文本
            const keywords = ['公司名称', 'HS编码', 'HS', '产品', '产品描述', '搜索'];

            const allEls = document.querySelectorAll('*');
            for (const el of allEls) {
                const rect = el.getBoundingClientRect();
                // 只考虑可见的、尺寸合理的元素（可能是 tab）
                if (rect.width > 10 && rect.height > 10 && rect.width < 300 && rect.height < 60) {
                    const text = (el.innerText || '').trim();
                    if (text.length > 1 && text.length < 50) {
                        for (const kw of keywords) {
                            if (text.includes(kw)) {
                                const style = getComputedStyle(el);
                                results.push({
                                    tag: el.tagName,
                                    text: text,
                                    className: (el.className || '').substring(0, 100),
                                    role: el.getAttribute('role') || '',
                                    ariaSelected: el.getAttribute('aria-selected') || '',
                                    ariaCurrent: el.getAttribute('aria-current') || '',
                                    display: style.display,
                                    color: style.color,
                                    backgroundColor: style.backgroundColor,
                                    fontWeight: style.fontWeight,
                                    x: Math.round(rect.x),
                                    y: Math.round(rect.y),
                                    w: Math.round(rect.width),
                                    h: Math.round(rect.height),
                                    parentClass: (el.parentElement?.className || '').substring(0, 100),
                                    parentTag: el.parentElement?.tagName || '',
                                });
                                break; // 避免重复
                            }
                        }
                    }
                }
            }
            return results;
        }""")

        if not tabs_info:
            print("[诊断] 未找到任何包含 tab 关键词的可见元素")
        else:
            for i, t in enumerate(tabs_info):
                print(f"\n  Tab #{i+1}:")
                print(f"    文本: '{t['text']}'")
                print(f"    标签: <{t['tag']}>")
                print(f"    Class: {t['className']}")
                print(f"    Role: {t['role']} | aria-selected: {t['ariaSelected']} | aria-current: {t['ariaCurrent']}")
                print(f"    样式: display={t['display']}, color={t['color']}, bg={t['backgroundColor']}, weight={t['fontWeight']}")
                print(f"    位置: ({t['x']}, {t['y']}) 尺寸: {t['w']}x{t['h']}")
                print(f"    父级: <{t['parentTag']}> {t['parentClass']}")

        # === 2. 精确测试 text=HS编码 选择器 ===
        print()
        print("=" * 60)
        print("2. 测试各种选择器是否命中")
        print("=" * 60)

        selectors_to_test = [
            "text=HS编码",
            "text=HS 编码",
            "text=/^HS编码$/",
            "text=/HS编码/",
            "text=/HS/",
            "span:has-text('HS编码')",
            "div:has-text('HS编码')",
            "a:has-text('HS编码')",
            "li:has-text('HS编码')",
            "button:has-text('HS编码')",
            "text=公司名称",
        ]

        for sel in selectors_to_test:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    rect = el.bounding_box()
                    text = el.inner_text().strip()
                    cls = el.get_attribute('class') or ''
                    print(f"  [命中] '{sel}' → 文本='{text}', class='{cls[:60]}', pos=({rect})")
                else:
                    print(f"  [未命中] '{sel}' → 元素不存在或不可见")
            except Exception as e:
                print(f"  [异常] '{sel}' → {e}")

        # === 3. 扫描搜索区域完整 HTML ===
        print()
        print("=" * 60)
        print("3. 搜索区域完整 HTML（输入框附近 2000 字符）")
        print("=" * 60)

        html_snippet = page.evaluate(r"""() => {
            // 找包含"请输入公司"placeholder 的输入框
            const input = document.querySelector('input[placeholder*="公司"]') ||
                          document.querySelector('input[placeholder*="搜索"]') ||
                          document.querySelector('input');
            if (!input) return 'NO_INPUT_FOUND';

            // 找父级容器
            let container = input.parentElement;
            for (let i = 0; i < 8 && container; i++) {
                // 如果父级有多个直接子元素包含文本，可能是 tab 容器
                const children = Array.from(container.children);
                const textChildren = children.filter(c =>
                    c.innerText && c.innerText.trim().length > 1 && c.innerText.trim().length < 50
                );
                if (textChildren.length >= 2) {
                    return container.outerHTML.substring(0, 3000);
                }
                container = container.parentElement;
            }
            return input.parentElement?.outerHTML?.substring(0, 2000) || 'NO_CONTAINER';
        }""")

        print(f"  {html_snippet[:2000]}")

        print()
        print("[诊断] 完成。请将以上输出发给开发人员以修复 _select_hs_code_mode()")


if __name__ == "__main__":
    main()
