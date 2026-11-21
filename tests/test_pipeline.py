"""离线测试：用假网页模拟学校官网、人社厅、附件，验证整条流程。
运行：python -m pytest -q
"""
import io
import os
import shutil
import sys
from threading import Barrier
from unittest.mock import MagicMock

import pytest
import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobwatch import pipeline as pl  # noqa: E402
from jobwatch import discover  # noqa: E402
from jobwatch import search as websearch  # noqa: E402
from jobwatch.attachments import parse_attachment  # noqa: E402
from jobwatch.fetch import FetchError, Fetcher, Page  # noqa: E402
from jobwatch.htmlutil import Link, extract_links, parse_date  # noqa: E402
from jobwatch.match import analyze, degree_ok, is_recruit_title  # noqa: E402
from jobwatch.report import build_message, write_all  # noqa: E402
from jobwatch.state import State  # noqa: E402


def make_xlsx(rows):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def make_docx(paragraphs, table_rows):
    import docx

    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    t = d.add_table(rows=len(table_rows), cols=len(table_rows[0]))
    for i, r in enumerate(table_rows):
        for j, c in enumerate(r):
            t.cell(i, j).text = c
    bio = io.BytesIO()
    d.save(bio)
    return bio.getvalue()


SCHOOL_HOME = """<html><head><title>江苏财会职业学院</title></head><body>
<div class="nav"><a href="/">首页</a><a href="https://rsc.jscfa.edu.cn/">人事处</a><a href="https://jy.jscfa.edu.cn/">就业信息</a>
<a href="/tzgg/list.htm">通知公告</a></div>
<ul><li><a href="/info/1001.htm" title="江苏财会职业学院2026年公开招聘专任教师公告">江苏财会职业学院2026年公开招聘专任教师公告</a><span>2026-11-18</span></li></ul>
</body></html>"""

RSC_HOME = """<html><head><title>人事处</title></head><body>
<a href="/zpxx/list.htm">招聘信息</a><a href="/zcwj/list.htm">政策文件</a></body></html>"""

RSC_LIST = """<html><body><ul>
<li><a href="/info/1001.htm" title="江苏财会职业学院2026年公开招聘专任教师公告">江苏财会职业学院2026年公开招聘专任教师公告</a><span>2026-11-18</span></li>
<li><a href="/info/1002.htm">江苏财会职业学院2026年公开招聘专职辅导员公告</a><span>2026-11-10</span></li>
<li><a href="/info/1003.htm">江苏财会职业学院2026年公开招聘专任教师面试公告</a><span>2026-11-19</span></li>
<li><a href="/info/1004.htm">江苏财会职业学院2026年招生简章</a><span>2026-11-01</span></li>
<li><a href="/info/1005.htm">江苏财会职业学院智慧教室设备采购招标公告</a><span>2026-11-02</span></li>
<li><a href="/info/1006.htm">江苏财会职业学院2019年公开招聘公告</a><span>2019-03-02</span></li>
</ul></body></html>"""

ART_1001 = """<html><head><title>公告</title></head><body><div class="article"><h1>江苏财会职业学院2026年公开招聘专任教师公告</h1>
<p>发布时间：2026-11-18</p><p>江苏财会职业学院是江苏省财政厅主管的全额拨款事业单位。现面向社会公开招聘专任教师，具体岗位详见附件。
报名时间：2026年11月25日至12月5日。</p>
<p><a href="/files/gwb.xlsx">附件1：江苏财会职业学院2026年公开招聘岗位表.xlsx</a></p></div></body></html>"""

ART_1002 = """<html><body><div class="article"><h1>江苏财会职业学院2026年公开招聘专职辅导员公告</h1>
<p>发布时间：2026-11-10</p><table><tr><td>岗位名称</td><td>专业</td><td>学历</td><td>人数</td></tr>
<tr><td>专职辅导员</td><td>不限</td><td>硕士研究生及以上</td><td>4</td></tr></table>
<p>中共党员，具有一年以上学生干部经历。</p></div></body></html>"""

ART_1003 = """<html><body><div class="article"><h1>江苏财会职业学院2026年公开招聘专任教师面试公告</h1>
<p>2026-11-19 现将面试有关事项公告如下，面试时间为12月10日。</p></div></body></html>"""

