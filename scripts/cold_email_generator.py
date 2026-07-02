#!/usr/bin/env python3
"""
开发信生成器 — 核心逻辑模块
Cold Email Generator for MTSCO stainless steel pipe outreach.

给定公司名+国家，自动研究该公司、判断类型、生成开发信。

可独立运行（CLI），也可被 Streamlit 导入使用。

用法:
    # CLI 模式
    python scripts/cold_email_generator.py --name "SK Engineering" --country "韩国"
    python scripts/cold_email_generator.py --name "LAM RESEARCH" --country "韩国" --website "lamresearch.com"

    # 作为模块导入
    from scripts.cold_email_generator import CompanyResearcher, EmailGenerator

依赖: anthropic, duckduckgo_search, beautifulsoup4, requests
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从 scripts/ 内部导入同目录模块
sys.path.insert(0, str(Path(__file__).parent))

import argparse
import json
import re
import time
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
try:
    from ddgs import DDGS  # 新版包名 (v9+)
except ImportError:
    from duckduckgo_search import DDGS  # 旧版包名 (v8.x)

# ---------------------------------------------------------------------------
# 排除域名列表 — 从 google_verifier.py 复用并扩展
# ---------------------------------------------------------------------------
EXCLUDED_DOMAINS = {
    # B2B 平台
    "alibaba.com", "made-in-china.com", "globalsources.com", "tradekey.com",
    "ec21.com", "ecplaza.net", "exportersindia.com", "indiamart.com",
    "tradeindia.com", "b2bmap.com", "b2brazil.com",
    # 企业目录
    "zoominfo.com", "dnb.com", "bloomberg.com", "crunchbase.com",
    "kompass.com", "yellowpages.com", "yelp.com", "manta.com",
    "hotfrog.com", "cylex.com", "opencorporates.com",
    # 社交媒体
    "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "tiktok.com",
    # 百科/新闻
    "wikipedia.org", "reuters.com", "bloomberg.com", "wsj.com",
    "ft.com", "prnewswire.com", "businesswire.com",
    # 海关/贸易数据
    "tendata.cn", "panjiva.com", "importgenius.com", "importyeti.com",
    "datamyne.com", "zauba.com",
    # 其他
    "glassdoor.com", "indeed.com", "wikipedia.org",
}


def is_excluded_url(url: str) -> bool:
    """检查 URL 是否属于排除域名列表。"""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    # 去掉 www. 前缀
    if domain.startswith("www."):
        domain = domain[4:]
    for excluded in EXCLUDED_DOMAINS:
        if domain == excluded or domain.endswith("." + excluded):
            return True
    return False


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ResearchResult:
    """公司研究结果"""
    company_name: str
    country: str
    search_results: list[dict] = field(default_factory=list)
    page_texts: list[str] = field(default_factory=list)
    combined_text: str = ""
    research_timestamp: str = ""

    def __post_init__(self):
        if not self.research_timestamp:
            self.research_timestamp = datetime.now().isoformat()


@dataclass
class EmailResult:
    """生成的开发信结果"""
    company_name: str
    country: str
    company_type: str = ""           # end_user / epc / subcontractor / trader
    classification_reason: str = ""
    subject: str = ""
    body: str = ""
    research_summary: str = ""
    generated_at: str = ""

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now().isoformat()

    @property
    def full_email(self) -> str:
        """完整邮件（主题 + 正文），适合复制粘贴。"""
        return f"Subject: {self.subject}\n\n{self.body}"

    @property
    def company_type_label(self) -> str:
        """公司类型的中文标签。"""
        labels = {
            "end_user": "End User (终端用户)",
            "epc": "EPC (工程总包)",
            "subcontractor": "Subcontractor (分包商/安装商)",
            "trader": "Trader (贸易商)",
        }
        return labels.get(self.company_type, self.company_type)


# ---------------------------------------------------------------------------
# CompanyResearcher — 网络搜索 + 页面抓取
# ---------------------------------------------------------------------------

class CompanyResearcher:
    """搜索公司信息，收集研究资料。

    使用 DuckDuckGo 搜索公司名+国家，抓取前几个非排除域名的页面。
    """

    # 请求超时
    SEARCH_TIMEOUT = 15        # DDG 搜索超时
    PAGE_FETCH_TIMEOUT = 15    # 单页面抓取超时
    MAX_PAGE_LENGTH = 4000     # 单页最大字符数
    MAX_SEARCH_RESULTS = 10    # 搜索返回数
    TOP_N_PAGES = 3            # 实际抓取前 N 个有效页面
    DELAY_BETWEEN_FETCHES = 1.0  # 抓取间隔（秒）

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        })

    def search(self, company_name: str, country: str = "") -> list[dict]:
        """搜索公司信息，返回过滤后的搜索结果列表。

        每个结果包含: title, url, snippet
        自动排除 B2B 平台、社交媒体等非目标域名。
        尝试多个搜索查询以提高命中率。
        """
        # 构建多个搜索查询，从最具体到最泛
        queries = []
        if country:
            queries.append(f"{company_name} {country} official website")
            queries.append(f"{company_name} {country} company")
        queries.append(f"{company_name} official website")
        queries.append(f"{company_name} company profile")

        all_results = []
        seen_urls = set()

        for query in queries:
            if len(all_results) >= self.MAX_SEARCH_RESULTS:
                break
            try:
                with DDGS() as ddgs:
                    raw_results = list(ddgs.text(
                        query,
                        max_results=self.MAX_SEARCH_RESULTS,
                    ))

                for r in raw_results:
                    url = r.get("href", "")
                    if not url or url in seen_urls:
                        continue
                    if is_excluded_url(url):
                        continue
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("body", ""),
                    })

            except Exception as e:
                # SSL 或网络错误，尝试下一个查询
                print(f"[搜索] 查询 '{query[:50]}...' 失败: {e}")
                continue

        return all_results[:self.MAX_SEARCH_RESULTS]

    def read_page(self, url: str) -> str:
        """抓取并提取页面文本内容。"""
        try:
            resp = self._session.get(url, timeout=self.PAGE_FETCH_TIMEOUT)
            resp.raise_for_status()

            # 尝试检测编码
            resp.encoding = resp.apparent_encoding or "utf-8"

            soup = BeautifulSoup(resp.text, "html.parser")

            # 移除脚本、样式、导航、页脚等无关元素
            for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                                       "noscript", "iframe", "form"]):
                tag.decompose()

            # 获取可见文本
            text = soup.get_text(separator="\n", strip=True)

            # 清理多余空行
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)

            # 截断
            if len(text) > self.MAX_PAGE_LENGTH:
                text = text[:self.MAX_PAGE_LENGTH] + "..."

            return text

        except requests.RequestException as e:
            print(f"[抓取] {url[:60]}... 失败: {e}")
            return ""
        except Exception as e:
            print(f"[解析] {url[:60]}... 失败: {e}")
            return ""

    def research(self, company_name: str, country: str = "",
                 website: str = "") -> ResearchResult:
        """执行完整研究流程：搜索 → 抓取 → 合并。

        Args:
            company_name: 公司名称
            country: 国家（可选但推荐）
            website: 已知的公司网站（可选，跳过搜索直接抓取该站）

        Returns:
            ResearchResult 包含所有研究数据
        """
        result = ResearchResult(
            company_name=company_name,
            country=country,
        )

        if website:
            # 用户提供了网站，直接抓取
            print(f"[研究] 直接抓取指定网站: {website}")
            if not website.startswith("http"):
                website = "https://" + website
            text = self.read_page(website)
            if text:
                result.page_texts.append(text)
            result.search_results = [{"title": "User-provided website", "url": website, "snippet": ""}]
        else:
            # 搜索公司
            print(f"[研究] 搜索: {company_name}, {country}")
            results = self.search(company_name, country)

            if not results:
                # 不带国家再试一次
                print(f"[研究] 无结果，重试不带国家...")
                results = self.search(company_name)

            result.search_results = results
            print(f"[研究] 找到 {len(results)} 个有效搜索结果")

            # 抓取前 N 个页面
            for i, r in enumerate(results[:self.TOP_N_PAGES]):
                url = r["url"]
                print(f"[研究] 抓取 ({i+1}/{min(len(results), self.TOP_N_PAGES)}): {url[:80]}...")
                text = self.read_page(url)
                if text:
                    result.page_texts.append(text)
                if i < min(len(results), self.TOP_N_PAGES) - 1:
                    time.sleep(self.DELAY_BETWEEN_FETCHES)

        # 合并文本
        parts = []
        parts.append(f"Company: {company_name}")
        if country:
            parts.append(f"Country: {country}")

        # 添加搜索摘要
        if result.search_results:
            parts.append("\n--- Search Results ---")
            for r in result.search_results[:5]:
                parts.append(f"• {r['title']}")
                parts.append(f"  URL: {r['url']}")
                parts.append(f"  {r['snippet']}")

        # 添加页面内容
        if result.page_texts:
            for i, text in enumerate(result.page_texts):
                parts.append(f"\n--- Page {i+1} Content ---")
                parts.append(text)

        result.combined_text = "\n".join(parts)
        return result


# ---------------------------------------------------------------------------
# EmailGenerator — 调用 Claude API 分类 + 生成邮件
# ---------------------------------------------------------------------------

class EmailGenerator:
    """使用 Anthropic Claude API 分析公司并生成开发信。"""

    DEFAULT_MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 2000
    TEMPERATURE = 0.6  # 稍微降低以减少编造

    def __init__(self, api_key: str, model: str = ""):
        """
        Args:
            api_key: Anthropic API key
            model: 模型 ID，默认 claude-sonnet-4-20250514
        """
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._prompts = self._load_prompts()

    def _load_prompts(self) -> dict:
        """加载提示词配置。"""
        config_path = Path(__file__).parent / "config" / "email_prompts.yaml"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"[配置] 加载 email_prompts.yaml 失败: {e}")
            return {}

    def generate(self, company_name: str, country: str = "",
                 research_data: str = "", website: str = "",
                 company_type_hint: str = "") -> EmailResult:
        """生成开发信。

        Args:
            company_name: 公司名称
            country: 国家
            research_data: 研究数据文本
            website: 已知网站（可选）
            company_type_hint: 用户手动指定的公司类型（可选，覆盖 AI 判断）

        Returns:
            EmailResult 包含分类结果和生成的邮件
        """
        system_prompt = self._prompts.get("system_prompt", "")
        if not system_prompt:
            raise ValueError("无法加载 system_prompt，请检查 email_prompts.yaml")

        # 构建用户提示
        website_hint = f"Known Website: {website}" if website else ""
        user_template = self._prompts.get("user_prompt_template", "")
        if user_template:
            user_message = user_template.format(
                company_name=company_name,
                country=country,
                website_hint=website_hint,
                research_data=research_data or "No detailed research data available. Use your knowledge of this company.",
            )
        else:
            # 模板加载失败时的回退
            user_message = f"""Company: {company_name}
