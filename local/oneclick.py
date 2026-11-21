"""Windows 双击入口。首次安装发布流程，以后只提交本机的补查批次。"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


REPO = "Sunny020810/jiangsu-jobwatch"
REMOTE = f"https://github.com/{REPO}.git"
PUSH_URL = f"https://Sunny020810@github.com/{REPO}.git"
SITE = "https://sunny020810.github.io/jiangsu-jobwatch/"
BASE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "JiangsuJobwatch"
PATCH = Path(__file__).with_name("install.patch")


def git(repo, *args, check=True, capture=False):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          stdout=subprocess.PIPE if capture else None, text=True, encoding="utf-8", errors="replace")


def publish_commit(repo, message):
    git(repo, "-c", "user.name=Jobwatch Local", "-c", "user.email=jobwatch-local@users.noreply.github.com",
        "commit", "-qm", message)
    for attempt in range(4):
        base_revision = git(repo, "rev-parse", "origin/main", capture=True).stdout.strip()
        pushed = git(repo, "push", "origin", "HEAD:main", check=False)
        if pushed.returncode == 0:
            return
        git(repo, "fetch", "-q", "origin", "main")
        if git(repo, "rev-parse", "origin/main", capture=True).stdout.strip() == base_revision:
            raise RuntimeError("GitHub 未确认提交。请查看上方提示，并用 Sunny020810 授权后重新双击。")
        git(repo, "-c", "user.name=Jobwatch Local", "-c", "user.email=jobwatch-local@users.noreply.github.com",
            "rebase", "origin/main")
    raise RuntimeError("仓库正在更新。重新双击即可重试。")


def install(repo):
    if (repo / "jobwatch/supplement.py").is_file():
        workflow = (repo / ".github/workflows/daily.yml").read_text(encoding="utf-8")
        if "local-inbox/*.json" not in workflow or "jobwatch.supplement merge" not in workflow:
            raise RuntimeError("线上补查模块和工作流版本不一致，请检查 daily.yml。")
        return
    print("首次接入：将安装本地批次合并和原网址发布流程。", flush=True)
    git(repo, "apply", "--check", str(PATCH))
    git(repo, "apply", "--index", str(PATCH))
    publish_commit(repo, "接入本地补查，保留北京时间 04:00 定时发布")


def environment(repo):
    prefix = BASE / ("python-%s.%s" % sys.version_info[:2])
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(prefix)], check=True)
    req = repo / "requirements.txt"
    version = hashlib.sha256(req.read_bytes()).hexdigest()
    stamp = prefix / "requirements.sha256"
    if not stamp.exists() or stamp.read_text().strip() != version:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)], check=True)
        stamp.write_text(version)
    return python


def visible(receipt):
    request = Request(SITE + "?check=" + receipt + "&t=" + str(int(time.time())),
                      headers={"Cache-Control": "no-cache", "User-Agent": "JiangsuJobwatch"})
    try:
        with urlopen(request, timeout=12) as response:
            return ('"' + receipt + '"').encode() in response.read()
    except (URLError, TimeoutError, OSError):
        return False


def send_pending(repo, pending):
    batch = json.loads(pending.read_text(encoding="utf-8"))
    if visible(batch["id"]):
        pending.unlink()
        print("已确认上一次的补查发布成功。")
        return
    # 每次投递使用独立文件，GitHub 按批次 id 去重；发布中断后再次投递也安全。
    filename = "local-inbox/" + uuid4().hex + ".json"
    shutil.copyfile(pending, repo / filename)
    git(repo, "add", "--", filename)
    publish_commit(repo, f"提交 {batch['day']} 本地补查（{len(batch['schools'])} 所）")
    print(f"已提交，等待原网页发布。如遇凌晨任务会自动排队。\nhttps://github.com/{REPO}/actions", flush=True)
    deadline = time.monotonic() + 150 * 60
    while time.monotonic() < deadline:
        if visible(batch["id"]):
            pending.unlink()
            print("网页已更新：" + SITE, flush=True)
            return
        time.sleep(20)
    raise RuntimeError("暂未在网页看到本批次。结果已保存在本机，查看 Actions 后重新双击即可续传。")


@contextmanager
def single_instance():
    import msvcrt
    with (BASE / "running.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("另一扇窗口正在补查或等待发布，请查看那扇窗口。") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    if sys.version_info < (3, 10):
        raise RuntimeError("需要 Python 3.10 或更高版本。")
    if not shutil.which("git"):
        raise RuntimeError("请先安装 Git for Windows（含 Git Credential Manager），再双击。")
    BASE.mkdir(parents=True, exist_ok=True)
    pending = BASE / "pending.json"
    with single_instance(), tempfile.TemporaryDirectory(prefix="jobwatch-") as tmp:
        repo = Path(tmp) / "repo"
        subprocess.run(["git", "clone", "-q", "--branch", "main", "--single-branch", REMOTE, str(repo)], check=True)
        git(repo, "remote", "set-url", "--push", "origin", PUSH_URL)
        install(repo)
        if pending.exists():
            print("正在恢复上一次的补查批次。", flush=True)
        else:
            python = environment(repo)
            subprocess.run([str(python), "-u", "-m", "jobwatch.supplement", "collect", str(pending)],
                           cwd=repo, check=True)
        if pending.exists():
            send_pending(repo, pending)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"补查未完成：{exc}", file=sys.stderr)
        sys.exit(1)
