"""推送：GitHub Issue / 微信（PushPlus、Server酱）/ 邮件。全部可选，按环境变量自动启用。"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

import requests


def _md_to_html(md: str) -> str:
    try:
        import markdown  # type: ignore

        return markdown.markdown(md)
    except Exception:
        import html

        return "<pre style='white-space:pre-wrap;font-family:sans-serif'>" + html.escape(md) + "</pre>"


def send_all(title: str, body_md: str, log=print) -> list[str]:
    done = []
    # 1) GitHub Issue（Actions 自带 GITHUB_TOKEN）
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if token and repo and os.getenv("NOTIFY_GITHUB_ISSUE", "true") == "true":
        try:
            api = f"https://api.github.com/repos/{repo}/issues"
            hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            r = requests.post(api, headers=hdr, json={"title": title, "body": body_md[:60000], "labels": ["日报"]}, timeout=30)
            if r.status_code == 422:  # 标签问题时不带标签重试
                r = requests.post(api, headers=hdr, json={"title": title, "body": body_md[:60000]}, timeout=30)
            r.raise_for_status()
            done.append("GitHub Issue")
        except Exception as e:
            log(f"[notify] GitHub Issue 失败：{e}")
    # 2) PushPlus 微信
    if os.getenv("PUSHPLUS_TOKEN"):
        try:
            r = requests.post("https://www.pushplus.plus/send", timeout=30, json={
                "token": os.getenv("PUSHPLUS_TOKEN"), "title": title, "content": body_md[:18000], "template": "markdown"})
            r.raise_for_status()
            done.append("PushPlus")
        except Exception as e:
            log(f"[notify] PushPlus 失败：{e}")
    # 3) Server酱
    if os.getenv("SERVERCHAN_KEY"):
        key = os.getenv("SERVERCHAN_KEY")
        url = f"https://{key[4:].split('t')[0]}.push.ft07.com/send/{key}.send" if key.startswith("sctp") \
            else f"https://sctapi.ftqq.com/{key}.send"
        try:
            r = requests.post(url, data={"title": title[:100], "desp": body_md[:30000]}, timeout=30)
            r.raise_for_status()
            done.append("Server酱")
        except Exception as e:
            log(f"[notify] Server酱 失败：{e}")
    # 4) 邮件
    if os.getenv("SMTP_HOST") and os.getenv("MAIL_TO"):
        try:
            msg = MIMEText(_md_to_html(body_md), "html", "utf-8")
            msg["Subject"] = title
            sender = os.getenv("SMTP_USER", "")
            msg["From"] = formataddr(("招聘日报", sender))
            msg["To"] = os.getenv("MAIL_TO")
            port = int(os.getenv("SMTP_PORT", "465"))
            cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
            with cls(os.getenv("SMTP_HOST"), port, timeout=30) as s:
                if port != 465:
                    s.starttls()
                s.login(sender, os.getenv("SMTP_PASS", ""))
                s.sendmail(sender, [x.strip() for x in os.getenv("MAIL_TO").split(",")], msg.as_string())
            done.append("邮件")
        except Exception as e:
            log(f"[notify] 邮件失败：{e}")
    return done
