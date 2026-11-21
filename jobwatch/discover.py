"""自动探测：学校官网是否正确、招聘栏目在哪里。"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .fetch import Fetcher, FetchError, Page
from .htmlutil import clean, extract_links, page_title, same_site
from .match import is_recruit_title
from .search import not_blacklisted, search

NAV_RE = re.compile(r"人才招聘|招聘信息|招聘公告|人才引进|招贤纳士|诚聘|人事招聘|招聘|人事处|人事部|人力资源|组织人事|教师工作部|人事|师资")
NOTICE_RE = re.compile(r"通知公告|公告通知|信息公开|公示公告|通知")
NAV_EXCLUDE = re.compile(r"就业|招生|学生|校友|毕业|研究生招生|继续教育")
HOST_EXCLUDE = re.compile(r"^(jy|job|jobs|career|zs|zsb|zsw|zsjy|jiuye|zhaosheng|jyw|jyzx|xsc|alumni)\.")


def _root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def page_matches(page: Page, names: list[str]) -> bool:
    title = page_title(page.text)
    body = clean(BeautifulSoup(page.text[:200000], "lxml").get_text(" "))[:4000]
    return any(n in title or n in body for n in names if n)


def resolve_homepage(fetcher: Fetcher, name: str, aliases: list[str], configured: str) -> tuple[str | None, Page | None, str]:
    """返回 (首页地址, 首页内容, 说明)。"""
    names = [name, *aliases]
    note = "没有配置官网地址"
    if configured:
        try:
            pg = fetcher.get(configured)
            if page_matches(pg, names):
                return pg.url, pg, "官网已验证"
            note = f"首页有响应，但未匹配到校名（标题：{page_title(pg.text)[:45]}）"
        except FetchError as e:
            note = f"配置的首页请求失败：{e}"
    for q in (f"{name} 官网", f"{name}"):
        for link in search(fetcher, q)[:8]:
            if not not_blacklisted(link.url):
                continue
            host = urlparse(link.url).hostname or ""
            if not (host.endswith(".cn") or host.endswith(".com") or host.endswith(".net")):
                continue
            try:
                pg = fetcher.get(_root(link.url))
            except FetchError:
                continue
            if page_matches(pg, names):
                return pg.url, pg, "官网由搜索自动找到"
    return (configured or None), None, note + "；搜索也未能确认官网"


def _usable_nav(link, home: str) -> bool:
    host = (urlparse(link.url).hostname or "").lower()
    return same_site(link.url, home) and not HOST_EXCLUDE.match(host) and not NAV_EXCLUDE.search(link.title) \
        and len(link.title) <= 12


def score_list_page(html: str, url: str) -> int:
    return sum(1 for l in extract_links(html, url) if is_recruit_title(l.title))


def find_recruit_lists(fetcher: Fetcher, home: str, home_html: str, max_lists: int = 4) -> list[str]:
    """从首页出发，最多两层，找“招聘信息/人才引进/通知公告”这类列表页，按其中招聘标题数量排序。"""
    links = extract_links(home_html, home)
    level1 = [l for l in links if _usable_nav(l, home) and (NAV_RE.search(l.title) or NOTICE_RE.search(l.title))]
    # 人事/招聘优先，通知公告其次
    level1.sort(key=lambda l: (0 if NAV_RE.search(l.title) else 1, len(l.title)))
    scored: dict[str, int] = {}
    visited = set()
    level2 = []
    for l in level1[:12]:
        if l.url in visited:
            continue
        visited.add(l.url)
        try:
            pg = fetcher.get(l.url, referer=home)
        except FetchError:
            continue
        s = score_list_page(pg.text, pg.url)
        if s:
            scored[pg.url] = s
        if NAV_RE.search(l.title):  # 进入人事处网站后，再找“招聘信息”子栏目
            for l2 in extract_links(pg.text, pg.url):
                if _usable_nav(l2, home) and re.search(r"招聘|引进|招贤|诚聘|公告", l2.title) and l2.url not in visited:
                    level2.append(l2)
    for l2 in level2[:10]:
        if l2.url in visited:
            continue
        visited.add(l2.url)
        try:
            pg = fetcher.get(l2.url, referer=home)
        except FetchError:
            continue
        s = score_list_page(pg.text, pg.url)
        if s:
            scored[pg.url] = max(s, scored.get(pg.url, 0))
    best = sorted(scored.items(), key=lambda kv: -kv[1])
    return [u for u, _ in best[:max_lists]]
