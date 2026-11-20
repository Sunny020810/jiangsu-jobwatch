"""判断一条公告/岗位是否适合金融专业硕士。

分级：
  A  重点：岗位专业要求明确包含金融（或江苏专业目录里含金融的“经济类”），学历接受硕士
  B  可能：专业不限 / 相近专业（经济、财会、工商管理…）/ 金融但只招博士 / 附件读不出来需人工看
  C  其他：江苏高职的其他招聘公告（全部列出，确保不漏）
  P  进展：面试、笔试、公示、拟聘用等后续通知
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TITLE_RECRUIT = re.compile(
    r"招聘|引进|招贤|诚聘|聘用|招录|选聘|聘请|聘任|编外|辅导员|师资|人才需求|高层次人才|紧缺人才|招考|岗位需求|人员需求")
TITLE_EXCLUDE = re.compile(
    r"招生|招标|中标|采购|比选|询价|成交|竞价|谈判|磋商|供应商|双选会|宣讲会|招聘会|专场招聘|企业招聘|"
    r"实习生|勤工|志愿者招募|学生.*招聘|招聘.*学生|校友|就业信息|就业推荐")
TITLE_PROCESS = re.compile(
    r"公示|名单|成绩|面试|笔试|考察|体检|拟聘|拟录|资格复审|资格审查|递补|取消|核减|准考证|考试安排|结果|开考比例|放弃")
MULTI_UNIT = re.compile(r"统一公开招聘|统一招聘|所属事业单位|部分事业单位|事业单位公开招聘|事业单位统一")
VOCATIONAL_HINT = re.compile(r"职业|高职|专科|职院|职业大学|职业技术大学")

DEGREE_HDR = ("学历", "学位")
MAJOR_HDR = ("专业", "学科", "研究方向")
POST_HDR = ("岗位名称", "职位名称", "招聘岗位", "岗位", "职位")
COUNT_HDR = ("人数", "招聘数", "计划")
UNIT_HDR = ("招聘单位", "用人单位", "单位名称")


@dataclass
class RowHit:
    tier: str
    school: str
    post: str
    major: str
    degree: str
    count: str
    reason: str


@dataclass
class Verdict:
    tier: str = "C"
    kind: str = "招聘公告"              # 招聘公告 / 招聘进展
    reasons: list[str] = field(default_factory=list)
    rows: list[RowHit] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    schools: list[str] = field(default_factory=list)
    needs_manual: bool = False
    phd_only: bool = False
    drop: bool = False                  # 多单位公告里没有任何高职岗位 → 丢弃


def is_recruit_title(title: str) -> bool:
    return bool(TITLE_RECRUIT.search(title)) and not TITLE_EXCLUDE.search(title)


def is_process_title(title: str) -> bool:
    return bool(TITLE_PROCESS.search(title))


def find_schools(text: str, schools: list[dict]) -> list[str]:
    text = re.sub(r"\s+", "", text)  # 表格折行会把校名拆开
    found = []
    for s in schools:
        for n in [s["name"], *(s.get("aliases") or [])]:
            if n and n in text:
                found.append(s["name"])
                break
    return found


def degree_ok(s: str) -> bool:
    if not s.strip():
        return True
    if any(k in s for k in ("硕士", "本科", "学士", "不限", "大专", "专科")):
        return True
    rest = s.replace("博士研究生", "").replace("博士", "")
    return "研究生" in rest


def _has(s: str, words) -> list[str]:
    s = re.sub(r"\s+", "", s)
    return [w for w in words if w and w in s]


def _open_major(major: str) -> bool:
    m = major.replace(" ", "")
    return m in ("不限", "专业不限", "不限专业", "无", "无要求") or "专业不限" in m or "不限专业" in m


def _header_index(rows: list[list[str]]) -> int | None:
    for i, r in enumerate(rows[:6]):
        joined = "".join(r)
        if any(h in joined for h in MAJOR_HDR) and (any(h in joined for h in DEGREE_HDR) or any(h in joined for h in POST_HDR)):
            return i
    return None


def _pick(hdr: list[str], row: list[str], keys, skip=r"代码|编号|序号|类别|等级|类型|性质") -> str:
    vals = []
    for i, h in enumerate(hdr):
        if i < len(row) and any(k in h for k in keys) and row[i] and not re.search(skip, h):
            vals.append(row[i])
    return " ".join(vals)


def analyze_tables(tables, schools, cfg, multi_unit: bool, default_school: str = "") -> list[RowHit]:
    strong, weak = cfg["finance_strong"], cfg["finance_weak"]
    hits: list[RowHit] = []
    for rows in tables:
        hi = _header_index(rows)
        if hi is None:
            continue
        hdr = rows[hi]
        # 两行表头（如“专业”下面再分本科/研究生）合并
        if hi + 1 < len(rows) and not any(re.search(r"\d", c) for c in rows[hi + 1]) and \
                any(any(h in c for h in MAJOR_HDR + DEGREE_HDR) for c in rows[hi + 1]):
            sub = rows[hi + 1]
            hdr = [(hdr[i] if i < len(hdr) else "") + (sub[i] if i < len(sub) else "") for i in range(max(len(hdr), len(sub)))]
            hi += 1
        for row in rows[hi + 1:]:
            row_text = " ".join(row)
            if not row_text.strip():
                continue
            school = ""
            found = find_schools(row_text, schools)
            if found:
                school = found[0]
            elif multi_unit:
                continue  # 统一招聘大表：只看高职院校的岗位
            else:
                school = default_school
            major = _pick(hdr, row, MAJOR_HDR) or row_text
            degree = _pick(hdr, row, DEGREE_HDR) or row_text
            post = (_pick(hdr, row, ("岗位名称", "职位名称", "招聘岗位")) or _pick(hdr, row, POST_HDR))[:40]
            count = _pick(hdr, row, COUNT_HDR)[:10]
            ok = degree_ok(degree)
            s_hit, w_hit = _has(major, strong), _has(major, weak)
            tier, reason = "", ""
            if s_hit:
                tier, reason = ("A", f"专业含「{s_hit[0]}」") if ok else ("B", f"专业含「{s_hit[0]}」但仅招博士")
            elif _open_major(major):
                tier, reason = ("B", "专业不限") if ok else ("", "")
            elif w_hit:
                tier, reason = ("B", f"相近专业「{w_hit[0]}」") if ok else ("", "")
            elif _has(row_text, strong) and ok:
                tier, reason = "B", f"备注/其他条件提到「{_has(row_text, strong)[0]}」"
            if tier:
                hits.append(RowHit(tier, school, post, major[:80], degree[:40], count, reason))
            elif multi_unit:  # 统一招聘里的其他高职岗位也记下来，保证这所学校的招聘不漏
                hits.append(RowHit("C", school, post, major[:80], degree[:40], count, ""))
    return hits


_SENT_SPLIT = re.compile(r"[。；;\n]")


def analyze_windows(compact: str, schools, cfg, multi_unit: bool, default_school: str = "") -> list[RowHit]:
    """没有可用表格（比如无框线的 PDF）时的近似识别：
    在每个学校名附近（多单位公告）或每个金融关键词附近（单校公告）取一段文字判断。"""
    strong, weak = cfg["finance_strong"], cfg["finance_weak"]
    hits = []
    if multi_unit:
        for s in schools:
            for n in [s["name"], *(s.get("aliases") or [])]:
                start = 0
                while n and (i := compact.find(n, start)) >= 0:
                    start = i + len(n)
                    win = compact[i: i + 160]
                    nxt = [compact.find(o["name"], i + len(n)) for o in schools]
                    nxt = [x for x in nxt if x > 0]
                    if nxt:
                        win = compact[i: min(i + 160, min(nxt))]
                    s_hit, w_hit = _has(win, strong), _has(win, weak)
                    ok = degree_ok(win)
                    if s_hit and ok:
                        hits.append(RowHit("A", s["name"], "", win[:80], "", "", f"附近文字含「{s_hit[0]}」（PDF 近似识别，请核对）"))
                    elif (s_hit or w_hit or "不限" in win) and ok:
                        hits.append(RowHit("B", s["name"], "", win[:80], "", "", "附近文字可能相关（PDF 近似识别，请核对）"))
                    else:
                        hits.append(RowHit("C", s["name"], "", win[:80], "", "", ""))
    return hits


def analyze(title: str, text: str, tables, attach_notes: list[str], image_count: int,
            schools: list[dict], cfg: dict, source_school: str = "") -> Verdict:
    v = Verdict()
    full = f"{title}\n{text}"
    v.kind = "招聘进展" if is_process_title(title) else "招聘公告"
    multi = bool(MULTI_UNIT.search(title)) and not source_school

    compact = re.sub(r"\s+", "", full)  # PDF 表格常把“江苏财会职\n业学院”折行，去掉空白再匹配
    v.schools = [source_school] if source_school else find_schools(compact, schools)
    v.rows = analyze_tables(tables, schools, cfg, multi_unit=multi, default_school=source_school)
    if not v.rows and (multi or not tables):
        v.rows = analyze_windows(compact, schools, cfg, multi_unit=multi, default_school=source_school)
    if multi:
        v.schools = sorted({r.school for r in v.rows if r.school} | set(find_schools(title, schools)))

    for role, kws in cfg["role_keywords"].items():
        if kws and _has(full, kws):
            v.roles.append(role)

    strong, weak = cfg["finance_strong"], cfg["finance_weak"]
    # 正文句子级匹配（没有表格、或岗位写在正文里时）
    for sent in _SENT_SPLIT.split(full):
        sent = sent.strip()
        if 4 < len(sent) < 400 and (_has(sent, strong) or "专业不限" in sent or "不限专业" in sent):
            v.snippets.append(sent[:160])
    v.snippets = v.snippets[:6]

    tiers = {r.tier for r in v.rows}
    if "A" in tiers:
        v.tier = "A"
        v.reasons.append("岗位表中有金融对口岗位")
    elif "B" in tiers:
        v.tier = "B"
        v.reasons.extend(sorted({r.reason for r in v.rows if r.tier == "B"}))
    elif multi:
        v.tier = "C"  # 多单位公告不能用全文判断（全文里是别的单位的岗位）
    else:
        text_strong = [s for s in v.snippets if _has(s, strong)]
        if not text_strong and _has(compact, strong):
            kw = _has(compact, strong)[0]
            i = compact.find(kw)
            text_strong = [compact[max(0, i - 60): i + 60]]
            v.snippets.append(text_strong[0])
        if text_strong:
            if any(degree_ok_sentence(s) for s in text_strong) or "硕士" in full:
                v.tier = "A"
                v.reasons.append(f"正文提到「{_has(text_strong[0], strong)[0]}」")
            else:
                v.tier = "B"
                v.phd_only = True
                v.reasons.append("提到金融，但似乎只招博士")
        elif "专业不限" in full or "不限专业" in full:
            v.tier = "B"
            v.reasons.append("有专业不限的岗位")
        elif _has(full, weak) and ("硕士" in full or "研究生" in full):
            v.tier = "B"
            v.reasons.append(f"提到相近专业「{_has(full, weak)[0]}」")

    # 读不出来的情况 → 至少给 B，绝不静默漏掉
    if attach_notes:
        v.needs_manual = True
        v.reasons.append("附件未能自动解析：" + "；".join(attach_notes[:3]))
    if len(text) < 150 and image_count > 0:
        v.needs_manual = True
        v.reasons.append("公告正文可能是图片")
    if ("详见附件" in text or "岗位表" in text) and not tables and not attach_notes and not v.rows and "附件" in text:
        if not re.search(r"\.(xls|doc|pdf)", text, re.I):
            v.needs_manual = True
            v.reasons.append("正文提到附件/岗位表，但没抓到附件")
    if v.needs_manual and v.tier == "C" and v.kind == "招聘公告":
        v.tier = "B"

    if multi and not v.schools and not v.needs_manual:
        v.drop = True  # 统一招聘公告里没有任何高职岗位
    if v.kind == "招聘进展" and v.tier == "C":
        v.tier = "P"
    return v


def degree_ok_sentence(s: str) -> bool:
    return any(k in s for k in ("硕士", "本科", "研究生及以上", "学士")) or ("研究生" in s.replace("博士研究生", ""))
