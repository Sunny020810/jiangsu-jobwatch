"""持久化状态：已见过的公告、学校站点探测结果、历史条目。存在仓库里，由 Actions 每天提交。"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def today_cn() -> date:
    return datetime.now(CN_TZ).date()


def norm_url(u: str) -> str:
    u = u.strip()
    u = re.sub(r"^https?://", "", u)
    return u.rstrip("/")


def norm_title(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t or "")


class State:
    def __init__(self, root: str):
        self.root = root
        self.lock = threading.Lock()
        self.seen: dict = self._load("state/seen.json", {})
        self.sites: dict = self._load("state/sites.json", {})
        self.fails: dict = self._load("state/fails.json", {})
        self.meta: dict = self._load("state/meta.json", {})
        self.items: list = self._load("data/items.json", [])
        self.title_index = {norm_title(i["title"]) + "|" + (i.get("school") or ""): i for i in self.items}

    def _path(self, rel):
        return os.path.join(self.root, rel)

    def _load(self, rel, default):
        try:
            with open(self._path(rel), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _dump(self, rel, obj):
        path = self._path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)

    def save(self, keep_days: int = 400):
        cutoff = (today_cn() - timedelta(days=keep_days)).isoformat()
        self.items = [i for i in self.items if (i.get("first_seen") or "9999") >= cutoff]
        self._dump("state/seen.json", self.seen)
        self._dump("state/sites.json", self.sites)
        self._dump("state/fails.json", self.fails)
        self._dump("state/meta.json", self.meta)
        self._dump("data/items.json", self.items)

    # ---- seen ----
    def is_seen(self, url: str) -> bool:
        return norm_url(url) in self.seen

    def mark_seen(self, url: str, title: str, tier: str):
        with self.lock:
            self.seen[norm_url(url)] = {"t": title[:80], "d": today_cn().isoformat(), "tier": tier}
            self.fails.pop(norm_url(url), None)

    def add_fail(self, url: str) -> int:
        with self.lock:
            k = norm_url(url)
            self.fails[k] = self.fails.get(k, 0) + 1
            return self.fails[k]

    # ---- items（跨来源去重：同一学校同一标题算一条）----
    def add_item(self, item: dict) -> bool:
        key = norm_title(item["title"]) + "|" + (item.get("school") or "")
        with self.lock:
            prev = self.title_index.get(key)
            if prev:
                if item["url"] not in prev.setdefault("alt_urls", []) and item["url"] != prev["url"]:
                    prev["alt_urls"].append(item["url"])
                # 新来源分析得更好（比如读到了附件）就用更高的级别
                rank = {"A": 0, "B": 1, "C": 2, "P": 3}
                if rank.get(item["tier"], 9) < rank.get(prev["tier"], 9):
                    for k in ("tier", "reasons", "rows", "snippets", "needs_manual"):
                        prev[k] = item[k]
                return False
            self.items.append(item)
            self.title_index[key] = item
            return True
