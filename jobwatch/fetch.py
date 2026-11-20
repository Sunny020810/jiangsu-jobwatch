"""网络请求：重试、编码识别、证书容错、统计。"""
from __future__ import annotations

import re
import threading
import time
import warnings
from collections import Counter
from dataclasses import dataclass

import requests

try:  # 静默“跳过证书校验”的警告
    from urllib3.exceptions import InsecureRequestWarning

    warnings.simplefilter("ignore", InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.I)


class FetchError(Exception):
    pass


@dataclass
class Page:
    url: str          # 最终地址（跟随跳转后）
    status: int
    text: str         # 已解码的文本（二进制文件为空）
    content: bytes
    content_type: str


def decode_html(content: bytes, header_encoding: str | None, apparent: str | None) -> str:
    enc = None
    m = _META_CHARSET.search(content[:4096])
    if m:
        enc = m.group(1).decode("ascii", "ignore").lower()
    elif header_encoding and header_encoding.lower() not in ("iso-8859-1", "latin-1"):
        enc = header_encoding.lower()
    elif apparent:
        enc = apparent.lower()
    if not enc:
        enc = "utf-8"
    if enc in ("gb2312", "gbk", "gb_2312-80"):
        enc = "gb18030"
    try:
        return content.decode(enc, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, timeout=25, retries=2, delay=0.4, allow_insecure=True, max_bytes=15 * 1024 * 1024):
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.allow_insecure = allow_insecure
        self.max_bytes = max_bytes
        self.stats = Counter()
        self._local = threading.local()

    def _session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            })
            self._local.s = s
        return s

    def get(self, url: str, binary: bool = False, referer: str | None = None) -> Page:
        last_err: Exception | None = None
        verify = True
        candidates = [url]
        if url.startswith("https://"):
            candidates.append("http://" + url[len("https://"):])  # 部分学校 https 不可用
        for cand in candidates:
            for attempt in range(self.retries + 1):
                try:
                    headers = {"Referer": referer} if referer else None
                    r = self._session().get(cand, timeout=self.timeout, verify=verify,
                                            headers=headers, stream=True, allow_redirects=True)
                    chunks, size = [], 0
                    for chunk in r.iter_content(65536):
                        size += len(chunk)
                        if size > self.max_bytes:
                            r.close()
                            raise FetchError(f"文件过大（>{self.max_bytes // 1024 // 1024}MB）")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    self.stats["ok" if r.status_code < 400 else f"http_{r.status_code}"] += 1
                    if r.status_code >= 400:
                        raise FetchError(f"HTTP {r.status_code}")
                    ctype = r.headers.get("Content-Type", "")
                    text = ""
                    if not binary:
                        apparent = None
                        try:
                            apparent = r.apparent_encoding
                        except Exception:
                            pass
                        text = decode_html(content, r.encoding, apparent)
                    time.sleep(self.delay)
                    return Page(r.url, r.status_code, text, content, ctype)
                except requests.exceptions.SSLError as e:
                    last_err = e
                    if self.allow_insecure and verify:
                        verify = False  # 证书有问题的学校网站很多，退回不校验
                        continue
                    break
                except FetchError as e:
                    last_err = e
                    if str(e).startswith("HTTP 4"):
                        break  # 4xx 不必重试
                    time.sleep(1 + attempt)
                except requests.RequestException as e:
                    last_err = e
                    self.stats["error"] += 1
                    time.sleep(1 + attempt * 2)
        raise FetchError(f"{type(last_err).__name__}: {last_err}" if last_err else "unknown error")
