"""附件解析：岗位表经常只放在附件里，必须读出来才能判断专业要求。
支持 xlsx / xls / docx / doc / wps / et / pdf / zip（内含上述文件）。"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field

from .htmlutil import clean

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass
class Parsed:
    ok: bool
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    note: str = ""


def _xlsx(data: bytes) -> Parsed:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    tables, texts = [], []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [clean(str(c)) if c is not None else "" for c in row]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
            texts.extend(" ".join(r) for r in rows)
    return Parsed(True, "\n".join(texts), tables)


def _xls(data: bytes) -> Parsed:
    import xlrd  # 只支持旧版 .xls

    book = xlrd.open_workbook(file_contents=data)
    tables, texts = [], []
    for sh in book.sheets():
        rows = []
        for i in range(sh.nrows):
            cells = [clean(str(v)) for v in sh.row_values(i)]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
            texts.extend(" ".join(r) for r in rows)
    return Parsed(True, "\n".join(texts), tables)


def _docx(data: bytes) -> Parsed:
    import docx

    d = docx.Document(io.BytesIO(data))
    texts = [p.text for p in d.paragraphs if p.text.strip()]
    tables = []
    for t in d.tables:
        rows = []
        for r in t.rows:
            cells = []
            for c in r.cells:
                v = clean(c.text)
                if not cells or cells[-1] != v:  # 合并单元格会重复，去重
                    cells.append(v)
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
            texts.extend(" ".join(r) for r in rows)
    return Parsed(True, "\n".join(texts), tables)


def _pdf(data: bytes) -> Parsed:
    import pdfplumber

    texts, tables = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:40]:
            texts.append(page.extract_text() or "")
            found = page.extract_tables() or []
            text_mode = False
            if not found:  # 无框线表格：按文字对齐切分
                found = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"}) or []
                text_mode = True
            for t in found:
                rows = [[clean(c or "") for c in r] for r in t]
                rows = [r for r in rows if any(r)]
                if text_mode:
                    rows = merge_continuation_rows(rows)
                if len(rows) >= 2:
                    tables.append(rows)
    text = "\n".join(texts)
    if len(text.strip()) < 30:
        return Parsed(False, text, tables, "PDF 可能是扫描图片，需人工查看")
    return Parsed(True, text, tables)


def merge_continuation_rows(rows: list[list[str]]) -> list[list[str]]:
    """单元格内折行在“按文字切分”时会变成新的一行（首列为空、内容很少），把它拼回上一行。"""
    out: list[list[str]] = []
    for r in rows:
        filled = sum(1 for c in r if c)
        if out and r and not r[0] and filled < max(2, sum(1 for c in out[-1] if c)):
            prev = out[-1]
            for i, c in enumerate(r):
                if c:
                    if i < len(prev):
                        prev[i] = prev[i] + c
                    else:
                        prev.append(c)
        else:
            out.append(list(r))
    return out


def _libreoffice_convert(data: bytes, suffix: str, target: str) -> bytes | None:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in" + suffix)
        with open(src, "wb") as f:
            f.write(data)
        try:
            subprocess.run([exe, "--headless", "--convert-to", target, "--outdir", td, src],
                           capture_output=True, timeout=120)
        except Exception:
            return None
        out = os.path.join(td, "in." + target)
        if os.path.exists(out):
            with open(out, "rb") as f:
                return f.read()
    return None


def _ole(data: bytes, name: str) -> Parsed:
    low = name.lower()
    # 1) 先当 xls 试
    if not low.endswith((".doc", ".wps")):
        try:
            return _xls(data)
        except Exception:
            pass
    # 2) doc：antiword
    if shutil.which("antiword"):
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
            f.write(data)
            path = f.name
        try:
            r = subprocess.run(["antiword", "-m", "UTF-8.txt", path], capture_output=True, timeout=60)
            txt = r.stdout.decode("utf-8", "replace")
            if len(txt.strip()) > 30:
                return Parsed(True, txt, [])
        except Exception:
            pass
        finally:
            os.unlink(path)
    # 3) LibreOffice 兜底（doc / wps / et / xls 都能转）
    is_sheet = low.endswith((".xls", ".et"))
    conv = _libreoffice_convert(data, ".xls" if is_sheet else ".doc", "xlsx" if is_sheet else "docx")
    if conv:
        try:
            return _xlsx(conv) if is_sheet else _docx(conv)
        except Exception:
            pass
    if not is_sheet:  # 文件名不可靠时再试另一种
        conv = _libreoffice_convert(data, ".xls", "xlsx")
        if conv:
            try:
                return _xlsx(conv)
            except Exception:
                pass
    return Parsed(False, note="旧版 Office/WPS 文件无法解析，需人工查看")


def parse_attachment(data: bytes, name: str, depth: int = 0) -> Parsed:
    try:
        if data[:4] == b"%PDF":
            return _pdf(data)
        if data[:2] == b"PK":
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
            if any(n.startswith("xl/") for n in names):
                return _xlsx(data)
            if any(n.startswith("word/") for n in names):
                return _docx(data)
            if depth == 0:  # 普通压缩包：解析里面的文件
                merged = Parsed(True)
                notes = []
                for n in names[:20]:
                    if n.endswith("/"):
                        continue
                    try:
                        inner_name = n.encode("cp437").decode("gbk")
                    except Exception:
                        inner_name = n
                    p = parse_attachment(zf.read(n), inner_name, depth + 1)
                    merged.text += "\n" + p.text
                    merged.tables += p.tables
                    if not p.ok:
                        notes.append(f"{inner_name}: {p.note}")
                merged.note = "；".join(notes)
                merged.ok = not notes
                return merged
            return Parsed(False, note="压缩包嵌套，需人工查看")
        if data[:8] == OLE_MAGIC:
            return _ole(data, name)
        if data[:4] in (b"Rar!", b"7z\xbc\xaf"):
            return Parsed(False, note="RAR/7z 压缩包，需人工查看")
        head = data[:2000].lower()
        if b"<html" in head or b"<!doctype" in head or b"<table" in head:
            from .fetch import decode_html
            from .htmlutil import extract_article

            art = extract_article(decode_html(data, None, None), "http://localhost/")
            return Parsed(True, art.text, art.tables)
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            return Parsed(False, note="图片附件，需人工查看")
        try:
            txt = data.decode("utf-8")
        except UnicodeDecodeError:
            txt = data.decode("gb18030", "replace")
        return Parsed(len(txt.strip()) > 30, txt, [], "" if len(txt.strip()) > 30 else "未知格式，需人工查看")
    except Exception as e:  # 任何解析异常都不能让整个任务失败
        return Parsed(False, note=f"附件解析失败（{type(e).__name__}），需人工查看")