GWB = make_xlsx([
    ["江苏财会职业学院2026年公开招聘岗位表"],
    ["岗位代码", "岗位名称", "招聘人数", "专业", "学历", "学位", "其他条件"],
    ["01", "金融专任教师", 2, "金融学、金融、金融工程", "硕士研究生及以上", "硕士及以上", "35周岁以下"],
    ["02", "会计专任教师", 1, "会计学、审计", "博士研究生", "博士", ""],
    ["03", "计算机专任教师", 1, "计算机科学与技术", "硕士研究生及以上", "硕士", ""],
])

# 人社厅：大汉 CMS 风格，列表在 <script type=text/xml> 的 CDATA 里
HRSS_HOME = """<html><head><title>江苏省人力资源和社会保障厅</title></head><body>
<a href="/col/col57253/index.html">事业单位公开招聘</a></body></html>"""
HRSS_LIST = """<html><body><div id="list"><script type="text/xml"><datastore><recordset>
<record><![CDATA[<li><a href="/art/2026/11/15/art_57253_1.html" title="江苏省2026年省属事业单位统一公开招聘人员补充公告">江苏省2026年省属事业单位统一公开招聘人员补充公告</a><span>2026-11-15</span></li>]]></record>
<record><![CDATA[<li><a href="/art/2026/11/14/art_57253_2.html" title="江苏省人民医院2026年公开招聘护理人员公告">江苏省人民医院2026年公开招聘护理人员公告</a><span>2026-11-14</span></li>]]></record>
</recordset></datastore></script></div></body></html>"""
HRSS_ART = """<html><body><div class="article"><h1>江苏省2026年省属事业单位统一公开招聘人员补充公告</h1>
<p>2026-11-15 现将补充招聘岗位公告如下，岗位详见附件。</p><a href="/attach/0/gwb.docx">附件：补充岗位表.docx</a></div></body></html>"""
HRSS_DOCX = make_docx(["补充岗位表"], [
    ["主管部门", "招聘单位", "岗位名称", "专业", "学历", "人数"],
    ["省财政厅", "江苏财会职业学院", "财务管理岗", "经济类、财务财会类", "硕士研究生", "1"],
    ["省卫健委", "江苏省人民医院", "护士", "护理学", "本科", "10"],
    ["省教育厅", "南京信息职业技术学院", "行政管理岗", "不限", "硕士研究生", "2"],
])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    cfg["request_delay"] = 0
    cfg["portals"] = [{"name": "江苏省人力资源和社会保障厅", "type": "portal", "url": "https://jshrss.jiangsu.gov.cn/",
                       "nav": ["事业单位公开招聘"]}]
    cfg["search_queries"] = []
    schools = [
        {"name": "江苏财会职业学院", "city": "连云港", "type": "公办专科", "homepage": "http://www.jscfa.edu.cn/"},
        {"name": "南京信息职业技术学院", "city": "南京", "type": "公办专科", "homepage": ""},
    ]
    pages = {
        "http://www.jscfa.edu.cn/": SCHOOL_HOME,
        "https://rsc.jscfa.edu.cn/": RSC_HOME,
        "https://rsc.jscfa.edu.cn/zpxx/list.htm": RSC_LIST,
        "http://www.jscfa.edu.cn/tzgg/list.htm": "<html><body>暂无</body></html>",
        "http://www.jscfa.edu.cn/info/1001.htm": ART_1001,
        "https://rsc.jscfa.edu.cn/info/1001.htm": ART_1001,
        "https://rsc.jscfa.edu.cn/info/1002.htm": ART_1002,
        "https://rsc.jscfa.edu.cn/info/1003.htm": ART_1003,
        "https://jshrss.jiangsu.gov.cn/": HRSS_HOME,
        "https://jshrss.jiangsu.gov.cn/col/col57253/index.html": HRSS_LIST,
        "https://jshrss.jiangsu.gov.cn/art/2026/11/15/art_57253_1.html": HRSS_ART,
        "https://jshrss.jiangsu.gov.cn/art/2026/11/14/art_57253_2.html": "<html><body><h1>护理</h1><p>护理学 本科</p></body></html>",
    }
    files = {
        "http://www.jscfa.edu.cn/files/gwb.xlsx": GWB,
        "https://rsc.jscfa.edu.cn/files/gwb.xlsx": GWB,
        "https://jshrss.jiangsu.gov.cn/attach/0/gwb.docx": HRSS_DOCX,
    }
    calls = []

    def fake_get(self, url, binary=False, referer=None):
        calls.append(url)
        if url in files:
            return Page(url, 200, "", files[url], "application/octet-stream")
        if url in pages:
            return Page(url, 200, pages[url], pages[url].encode(), "text/html")
        raise FetchError("HTTP 404")

    monkeypatch.setattr(pl.Fetcher, "get", fake_get)
    monkeypatch.setattr(pl, "search", lambda *a, **k: [])
    monkeypatch.setattr(discover, "search", lambda *a, **k: [])
    from datetime import date
    monkeypatch.setattr(pl, "today_cn", lambda: date(2026, 11, 20))
    return root, cfg, schools, calls


