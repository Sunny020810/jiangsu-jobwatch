"""命令行入口。

  python -m jobwatch run                 # 完整运行（抓取 + 报告 + 推送）
  python -m jobwatch run --no-notify     # 不推送，只生成报告
  python -m jobwatch run --school 财会    # 只跑名字包含“财会”的学校（调试用）
  python -m jobwatch report              # 只用已有数据重新生成报告
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import yaml

from .notify import send_all
from .pipeline import Health, Runner
from .report import build_message, write_all
from .state import State, today_cn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_yaml(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobwatch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--no-notify", action="store_true")
    r.add_argument("--no-search", action="store_true")
    r.add_argument("--school")
    sub.add_parser("report")
    args = ap.parse_args(argv)

    cfg = load_yaml("config.yaml")
    schools = load_yaml("schools.yaml")["schools"]
    state = State(ROOT)
    baseline = not state.seen
    t0 = time.time()

    if args.cmd == "report":
        write_all(ROOT, state.items, set(), Health(), cfg, {"baseline": False})
        print("报告已重新生成：REPORT.md、docs/index.html")
        return 0

    runner = Runner(cfg, schools, state)
    new_items = runner.run(use_search=not args.no_search, only_school=args.school)
    new_ids = {id(i) for i in new_items}
    state.meta["last_run"] = today_cn().isoformat()
    state.save()
    run_info = {"baseline": baseline}
    write_all(ROOT, state.items, new_ids, runner.health, cfg, run_info)

    cnt = {t: sum(1 for i in new_items if i["tier"] == t) for t in "ABCP"}
    print(f"完成，用时 {time.time() - t0:.0f} 秒。新增：重点 {cnt['A']}，可能 {cnt['B']}，其他 {cnt['C']}，进展 {cnt['P']}")
    print(f"请求统计：{dict(runner.f.stats)}")

    if not args.no_notify:
        notify_cfg = cfg.get("notify", {})
        if notify_cfg.get("only_when_new") and not new_items:
            print("没有新增，按设置不推送")
        else:
            os.environ.setdefault("NOTIFY_GITHUB_ISSUE", "true" if notify_cfg.get("github_issue", True) else "false")
            repo = os.getenv("GITHUB_REPOSITORY", "")
            repo_url = f"https://github.com/{repo}" if repo else ""
            owner, _, name = repo.partition("/")
            pages_url = os.getenv("PAGES_URL") or (f"https://{owner}.github.io/{name}/" if repo and os.getenv("PAGES_ENABLED") == "true" else "")
            title, body = build_message(state.items, new_ids, runner.health, repo_url, pages_url, run_info)
            done = send_all(title, body)
            print("已推送：" + ("、".join(done) if done else "无（未配置推送渠道）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
