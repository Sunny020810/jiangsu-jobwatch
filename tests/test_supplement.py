from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobwatch import supplement as su
from jobwatch.pipeline import Health, Runner
from jobwatch.report import write_all
from jobwatch.state import State, today_cn


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path
    (root / "local-inbox").mkdir()
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    (root / "config.yaml").write_bytes(cfg_path.read_bytes())
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    health = Health()
    health.schools = {n: {"status": s, "detail": "云端检查记录"} for n, s in
                      (("甲校", "ok"), ("乙校", "warn"), ("丙校", "error"))}
    schools = [{"name": n, "type": cfg["include_types"][0]} for n in health.schools]
    (root / "schools.yaml").write_text(yaml.safe_dump({"schools": schools}), encoding="utf-8")
    health.portals["省厅"] = {"status": "ok", "detail": "保留汇总来源"}
    state = State(str(root))
    state.meta.update(last_run=today_cn().isoformat(), rotation=45)
    state.sites = deepcopy(health.schools)
    state.save()
    write_all(str(root), [], set(), health, cfg, {})
    return root


def item(title="招聘公告", url="https://example.org/jobs/1"):
    return dict(title=title, url=url, school="乙校", tier="C", first_seen=today_cn().isoformat(),
                source="官网·乙校", kind="招聘", reasons=[], rows=[], roles=[], schools=["乙校"])


def post(root, receipt, items, schools=None, day=None):
    batch = {"id": receipt * 32, "items": items, "schools": schools or {"乙校": "ok"},
             "day": (day or today_cn()).isoformat()}
    path = root / "local-inbox" / (receipt + ".json")
    path.write_text(json.dumps(batch), encoding="utf-8")


def test_collect_only_remaining_exact_schools(repo, monkeypatch, tmp_path):
    checked = []
    def school(self, s):
        checked.append(s["name"])
        self.health.schools[s["name"]] = {"status": "ok"}
        return []
    monkeypatch.setattr(Runner, "crawl_school", school)
    monkeypatch.setattr(Runner, "crawl_portal", lambda *a: pytest.fail("unexpected portal scan"))
    monkeypatch.setattr(Runner, "crawl_search", lambda *a: pytest.fail("unexpected general search"))
    out = tmp_path / "pending.json"
    post(repo, "a", [], {"乙校": "ok"})
    su.merge(repo)
    su.collect(repo, out)
    assert checked == ["丙校"]
    assert su.read_json(out)["schools"] == {"丙校": "ok"}
    assert su.read_json(repo / "state/meta.json")["rotation"] == 45


def test_yesterday_or_complete_day_does_not_scan(repo, monkeypatch):
    tomorrow = today_cn() + timedelta(days=1)
    monkeypatch.setattr(su, "today_cn", lambda: tomorrow)
    out = repo / "pending.json"
    su.collect(repo, out)
    assert not out.exists()
    monkeypatch.setattr(su, "today_cn", lambda: date.fromisoformat(su.read_report(repo)["today"]))
    post(repo, "a", [], {"乙校": "ok", "丙校": "ok"})
    su.merge(repo)
    su.collect(repo, out)
    assert not out.exists()


def test_idempotent_merge_preserves_remote_items_cloud_health_and_status(repo):
    state = State(str(repo))
    state.add_item(item("云端先发现的公告", "https://cloud.example/one"))
    state.save()
    cloud = su.read_report(repo)["health"]
    original_sites = su.read_json(repo / "state/sites.json")
    post(repo, "a", [item(), item("云端先发现的公告", "https://alt.example/one")])
    su.merge(repo)
    post(repo, "a", [item()])
    post(repo, "b", [item("同网址换了标题")], {"乙校": "warn", "丙校": "error"})
    su.merge(repo)
    page = su.read_report(repo)
    assert len(page["items"]) == 2 and all(i["new"] for i in page["items"])
    assert len(State(str(repo)).items) == 2
    assert "https://alt.example/one" in next(i for i in page["items"] if i["title"] == "云端先发现的公告")["alt"]
    assert page["health"] == cloud and su.read_json(repo / "state/sites.json") == original_sites
    assert page["local_receipts"] == ["a"*32, "b"*32]
    assert "正常 1 所" in page["local_note"] and "新增公告 1 条" in page["local_note"]
    md = (repo / "REPORT.md").read_text(encoding="utf-8")
    assert md.count("今日另经本地补查") == 1 and "保留汇总来源" in md
    assert not list((repo / "local-inbox").glob("*.json"))


def test_late_batch_does_not_change_today_school_status(repo):
    post(repo, "c", [item()], {"丙校": "ok"}, today_cn()-timedelta(days=1))
    su.merge(repo)
    page = su.read_report(repo)
    assert len(page["items"]) == 1 and not page["local_note"]
    assert today_cn().isoformat() not in su.read_ledger(repo)["days"]