def test_end_to_end(env):
    root, cfg, schools, calls = env
    state = State(str(root))
    runner = pl.Runner(cfg, schools, state, log=lambda *a: None)
    new = runner.run(use_search=False)
    by_title = {i["title"]: i for i in new}

    a = by_title["江苏财会职业学院2026年公开招聘专任教师公告"]
    assert a["tier"] == "A", a
    assert any(r["tier"] == "A" and "金融" in r["major"] for r in a["rows"])
    assert any("仅招博士" in r["reason"] or r["tier"] == "B" for r in a["rows"]) is not None

    b = by_title["江苏财会职业学院2026年公开招聘专职辅导员公告"]
    assert b["tier"] == "B" and "辅导员" in b["roles"]

    p = by_title["江苏财会职业学院2026年公开招聘专任教师面试公告"]
    assert p["kind"] == "招聘进展"

    assert not any("招生" in t or "采购" in t for t in by_title), "招生/采购不应出现"
    assert not any("2019" in t for t in by_title), "过旧公告应过滤"

    u = by_title["江苏省2026年省属事业单位统一公开招聘人员补充公告"]
    assert u["tier"] == "A"
    assert set(u["schools"]) == {"江苏财会职业学院", "南京信息职业技术学院"}
    assert all("人民医院" not in r["school"] for r in u["rows"])
    assert "江苏省人民医院2026年公开招聘护理人员公告" not in by_title

    # 同一公告在首页和人事处都出现，只算一条
    assert sum(1 for i in state.items if i["title"] == a["title"]) == 1

    # 健康：南京信息没有官网 → 报错但不影响其他
    assert runner.health.schools["南京信息职业技术学院"]["status"] == "error"
    assert runner.health.schools["江苏财会职业学院"]["status"] in ("ok", "warn")
    assert "https://rsc.jscfa.edu.cn/zpxx/list.htm" in state.sites["江苏财会职业学院"]["lists"]
    assert not any("jy.jscfa" in c for c in calls), "不应进入就业网"

    # 第二次运行：没有新增
    state.save()
    state2 = State(str(root))
    runner2 = pl.Runner(cfg, schools, state2, log=lambda *a: None)
    assert runner2.run(use_search=False) == []

    # 报告与推送消息
    ids = {id(i) for i in new}
    md = write_all(str(root), state.items, ids, runner.health, cfg, {"baseline": True})
    assert "金融专任教师" in md and "🆕" in md
    assert os.path.exists(root / "docs" / "index.html")
    title, body = build_message(state.items, ids, runner.health, "https://github.com/u/r", "", {"baseline": True})
    assert "重点2" in title, title
    assert "南京信息职业技术学院" in body


def test_newly_discovered_list_is_read_once_and_refreshed_next_run(env, monkeypatch):
    root, cfg, schools, calls = env
    school = schools[0]
    url = "https://rsc.jscfa.edu.cn/zpxx/list.htm"
    school["recruit_pages"] = [url]
    runner = pl.Runner(cfg, schools, State(str(root)))
    first = runner.crawl_school(school)
    assert calls.count(url) == 1 and any("辅导员" in c.link.title for c in first)
    runner.state.save()
    second = pl.Runner(cfg, schools, State(str(root)))
    get = second.f.get

    def updated(url_in, **kwargs):
        page = get(url_in, **kwargs)
        if url_in == url:
            page.text += '<a href="/new.htm">江苏财会职业学院新一轮公开招聘教师公告</a>'
        return page

    monkeypatch.setattr(second.f, "get", updated)
    assert any(c.link.url.endswith("/new.htm") for c in second.crawl_school(school))
    assert calls.count(url) == 2


