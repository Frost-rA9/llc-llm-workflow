#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llc-llm-workflow · 证据规范化（口吻量化 + 证据包）

口径契约：kb/口径.md G5、§2.0–2.3

  L       = 遍历 dataList + kr model 匹配 + en/cn 均非空      → M1/M4/M6/M7/M8 的分母
  L_text  = L 且 en 含 [A-Za-z] 且 cn 含汉字                  → M2/M3 的分母
  C_text  = L_text 集的 cn 总字符数（去空白）                  → M5 的分母
  W_text  = L_text 集的 en 总词数

角色归属用**基准 model**，不含剧情变体（口径 G5 补充）：
  以实玛利 = `이스마엘`（不含 다친이스마엘 / 이스마엘다침 / 이스마엘F）

用法：
  python3 tools/voice.py --list                     # 列出 13 个角色与其基准 model
  python3 tools/voice.py --pack 以实玛利             # 生成 build/packs/以实玛利.md
  python3 tools/voice.py --pack all                 # 全部角色
  python3 tools/voice.py --table                    # 打印速查表数据
  python3 tools/voice.py --role 以实玛利            # 打印单角色指标表
  python3 tools/voice.py --resolve kb/style/角色/以实玛利.md   # 回填 @@model|file|id@@
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus as C  # noqa: E402

# ──────────────────────────────────────────────── 角色注册表
# 顺序：但丁在前，其余按罪人编号

ROLES: list[tuple[str, str, str]] = [
    ("단테",      "但丁",      "Dante"),
    ("이상",      "李箱",      "Yi Sang"),
    ("파우스트",   "浮士德",    "Faust"),
    ("돈키호테",   "堂吉诃德",  "Don Quixote"),
    ("료슈",      "良秀",      "Ryōshū"),
    ("뫼르소",     "默尔索",    "Meursault"),
    ("홍루",      "鸿璐",      "Hong Lu"),
    ("히스클리프",  "希斯克利夫", "Heathcliff"),
    ("이스마엘",   "以实玛利",  "Ishmael"),
    ("로쟈",      "罗佳",      "Rodion"),
    ("싱클레어",   "辛克莱",    "Sinclair"),
    ("오티스",     "奥提斯",    "Outis"),
    ("그레고르",   "格里高尔",  "Gregor"),
]
BY_CN = {cn: kr for kr, cn, _ in ROLES}
BY_KR = {kr: (cn, en) for kr, cn, en in ROLES}

# ──────────────────────────────────────────────── 模式表（口径 §2）

PATTERNS: list[tuple[str, str, str]] = [
    ("……", r"…+",    "标点"),
    ("！", r"！",     "标点"),
    ("？", r"？",     "标点"),
    ("。", r"。",     "标点"),
    ("我", r"我(?!们)", "自称"),
    ("我们", r"我们",   "自称"),
    ("咱", r"咱",     "自称"),
    ("俺", r"俺",     "自称"),
    ("本人", r"本人",   "自称"),
    ("在下", r"在下",   "自称"),
    ("老夫", r"老夫",   "自称"),
    ("人家", r"(?<!别)人家", "自称"),
    ("你", r"你",     "称呼"),
    ("你们", r"你们",   "称呼"),
    ("您", r"您",     "敬语"),
    ("请", r"请",     "敬语"),
    ("先生", r"先生",   "敬语"),
    ("小姐", r"小姐",   "敬语"),
    ("女士", r"女士",   "敬语"),
    ("呢", r"呢",     "语气"),
    ("啊", r"啊",     "语气"),
    ("吧", r"吧",     "语气"),
    ("呀", r"呀",     "语气"),
    ("哦", r"哦",     "语气"),
    ("啦", r"啦",     "语气"),
    ("嗯", r"嗯",     "语气"),
    ("哈", r"哈",     "语气"),
    ("呜", r"呜",     "语气"),
    ("吗", r"吗",     "语气"),
]
PAT_RE = {name: re.compile(rx) for name, rx, _ in PATTERNS}
PAT_GROUPS = {name: grp for name, _, grp in PATTERNS}

LATIN = re.compile(r"[A-Za-z]")
HAN = re.compile(r"[\u4e00-\u9fff]")
WS = re.compile(r"\s")
PLACEHOLDER = re.compile(r"@@([^|@]+)\|([^|@]+)\|([^@]*)@@")

# ──────────────────────────────────────────────── 辅助证据（E1–E3，非 M1–M8）
# E1 主题语域行占比 / E2 敬称三层行占比（kr/en/cn）/ E3 称呼对象行数
# 这三项支撑「专业语域」「英文丢敬称」两个关键结论。

