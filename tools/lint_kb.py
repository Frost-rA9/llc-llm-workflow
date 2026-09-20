#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llc-llm-workflow · markdown 收口断言（口径 §三/§四/§五/§六）

三道闸门（口径 §四）
  A1 引用可解析   md 中所有 `文件#id` 逐个在语料命中；`@@` 占位符残留 0
  A2 数字可复算   md 中数字 == tools/voice.py 重算值（保留位数按 M1–M8）
                  · 角色指南：按「模式行 × 指标列」逐格复算
                  · 速查表（kind=table）：横向表角色可落在前 3 格；复算 E2-cn/行数(E2)/E1；
                    并校验表格内外所有 `值（M#/E#）` 标注（同行角色或全队并集）与派生算式
  A3 术语有来源   每个术语/专名/称呼条目带来源（`文件#id` 或统计命令）

三条结构约束（口径 §三 硬规定）
  S1 角色指南引用 ≥ 5 处
  S2 角色指南指标 ≥ 3 类
  F1 头部规范（口径 §五）：语料基准 / 生成命令 / 口径依据 / 上游来源 / 权威等级

用法：
  python3 tools/lint_kb.py kb/style/角色/以实玛利.md     # 单文件
  python3 tools/lint_kb.py --all                          # kb/ 下全部 md
  python3 tools/lint_kb.py --all --strict                 # WARN 也算失败
  python3 tools/lint_kb.py --all --json                   # 机器可读

退出码：0 = 全过；1 = 有 FAIL（或 --strict 下含 WARN）。

数字书写约定（A2 的可见性要求）
  · 单指标值必须带公式编号：`均中字 20.5（M2）`、`行尾"。" 46.5%（M7）`
  · 派生值必须写成算式：`3.1 + 2.9 + 0.7 = 6.7`（工具会验算）
  · 允许跳过：前缀 `≈` / `约` / `~` / `±`，或行内含 `派生` / `合计`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus as C          # noqa: E402
import voice as V           # noqa: E402

# ──────────────────────────────────────────────── 常量

MDP = {"M1": 0, "M1_text": 0, "M2": 1, "M3": 1, "M2_text": 1, "M3_text": 1,
       "M4": 1, "M5": 2, "M6": 1, "M7": 1, "M8": 0, "H": 0,
       "E1": 1, "E2cn": 2, "E2rows": 0}
METRIC_RE = re.compile(r"\bM(\d)(_text)?\b")
ANNOT_RE = re.compile(r"(\d+(?:\.\d+)?)[\s*]*(?:%|％)?[\s*]*[（(]\s*(M\d(?:_text)?)\s*[)）]")
LABEL_NUM_RE = re.compile(r"\b(M\d(?:_text)?)[\s*]*[:：=][\s*]*(\d+(?:\.\d+)?)")
# 速查表（kind=table）专用：允许 E1 / E2-xx 也带标注（角色分支仍只用 ANNOT_RE）
ANNOT_ANY_RE = re.compile(
    r"(\d+(?:\.\d+)?)[\s*]*(?:%|％)?[\s*]*[（(]\s*(M\d(?:_text)?|E1|E2-(?:kr|en|cn))\s*[)）]")
NUM_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d])")
DEC_RE = re.compile(r"(?<![\d.])\d+\.\d+(?![\d])")
ARITH_RE = re.compile(r"([\d.]+(?:\s*\+\s*[^=\d\n]*?[\d.]+)+)\s*=\s*\**\s*(\d+(?:\.\d+)?)")
RATIO_RE = re.compile(r"(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d])")
SKIP_PREFIX = ("≈", "约", "~", "±", "～")
PLACEHOLDER_RE = re.compile(r"@@([^@]*)@@")
PLACEHOLDER_OK = re.compile(r"@@([^|@]+)\|([^|@]+)\|([^|@]+)@@")
REF_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_\-/]*?)(?:\.json)?#([A-Za-z0-9_\-]+(?:\[\d+\])?)")

