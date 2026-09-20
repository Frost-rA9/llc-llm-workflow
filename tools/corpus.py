#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llc-llm-workflow · 语料规范化（四语对齐）

口径契约：kb/口径.md
  G1 逻辑名映射   官方文件去语言前缀(KR_/EN_/JP_)；跳过 Info/ Font/
  G2 对齐键       必须遍历 dataList 列表（不去重）；StoryData 用 id，RPG 用 key
  G3 字段抽取     跳过 id/key/Id/ID/scene/code/type/category；model/teller 仅口吻分析保留；list 递归展开
  G4 产物格式     JSONL 每行 {"f":逻辑名,"l":语言,"id":键,"k":字段名,"t":文本}

用法：
  python3 tools/corpus.py build                # 建索引 + 导出语料
  python3 tools/corpus.py stats                # 规模统计
  python3 tools/corpus.py show <逻辑名>        # 四语逐条对照（如 StoryData/S1002B.json）
  python3 tools/corpus.py grep <关键词> -l cn  # 在指定语言检索
  python3 tools/corpus.py find <关键词>        # 按关键字找逻辑文件
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import tomllib

# ──────────────────────────────────────────────── 配置

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = tomllib.load(open(os.path.join(ROOT, "config.toml"), "rb"))

LANGS: tuple[str, ...] = tuple(CFG["lang"]["codes"])
PREFIX: dict[str, str] = dict(CFG["lang"]["prefix"])
SKIP_DIRS: set[str] = set(CFG["lang"]["skip_dirs"])

_BUILD = os.path.join(ROOT, CFG["build"]["dir"])


def lang_root(lang: str) -> str:
    """某语言的根目录。官方三语在 game_localize/<lang>，中文为零协 base。"""
    if lang == "cn":
        return os.path.join(ROOT, CFG["corpus"]["llc_base"])
    return os.path.join(ROOT, CFG["corpus"]["game_localize"], lang)


INDEX_PATH = os.path.join(_BUILD, "index.json")
CORPUS_PATH = os.path.join(_BUILD, "corpus.jsonl")

# G3：抽取时跳过的字段
SKIP_FIELDS = {"id", "key", "Id", "ID", "scene", "code", "type", "category"}
META_FIELDS = {"model", "teller"}


# ──────────────────────────────────────────────── 索引（G1）

def logical_name(lang: str, relpath: str) -> str:
    """官方文件去语言前缀 → 与零协侧对齐的逻辑名（口径 G1）。"""
    d, b = os.path.split(relpath)
    p = PREFIX.get(lang, "")
    if p and b.startswith(p):
        b = b[len(p):]
    return os.path.join(d, b) if d else b