THEME_CN = re.compile(r"大湖|白鲸|捕鲸叉|捕鲸枪|鱼叉|水手|船员|船长|大副|甲板|航海|罗盘|船")
HON = {
    # KR：씨/님 作敬称后缀（排除「선생님」「아가씨」等固定词）；양/군 因与「공양」「그렇군요」混淆不用
    "kr": re.compile(r"(?<!선생)(?<!아가)(?:씨|님)"),
    # EN：必须带点，避免把「Missing」误判为 Miss
    "en": re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mister|Ma'am)\.|\bSir\b"),
    "cn": re.compile(r"先生|小姐|女士"),
}
TARGET_RE = re.compile(r"(?:先生|小姐|女士)")
# E3 名称提取的停用字（避免把「想要把李箱先生」整段当名字）
NAME_STOP = set("的是把和在里子了有就也都这那不们个想要让次经看着对给被与及或者然后又很么什么怎么这么那么你我他她它上下前后中内外")
HAN1 = re.compile(r"[\u4e00-\u9fff]")


def extract_targets(cn: str) -> list[str]:
    """取敬称前最近的中文名（最长 5 字，剔掉停用字）。"""
    out = []
    for mo in TARGET_RE.finditer(cn):
        i, j = mo.start(), mo.start()
        while j > 0 and i - j < 5 and HAN1.match(cn[j - 1]):
            j -= 1
        run = cn[j:i]
        while run and run[0] in NAME_STOP:
            run = run[1:]
        if 2 <= len(run) <= 5:
            out.append(run)
    return out


def target_counts(rows: list) -> dict[str, int]:
    """E3：两遍法——先统计候选，再按字典取最长后缀（剔除「模仿鸿璐」这类粘连）。

    字典规则：2–3 字候选全收；4–5 字候选需在本角色语料出现 ≥2 次。
    """
    raw: list[list[str]] = []
    cnt: collections.Counter = collections.Counter()
    for r in rows:
        runs = extract_targets(r[4])
        raw.append(runs)
        for x in runs:
            cnt[x] += 1
    dic = {x for x, c in cnt.items() if len(x) <= 3 or c >= 2}
    out: collections.Counter = collections.Counter()
    for runs in raw:
        for x in runs:
            for L in range(len(x), 1, -1):
                if x[-L:] in dic:
                    out[x[-L:]] += 1
                    break
    return dict(out.most_common(20))


# ──────────────────────────────────────────────── 语料取数

def story_entries() -> list[tuple[str, str, str, str, str]]:
    """产出 (逻辑名, id, kr_text, en_text, cn_text, kr_model)，遍历列表不去重。"""
    index = C.build_index()
    out = []
    for logical in sorted(index):
        if not logical.startswith("StoryData" + os.sep):
            continue
        lans = index[logical]
        if not all(l in lans for l in ("kr", "en", "cn")):
            continue
        ei: dict = collections.defaultdict(list)
        ci: dict = collections.defaultdict(list)
        for r in C.load_records("en", lans["en"]):
            ei[C.record_key(r)].append(r)
        for r in C.load_records("cn", lans["cn"]):
            ci[C.record_key(r)].append(r)
        for r in C.load_records("kr", lans["kr"]):
            model = r.get("model")
            if not model:
                continue
            k = C.record_key(r)
            if not ei.get(k) or not ci.get(k):
                continue
            e = ei[k][0].get("content") or ""
            c = ci[k][0].get("content") or ""
            if e.strip() and c.strip():
                out.append((logical, k, r.get("content") or "", e, c, model))
    return out


_CACHE: list | None = None


def all_rows() -> list:
    global _CACHE
    if _CACHE is None:
        _CACHE = story_entries()
    return _CACHE


def role_rows(role_cn: str) -> list:
    kr = BY_CN.get(role_cn, role_cn)
    return [r for r in all_rows() if r[5] == kr]


def variant_models(role_cn: str) -> list[str]:
    """该角色的剧情变体 model（非基准，仅报告）。"""
    kr = BY_CN.get(role_cn, role_cn)
    found = {r[5] for r in all_rows()
             if r[5] and r[5] != kr and (r[5].startswith(kr) or r[5].endswith(kr))}
    return sorted(m for m in found if m != kr)


# ──────────────────────────────────────────────── 指标（口径 §2.3）

