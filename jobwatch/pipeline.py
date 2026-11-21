"""主流程：收集候选公告 → 抓详情和附件 → 分级 → 记录。"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from itertools import zip_longest
from time import monotonic
from urllib.parse import urlparse

from .attachments import parse_attachment
from .discover import find_recruit_lists, page_matches, resolve_homepage
from .fetch import Fetcher, FetchError
from .htmlutil import ATTACH_EXT, Link, extract_article, extract_deadline, extract_links, same_site
from .match import MULTI_UNIT, VOCATIONAL_HINT, analyze, find_schools, is_recruit_title
from .search import SearchStatus, not_blacklisted, search
from .state import State, today_cn

JS_CITIES = "南京|苏州|无锡|常州|镇江|扬州|泰州|南通|盐城|淮安|连云港|宿迁|徐州|江苏|昆山|江阴|张家港|常熟|太仓|宜兴|溧阳|句容"


def article_like(l: Link) -> bool:
    """排除“人才招聘”“招聘信息”这类栏目链接，只要具体公告。"""
    t = l.title.strip()
    return len(t) >= 8 and not re.fullmatch(r"(更多|more|MORE|>>|»|查看更多).*", t)


@dataclass
class Candidate:
    link: Link
    source: str
    school: str = ""          # 来自学校官网时已知学校


@dataclass
class Health:
    schools: dict = field(default_factory=dict)   # name -> {status, detail, home, lists}
    portals: dict = field(default_factory=dict)
    search: SearchStatus = field(default_factory=SearchStatus)
    lookup: SearchStatus = field(default_factory=SearchStatus)
    detail_errors: int = 0
    attachments_failed: int = 0


class Runner:
    def __init__(self, cfg: dict, schools: list[dict], state: State, log=partial(print, flush=True)):
        self.cfg = cfg
        self.schools = [s for s in schools if s.get("type") in cfg["include_types"]]
        self.school_map = {s["name"]: s for s in self.schools}
        self.state = state
        self.log = log
        self.f = Fetcher(timeout=cfg["request_timeout"], retries=cfg["request_retries"], delay=cfg["request_delay"],
                         allow_insecure=cfg["allow_insecure_ssl"], max_bytes=cfg["max_attachment_mb"] * 1024 * 1024,
                         connect_timeout=cfg.get("connect_timeout", cfg["request_timeout"]))
        self.health = Health()
        self.today = today_cn()
        self.min_date = self.today - timedelta(days=cfg["max_age_days"])

    # ------------------------------------------------------------------ 学校官网
    def crawl_school(self, s: dict) -> list[Candidate]:
        name = s["name"]
        site = self.state.sites.get(name, {})
        configured = s.get("homepage") or ""
        manual = s.get("recruit_pages") or []
        home_changed = configured != site.get("configured_home", site.get("home") or "")
        cached = [] if home_changed else [u for u in site.get("lists", []) if u not in site.get("manual_lists", [])]
        h = {"status": "ok", "detail": "", "home": site.get("home", ""), "lists": []}
        cands: list[Candidate] = []
        try:
            stale = (home_changed or not site.get("home") or not (manual or cached) or not site.get("discovered") or
                     date.fromisoformat(site["discovered"]) <= self.today - timedelta(days=self.cfg["rediscover_days"]))
            home_page = None
            parsed_pages = {}
            problems = []
            if stale or site.get("home_failed"):
                entry = configured if home_changed else site.get("home") or configured
                home, home_page, note = resolve_homepage(self.f, name, s.get("aliases") or [],
                                                         entry, status=self.health.lookup)
                h["home"] = home or ""
                if home_page is None:
                    problems.append(note)
                    site.update(home=home or "", home_failed=True)
                else:
                    cached = find_recruit_lists(self.f, home_page.url, home_page.text, parsed_pages=parsed_pages)
                    site.update(home=home_page.url, discovered=self.today.isoformat(), home_failed=False,
                                note=note)
            else:
                try:
                    home_page = self.f.get(site["home"])
                    if not page_matches(home_page, [name, *(s.get("aliases") or [])]):
                        home_page = None
                        raise FetchError("页面未显示可确认的校名")
                except FetchError as e:
                    site["home_failed"] = True
                    problems.append(f"首页未能读取：{e}")
            lists = list(dict.fromkeys([*manual, *cached]))
            site.update(configured_home=configured, lists=lists, manual_lists=list(manual))
            h["lists"] = lists
            page_links = []
            if home_page is not None:
                home_links = parsed_pages.get(home_page.url)
                page_links.append(home_links if home_links is not None else extract_links(home_page.text, home_page.url))
            list_count = 0
            for u in lists:
                try:
                    links = parsed_pages.get(u, [])
                    if not any(article_like(link) for link in links):
                        pg = self.f.get(u, referer=site.get("home") or None)
                        links = extract_links(pg.text, pg.url)
                    if not any(article_like(link) for link in links):
                        raise FetchError("未读到公告列表内容")
                    page_links.append(links)
                    list_count += 1
                except FetchError as e:
                    problems.append(f"栏目未能读取：{e}")
                    site["discovered"] = "2000-01-01"
            if home_page is not None and not lists:
                problems.append("首页可读，未定位到招聘栏目")
            if problems:
                detail = "；".join(dict.fromkeys(problems))
                if list_count:
                    detail = f"已读取 {list_count} 个栏目；{detail}"
                h.update(status="warn" if page_links else "error", detail=detail)
            list_urls = set(lists) | {site.get("home", "")}
            for links in page_links:
                for l in links:
                    if article_like(l) and l.url not in list_urls and is_recruit_title(l.title):
                        cands.append(Candidate(l, f"官网·{name}", school=name))
            # 列表页里一条招聘都没有，也提示
            if page_links and not cands and h["status"] == "ok":
                h.update(status="ok", detail="页面正常，暂无招聘标题")
        except Exception as e:  # 单个学校出错不影响其他学校
            h.update(status="error", detail=f"{type(e).__name__}: {e}")
        site["last_status"] = h["status"]
        with self.state.lock:
            self.state.sites[name] = site
            self.health.schools[name] = h
        return cands

    # ------------------------------------------------------------------ 聚合网站
    def _portal_keep(self, title: str, strict_school: bool) -> bool:
        if not is_recruit_title(title):
            return False
        if find_schools(title, self.schools):
            return True
        if strict_school:
            return False
        return bool(VOCATIONAL_HINT.search(title) and re.search(JS_CITIES, title)) or \
            bool(MULTI_UNIT.search(title) and re.search(JS_CITIES, title))

    def crawl_portal(self, p: dict) -> list[Candidate]:
        name = p["name"]
        cache = self.state.sites.get("portal:" + name, {})
        h = {"status": "ok", "detail": "", "url": cache.get("home") or p.get("url", "")}
        cands = []
        try:
            strict = "高校招聘" in name  # 全国栏目只要我们名单里的学校
            if p["type"] == "list":
                pg = self.f.get(p["url"])
                link_groups = [extract_links(pg.text, pg.url)]
            else:
                home_page = None
                city = name.removesuffix("市人力资源和社会保障局")
                names = [name, name.replace("人力资源和社会保障局", "人社局"),
                         name[:3] + "人力资源", city + "人社", city + "人力资源"]
                for url in dict.fromkeys(filter(None, (cache.get("home"), p.get("url")))):
                    try:
                        pg = self.f.get(url)
                    except FetchError:
                        continue
                    home_links = extract_links(pg.text, pg.url)
                    if city == name or page_matches(pg, names) or any(
                        term in link.title for link in home_links for term in p.get("nav", [])
                    ):
                        home_page = pg
                        break
                if home_page is None:  # 自动找官网
                    for l in search(self.f, f"{name} 官网", status=self.health.lookup)[:6]:
                        host = urlparse(l.url).hostname or ""
                        if host.endswith(".gov.cn") and not_blacklisted(l.url):
                            try:
                                pg = self.f.get(f"{urlparse(l.url).scheme}://{host}/")
                            except FetchError:
                                continue
                            if page_matches(pg, names):
                                home_page = pg
                                home_links = extract_links(pg.text, pg.url)
                                break
                if home_page is None:
                    raise FetchError("入口网站打不开，也没能自动找到")
                cache["home"] = home_page.url
                h["url"] = home_page.url
                link_groups = [home_links]
                navs = [l for l in home_links
                        if any(k in l.title for k in p.get("nav", [])) and same_site(l.url, home_page.url)]
                for l in navs[:5]:
                    try:
                        pg = self.f.get(l.url, referer=home_page.url)
                        link_groups.append(extract_links(pg.text, pg.url))
                    except FetchError:
                        continue
            for links in link_groups:
                for l in links:
                    if article_like(l) and self._portal_keep(l.title, strict):
                        cands.append(Candidate(l, name))
            h["detail"] = f"{len(link_groups)} 个页面，{len(cands)} 条相关标题"
        except Exception as e:
            h.update(status="error", detail=f"{type(e).__name__}: {e}")
        with self.state.lock:
            self.state.sites["portal:" + name] = cache
            self.health.portals[name] = h
        return cands

    # ------------------------------------------------------------------ 搜索补漏
    def crawl_search(self) -> list[Candidate]:
        queries = [(q, None) for q in self.cfg.get("search_queries", [])]
        n = min(self.cfg.get("search_schools_per_day", 15), len(self.schools))
        start = self.state.meta.get("rotation", 0) % max(1, len(self.schools))
        batch = (self.schools + self.schools)[start:start + n]
        self.state.meta["rotation"] = start + n
        queries.extend((f"{s['name']} 招聘", s) for s in batch)
        cands = []
        with ThreadPoolExecutor(min(3, self.cfg["workers"])) as ex:
            futures = [ex.submit(search, self.f, q, fresh="month" if school else "week",
                                 status=self.health.search) for q, school in queries]
            for (q, school), future in zip(queries, futures):
                try:
                    links = future.result()
                except Exception as e:
                    self.log(f"[warn] 搜索异常（{q}）：{e}")
                    continue
                for link in links:
                    text = link.title + link.context
                    if school:
                        if is_recruit_title(link.title) and any(
                            name in text for name in [school["name"], *(school.get("aliases") or [])]
                        ):
                            cands.append(Candidate(link, "搜索补漏", school=school["name"]))
                    elif self._portal_keep(link.title, strict_school=False) or (
                        is_recruit_title(link.title) and find_schools(text, self.schools)
                    ):
                        cands.append(Candidate(link, "搜索补漏"))
        return cands

    # ------------------------------------------------------------------ 详情处理
    def process(self, c: Candidate) -> dict | None:
        link = c.link
        if link.pub_date and link.pub_date < self.min_date:
            self.state.mark_seen(link.url, link.title, "old")
            return None
        attach_notes, tables, text, title, images, pub = [], [], "", link.title, 0, link.pub_date
        try:
            path = urlparse(link.url).path.lower()
            pg = self.f.get(link.url, binary=path.endswith(ATTACH_EXT))
            if path.endswith(ATTACH_EXT) or "html" not in (pg.content_type or "html"):
                p = parse_attachment(pg.content, path)
                text, tables = p.text, p.tables
                if not p.ok:
                    attach_notes.append(p.note)
            else:
                art = extract_article(pg.text, pg.url)
                text, tables, images = art.text, art.tables, art.image_count
                pub = pub or art.pub_date
                short = title.rstrip(".…").strip()
                if art.title and is_recruit_title(art.title) and len(art.title) > len(short):
                    title = art.title
                if "gaoxiaojob.com/announcement/detail/" in pg.url:
                    m = re.search(r"/detail/(\d+)", pg.url)
                    if m:
                        try:
                            jl = self.f.get(f"https://www.gaoxiaojob.com/announcement/job-list?id={m.group(1)}")
                            ja = extract_article(jl.text, jl.url)
                            text += "\n" + ja.text
                            tables += ja.tables
                        except FetchError:
                            pass
                for a in art.attachments[:8]:
                    try:
                        ap = self.f.get(a.url, binary=True, referer=pg.url)
                        p = parse_attachment(ap.content, a.title + urlparse(a.url).path)
                        text += "\n" + p.text
                        tables += p.tables
                        if not p.ok:
                            attach_notes.append(f"{a.title[:30]}：{p.note}")
                            self.health.attachments_failed += 1
                    except FetchError as e:
                        attach_notes.append(f"{a.title[:30]}：下载失败（{e}）")
                        self.health.attachments_failed += 1
        except FetchError as e:
            self.health.detail_errors += 1
            n = self.state.add_fail(link.url)
            if n < 3:
                return None  # 明天再试
            attach_notes.append(f"详情页连续 {n} 天打不开（{e}）")

        if pub and pub < self.min_date:
            self.state.mark_seen(link.url, title, "old")
            return None
        v = analyze(title, text, tables, attach_notes, images, self.schools, self.cfg, source_school=c.school)
        if v.drop:
            self.state.mark_seen(link.url, title, "drop")
            return None
        if not c.school and not v.schools:
            # 来自聚合/搜索，但识别不出是哪所江苏高职：只有标题像江苏高职才保留
            if not (VOCATIONAL_HINT.search(title) and re.search(JS_CITIES, title + text[:300])):
                self.state.mark_seen(link.url, title, "drop")
                return None
        best = sorted([r for r in v.rows if r.school], key=lambda r: r.tier)
        school = c.school or (best[0].school if best else (v.schools[0] if v.schools else ""))
        info = self.school_map.get(school, {})
        deadline = extract_deadline(text[:6000], pub, self.today)
        item = {
            "title": title, "url": link.url, "source": c.source, "school": school,
            "city": info.get("city", ""), "stype": info.get("type", ""),
            "deadline": deadline.isoformat() if deadline else None,
            "schools": v.schools, "pub_date": pub.isoformat() if pub else None,
            "first_seen": self.today.isoformat(), "tier": v.tier, "kind": v.kind,
            "reasons": v.reasons, "roles": v.roles, "snippets": v.snippets,
            "rows": [r.__dict__ for r in v.rows[:30]], "needs_manual": v.needs_manual,
        }
        self.state.mark_seen(link.url, title, v.tier)
        is_new = self.state.add_item(item)
        return item if is_new else None

    # ------------------------------------------------------------------ 总调度
    def run(self, use_search=True, only_school: str | None = None, *, school_names: set[str] | None = None) -> list[dict]:
        schools = [s for s in self.schools if (school_names is None or s["name"] in school_names)
                   and (not only_school or only_school in s["name"])]
        school_only = bool(only_school) or school_names is not None
        cands: list[Candidate] = []
        portals = [] if school_only else self.cfg.get("portals", [])
        start = monotonic()
        self.log(f"开始检查 {len(schools)} 所学校、{len(portals)} 个汇总来源")
        with ThreadPoolExecutor(self.cfg.get("source_workers", self.cfg["workers"])) as ex:
            search_job = ex.submit(self.crawl_search) if use_search and not school_only else None
            school_jobs, portal_jobs = [], []
            for school, portal in zip_longest(schools, portals):
                if school is not None:
                    school_jobs.append(ex.submit(self.crawl_school, school))
                if portal is not None:
                    portal_jobs.append(ex.submit(self.crawl_portal, portal))
            jobs = school_jobs + portal_jobs + ([search_job] if search_job else [])
            results = {}
            for done, fu in enumerate(as_completed(jobs), start=1):
                try:
                    results[fu] = fu.result()
                except Exception as e:  # pragma: no cover
                    self.log(f"[warn] 数据源异常：{e}")
                if done % 10 == 0 or done == len(jobs):
                    self.log(f"来源检查 {done}/{len(jobs)}，已用 {monotonic() - start:.0f} 秒")
            # 按官网、汇总来源、补充搜索的顺序去重，避免完成先后影响同一公告的来源。
            for job in jobs:
                cands.extend(results.get(job, ()))
        # 去重 + 跳过已处理
        uniq: dict[str, Candidate] = {}
        for c in cands:
            if self.state.is_seen(c.link.url):
                continue
            prev = uniq.get(c.link.url)
            if prev is None or (c.school and not prev.school):
                uniq[c.link.url] = c
        self.log(f"来源检查用时 {monotonic() - start:.0f} 秒。候选公告 {len(cands)} 条，其中未处理过的 {len(uniq)} 条")
        start = monotonic()
        new_items = []
        with ThreadPoolExecutor(self.cfg["workers"]) as ex:
            for fu in as_completed([ex.submit(self.process, c) for c in uniq.values()]):
                try:
                    it = fu.result()
                except Exception as e:  # pragma: no cover
                    self.log(f"[warn] 处理失败：{e}")
                    continue
                if it:
                    new_items.append(it)
        self.log(f"公告详情及附件用时 {monotonic() - start:.0f} 秒")
        return new_items