HEADER_KEYS = ["语料基准", "生成命令", "口径依据", "上游来源", "权威等级"]
SKIP_FILES = {"agent-input.md", "口径.md"}          # 工作流前置文档，非 KB 交付物
SOURCE_SECTION_RE = re.compile(r"术语|专名|称呼|自称|口癖|词汇|对照|易错|翻错|来源")
SOURCE_COL_RE = re.compile(r"来源|证据|出处|引用")
SRC_INLINE_RE = re.compile(r"python3\s+tools/|`[^`]*#|来源[:：]|派生")

_HDR_KEYWORDS = [("每百句", "M4"), ("每千字", "M5"), ("行级", "M6"), ("行尾", "M7"),
                 ("均中字", "M2"), ("均英词", "M3"), ("句数", "M1")]


# ──────────────────────────────────────────────── 语料解析缓存

_IDX: dict | None = None
_IDS: dict[str, set | None] = {}
_ROLE_METRICS: dict[str, dict] = {}


def index() -> dict:
    global _IDX
    if _IDX is None:
        _IDX = C.build_index()
    return _IDX


def ids_of(logical: str) -> set | None:
    if logical in _IDS:
        return _IDS[logical]
    langs = index().get(logical)
    if not langs:
        _IDS[logical] = None
        return None
    s: set = set()
    for lang, rel in langs.items():
        for rec in C.load_records(lang, rel):
            k = C.record_key(rec)
            if k is not None:
                s.add(str(k))
    _IDS[logical] = s
    return s


def norm_logical(raw: str) -> str | None:
    raw = raw.strip().strip("`").lstrip("[").rstrip("]")
    idx = index()
    if raw in idx:
        return raw
    for cand in (raw + ".json", raw.replace("\\", "/")):
        if cand in idx:
            return cand
    hits = [k for k in idx if k == raw or k.endswith("/" + raw)]
    return hits[0] if len(hits) == 1 else None


def role_metrics(role_cn: str) -> dict:
    if role_cn not in _ROLE_METRICS:
        _ROLE_METRICS[role_cn] = V.metrics(V.role_rows(role_cn))
    return _ROLE_METRICS[role_cn]


# ──────────────────────────────────────────────── 文档读取 / 去噪

def read_doc(path: str) -> tuple[str, str]:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    body = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    return raw, body


def doc_kind(path: str) -> tuple[str, str | None]:
    """→ (kind, role_cn)；kind ∈ role|table|generic"""
    rel = os.path.relpath(path, C.ROOT)
    stem = os.path.splitext(os.path.basename(path))[0]
    if os.sep + "style" + os.sep + "角色" + os.sep in rel or rel.startswith("kb/style/角色/"):
        if stem in V.BY_CN:
            return "role", stem
    if stem in V.BY_CN:                     # 文件名即角色名 → 按角色指南检查
        return "role", stem
    if stem in ("角色口吻速查",):
        return "table", None
    return "generic", None


# ──────────────────────────────────────────────── 表格解析

def split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", line)) and "-" in line


def parse_tables(body: str) -> list[dict]:
    """→ [{start,end,header,rows:[(lineno,cells)]}]（行号 0 基）"""
    lines = body.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and is_sep(lines[i + 1]):
            header = split_cells(lines[i])
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                if not is_sep(lines[j]):
                    rows.append((j, split_cells(lines[j])))
                j += 1
            out.append({"start": i, "end": j, "header": header, "rows": rows})
            i = j
        else:
            i += 1
    return out


def cell_metric(cell: str) -> str | None:
    t = cell.replace("`", "").replace("*", "").strip()
    m = METRIC_RE.search(t)
    if m:
        return "M" + m.group(1) + (m.group(2) or "")
    if re.search(r"E2|敬称", t):
        return "E2rows" if "行数" in t else "E2cn"
    if re.search(r"E1|主题语域", t):
        return "E1"
    for kw, met in _HDR_KEYWORDS:
        if kw in t:
            return met
    if re.fullmatch(r"H", t):
        return "H"
    return None


def cell_pattern(cell: str) -> str | None:
    t = cell.replace("`", "").replace("*", "").replace('"', "").replace("“", "").replace("”", "").strip()
    if t in V.PAT_RE:
        return t
    m = re.search(r"含(.+)", t)
    if m and m.group(1) in V.PAT_RE:
        return m.group(1)
    return None


# ──────────────────────────────────────────────── 数字工具