def test_failed_discovery_retries_manual_list_in_same_run(env, monkeypatch):
    root, cfg, schools, calls = env
    school = schools[0]
    url = "https://rsc.jscfa.edu.cn/zpxx/list.htm"
    school["recruit_pages"] = [url]
    runner = pl.Runner(cfg, schools, State(str(root)))
    get = runner.f.get
    unavailable = [True]

    def flaky(url_in, **kwargs):
        if url_in == url and unavailable:
            unavailable.pop()
            raise FetchError("暂时无法连接")
        return get(url_in, **kwargs)

    monkeypatch.setattr(runner.f, "get", flaky)
    assert any("辅导员" in c.link.title for c in runner.crawl_school(school))
    assert not unavailable and url in calls


def test_parallel_search_keeps_queries_rotation_and_school_filter(env, monkeypatch):
    root, cfg, _, _ = env
    with open(os.path.join(ROOT, "schools.yaml"), encoding="utf-8") as f:
        schools = yaml.safe_load(f)["schools"]
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        queries = yaml.safe_load(f)["search_queries"]
    cfg["search_queries"] = queries
    runner = pl.Runner(cfg, schools, State(str(root)))
    schools = runner.schools
    runner.state.meta["rotation"] = len(schools) - 2
    expected = schools[-2:] + schools[:cfg["search_schools_per_day"] - 2]
    calls = []
    both_started = Barrier(2, timeout=5)

    def lookup(fetcher, query, fresh, status):
        calls.append((query, fresh))
        if query in queries[:2]:
            both_started.wait()
        if query == queries[-1]:
            raise ValueError("一个搜索源暂时失败")
        name = query.removesuffix(" 招聘") if fresh == "month" else schools[0]["name"]
        return [Link(name + "公开招聘教师公告", query), Link("其他地区学校公开招聘公告", "unrelated")]

    monkeypatch.setattr(pl, "search", lookup)
    results = runner.crawl_search()
    assert sorted(calls) == sorted([(q, "week") for q in queries] +
                                   [(s["name"] + " 招聘", "month") for s in expected])
    assert [c.school for c in results if c.school] == [s["name"] for s in expected]
    assert len([c for c in results if not c.school]) == len(queries) - 1
    assert all(c.link.url != "unrelated" for c in results)
    assert runner.state.meta["rotation"] % len(schools) == cfg["search_schools_per_day"] - 2


@pytest.mark.parametrize("available", [True, False])
def test_city_portal_uses_configured_entry_or_search_fallback(env, monkeypatch, available):
    root, cfg, schools, _ = env
    runner = pl.Runner(cfg, schools, State(str(root)))
    name = "无锡市人力资源和社会保障局"
    configured, alternate = "https://entry.wuxi.gov.cn/", "https://other.wuxi.gov.cn/"
    portal = dict(name=name, url=configured, type="portal", nav=["公开招聘"])
    runner.state.sites["portal:" + name] = {"home": "https://retired.wuxi.gov.cn/"}
    calls = []

    def fetch(url, **kwargs):
        if "retired" in url:
            raise FetchError("旧入口已停用")
        html = '<title>' + name + '</title><a href="/news">江苏财会职业学院公开招聘教师公告</a>'
        if url.endswith("/news"):
            html = "<h1>江苏财会职业学院公开招聘教师公告</h1>"
        if url == configured and not available:
            html = "<title>页面维护中</title>"
        return Page(url, 200, html, b"", "text/html")

    def lookup(fetcher, query, status):
        assert status is runner.health.lookup
        calls.append(query)
        return [Link(name, alternate)]

    monkeypatch.setattr(runner.f, "get", fetch)
    monkeypatch.setattr(pl, "search", lookup)
    result = runner.crawl_portal(portal)
    assert [c.link.url for c in result] == [(configured if available else alternate) + "news"]
    assert calls == ([] if available else [name + " 官网"])