def metrics(rows: list) -> dict:
    L = len(rows)
    rows_t = [r for r in rows if LATIN.search(r[3]) and HAN.search(r[4])]
    L_t = len(rows_t)
    C_t = sum(len(WS.sub("", r[4])) for r in rows_t)
    W_t = sum(len(r[3].split()) for r in rows_t)
    C_a = sum(len(WS.sub("", r[4])) for r in rows)      # 全体口径（含纯符号行）
    W_a = sum(len(r[3].split()) for r in rows)
    m = {
        "L": L, "L_text": L_t, "C_text": C_t, "W_text": W_t,
        "C_all": C_a, "W_all": W_a,
        "M2": (C_a / L) if L else 0.0,
        "M3": (W_a / L) if L else 0.0,
        # 对账列：分母改为 L_text（与主口径唯一差别在分母）
        "M2_text": (C_a / L_t) if L_t else 0.0,
        "M3_text": (W_a / L_t) if L_t else 0.0,
        "files": len({r[0] for r in rows}),
        "kr_model": rows[0][5] if rows else None,
        "pats": {},
    }
    # E1/E2/E3 辅助证据
    n_theme = sum(1 for r in rows if THEME_CN.search(r[4]))
    hon = {}
    for lang, rx in HON.items():
        idx = {"kr": 2, "en": 3, "cn": 4}[lang]
        n = sum(1 for r in rows if rx.search(r[idx]))
        hon[lang] = {"rows": n, "pct": (n * 100 / L) if L else 0.0}
    targets = target_counts(rows)
    m["E"] = {
        "theme": {"rows": n_theme, "pct": (n_theme * 100 / L) if L else 0.0},
        "hon": hon,
        "targets": targets,
    }
    for name, rx_raw, _grp in PATTERNS:
        rx = PAT_RE[name]
        H = sum(len(rx.findall(r[4])) for r in rows)
        Rh = sum(1 for r in rows if rx.search(r[4]))
        Rx = re.compile(rx_raw + r"$")
        Rt = sum(1 for r in rows if Rx.search(r[4]))
        m["pats"][name] = {
            "组": _grp, "H": H,
            "M4": (H * 100 / L) if L else 0.0,
            "M5": (H * 1000 / C_a) if C_a else 0.0,
            "M6": (Rh * 100 / L) if L else 0.0,
            "M7": (Rt * 100 / L) if L else 0.0,
        }
    return m


# ──────────────────────────────────────────────── 抽样

def sample_rows(rows: list, pattern: str | None, n: int) -> list:
    """确定性抽样：优先分散在不同文件。"""
    pool = rows
    if pattern:
        rx = PAT_RE[pattern]
        pool = [r for r in rows if rx.search(r[4])]
    if len(pool) <= n:
        return pool
    step = len(pool) / n
    return [pool[int(i * step)] for i in range(n)]


# ──────────────────────────────────────────────── 证据包

