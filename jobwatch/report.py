"""生成报告：REPORT.md（GitHub 里直接看）、docs/index.html（网页版）、推送消息。"""
from __future__ import annotations

import html
import json
import os
from datetime import date, datetime, timedelta
from urllib.parse import quote

from .state import CN_TZ, today_cn

TIER_NAME = {"A": "🔴 重点：金融对口", "B": "🟡 可能符合，建议查看", "C": "⚪ 其他招聘公告（全部列出，防止漏掉）",
             "P": "🔵 招聘进展（笔试/面试/公示等）"}
TIER_SHORT = {"A": "重点", "B": "可能", "C": "其他", "P": "进展"}


def in_window(item: dict, days: int, today: date) -> bool:
    d = item.get("pub_date") or item.get("first_seen")
    try:
        return date.fromisoformat(d) >= today - timedelta(days=days)
    except (TypeError, ValueError):
        return True


def _sort_key(i):
    return (i.get("pub_date") or i.get("first_seen") or ""), i.get("first_seen", "")


def _row_summary(item: dict, limit=4) -> str:
    rows = [r for r in item.get("rows", []) if r["tier"] in ("A", "B")]
    rows.sort(key=lambda r: r["tier"])
    parts = []
    for r in rows[:limit]:
        bits = [b for b in (r.get("school") if r.get("school") != item.get("school") else "", r.get("post"),
                            "专业:" + r["major"][:40] if r.get("major") else "",
                            "学历:" + r["degree"][:20] if r.get("degree") else "",
                            ("人数:" + r["count"]) if r.get("count") else "") if b]
        parts.append(("【对口】" if r["tier"] == "A" else "【可能】") + " ".join(bits))
    if len(rows) > limit:
        parts.append(f"……另有 {len(rows) - limit} 个相关岗位")
    return "；".join(parts)


def school_label(item: dict) -> str:
    n = len(item.get("schools") or [])
    base = item.get("school") or "待确认"
    return f"{base} 等{n}所" if n > 1 else base


def _md_escape(s: str) -> str:
    return (s or "").replace("|", "／").replace("\n", " ")


def build_markdown(items, new_ids, health, cfg, today, run_info) -> str:
    win = cfg["report_window_days"]
    shown = sorted([i for i in items if in_window(i, win, today)], key=_sort_key, reverse=True)
    new_count = {t: sum(1 for i in items if id(i) in new_ids and i["tier"] == t) for t in "ABCP"}
    out = [f"# 江苏高职招聘日报（金融专业硕士）",
           f"",
           f"更新时间：{datetime.now(CN_TZ):%Y-%m-%d %H:%M}（北京时间）　·　展示近 {win} 天公告　·　🆕 = 本次新发现",
           f"",
           f"**本次新增**：重点 {new_count['A']}　可能 {new_count['B']}　其他 {new_count['C']}　进展 {new_count['P']}",
           ""]
    errs = sum(1 for h in health.schools.values() if h["status"] == "error")
    if health.schools and errs > len(health.schools) / 2:
        out += [f"> ⚠️ 本次有 {errs}/{len(health.schools)} 所学校官网抓取失败，结果可能不完整，详见文末健康检查。", ""]
    if run_info.get("baseline"):
        out += ["> 这是首次运行，下面是近期已发布的全部存量公告；以后每天只推送新增。", ""]
    for t in "ABCP":
        group = [i for i in shown if i["tier"] == t]
        out.append(f"## {TIER_NAME[t]}（{len(group)}）")
        if not group:
            out += ["", "暂无", ""]
            continue
        out += ["", "| 发布 | 学校 | 公告 | 说明 |", "|---|---|---|---|"]
        for i in group:
            flag = "🆕 " if id(i) in new_ids else ""
            info = _row_summary(i) if t in "AB" else ""
            reasons = "；".join(i.get("reasons", [])[:3])
            dl = f"报名截止 {i['deadline']}" if i.get("deadline") else ""
            note = "；".join(x for x in (dl, info, reasons, "⚠️需人工查看" if i.get("needs_manual") else "") if x)
            out.append(f"| {i.get('pub_date') or '未知'} | {_md_escape(school_label(i))} | "
                       f"{flag}[{_md_escape(i['title'])}]({i['url']}) | {_md_escape(note)[:300]} |")
        out.append("")
    if run_info.get("local_note"):
        out += [run_info["local_note"], ""]
    out += run_info.get("cloud_health_md") or build_health_md(health)
    return "\n".join(out)