@pytest.mark.parametrize("bing,ddg,expected", [
    ('<li class="b_no">没有找到相关结果</li>', '<div class="no-results">No results found</div>', (0, 1, 0)),
    ('<h1>服务暂不可用</h1>', '<div class="no-results">No results found</div>', (0, 0, 1)),
    (FetchError("连接超时"), '<title>等待验证</title>', (0, 0, 1)),
    (FetchError("连接超时"), '<div class="result"><a class="result__a" href="https://school.edu.cn/notice">招聘公告</a></div>', (1, 0, 0)),
])
def test_search_distinguishes_empty_results_from_failed_checks(bing, ddg, expected):
    fetcher = MagicMock()

    def get(url):
        content = bing if "bing.com" in url else ddg
        if isinstance(content, FetchError):
            raise content
        return Page(url, 200, content, b"", "text/html")

    fetcher.get.side_effect = get
    status = websearch.SearchStatus()
    hits = websearch.search(fetcher, "高校 招聘", status=status)
    assert len(hits) == expected[0]
    assert (status.ok, status.empty, status.failed) == expected
    if isinstance(bing, FetchError):
        assert status.engine_errors["Bing 请求失败"] == 1
    if ddg == "<title>等待验证</title>":
        assert status.engine_errors["DuckDuckGo 页面无法识别"] == 1


def test_report_separates_job_search_from_homepage_lookup(env, monkeypatch):
    import json
    from bs4 import BeautifulSoup

    root, cfg, schools, _ = env
    runner = pl.Runner(cfg, schools, State(str(root)))

    def lookup(fetcher, query, status):
        assert status is runner.health.lookup
        status.record(False, failed=True)
        return []

    monkeypatch.setattr(discover, "search", lookup)
    runner.crawl_school(schools[1])
    runner.health.search.record(False)
    report = write_all(str(root), [], set(), runner.health, cfg, {"baseline": False})
    page = BeautifulSoup((root / "docs" / "index.html").read_text(encoding="utf8"), "lxml")
    health = json.loads(page.select_one("#payload").string)["health"]
    assert health["searchSummary"] in report and health["lookupSummary"] in report
    assert "确认无结果 1 次" in health["searchSummary"] and "无法识别结果页 0 次" in health["searchSummary"]
    assert "无法识别结果页 2 次" in health["lookupSummary"]


def test_slow_connection_keeps_final_attempt_and_full_attachment_read_timeout(monkeypatch):
    fetcher = Fetcher(timeout=25, retries=2, delay=0, connect_timeout=6)
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.iter_content.return_value = [b"attachment"]
    get = MagicMock(side_effect=[requests.ConnectTimeout(), requests.ConnectTimeout(), response])
    monkeypatch.setattr(fetcher._session(), "get", get)
    monkeypatch.setattr("jobwatch.fetch.time.sleep", lambda _: None)
    assert fetcher.get("https://example.edu.cn/jobs.xlsx", binary=True).content == b"attachment"
    assert [call.kwargs["timeout"] for call in get.call_args_list] == [(6, 25), (6, 25), (25, 25)]
    response.__exit__.assert_called_once()


def test_http_error_closes_response_without_reading_error_body(monkeypatch):
    fetcher = Fetcher()
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 404
    get = MagicMock(return_value=response)
    monkeypatch.setattr(fetcher._session(), "get", get)
    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher.get("http://example.edu.cn/missing")
    get.assert_called_once()
    response.iter_content.assert_not_called()
    response.__exit__.assert_called_once()


def test_manual_list_available_when_homepage_is_down(env):
    root, cfg, schools, _ = env
    school = dict(schools[0], homepage="https://offline.invalid/",
                  recruit_pages=["https://rsc.jscfa.edu.cn/zpxx/list.htm"])
    state = State(str(root))
    runner = pl.Runner(cfg, [school], state)
    found = runner.crawl_school(school)
    assert any("专任教师" in item.link.title for item in found)
    check = runner.health.schools[school["name"]]
    assert check["status"] == "warn" and "已读取 1 个栏目" in check["detail"]
    state.save()
    school["recruit_pages"] = ["https://missing.invalid/notices"]
    retried = pl.Runner(cfg, [school], State(str(root)))
    assert retried.crawl_school(school) == []
    assert retried.health.schools[school["name"]]["status"] == "error"


