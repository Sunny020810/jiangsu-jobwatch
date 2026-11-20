"""HTML 解析：链接、日期、正文、表格。"""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup

ATTACH_EXT = (".xls", ".xlsx", ".doc", ".docx", ".pdf", ".wps", ".et", ".zip", ".rar", ".7z", ".csv")

_DATE_PATTERNS = [
    re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"),
]
_A_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#][^"']*)["'][^>]*>(.*?)</a>""", re.I | re.S)
_TITLE_ATTR = re.compile(r"""title\s*=\s*["']([^"']+)["']""", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass
class Link:
    title: str
    url: str
    context: str = ""          # 链接所在行（li/tr）的文字，用来找日期
    pub_date: date | None = None


def clean(s: str) -> str:
    return _WS.sub(" ", htmllib.unescape(s or "")).strip()


def parse_date(text: str) -> date | None:
    if not text:
        return None
    for p in _DATE_PATTERNS:
        for m in p.finditer(text):
            y, mo, d = (int(x) for x in m.groups())
            try:
                dt = date(y, mo, d)
            except ValueError:
                continue
            if 2015 <= y <= 2100:
                return dt
    # 列表常见的 “[11-20]” 只有月日，年份未知 → 不猜
    return None


def normalize_url(href: str, base: str) -> str | None:
    href = (href or "").strip()
    if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    u = urldefrag(urljoin(base, href))[0]
    if urlparse(u).scheme not in ("http", "https"):
        return None
    return u


def extract_links(html: str, base_url: str) -> list[Link]:
    """抓出页面全部链接。除了普通 <a>，也会扫 <script> / CDATA 里的链接
    （江苏政府网站常用的大汉 CMS 把列表放在 <script type="text/xml"> 里）。"""
    out: dict[str, Link] = {}
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        url = normalize_url(a["href"], base_url)
        if not url:
            continue
        title = clean(a.get("title") or "") or clean(a.get_text(" "))
        if not title:
            continue
        row = a.find_parent(["li", "tr", "dd", "p"]) or a.parent
        ctx = clean(row.get_text(" ")) if row is not None else title
        if len(ctx) > 300:
            ctx = ctx[:300]
        link = Link(title=title, url=url, context=ctx, pub_date=parse_date(ctx))
        prev = out.get(url)
        if prev is None or len(link.title) > len(prev.title):
            out[url] = link
    # 原始文本兜底（script / CDATA 中的 <a>）
    for m in _A_RE.finditer(html):
        url = normalize_url(htmllib.unescape(m.group(1)), base_url)
        if not url or url in out:
            continue
        inner = m.group(0)
        t = _TITLE_ATTR.search(inner[: inner.find(">") + 1])
        title = clean(t.group(1)) if t else clean(_TAG.sub(" ", m.group(2)))
        if not title:
            continue
        tail = html[m.end(): m.end() + 200]
        ctx = title + " " + clean(_TAG.sub(" ", tail))
        out[url] = Link(title=title, url=url, context=ctx[:300], pub_date=parse_date(ctx))
    return list(out.values())


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    return clean(soup.title.get_text()) if soup.title else ""


@dataclass
class Article:
    title: str
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)  # 每个表格 = 行列表，行 = 单元格列表
    attachments: list[Link] = field(default_factory=list)
    image_count: int = 0
    pub_date: date | None = None


def extract_article(html: str, base_url: str) -> Article:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    title = ""
    for sel in ["h1", "h2", ".title", ".article-title", ".arti_title", "title"]:
        el = soup.select_one(sel)
        if el and clean(el.get_text()):
            title = clean(el.get_text())
            break

    # 选“汉字最多”的容器当正文，避免把整站导航都算进去
    best, best_score = soup.body or soup, 0
    for el in soup.find_all(["div", "article", "section", "td"]):
        txt = el.get_text(" ", strip=True)
        cjk = sum(1 for ch in txt if "一" <= ch <= "鿿")
        links_txt = sum(len(a.get_text(strip=True)) for a in el.find_all("a"))
        score = cjk - links_txt * 1.5
        if score > best_score:
            best, best_score = el, score
    text = clean(best.get_text(" "))

    tables = []
    for t in best.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if len(rows) >= 2:
            tables.append(rows)

    attachments = []
    seen = set()
    for a in soup.find_all("a", href=True):
        url = normalize_url(a["href"], base_url)
        if not url or url in seen:
            continue
        name = clean(a.get_text(" ")) or clean(a.get("title") or "")
        path = urlparse(url).path.lower()
        low = url.lower()
        looks_download = any(k in low for k in ("download", "attach", "/file", "fileid", "getfile"))
        named_like_file = any(k in name for k in ("附件", "岗位", "职位", "表", ".xls", ".doc", ".pdf", ".wps"))
        if path.endswith(ATTACH_EXT) or (looks_download and named_like_file):
            seen.add(url)
            attachments.append(Link(title=name or path.rsplit("/", 1)[-1], url=url))
    imgs = len(best.find_all("img"))
    return Article(title=title, text=text, tables=tables, attachments=attachments,
                   image_count=imgs, pub_date=parse_date(text[:400]) or parse_date(text))


def same_site(url_a: str, url_b: str) -> bool:
    """是否同一学校站点（按注册域名，比较后两段 / edu.cn 后三段）。"""
    def root(u):
        host = (urlparse(u).hostname or "").lower()
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in ("edu", "gov", "com", "org", "net", "js") and parts[-1] == "cn":
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    return root(url_a) == root(url_b)


_DL_SPAN = re.compile(r"(报名截止|截止时间|截止日期|报名时间|报名日期|网上报名|报名方式|报名)[^。；;\n]{0,80}")
_FULL = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_PART = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def extract_deadline(text: str, pub: date | None, today: date) -> date | None:
    """从“报名时间：2026年11月25日至12月5日”这类句子里取出最后一个日期作为报名截止日。"""
    base = pub or today
    for m in _DL_SPAN.finditer(text or ""):
        span = m.group(0)
        dates = []
        for f in _FULL.finditer(span):
            try:
                dates.append((f.start(), date(int(f.group(1)), int(f.group(2)), int(f.group(3)))))
            except ValueError:
                pass
        full_spans = [(f.start(), f.end()) for f in _FULL.finditer(span)]
        for pm in _PART.finditer(span):
            if any(a <= pm.start() < b for a, b in full_spans):
                continue
            # 年份：优先参照同一句里前面的完整日期（“2026年4月7日-4月16日”），否则参照发布日期
            before = [d for pos, d in dates if pos < pm.start()]
            ref = before[-1] if before else base
            try:
                d = date(ref.year, int(pm.group(1)), int(pm.group(2)))
            except ValueError:
                continue
            if d < ref - timedelta(days=30 if not before else 0):
                d = date(ref.year + 1, d.month, d.day)
            dates.append((pm.start(), d))
        if dates:
            dates.sort()
            return dates[-1][1]
    return None
