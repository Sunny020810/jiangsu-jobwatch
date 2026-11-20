"""搜索引擎补充检索（Bing，失败时退回 DuckDuckGo）。
用途：①自动找学校官网；②每天全网补漏；③按学校轮换搜索近期招聘。"""
from __future__ import annotations

from urllib.parse import parse_qs, quote, unquote, urlparse

from bs4 import BeautifulSoup

from .fetch import Fetcher, FetchError
from .htmlutil import Link, clean, parse_date

# Bing 的时间过滤：ez1=24 小时，ez2=一周，ez3=一个月
FRESH = {"day": "ez1", "week": "ez2", "month": "ez3"}

BLACKLIST = (
    "baike.baidu", "zhihu.com", "dxsbb.com", "bendibao.com", "eol.cn", "gaokao", "sohu.com", "163.com",
    "qq.com", "sina.com", "ifeng.com", "toutiao", "douyin", "bilibili", "weibo", "maigoo", "wikipedia",
    "xiaohongshu", "baidu.com", "bing.com", "csdn", "docin", "doc88", "wenku",
)


class SearchStatus:
    def __init__(self):
        self.ok = 0
        self.empty = 0
        self.failed = 0


def _bing(fetcher: Fetcher, query: str, fresh: str | None) -> list[Link]:
    url = f"https://cn.bing.com/search?q={quote(query)}&count=30&setlang=zh-Hans"
    if fresh:
        url += f"&filters=ex1%3a%22{FRESH[fresh]}%22"
    page = fetcher.get(url)
    soup = BeautifulSoup(page.text, "lxml")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href", "").startswith("http"):
            continue
        cap = li.select_one(".b_caption p") or li
        snippet = clean(cap.get_text(" "))
        out.append(Link(clean(a.get_text(" ")), a["href"], snippet[:300], parse_date(snippet)))
    return out


def _ddg(fetcher: Fetcher, query: str, fresh: str | None) -> list[Link]:
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}&kl=cn-zh"
    if fresh:
        url += "&df=" + {"day": "d", "week": "w", "month": "m"}[fresh]
    page = fetcher.get(url)
    soup = BeautifulSoup(page.text, "lxml")
    out = []
    for res in soup.select(".result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        if not href.startswith("http"):
            continue
        sn = res.select_one(".result__snippet")
        snippet = clean(sn.get_text(" ")) if sn else ""
        out.append(Link(clean(a.get_text(" ")), href, snippet[:300], parse_date(snippet)))
    return out


def search(fetcher: Fetcher, query: str, fresh: str | None = None, status: SearchStatus | None = None) -> list[Link]:
    for engine in (_bing, _ddg):
        try:
            res = engine(fetcher, query, fresh)
        except FetchError:
            continue
        if res:
            if status:
                status.ok += 1
            return res
    if status:
        status.empty += 1
    return []


def not_blacklisted(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return not any(b in host for b in BLACKLIST)