def build_pack(role_cn: str, n_samples: int = 60) -> str:
    rows = role_rows(role_cn)
    m = metrics(rows)
    en_name = BY_KR.get(m["kr_model"] or "", ("", ""))[1]
    out = []
    A = out.append
    A(f"# 证据包 · {role_cn} / {en_name}\n")
    A("> 生成：`python3 tools/voice.py --pack %s`" % role_cn)
    A("> 口径：`kb/口径.md` G1–G5、M1–M8、§2.0–2.3")
    A(f"> 语料基准：零协 `{C.CFG['base']['llc_version']}` × 游戏 `{{kr,en,jp}}`\n")
    A("---\n")

    A("## 0. 语料口径\n")
    A(f"- kr `model`（基准，不含剧情变体）：`{m['kr_model']}`")
    var = variant_models(role_cn)
    A(f"- ⚠️ 已排除的剧情变体 model（{len(var)} 个）：{', '.join('`'+v+'`' for v in var) if var else '无'}")
    A(f"- 出场逻辑文件数：**{m['files']}**")
    A(f"- **`L` = {m['L']}**（M1 主口径；分母用于 M1/M4/M6/M7/M8）")
    A(f"- **`L_text` = {m['L_text']}**（文本口径；分母用于 M2/M3）")
    A(f"- `C_all` = {m['C_all']} 字　`W_all` = {m['W_all']} 词（M5 分母，口径 §2.2）")
    A(f"- `C_text` = {m['C_text']} 字　`W_text` = {m['W_text']} 词（`L_text` 集，对账用）\n")

    A("## 1. M1–M3\n")
    A("| 指标 | 值 | 公式 |")
    A("|---|---:|---|")
    A(f"| 句数 L | **{m['L']}** | 遍历 dataList + en/cn 非空 |")
    A(f"| 句数 L_text | **{m['L_text']}** | L 且 en 含字母 + cn 含汉字 |")
    A(f"| 均中字 | **{m['M2']:.1f}** | C_all / L（M2） |")
    A(f"| 均英词 | **{m['M3']:.1f}** | W_all / L（M3） |")
    A(f"| 均中字（对账 M2_text） | {m['M2_text']:.1f} | C_all / L_text |")
    A(f"| 均英词（对账 M3_text） | {m['M3_text']:.1f} | W_all / L_text |\n")

    A("## 2. M4/M5/M6/M7/M8 全表\n")
    A("| 模式 | 组 | H | M4 每百句 | M5 每千字 | M6 行级% | M7 行尾% |")
    A("|---|---|---:|---:|---:|---:|---:|")
    for name, _, grp in PATTERNS:
        p = m["pats"][name]
        if p["H"] == 0:
            continue
        A(f"| `{name}` | {grp} | {p['H']} | {p['M4']:.1f} | {p['M5']:.2f} | {p['M6']:.1f} | {p['M7']:.1f} |")
    A("")
    A("> M8（x/N 计数比）= `H` / `L`；上表 `H` 列即分子。\n")

    A("## 3. 敬语 / 称呼证据\n")
    for grp in ("敬语", "称呼"):
        items = [(n_, m["pats"][n_]) for n_ in m["pats"] if m["pats"][n_]["组"] == grp and m["pats"][n_]["H"]]
        if not items:
            continue
        A(f"**{grp}**（M4 每百句降序）\n")
        A("| 模式 | H | M4 | M5 |")
        A("|---|---:|---:|---:|")
        for n_, p in sorted(items, key=lambda x: -x[1]["M4"]):
            A(f"| `{n_}` | {p['H']} | {p['M4']:.1f} | {p['M5']:.2f} |")
        A("")

    A("## 4. EN/CN 实例（真实语料，供写作引用）\n")
    A("> ⚠️ 写作时**只允许**写占位符 `@@<model>|<file>|<id>@@`，由 `--resolve` 回填；**禁止手抄**。\n")
    for pat in ["……", "？", "！", "您", "我", "我们"]:
        if not m["pats"][pat]["H"]:
            continue
        A(f"### 模式 `{pat}`（M4 {m['pats'][pat]['M4']:.1f}，H {m['pats'][pat]['H']}）\n")
        for logical, rid, kr, en, cn, _model in sample_rows(rows, pat, 6):
            A(f"- `[{logical}#{rid}]`  `@@{m['kr_model']}|{logical}|{rid}@@`")
            A(f"  - EN: {en[:160]}")
            A(f"  - CN: {cn[:160]}")
        A("")

    A("### 通用样本（分散抽样）\n")
    for logical, rid, kr, en, cn, _model in sample_rows(rows, None, n_samples):
        A(f"- `{logical}#{rid}`  EN: {en[:110]}  ｜ CN: {cn[:110]}")
    A("")

    e = m["E"]
    A("## 5. 辅助证据（E1–E3，非 M1–M8）\n")
    A(f"- **E1 主题语域**（大湖/捕鲸/船只类词）：**{e['theme']['rows']} 行（{e['theme']['pct']:.1f}%）**")
    A("- **E2 敬称三层**（行含敬称标记）："
      + "　".join(f"{k.upper()} {e['hon'][k]['rows']} 行（{e['hon'][k]['pct']:.1f}%）"
                  for k in ("kr", "en", "cn")))
    A(f"- **E3 称呼对象**（前 20，行数）："
      + "、".join(f"{k}×{v}" for k, v in e["targets"].items()))
    A("")
    return "\n".join(out)


# ──────────────────────────────────────────────── 回填

def resolve(md_path: str) -> int:
    rows = {(r[0], str(r[1])): r for r in all_rows()}
    with open(md_path, encoding="utf-8") as fh:
        text = fh.read()
    n = 0

    def sub(mo: re.Pattern) -> str:
        nonlocal n
        logical, rid = mo.group(2), mo.group(3)
        r = rows.get((logical, str(rid)))
        if not r:
            return mo.group(0)
        n += 1
        return f"{r[3]}  ｜ CN: {r[4]}"

    text = PLACEHOLDER.sub(sub, text)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    left = len(PLACEHOLDER.findall(text))
    return n


# ──────────────────────────────────────────────── CLI