def _search_summary(status) -> str:
    counts = f"有结果 {status.ok} 次，确认无结果 {status.empty} 次，请求失败或无法识别结果页 {status.failed} 次"
    if status.engine_errors:
        attempts = "、".join(f"{reason} {count} 次" for reason, count in sorted(status.engine_errors.items()))
        return f"{counts}（按引擎统计：{attempts}）"
    return counts


def build_health_md(health) -> list[str]:
    sch = health.schools
    ok = [n for n, h in sch.items() if h["status"] == "ok"]
    warn = [n for n, h in sch.items() if h["status"] == "warn"]
    err = [n for n, h in sch.items() if h["status"] == "error"]
    out = ["## 🩺 抓取健康情况", "",
           f"- 学校官网：正常 {len(ok)}，提示 {len(warn)}，异常 {len(err)}（共 {len(sch)} 所）",
           f"- 招聘公告补搜：{_search_summary(health.search)}",
           f"- 查找官网入口：{_search_summary(health.lookup)}",
           f"- 详情页打不开 {health.detail_errors} 次（会自动重试 3 天），附件未能解析 {health.attachments_failed} 个（已标为需人工查看）",
           ""]
    if warn or err:
        out += ["### 需要关注的学校", "",
                "这些学校的官网检查不完整。其他来源可能提供补充公告，实际结果见下方汇总网站及搜索统计。", "",
                "| 学校 | 状态 | 说明 | 帮你找 |", "|---|---|---|---|"]
        for n in err + warn:
            h = sch[n]
            q = quote(f"{n} 人事处 招聘")
            out.append(f"| {n} | {'❌' if h['status'] == 'error' else '⚠️'} | {_md_escape(h.get('detail', ''))[:120]} | "
                       f"[搜索](https://cn.bing.com/search?q={q}) |")
        out.append("")
    if health.portals:
        out += ["### 汇总网站", "", "| 来源 | 状态 | 说明 |", "|---|---|---|"]
        for n, h in health.portals.items():
            out.append(f"| {n} | {'✅' if h['status'] == 'ok' else '❌'} | {_md_escape(h.get('detail', ''))[:150]} |")
        out.append("")
    return out


def build_message(items, new_ids, health, repo_url: str, pages_url: str, run_info) -> tuple[str, str]:
    new = [i for i in items if id(i) in new_ids]
    cnt = {t: sum(1 for i in new if i["tier"] == t) for t in "ABCP"}
    today = today_cn()
    title = f"江苏高职招聘日报 {today:%m-%d}：重点{cnt['A']} 可能{cnt['B']} 其他{cnt['C']}"
    lines = [f"**{title}**", ""]
    errs = sum(1 for h in health.schools.values() if h["status"] == "error")
    if health.schools and errs > len(health.schools) / 2:
        title = "⚠️ " + title
        lines += [f"⚠️ **今天有 {errs}/{len(health.schools)} 所学校官网抓取失败，结果可能不完整。**"
                  "可能是网络或网站限制，请到 Actions 页面查看日志，或改在国内电脑运行。", ""]
    if run_info.get("baseline"):
        lines += ["首次运行：以下为近期存量公告，以后只推送新增。", ""]
    for t in ("A", "B"):
        group = sorted([i for i in new if i["tier"] == t], key=_sort_key, reverse=True)
        if group:
            lines.append(f"### {TIER_NAME[t]}")
            for i in group[:40]:
                extra = _row_summary(i, 2) or "；".join(i.get("reasons", [])[:2])
                if i.get("deadline"):
                    extra = f"报名截止 {i['deadline'][5:]}　" + extra
                lines.append(f"- [{school_label(i)}｜{i['title']}]({i['url']})" + (f"\n  - {extra}" if extra else ""))
            lines.append("")
    others = [i for i in new if i["tier"] in ("C", "P")]
    if others:
        lines.append(f"### 其他新公告 {len(others)} 条")
        for i in sorted(others, key=_sort_key, reverse=True)[:30]:
            lines.append(f"- [{TIER_SHORT[i['tier']]}] [{i.get('school') or ''}｜{i['title']}]({i['url']})")
        if len(others) > 30:
            lines.append(f"- ……共 {len(others)} 条，见完整报告")
        lines.append("")
    if not new:
        lines += ["今天没有发现新的招聘公告。", ""]
    sch = health.schools
    bad = [n for n, h in sch.items() if h["status"] == "error"]
    ok = sum(1 for h in sch.values() if h['status'] == 'ok')
    lines.append(f"🩺 {len(sch)} 所学校中，正常 {ok}，部分可读 {len(sch) - ok - len(bad)}，未能读取或确认 {len(bad)}"
                 + (f"（{'、'.join(bad[:10])}{'等' if len(bad) > 10 else ''}）" if bad else ""))
    if repo_url:
        lines.append(f"\n📄 [完整报告]({repo_url}/blob/main/REPORT.md)" + (f"　🌐 [网页版]({pages_url})" if pages_url else ""))
    return title, "\n".join(lines)