Country: {country}
{website_hint}

Research Data:
{research_data or 'No detailed research data available.'}

Classify this company as end_user/epc/subcontractor/trader, then generate the email.
Output as JSON with keys: company_type, classification_reason, subject, body."""

        # 如果用户指定了类型，加入提示
        if company_type_hint:
            user_message += f"\n\nNote: The user has indicated this company is likely a **{company_type_hint}**. Use this as a strong hint for classification."

        print(f"[生成] 调用 {self._model}...")
        start = time.time()

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self.MAX_TOKENS,
                temperature=self.TEMPERATURE,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            elapsed = time.time() - start
            print(f"[生成] 完成，耗时 {elapsed:.1f}s")

            # 解析响应 — 提取文本块（跳过 thinking blocks）
            raw_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw_text += block.text
            if not raw_text:
                raise ValueError("API 返回了空的响应内容")

            return self._parse_response(raw_text, company_name, country)

        except Exception as e:
            print(f"[生成] API 调用失败: {e}")
            raise

    def _parse_response(self, raw_text: str, company_name: str, country: str) -> EmailResult:
        """解析 LLM 返回的 JSON 响应。

        容错处理：如果 LLM 返回的不是纯 JSON（例如包裹在 markdown 代码块中），
        尝试提取 JSON 部分。如果完全无法解析，把整个响应当邮件正文返回。
        """
        result = EmailResult(company_name=company_name, country=country)

        # 尝试提取 JSON 块
        json_text = raw_text

        # 去掉 markdown 代码块标记
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw_text, re.DOTALL)
        if m:
            json_text = m.group(1)
        else:
            # 尝试找到第一个 { 和最后一个 }
            start_idx = raw_text.find("{")
            end_idx = raw_text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_text = raw_text[start_idx:end_idx + 1]

        try:
            data = json.loads(json_text)
            result.company_type = data.get("company_type", "unknown")
            result.classification_reason = data.get("classification_reason", "")
            result.subject = data.get("subject", "")
            result.body = data.get("body", "")
            result.research_summary = data.get("classification_reason", "")
        except json.JSONDecodeError:
            # JSON 解析失败，尝试从原始文本中提取
            print("[解析] JSON 解析失败，尝试从原始文本提取...")
            result.body = raw_text
            # 尝试提取主题行
            m = re.search(r'Subject:\s*(.+?)(?:\n|$)', raw_text, re.IGNORECASE)
            if m:
                result.subject = m.group(1).strip()
            # 尝试提取公司类型
            for ct in ["end_user", "epc", "subcontractor", "trader"]:
                if ct in raw_text.lower():
                    result.company_type = ct
                    break

        return result


# ---------------------------------------------------------------------------
# 输出辅助函数
# ---------------------------------------------------------------------------

def format_terminal_output(result: EmailResult) -> str:
    """格式化终端输出。"""
    lines = []
    sep = "=" * 60
    lines.append(sep)
    lines.append(f"  开发信 — {result.company_name}")
    lines.append(sep)
    lines.append(f"  公司类型: {result.company_type_label}")
    if result.classification_reason:
        lines.append(f"  分类理由: {result.classification_reason}")
    lines.append(sep)
    lines.append("")
    lines.append(f"Subject: {result.subject}")
    lines.append("")
    lines.append(result.body)
    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def save_to_markdown(result: EmailResult, output_dir: str = "output") -> Path:
    """保存邮件到 Markdown 文件。"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 生成安全的文件名
    safe_name = re.sub(r'[^\w\s-]', '', result.company_name)
    safe_name = re.sub(r'[-\s]+', '_', safe_name).strip('_')[:50]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"cold_email_{safe_name}_{timestamp}.md"

    filepath = output_path / filename
    content = f"""# 开发信 — {result.company_name}

**国家**: {result.country}
**公司类型**: {result.company_type_label}
**生成时间**: {result.generated_at}

---

## 邮件

**Subject: {result.subject}**

{result.body}

---

## 分类理由

{result.classification_reason}
"""
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="开发信生成器 — 输入公司名+国家，自动研究并生成开发信",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/cold_email_generator.py --name "SK Engineering & Construction" --country "韩国"
  python scripts/cold_email_generator.py --name "LAM RESEARCH KOREA" --country "韩国" --website lamresearch.com
  python scripts/cold_email_generator.py --name "SCOPE METALS" --country "以色列" --type trader
        """,
    )
    parser.add_argument("--name", required=True, help="公司名称（中英文均可）")
    parser.add_argument("--country", required=True, help="公司所在国家")
    parser.add_argument("--website", default="", help="公司官网（可选，跳过搜索直接抓取）")
    parser.add_argument("--type", dest="company_type", default="",
                        choices=["end_user", "epc", "subcontractor", "trader"],
                        help="手动指定公司类型（可选，覆盖 AI 判断）")
    parser.add_argument("--output", default="", help="输出文件路径（可选，默认保存到 output/）")
    parser.add_argument("--api-key", default="", help="Anthropic API Key（可选，默认从环境变量 ANTHROPIC_API_KEY 读取）")
    parser.add_argument("--model", default="", help="模型 ID（可选，默认 claude-sonnet-4-20250514）")
    parser.add_argument("--no-save", action="store_true", help="不保存文件，仅终端输出")

    args = parser.parse_args()

    # 获取 API Key
    import os
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("错误: 未设置 ANTHROPIC_API_KEY。请通过以下方式之一提供:")
        print("  1. 环境变量: export ANTHROPIC_API_KEY=sk-ant-...")
        print("  2. 命令行参数: --api-key sk-ant-...")
        sys.exit(1)

    # 步骤 1: 研究公司
    print(f"\n{'='*60}")
    print(f"  开发信生成器")
    print(f"  公司: {args.name}")
    print(f"  国家: {args.country}")
    print(f"{'='*60}\n")

    researcher = CompanyResearcher()
    research = researcher.research(
        company_name=args.name,
        country=args.country,
        website=args.website,
    )

    if not research.combined_text:
        print("[警告] 未获取到研究数据，将仅使用公司名和国家生成邮件。")
        research.combined_text = f"Company: {args.name}\nCountry: {args.country}"

    # 步骤 2: 生成邮件
    print(f"\n[生成] 正在调用 Claude API...\n")
    generator = EmailGenerator(api_key=api_key, model=args.model)
    email_result = generator.generate(
        company_name=args.name,
        country=args.country,
        research_data=research.combined_text,
        website=args.website,
        company_type_hint=args.company_type,
    )

    # 步骤 3: 输出
    print(format_terminal_output(email_result))

    # 步骤 4: 保存
    if not args.no_save:
        if args.output:
            filepath = Path(args.output)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(email_result.full_email, encoding="utf-8")
        else:
            filepath = save_to_markdown(email_result)
        print(f"\n[保存] 已保存到: {filepath}")


if __name__ == "__main__":
    main()