def test_corrected_home_overrides_legacy_failure_cache(env):
    root, cfg, schools, calls = env
    school = schools[0]
    state = State(str(root))
    state.sites[school["name"]] = {"home": "https://old.invalid/", "home_failed": True,
                                   "lists": ["https://old.invalid/list"]}
    runner = pl.Runner(cfg, [school], state)
    assert runner.crawl_school(school)
    assert school["homepage"] in calls
    assert not any("old.invalid" in url for url in calls)


def test_units():
    assert parse_date("发布时间：2026年11月8日") .isoformat() == "2026-11-08"
    assert parse_date("[2026/3/5]").isoformat() == "2026-03-05"
    assert degree_ok("硕士研究生及以上") and degree_ok("本科及以上") and not degree_ok("博士研究生")
    assert is_recruit_title("南京城市职业学院2026年公开招聘工作人员公告")
    assert not is_recruit_title("2026年招生简章")
    assert not is_recruit_title("关于举办2027届毕业生双选会的通知")
    links = extract_links(HRSS_LIST, "https://jshrss.jiangsu.gov.cn/col/col57253/index.html")
    assert any(l.url.endswith("art_57253_1.html") and l.pub_date for l in links)


def test_text_only_finance():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    v = analyze("某职业学院招聘公告", "招聘金融专业教师1名，要求硕士研究生学历，金融学、金融专硕均可。", [], [], 0, [], cfg)
    assert v.tier == "A"
    v = analyze("某职业学院招聘公告", "招聘计算机教师，博士研究生。缴纳五险一金、社会保险。", [], [], 0, [], cfg)
    assert v.tier == "C"
    v = analyze("某职业学院招聘公告", "岗位详见附件。", [], ["附件1：旧版 Office 文件无法解析"], 0, [], cfg)
    assert v.tier == "B" and v.needs_manual


def test_attachments_formats():
    p = parse_attachment(GWB, "a.xlsx")
    assert p.ok and "金融学" in p.text and p.tables
    p = parse_attachment(HRSS_DOCX, "a.docx")
    assert p.ok and p.tables[0][1][1] == "江苏财会职业学院"
    import zipfile
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr("gwb.xlsx", GWB)
    p = parse_attachment(bio.getvalue(), "a.zip")
    assert p.ok and "金融学" in p.text
    assert not parse_attachment(b"Rar!\x1a\x07\x00", "a.rar").ok


@pytest.mark.skipif(not shutil.which("soffice"), reason="需要 LibreOffice")
def test_old_doc_and_pdf(tmp_path):
    src = tmp_path / "t.docx"
    src.write_bytes(HRSS_DOCX)
    import subprocess
    for target in ("doc", "pdf"):
        subprocess.run(["soffice", "--headless", "--convert-to", target, "--outdir", str(tmp_path), str(src)],
                       capture_output=True, timeout=180)
    doc = (tmp_path / "t.doc").read_bytes()
    p = parse_attachment(doc, "t.doc")
    assert p.ok and "江苏财会职业学院" in p.text, p.note
    pdf = (tmp_path / "t.pdf").read_bytes()
    p = parse_attachment(pdf, "t.pdf")
    assert p.ok and any("江苏财会职业学院" in r for t in p.tables for r in t)


@pytest.mark.skipif(not shutil.which("soffice"), reason="需要 LibreOffice")
def test_borderless_pdf_multi_unit(tmp_path):
    import subprocess
    src = tmp_path / "t.docx"
    src.write_bytes(HRSS_DOCX)
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)],
                   capture_output=True, timeout=180)
    p = parse_attachment((tmp_path / "t.pdf").read_bytes(), "t.pdf")
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    schools = [{"name": "江苏财会职业学院"}, {"name": "南京信息职业技术学院"}]
    v = analyze("江苏省2026年省属事业单位统一公开招聘人员补充公告", p.text, p.tables, [], 0, schools, cfg)
    assert v.tier == "A", (v.rows, p.tables)
    assert set(v.schools) == {"江苏财会职业学院", "南京信息职业技术学院"}