def build_html(items, new_ids, health, cfg, today, run_info=None) -> str:
    win = cfg["report_window_days"]
    shown = sorted([i for i in items if in_window(i, win, today)], key=_sort_key, reverse=True)
    data = []
    for i in shown:
        rows = sorted([r for r in i.get("rows", []) if r["tier"] in ("A", "B")], key=lambda r: r["tier"])[:6]
        data.append({
            "t": i["tier"], "title": i["title"], "url": i["url"], "school": school_label(i),
            "city": i.get("city", ""), "stype": i.get("stype", ""), "date": i.get("pub_date"),
            "dl": i.get("deadline"), "new": id(i) in new_ids, "roles": i.get("roles", []),
            "rows": [{"t": r["tier"], "post": r.get("post", ""), "major": r.get("major", ""),
                      "degree": r.get("degree", ""), "count": r.get("count", ""),
                      "school": r["school"] if r.get("school") and r["school"] != i.get("school") else ""} for r in rows],
            "why": "；".join(i.get("reasons", [])[:2]), "manual": i.get("needs_manual", False),
            "alt": i.get("alt_urls", [])[:3],
        })
    sch = health.schools
    err = [n for n, h in sch.items() if h["status"] == "error"]
    warn = [n for n, h in sch.items() if h["status"] == "warn"]
    payload = {
        "updated": f"{datetime.now(CN_TZ):%Y-%m-%d %H:%M}", "today": today.isoformat(), "window": win,
        "items": data,
        "health": {"total": len(sch), "err": len(err), "warn": len(warn), "errors": err, "warnings": warn,
                   "searchOk": health.search.ok, "searchEmpty": health.search.empty, "searchFailed": health.search.failed,
                   "searchSummary": _search_summary(health.search), "lookupSummary": _search_summary(health.lookup),
                   "portals": len(health.portals),
                   "portalsOk": sum(1 for h in health.portals.values() if h["status"] == "ok")},
    }
    if run_info:
        if "cloud_health" in run_info:
            payload["health"] = run_info["cloud_health"]
        payload["local_note"] = run_info.get("local_note", "")
        payload["local_receipts"] = run_info.get("local_receipts", [])
    tpl_path = os.path.join(os.path.dirname(__file__), "web.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    return tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))


def write_all(root, items, new_ids, health, cfg, run_info):
    today = date.fromisoformat(run_info["report_day"]) if "report_day" in run_info else today_cn()
    md = build_markdown(items, new_ids, health, cfg, today, run_info)
    with open(os.path.join(root, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write(md)
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "docs", "index.html"), "w", encoding="utf-8") as f:
        f.write(build_html(items, new_ids, health, cfg, today, run_info))
    return md