def _pruned_walk(root: str):
    """遍历 root，剪掉 SKIP_DIRS（仅针对根下一级）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != "." and rel_dir.split(os.sep)[0] in SKIP_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not (rel_dir == "." and d in SKIP_DIRS)]
        yield dirpath, filenames


def build_index(force: bool = False) -> dict[str, dict[str, str]]:
    """扫描四语目录 → {逻辑名: {lang: 相对路径}}。"""
    if not force and os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    index: dict[str, dict[str, str]] = {}
    for lang in LANGS:
        root = lang_root(lang)
        if not os.path.isdir(root):
            print(f"  [!] 缺语言目录: {lang} → {root}", file=sys.stderr)
            continue
        for dirpath, filenames in _pruned_walk(root):
            for fn in filenames:
                if not fn.endswith(".json"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                index.setdefault(logical_name(lang, rel), {})[lang] = rel
    os.makedirs(_BUILD, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=0, sort_keys=True)
    return index


# ──────────────────────────────────────────────── 读取（G2/G3）

def load_records(lang: str, rel: str) -> list[dict]:
    """读一个 JSON，返回记录列表（遍历 dataList，不去重）。

    编码固定 utf-8-sig（口径 G3 补充）：官方三语中各有 9 个文件带 UTF-8 BOM，
    若用 utf-8 读取会因 BOM 报错而被静默丢弃（实测三语共丢 1,035 行）。
    """
    path = os.path.join(lang_root(lang), rel)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"  [!] 解析失败 {lang}/{rel}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("dataList", "list", "items", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        return []          # 空 dict / 非记录对象 → 0 条（不当作 1 条空记录）
    return []


def record_key(rec: dict):
    """记录键（口径 G2）：id → key → Id → ID，统一字符串化。"""
    for k in ("id", "key", "Id", "ID"):
        if k in rec and rec[k] is not None:
            return str(rec[k])
    return None


def text_fields(rec: dict, keep_meta: bool = False) -> dict[str, str]:
    """抽取记录内所有文本字段（口径 G3）；list 递归展开为 `path[i]`。"""
    skip = set(SKIP_FIELDS) | (set() if keep_meta else META_FIELDS)
    out: dict[str, str] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, str):
            if path and node.strip():
                out[path] = node
            return
        if isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if path == "" and k in skip:
                    continue
                walk(v, f"{path}.{k}" if path else k)

    for k, v in rec.items():
        if k in skip:
            continue
        walk(v, k)
    return out


def iter_corpus(langs=LANGS, index: dict | None = None):
    """逐条产出 (逻辑名, lang, 记录)。"""
    index = index if index is not None else build_index()
    for logical in sorted(index):
        for lang in langs:
            rel = index[logical].get(lang)
            if not rel:
                continue
            for rec in load_records(lang, rel):
                yield logical, lang, rec


# ──────────────────────────────────────────────── 导出（G4）

def export_corpus() -> int:
    index = build_index(force=True)
    os.makedirs(_BUILD, exist_ok=True)
    per_lang = collections.Counter()
    n = 0
    with open(CORPUS_PATH, "w", encoding="utf-8") as fh:
        for logical, lang, rec in iter_corpus(index=index):
            key = record_key(rec)
            for k, t in text_fields(rec).items():
                fh.write(json.dumps({"f": logical, "l": lang, "id": key, "k": k, "t": t},
                                    ensure_ascii=False) + "\n")
                n += 1
                per_lang[lang] += 1
    print(f"  逻辑文件数 {len(index)}")
    print(f"  文本行数   {n}  → {CORPUS_PATH}")
    print(f"  各语言     " + "  ".join(f"{k}={v}" for k, v in sorted(per_lang.items())))
    return n


# ──────────────────────────────────────────────── CLI

def cmd_build(_a) -> None:
    print("[1/2] 建索引 …")
    index = build_index(force=True)
    cov = collections.Counter(len(v) for v in index.values())
    print(f"  逻辑文件 {len(index)}；语言覆盖分布 {dict(sorted(cov.items()))}")
    print("[2/2] 导出语料 …")
    export_corpus()


def cmd_stats(_a) -> None:
    index = build_index()
    per_lang = collections.Counter()
    for logical, langs in index.items():
        for lang in langs:
            per_lang[lang] += 1
    print(f"逻辑文件数: {len(index)}")
    for lang in LANGS:
        print(f"  {lang:<3} {per_lang[lang]:>6}")
    if os.path.exists(CORPUS_PATH):
        n = sum(1 for _ in open(CORPUS_PATH, encoding="utf-8"))
        print(f"corpus.jsonl 行数: {n}  ({os.path.getsize(CORPUS_PATH)/1048576:.1f} MB)")


def cmd_audit(_a) -> None:
    """编码/空文件审计 —— 防止静默丢数据。"""
    index = build_index()
    bom_files, empty_files = [], []
    for logical, langs in sorted(index.items()):
        for lang, rel in langs.items():
            p = os.path.join(lang_root(lang), rel)
            try:
                with open(p, "rb") as fh:
                    raw = fh.read()
            except Exception:
                continue
            if raw[:3] == b"\xef\xbb\xbf":
                bom_files.append((lang, logical))
            if raw.strip() in (b"{}", b""):
                empty_files.append((lang, logical))
    print(f"带 UTF-8 BOM 的文件: {len(bom_files)}（必须用 utf-8-sig 读）")
    for lang, lg in bom_files[:15]:
        print(f"  {lang}  {lg}")
    print(f"\n空内容文件: {len(empty_files)}")
    for lang, lg in empty_files[:15]:
        print(f"  {lang}  {lg}")


def _find_logical(index: dict, needle: str) -> list[str]:
    n = needle.lower()
    return [k for k in index if n in k.lower()]


def cmd_show(a) -> None:
    index = build_index()
    hits = _find_logical(index, a.name)
    if not hits:
        print(f"找不到: {a.name}", file=sys.stderr)
        return
    logical = hits[0]
    if len(hits) > 1:
        print(f"（匹配 {len(hits)} 个，显示 {logical}）")
    data = {lang: {record_key(r): r for r in load_records(lang, index[logical][lang])}
            for lang in index[logical]}
    ids: list = []
    for lang in LANGS:
        for rid in data.get(lang, {}):
            if rid not in ids:
                ids.append(rid)
    print(f"# {logical}   语料 {sorted(data)}   记录 {len(ids)}")
    for rid in ids:
        print(f"\n--- id={rid}")
        for lang in LANGS:
            rec = data.get(lang, {}).get(rid)
            if not rec:
                continue
            for k, t in text_fields(rec, keep_meta=True).items():
                if k == "model" or t:
                    print(f"  {lang.upper()}| {k}={t!r}")


def cmd_grep(a) -> None:
    langs = [a.lang] if a.lang and a.lang != "all" else list(LANGS)
    pat = re.compile(a.pattern)
    for logical, lang, rec in iter_corpus(langs=langs):
        for k, t in text_fields(rec, keep_meta=True).items():
            if pat.search(t):
                print(f"{logical}#{record_key(rec)} [{lang}/{k}] {t[:120]}")


def cmd_find(a) -> None:
    for k in _find_logical(build_index(), a.name):
        print(k)


def main() -> None:
    p = argparse.ArgumentParser(description="语料规范化（口径 G1–G4）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("audit").set_defaults(fn=cmd_audit)
    s = sub.add_parser("show"); s.add_argument("name"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("grep"); s.add_argument("pattern"); s.add_argument("-l", "--lang", default="all"); s.set_defaults(fn=cmd_grep)
    s = sub.add_parser("find"); s.add_argument("name"); s.set_defaults(fn=cmd_find)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