def cmd_list(_a) -> None:
    print(f"{'kr model':<14}{'中文':<10}{'EN':<14}{'出场文件':>7}{'L':>7}{'L_text':>8}")
    for kr, cn, en in ROLES:
        rows = role_rows(cn)
        m = metrics(rows)
        print(f"{kr:<14}{cn:<10}{en:<14}{m['files']:>7}{m['L']:>7}{m['L_text']:>8}")


def cmd_role(a) -> None:
    rows = role_rows(a.role)
    m = metrics(rows)
    print(f"角色 {a.role}  kr={m['kr_model']}  文件 {m['files']}  L={m['L']}  L_text={m['L_text']}")
    print(f"均中字 {m['M2']:.1f}（M2）  均英词 {m['M3']:.1f}（M3）")
    print(f"{'模式':<8}{'H':>6}{'M4':>8}{'M5':>8}{'M6':>8}{'M7':>8}")
    for name, _, _grp in PATTERNS:
        p = m["pats"][name]
        if not p["H"]:
            continue
        print(f"{name:<8}{p['H']:>6}{p['M4']:>8.1f}{p['M5']:>8.2f}{p['M6']:>8.1f}{p['M7']:>8.1f}")


def cmd_compare(_a) -> None:
    """13 角色横向对比（E2 敬称行占比，口径同 §2.4）"""
    print("| 排名 | 角色 | 敬称行占比 E2-cn | 行数 | 主题语域 E1 |")
    print("|---:|---|---:|---:|---:|")
    data = []
    for _kr, cn, en in ROLES:
        m = metrics(role_rows(cn))
        data.append((cn, en, m))
    for i, (cn, en, m) in enumerate(
            sorted(data, key=lambda x: -x[2]["E"]["hon"]["cn"]["pct"]), 1):
        h = m["E"]["hon"]["cn"]
        print(f"| {i} | {cn} {en} | **{h['pct']:.2f}%** | {h['rows']} | {m['E']['theme']['pct']:.1f}% |")


def cmd_table(_a) -> None:
    cols = ["……", "！", "？", "我", "我们", "你", "您", "呢", "啊", "吧"]
    hdr = ["角色", "句数", "均中字"] + cols
    print("| " + " | ".join(hdr) + " |")
    print("|" + "---|" * len(hdr))
    for kr, cn, en in ROLES:
        m = metrics(role_rows(cn))
        row = [f"{cn} {en}", str(m["L"]), f"{m['M2']:.1f}"]
        row += [f"{m['pats'][c]['M4']:.1f}" for c in cols]
        print("| " + " | ".join(row) + " |")


def cmd_pack(a) -> None:
    outdir = os.path.join(C.ROOT, C.CFG["build"]["dir"], "packs")
    os.makedirs(outdir, exist_ok=True)
    target = a.pack
    targets = [cn for _kr, cn, _en in ROLES] if target == "all" else [target]
    for cn in targets:
        p = os.path.join(outdir, f"{cn}.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(build_pack(cn, a.samples))
        print(f"  {os.path.relpath(p, C.ROOT)}")


def cmd_resolve(a) -> None:
    n = resolve(a.path)
    print(f"  回填 {n} 处引用 → {a.path}")


def main() -> None:
    p = argparse.ArgumentParser(
        description="证据规范化（口径 G5 / §2）",
        epilog="例: voice.py --pack 以实玛利 | voice.py --table | voice.py --resolve kb/style/角色/以实玛利.md",
    )
    p.add_argument("--list", action="store_true", help="列出 13 个角色与其基准 model")
    p.add_argument("--role", metavar="中文名", help="打印单角色指标表")
    p.add_argument("--table", action="store_true", help="打印速查表数据（Markdown）")
    p.add_argument("--compare", action="store_true", help="13 角色横向对比（E2 敬称 / E1 主题语域）")
    p.add_argument("--pack", metavar="中文名|all", help="生成证据包到 build/packs/")
    p.add_argument("--resolve", metavar="MD路径", help="回填 @@model|file|id@@ 占位符")
    p.add_argument("--samples", type=int, default=60, help="证据包的通用样本数（默认 60）")
    a = p.parse_args()
    if a.list:
        return cmd_list(a)
    if a.role:
        return cmd_role(a)
    if a.table:
        return cmd_table(a)
    if a.compare:
        return cmd_compare(a)
    if a.pack:
        return cmd_pack(a)
    if a.resolve:
        a.path = a.resolve
        return cmd_resolve(a)
    p.print_help()


if __name__ == "__main__":
    main()