def fnum(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def num_ok(written: str, expected: float, dp: int) -> bool:
    v = fnum(written)
    return v is not None and abs(v - expected) < 0.5 * 10 ** (-dp) + 1e-9


def allowed_map(m: dict) -> dict[str, set[str]]:
    """{数值字符串(0/1/2 位小数变体): {标签}}"""
    amap: dict[str, set[str]] = {}

    def add(v: float, label: str, dp: int) -> None:
        for d in {0, 1, 2, dp}:
            amap.setdefault(f"{v:.{d}f}", set()).add(label)

    add(m["L"], "M1 句数 L", 0)
    add(m["L_text"], "M1_text 句数 L_text", 0)
    add(m["M2"], "M2 均中字", 1)
    add(m["M3"], "M3 均英词", 1)
    add(m["M2_text"], "M2_text 对账均中字", 1)
    add(m["M3_text"], "M3_text 对账均英词", 1)
    add(m["files"], "出场文件数", 0)
    add(m["C_text"], "C_text", 0)
    add(m["W_text"], "W_text", 0)
    e = m.get("E") or {}
    if e:
        add(e["theme"]["pct"], "E1 主题语域行占比", 1)
        for lang, d in e["hon"].items():
            add(d["pct"], f"E2 敬称行占比 {lang.upper()}", 1)
        for name, c in e["targets"].items():
            add(c, f"E3 称呼「{name}」行数", 0)
    for name, p in m["pats"].items():
        add(p["H"], f"H:{name}", 0)
        add(p["M4"], f"M4:{name}", 1)
        add(p["M5"], f"M5:{name}", 2)
        add(p["M6"], f"M6:{name}", 1)
        add(p["M7"], f"M7:{name}", 1)
    return amap


def expected_annot(m: dict, tok: str) -> float | None:
    """标注 token（M#/E1/E2-xx）→ 期望值；无单值定义时返回 None（转 amap 兜底）。"""
    if tok == "E2-kr":
        return m["E"]["hon"]["kr"]["pct"]
    if tok == "E2-en":
        return m["E"]["hon"]["en"]["pct"]
    if tok == "E2-cn":
        return expected_for(m, "E2cn", None)
    if tok == "E1":
        return expected_for(m, "E1", None)
    return expected_for(m, tok, None)


def annot_value_ok(written: str, tok: str, m: dict, amap: dict[str, set[str]]) -> bool:
    """`值（M#/E#）` 是否成立：有单值定义则精确比对，否则须落在该角色指标集内。"""
    exp = expected_annot(m, tok)
    if exp is not None:
        return num_ok(written, exp, MDP.get(tok, 1))
    return written in amap


def expected_for(m: dict, metric: str, pat: str | None) -> float | None:
    if metric == "M1":
        return m["L"]
    if metric == "M1_text":
        return m["L_text"]
    if metric == "M2":
        return m["M2"]
    if metric == "M3":
        return m["M3"]
    if metric == "M2_text":
        return m["M2_text"]
    if metric == "M3_text":
        return m["M3_text"]
    if metric == "E1":
        return m["E"]["theme"]["pct"]
    if metric == "E2cn":
        return m["E"]["hon"]["cn"]["pct"]
    if metric == "E2rows":
        return m["E"]["hon"]["cn"]["rows"]
    if pat is None:
        return None
    p = m["pats"].get(pat)
    if not p:
        return None
    return {"H": p["H"], "M4": p["M4"], "M5": p["M5"], "M6": p["M6"], "M7": p["M7"]}.get(metric)


# ──────────────────────────────────────────────── A1

def extract_refs(body: str) -> list[dict]:
    refs = []
    for mo in REF_RE.finditer(body):
        raw_l, raw_id = mo.group(1), mo.group(2)
        if raw_l in ("", "#"):
            continue
        idx_suffix = None
        rid = raw_id
        m2 = re.fullmatch(r"([A-Za-z0-9_\-]+)\[(\d+)\]", raw_id)
        if m2:
            rid, idx_suffix = m2.group(1), int(m2.group(2))
        refs.append({"raw": mo.group(0), "logical_raw": raw_l, "id": rid, "idx": idx_suffix,
                     "line": body[:mo.start()].count("\n") + 1})
    return refs


def check_a1(body: str) -> tuple[list[str], list[str], list[dict]]:
    fails, warns, good = [], [], []
    # 占位符
    for mo in PLACEHOLDER_RE.finditer(body):
        inner = mo.group(1)
        line = body[:mo.start()].count("\n") + 1
        if PLACEHOLDER_OK.fullmatch(mo.group(0)):
            model, logical, rid = PLACEHOLDER_OK.fullmatch(mo.group(0)).groups()
            lg = norm_logical(logical)
            ids = ids_of(lg) if lg else None
            if lg and ids is not None and rid in ids:
                good.append({"logical": lg, "id": rid, "line": line, "ph": True})
            else:
                fails.append(f"L{line} 占位符目标不在语料：`{mo.group(0)}`")
        else:
            fails.append(f"L{line} 占位符格式非法（应 `@@model|file|id@@`）：`{mo.group(0)}`")
    # 真实引用
    for r in extract_refs(body):
        lg = norm_logical(r["logical_raw"])
        if not lg:
            fails.append(f"L{r['line']} 逻辑文件不存在：`{r['raw']}`")
            continue
        ids = ids_of(lg)
        if ids is None:
            fails.append(f"L{r['line']} 无法读取：`{r['raw']}`")
        elif r["id"] not in ids:
            fails.append(f"L{r['line']} id 不在语料：`{r['raw']}`（{lg} 共 {len(ids)} 条）")
        else:
            good.append({"logical": lg, "id": r["id"], "line": r["line"], "ph": False})
    if len(good) < 1:
        warns.append("未发现任何可解析引用")
    return fails, warns, good


# ──────────────────────────────────────────────── A2

def check_a2(body: str, kind: str, role_cn: str | None) -> tuple[list[str], list[str], set[str], int]:
    fails, warns = [], []
    used: set[str] = set()
    checked = 0
    if kind == "role" and role_cn:
        m = role_metrics(role_cn)
        amap = allowed_map(m)
        bn = body.replace("`", " ")          # 去反引号，保持字符偏移
        lines = bn.split("\n")
        consumed: set[int] = set()

        # ③ 先做算式验算（后面的标注/小数扫描要跳过算式命中区）
        arith_spans: list[tuple[int, int]] = []
        result_spans: list[tuple[int, int]] = []
        for mo in ARITH_RE.finditer(bn):
            line = bn[:mo.start()].count("\n") + 1
            addends = [fnum(x) for x in NUM_RE.findall(mo.group(1))]
            res = fnum(mo.group(2))
            dp = len(mo.group(2).split(".")[1]) if "." in mo.group(2) else 0
            if res is None or not addends:
                continue
            checked += 1
            arith_spans.append((mo.start(), mo.end()))
            result_spans.append((mo.start(2), mo.end(2)))
            if abs(sum(addends) - res) >= 0.5 * 10 ** (-dp) + 1e-9:
                fails.append(f"L{line} 算式不成立：{' + '.join(f'{x:g}' for x in addends)} "
                             f"= {mo.group(2)}（应为 {sum(addends):.{dp}f}）")

        def in_spans(pos: int, spans: list) -> bool:
            return any(a <= pos < b for a, b in spans)

        # ① 表格：模式行 × 指标列
        for tb in parse_tables(bn):
            cols = {i: cell_metric(h) for i, h in enumerate(tb["header"])}
            cols = {i: v for i, v in cols.items() if v}
            for lineno, cells in tb["rows"]:
                pats = [cell_pattern(c) for c in cells]
                pat = next((p for p in pats if p), None)
                # 跨角色对比行：首两格出现其他角色名 → 用该角色的指标复算
                mrow = m
                if pat is None:
                    head = " ".join(cells[:2])
                    for cn in V.BY_CN:
                        if cn != role_cn and cn in head:
                            mrow = role_metrics(cn)
                            break
                if not pat and mrow is m:
                    continue
                ok_row = False
                for i, met in cols.items():
                    if i >= len(cells):
                        continue
                    mo = NUM_RE.search(cells[i].replace("**", ""))
                    if not mo:
                        continue
                    exp = expected_for(mrow, met, pat)
                    if exp is None:
                        continue
                    checked += 1
                    used.add(met)
                    if num_ok(mo.group(1), exp, MDP.get(met, 1)):
                        ok_row = True
                    else:
                        fails.append(f"L{lineno+1} 表格 `{pat or met}` {met} 写 {mo.group(1)} ≠ "
                                     f"{exp:.{MDP.get(met,1)}f}")
                if ok_row:
                    consumed.add(lineno)

        # ② 行内 `值（M#）` / `M#：值`（无模式限定 → 需在指标集内）
        def annot_check(written: str, met: str, line: int, form: str) -> None:
            nonlocal checked, used
            form = form.replace("*", "").strip()
            exp = expected_for(m, met, None)
            checked += 1
            if exp is not None:
                used.add(met)
                if not num_ok(written, exp, MDP.get(met, 1)):
                    fails.append(f"L{line} 标注 `{form}` ≠ {exp:.{MDP.get(met,1)}f}")
                return
            if written in amap:
                used.update(amap[written])
                return
            fails.append(f"L{line} 标注 `{form}` 不等于任何单指标值"
                         f"（若是合计，请写成 `3.1+2.9+0.7=6.7（{met}）`）")

        for mo in ANNOT_RE.finditer(bn):
            if in_spans(mo.start(1), result_spans):
                continue
            arith_spans.append((mo.start(1), mo.end(1)))
            annot_check(mo.group(1), mo.group(2), bn[:mo.start()].count("\n") + 1, mo.group(0))
        for mo in LABEL_NUM_RE.finditer(bn):
            if in_spans(mo.start(2), result_spans):
                continue
            arith_spans.append((mo.start(2), mo.end(2)))
            annot_check(mo.group(2), mo.group(1), bn[:mo.start()].count("\n") + 1, mo.group(0))

        # ④ 未标注小数：必须在指标集内
        for mo in DEC_RE.finditer(bn):
            line = bn[:mo.start()].count("\n") + 1
            if line - 1 in consumed or in_spans(mo.start(), arith_spans):
                continue
            if any(p in bn[max(0, mo.start() - 3):mo.start()] for p in SKIP_PREFIX):
                continue
            if mo.start() and bn[mo.start() - 1].isalpha():   # v1.1 / M2.5 之类
                continue
            line_txt = lines[line - 1]
            if any(k in line_txt for k in ("派生", "合计", "≈", "约")):
                warns.append(f"L{line} 派生值未验算：`{mo.group(0)}`（行内未列加数，"
                             f"建议写成 `3.1+2.9+0.7=6.7`）")
                continue
            tok = mo.group(0)
            if tok in amap:
                checked += 1
                used |= amap[tok]
                continue
            v = fnum(tok)
            near = sorted(((abs(fnum(k) - v), k, next(iter(lbl))) for k, lbl in amap.items()),
                          key=lambda x: x[0])[:3]
            hint = "；".join(f"{k}（{lbl}）" for _d, k, lbl in near)
            ctx = bn[max(0, mo.start() - 40):mo.end() + 20].replace("\n", " ").strip()
            fails.append(f"L{line} 小数 `{tok}` 不在指标集内｜最近：{hint}｜上下文 …{ctx}…")

        # ⑤ x/N 计数比（仅当分母是 L / L_text）
        for mo in RATIO_RE.finditer(bn):
            num, den = int(mo.group(1)), int(mo.group(2))
            if den not in (m["L"], m["L_text"]):
                continue
            hs = {p["H"] for p in m["pats"].values()} | {m["L"], m["L_text"], m["files"]}
            line = bn[:mo.start()].count("\n") + 1
            if num in hs:
                checked += 1
                used.add("M8")
            else:
                warns.append(f"L{line} 计数比 `{num}/{den}` 的分子不是已知 H（M8 存疑）")
        return fails, warns, used, checked

    if kind == "table":
        bn = body.replace("`", " ")          # 去反引号，保持字符偏移
        tables = parse_tables(bn)
        table_lines: set[int] = set()
        for tb in tables:
            table_lines.update(range(tb["start"], tb["end"]))

        # ③ 先做算式验算（后面的标注扫描要跳过算式命中区）
        arith_spans: list[tuple[int, int]] = []
        result_spans: list[tuple[int, int]] = []
        for mo in ARITH_RE.finditer(bn):
            line = bn[:mo.start()].count("\n") + 1
            addends = [fnum(x) for x in NUM_RE.findall(mo.group(1))]
            res = fnum(mo.group(2))
            dp = len(mo.group(2).split(".")[1]) if "." in mo.group(2) else 0
            if res is None or not addends:
                continue
            checked += 1
            arith_spans.append((mo.start(), mo.end()))
            result_spans.append((mo.start(2), mo.end(2)))
            if abs(sum(addends) - res) >= 0.5 * 10 ** (-dp) + 1e-9:
                fails.append(f"L{line} 算式不成立：{' + '.join(f'{x:g}' for x in addends)} "
                             f"= {mo.group(2)}（应为 {sum(addends):.{dp}f}）")

        def in_spans(pos: int, spans: list) -> bool:
            return any(a <= pos < b for a, b in spans)

        # ① 表格：指标列逐格复算 + 非指标列的行内标注
        for tb in tables:
            cols: dict[int, tuple[str, str | None]] = {}
            for i, h in enumerate(tb["header"]):
                pat = cell_pattern(h)
                met = cell_metric(h) or ("M4" if pat else None)
                if met:
                    cols[i] = (met, pat)
            for lineno, cells in tb["rows"]:
                if not cells:
                    continue
                # 角色可能在第 1 格（量化总表）或第 2 格（带「排名」列的横向表）
                role = None
                for c in cells[:3]:
                    for cn in V.BY_CN:
                        if cn in c:
                            role = cn
                            break
                    if role:
                        break
                if not role:
                    continue
                m = role_metrics(role)
                amap = allowed_map(m)
                for i, (met, pat) in cols.items():
                    if i >= len(cells):
                        continue
                    mo = NUM_RE.search(cells[i].replace("**", ""))
                    if not mo:
                        continue
                    exp = expected_for(m, met, pat)
                    if exp is None:
                        continue
                    checked += 1
                    used.add(met)
                    if not num_ok(mo.group(1), exp, MDP.get(met, 1)):
                        fails.append(f"L{lineno+1} {role} {met} 写 {mo.group(1)} ≠ "
                                     f"{exp:.{MDP.get(met,1)}f}")
                # 非指标列里的 `值（M#/E#）` 必须落在该行角色的指标集内
                # （含 `=` 的单元格是派生值算式，已由 ③ 验算，不再单独查标注）
                for i, cell in enumerate(cells):
                    if i in cols:
                        continue
                    txt = cell.replace("**", "")
                    if "=" in txt:
                        continue
                    for mo in ANNOT_ANY_RE.finditer(txt):
                        checked += 1
                        used.add(mo.group(2))
                        if not annot_value_ok(mo.group(1), mo.group(2), m, amap):
                            fails.append(f"L{lineno+1} {role} 标注 `{mo.group(0)}` "
                                         f"不在其指标集内")
                    for mo in LABEL_NUM_RE.finditer(txt):
                        checked += 1
                        used.add(mo.group(1))
                        if not annot_value_ok(mo.group(2), mo.group(1), m, amap):
                            fails.append(f"L{lineno+1} {role} 标注 `{mo.group(0)}` "
                                         f"不在其指标集内")

        # ② 表格外散文：`值（M#/E#）` 落在同行角色（否则任一角色）的指标集内
        amaps = {cn: allowed_map(role_metrics(cn)) for cn in V.BY_CN}
        union: dict[str, set[str]] = {}
        for _cn, am in amaps.items():
            for v, labels in am.items():
                union.setdefault(v, set()).update(labels)

        prose_lines = bn.split("\n")

        def prose_annot(pos: int, val: str, tok: str) -> None:
            nonlocal checked
            lineno = bn[:pos].count("\n")
            if lineno in table_lines or in_spans(pos, result_spans):
                return
            checked += 1
            used.add(tok)
            line = prose_lines[lineno]
            role_line = next((cn for cn in V.BY_CN if cn in line), None)
            if role_line:
                if not annot_value_ok(val, tok, role_metrics(role_line), amaps[role_line]):
                    fails.append(f"L{lineno+1} 标注 `{val}（{tok}）` "
                                 f"不匹配 {role_line} 的 {tok} 指标值")
            elif val not in union:
                fails.append(f"L{lineno+1} 标注 `{val}（{tok}）` 不匹配任何角色的 {tok} 指标值")

        for mo in ANNOT_ANY_RE.finditer(bn):
            prose_annot(mo.start(1), mo.group(1), mo.group(2))
        for mo in LABEL_NUM_RE.finditer(bn):
            prose_annot(mo.start(2), mo.group(2), mo.group(1))
        return fails, warns, used, checked

    warns.append("A2 跳过：非角色指标文档（无 M1–M8 复算基准）")
    return fails, warns, used, checked


# ──────────────────────────────────────────────── A3

def check_a3(body: str, doc_has_cmd: bool) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    lines = body.split("\n")
    # 章节切分
    heads = [(i, l) for i, l in enumerate(lines) if l.startswith("## ")]
    for n, (start, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        if not SOURCE_SECTION_RE.search(title):
            continue
        seg = lines[start:end]
        seg_body = "\n".join(seg)
        seg_has_src = bool(SRC_INLINE_RE.search(seg_body))
        # 表格
        for tb in parse_tables(seg_body):
            hdr = tb["header"]
            src_cols = [i for i, h in enumerate(hdr) if SOURCE_COL_RE.search(h)]
            for lineno, cells in tb["rows"]:
                glob = start + tb["start"] + 2 + lineno + 1  # 近似行号
                row_txt = " | ".join(cells)
                inline = bool(re.search(r"#|@@|python3\s+tools/|来源[:：]", row_txt))
                if src_cols:
                    cellsrc = " ".join(cells[i] for i in src_cols if i < len(cells))
                    if not cellsrc.strip() or cellsrc.strip() in ("—", "-", "⏳", "待补", "TBD"):
                        fails.append(f"L{glob} `{title}` 条目缺来源：{row_txt[:70]}")
                    elif not re.search(r"#|@@|python3\s+tools/|M\d|H", cellsrc):
                        warns.append(f"L{glob} `{title}` 来源列内容可疑：{cellsrc[:50]}")
                elif not inline and not seg_has_src and not doc_has_cmd:
                    fails.append(f"L{glob} `{title}` 条目无来源：{row_txt[:70]}")
                elif not inline and re.search(r"⏳|待补|TBD|TODO", row_txt):
                    warns.append(f"L{glob} `{title}` 条目未完成：{row_txt[:70]}")
        # 列表条目
        for i, l in enumerate(seg):
            if not re.match(r"^\s*(?:\d+\.|[-*])\s+\S", l):
                continue
            glob = start + i + 1
            if re.search(r"#|@@|python3\s+tools/|来源[:：]", l):
                continue
            if re.search(r"⏳|待补|TBD|TODO|待 Phase", l):
                warns.append(f"L{glob} `{title}` 条目未完成：{l.strip()[:70]}")
            elif not seg_has_src and not doc_has_cmd:
                fails.append(f"L{glob} `{title}` 条目无来源：{l.strip()[:70]}")
    return fails, warns


# ──────────────────────────────────────────────── S1/S2/F1

def check_struct(kind: str, good_refs: list[dict], used_metrics: set[str]):
    """→ (s1_fail, s1_warn, s2_fail, s2_warn)"""
    if kind != "role":
        return [], [], [], []
    n_ref = len(good_refs)
    distinct = len({(g["logical"], g["id"]) for g in good_refs})
    if n_ref < 5:
        s1f = [f"S1 引用 ≥5 处：实际 {n_ref} 处（distinct {distinct}）"]
        s1w: list[str] = []
    else:
        s1f, s1w = [], [f"S1 引用 {n_ref} 处（distinct {distinct}）"]
    classes = sorted({mo.group(0) for x in used_metrics
                      if (mo := re.match(r"M\d(?:_text)?", x))})
    if len(classes) < 3:
        s2f = [f"S2 指标 ≥3 类：实际 {len(classes)} 类 {classes or '（无）'}"]
        s2w: list[str] = []
    else:
        s2f, s2w = [], [f"S2 指标 {len(classes)} 类：{', '.join(classes)}"]
    return s1f, s1w, s2f, s2w


def check_header(body: str, kind: str) -> tuple[list[str], list[str]]:
    fails, warns = [], []
    miss = [k for k in HEADER_KEYS if k not in body]
    if miss:
        msg = "F1 头部缺字段：" + ", ".join(miss)
        (fails if kind == "role" else warns).append(msg)
    return fails, warns


# ──────────────────────────────────────────────── 单文件

def lint_file(path: str) -> dict:
    raw, body = read_doc(path)
    kind, role = doc_kind(path)
    has_cmd = "python3 tools/" in raw
    a1f, a1w, refs = check_a1(body)
    a2f, a2w, used, checked = check_a2(body, kind, role)
    a3f, a3w = check_a3(body, has_cmd)
    s1f, s1w, s2f, s2w = check_struct(kind, refs, used)
    f1f, f1w = check_header(raw, kind)
    return {"path": path, "kind": kind, "role": role,
            "A1": {"fail": a1f, "warn": a1w, "refs": len(refs), "distinct": len({(g['logical'], g['id']) for g in refs})},
            "A2": {"fail": a2f, "warn": a2w, "checked": checked, "metrics": sorted(used)},
            "A3": {"fail": a3f, "warn": a3w},
            "S1": {"fail": s1f, "warn": s1w}, "S2": {"fail": s2f, "warn": s2w},
            "F1": {"fail": f1f, "warn": f1w}}


def main() -> None:
    p = argparse.ArgumentParser(description="markdown 收口断言（口径 §三/§四/§五）")
    p.add_argument("paths", nargs="*", help="待检 md 路径")
    p.add_argument("--all", action="store_true", help="检查 kb/ 下全部 md")
    p.add_argument("--strict", action="store_true", help="WARN 也计入失败")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    a = p.parse_args()

    paths = list(a.paths)
    if a.all:
        for dirpath, _dn, fns in os.walk(os.path.join(C.ROOT, C.CFG["kb"]["dir"])):
            for fn in sorted(fns):
                if fn.endswith(".md") and fn not in SKIP_FILES:
                    paths.append(os.path.join(dirpath, fn))
    if not paths:
        p.print_help()
        sys.exit(2)

    results, bad = [], 0
    for path in paths:
        r = lint_file(path)
        results.append(r)
        nf = sum(len(r[k]["fail"]) for k in ("A1", "A2", "A3", "S1", "S2", "F1"))
        nw = sum(len(r[k]["warn"]) for k in ("A1", "A2", "A3", "S1", "S2", "F1"))
        if nf or (a.strict and nw):
            bad += 1

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            rel = os.path.relpath(r["path"], C.ROOT)
            nf = sum(len(r[k]["fail"]) for k in ("A1", "A2", "A3", "S1", "S2", "F1"))
            nw = sum(len(r[k]["warn"]) for k in ("A1", "A2", "A3", "S1", "S2", "F1"))
            head = "✅ PASS" if not nf else "❌ FAIL"
            if nf == 0 and nw:
                head = "⚠️  WARN"
            print(f"\n{'='*78}\n{head}  {rel}   [{r['kind']}{'/'+r['role'] if r['role'] else ''}]  FAIL {nf} / WARN {nw}")
            for k in ("A1", "A2", "A3", "S1", "S2", "F1"):
                blk = r[k]
                if k == "A2":
                    stat = f"（已校验 {blk['checked']} 个数字；指标 {','.join(blk['metrics']) or '无'}）"
                elif k == "A1":
                    stat = f"（可解析引用 {blk['refs']} 处 / distinct {blk['distinct']}）"
                else:
                    stat = ""
                if blk["fail"]:
                    print(f"  {k} FAIL{stat}")
                    for x in blk["fail"][:20]:
                        print(f"      ✗ {x}")
                    if len(blk["fail"]) > 20:
                        print(f"      … 另有 {len(blk['fail'])-20} 条")
                elif blk["warn"]:
                    print(f"  {k} WARN{stat}")
                    for x in blk["warn"][:8]:
                        print(f"      ! {x}")
                else:
                    print(f"  {k} PASS{stat}")
        print(f"\n{'='*78}\n文件 {len(results)} 个，异常 {bad} 个")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
