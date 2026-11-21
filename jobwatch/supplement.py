"""本机按当天云端日报选校；Actions 在同一发布队列中合并检查结果。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import re
from uuid import uuid4

from bs4 import BeautifulSoup
import yaml

from .pipeline import Health, Runner
from .report import write_all
from .state import State, norm_title, norm_url, today_cn


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_report(root):
    page = BeautifulSoup((root / "docs/index.html").read_text(encoding="utf-8"), "lxml")
    payload = page.select_one("script#payload")
    if not payload or not payload.string:
        raise ValueError("无法读取线上日报的学校检查结果")
    report = json.loads(payload.string)
    if read_json(root / "state/meta.json").get("last_run") != report["today"] or report.get("demo"):
        raise ValueError("仓库状态和已生成的日报日期不一致，请等云端运行结束")
    health = report["health"]
    if not health["total"] or len(health["errors"]) != health["err"] or len(health["warnings"]) != health["warn"]:
        raise ValueError("云端学校检查结果不完整")
    return report


def read_ledger(root):
    path = root / "state/local-checks.json"
    return read_json(path) if path.exists() else {"days": {}, "receipts": []}


def collect(root, destination):
    report = read_report(root)
    day = today_cn().isoformat()
    if report["today"] != day:
        print(f"北京时间今天是 {day}，最新云端日报为 {report['today']}。请等今天的定时任务发布后再补查。")
        return
    health = report["health"]
    prior = read_ledger(root)["days"].get(day, {}).get("schools", {})
    names = {n for n in health["errors"] + health["warnings"] if prior.get(n) != "ok"}
    if not names:
        print("当天没有待补查的学校。")
        return
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    schools = yaml.safe_load((root / "schools.yaml").read_text(encoding="utf-8"))["schools"]
    state = State(str(root))
    before = {id(i): deepcopy(i) for i in state.items}
    runner = Runner(cfg, schools, state)
    if names - set(runner.school_map):
        raise ValueError("日报与学校配置不一致，请更新仓库后重试")
    print(f"本地补查 {len(names)} 所学校（云端日报 {day}）", flush=True)
    runner.run(use_search=False, school_names=names)
    statuses = {n: runner.health.schools.get(n, {}).get("status", "error") for n in sorted(names)}
    items = [item for item in state.items if item != before.get(id(item))]
    batch = {"id": uuid4().hex, "day": day, "schools": statuses, "items": items}
    tmp = destination.with_suffix(".tmp")
    tmp.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    tmp.replace(destination)
    print(f"已补查 {len(names)} 所；待合并的新公告或补充来源共 {len(items)} 条。", flush=True)


def merge_item(state, urls, item):
    addresses = [item["url"], *item.get("alt_urls", [])]
    if not all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in addresses):
        raise ValueError("公告地址格式错误")
    key = norm_title(item["title"]) + "|" + (item.get("school") or "")
    known = next((urls[norm_url(u)] for u in addresses if norm_url(u) in urls), None)
    known = known or state.title_index.get(key)
    if known is not None:
        item = dict(item, title=known["title"], school=known.get("school", ""))
    is_new = state.add_item(item)
    saved = item if is_new else known
    for u in addresses:
        if u != saved["url"] and u not in saved.get("alt_urls", []):
            saved.setdefault("alt_urls", []).append(u)
        urls[norm_url(u)] = saved
        state.mark_seen(u, saved["title"], saved["tier"])
    return is_new


def merge(root):
    pending = sorted((root / "local-inbox").glob("*.json"))
    ledger = read_ledger(root)
    if not pending and not ledger["receipts"]:
        return
    report = read_report(root)
    day = report["today"]
    # 已发布的原始健康检查保留各校的错误详情、汇总来源以及搜索统计。
    marker = "## 🩺 抓取健康情况"
    md = (root / "REPORT.md").read_text(encoding="utf-8")
    if marker not in md:
        raise ValueError("找不到云端日报的健康检查，已停止合并")
    for p in ("data/items.json", "state/seen.json", "state/sites.json", "state/fails.json"):
        read_json(root / p)
    state = State(str(root))
    urls = {norm_url(u): i for i in state.items for u in [i["url"], *i.get("alt_urls", [])]}
    accepted = set(ledger["receipts"])
    rank = {"error": 0, "warn": 1, "ok": 2}
    for path in pending:
        batch = read_json(path)
        receipt = batch["id"]
        if not re.fullmatch(r"[a-f0-9]{32}", receipt):
            raise ValueError(f"无效的补查批次：{path.name}")
        if receipt in accepted:
            continue
        batch_day = date.fromisoformat(batch["day"]).isoformat()
        if batch_day > day:
            raise ValueError("补查日期晚于最新云端日报")
        record = ledger["days"].setdefault(batch_day, {"schools": {}, "added": 0})
        for n, status in batch["schools"].items():
            if status not in rank:
                raise ValueError(f"学校 {n} 的检查状态无效")
            old = record["schools"].get(n)
            if old is None or rank[status] > rank[old]:
                record["schools"][n] = status
        for item in batch["items"]:
            record["added"] += int(merge_item(state, urls, item))
        accepted.add(receipt)
        ledger["receipts"].append(receipt)

    info = {"report_day": day, "cloud_health": report["health"],
            "cloud_health_md": (marker + md.split(marker, 1)[1]).splitlines(),
            "local_receipts": ledger["receipts"][-100:]}
    record = ledger["days"].get(day)
    if record:
        counts = list(record["schools"].values())
        info["local_note"] = (f"今日另经本地补查 {len(counts)} 所：正常 {counts.count('ok')} 所，"
                              f"部分可读 {counts.count('warn')} 所，未能读取 {counts.count('error')} 所；"
                              f"合并新增公告 {record['added']} 条。下方健康检查为云端结果。")
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    new_ids = {id(i) for i in state.items if i.get("first_seen") == day}
    write_all(str(root), state.items, new_ids, Health(), cfg, info)
    if pending:
        state.save()
        state._dump("state/local-checks.json", ledger)
        for path in pending:
            path.unlink()
    print(info.get("local_note", "已合并历史补查结果"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="action", required=True)
    collect_cmd = sub.add_parser("collect")
    collect_cmd.add_argument("destination", type=Path)
    sub.add_parser("merge")
    args = ap.parse_args()
    root = Path.cwd()
    if args.action == "collect":
        collect(root, args.destination)
    else:
        merge(root)


if __name__ == "__main__":
    main()
