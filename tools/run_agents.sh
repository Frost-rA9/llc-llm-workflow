#!/usr/bin/env bash
# llc-llm-workflow · 角色指南并行生成（Phase C2）
#
# 用法：
#   ./tools/run_agents.sh 李箱                  # pilot：单个角色
#   ./tools/run_agents.sh --all -j 4            # 全部 13 个角色，并发 4
#   ./tools/run_agents.sh --all --dry-run       # 只打印 prompt，不调用 pi
#   ./tools/run_agents.sh 但丁 --model teamorouter/deepseek-flash --no-retry
#
# 说明：
#   · 每个角色 = 一个无头 pi 子进程（`pi -p`），互不共享上下文
#   · 子进程的凭据目录指向可写副本（沙箱把 ~/.pi 挂成只读，无法创建 *.lock）
#   · 收口由脚本负责：agent 自检 + 脚本再跑一次 `--resolve` + `lint_kb`（FAIL 则重试一次）
#   · 日志：build/_agents/<角色>.log ；lint 结果：build/_agents/<角色>.lint.json
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ALL_ROLES=(但丁 李箱 浮士德 堂吉诃德 良秀 默尔索 鸿璐 希斯克利夫 以实玛利 罗佳 辛克莱 奥提斯 格里高尔)

JOBS="${PI_AGENT_JOBS:-4}"
BATCHES="${PI_AGENT_BATCHES:-}"
MODEL="${PI_AGENT_MODEL:-teamorouter/deepseek-flash}"
THINKING="${PI_AGENT_THINKING:-high}"
TIMEOUT="${PI_AGENT_TIMEOUT:-1200}"
RETRY="${PI_AGENT_RETRY:-1}"
AGENT_HOME="${PI_AGENT_HOME:-/tmp/pi-agent-home}"
LOGDIR="$ROOT/build/_agents"
DRY=0
ROLES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --all)      ROLES=("${ALL_ROLES[@]}") ;;
    -j|--jobs)  JOBS="$2"; shift ;;
    --batches)  BATCHES="$2"; shift ;;
    --model)    MODEL="$2"; shift ;;
    --thinking) THINKING="$2"; shift ;;
    --timeout)  TIMEOUT="$2"; shift ;;
    --no-retry) RETRY=0 ;;
    --dry-run)  DRY=1 ;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0 ;;
    -*)         echo "未知参数: $1" >&2; exit 2 ;;
    *)          ROLES+=("$1") ;;
  esac
  shift
done
[ ${#ROLES[@]} -eq 0 ] && { echo "用法: $0 <角色...> | --all   （--help 看全部参数）" >&2; exit 2; }

# ── 凭据目录：复制到可写位置（600，不入项目；跑完可删）
stage_home() {
  mkdir -p "$AGENT_HOME"; chmod 700 "$AGENT_HOME"
  for f in auth.json models.json models-store.json; do
    src="$HOME/.pi/agent/$f"; dst="$AGENT_HOME/$f"
    [ -f "$src" ] || continue
    if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
      cp "$src" "$dst"; chmod 600 "$dst"
    fi
  done
  export PI_CODING_AGENT_DIR="$AGENT_HOME"
}

# ── prompt（用 bash 字符串替换，避免 sed 定界符与内容冲突）
prompt_for() {
  local role="$1" extra="$2"
  local tpl
  tpl="$(cat <<'EOF'
你是「llc-llm-workflow」的角色风格指南撰写 agent，负责角色：@@ROLE@@

## 任务
写出完整的一份 `kb/style/角色/@@ROLE@@.md`，并让它通过收口断言（lint **FAIL 0**）。

## 必读（按顺序，只读）
1. `kb/agent-input.md`   —— 输入契约（10 节结构 / 数字写法 / 红线）
2. `kb/口径.md`          —— 口径定义（G1–G5 / M1–M8 / E1–E3 / 断言 A1–A3·S1·S2·F1）
3. `kb/_templates/角色指南.md.tmpl` —— **冻结模板 v1.0**（必须逐节照此结构）
4. `build/packs/@@ROLE@@.md` —— **唯一数字来源**（§0 口径 / §1 M1–M3 / §2 M4–M8 全表 / §3 敬语称呼 / §4 可用例句占位符 / §5 E1–E3）
5. `kb/style/角色/以实玛利.md` —— **样张**（已 FAIL 0；请对齐其深度：约 200 行、引用 ≥40 处、E1–E3 与横向对比表齐全）

## 硬要求
- 结构：严格照模板的 10 节 + 「### 敬称行占比 · 全队横向（E2-cn）」子节
- 数字：**只准**来自 `build/packs/@@ROLE@@.md` 与 `python3 tools/voice.py --compare`；
  指标必须带公式编号（如 `均中字 **20.5**（M2）`）；派生值写成算式（`3.1 + 2.9 + 0.7 = 6.7（M4）`）
- 引用：≥5 处（**建议 ≥30**）；**只写占位符** `@@<kr_model>|<file>|<id>@@`，**禁止手抄例句**
- 必须写 E1（主题语域）/ E2（敬称三层 KR-EN-CN）/ E3（称呼对象清单）三块；横向对比表 13 行照抄
- 引用唯一合法来源是 `corpus/`；**不得引用任何外部资料**
- **另有三条 lint 抓不到的纪律（见 agent-input.md §3.1）**：
  D1 身份/背景断言必须配**真的支持它**的内联 `文件#id`；
  D2 对比性断言（最高/最低/偏低/全队第 N）必须给 `--table`/`--compare` 来源，或改为定性表述；
  D3 EN→CN 映射若无 pack 证据，必须标「推断」
- **只允许写 `kb/style/角色/@@ROLE@@.md` 这一个文件**；其余文件一律只读

## 收口（必须做到 FAIL 0）
```bash
python3 tools/voice.py --resolve kb/style/角色/@@ROLE@@.md
python3 tools/lint_kb.py kb/style/角色/@@ROLE@@.md
```
lint 报 FAIL 时：按报错逐条修正 → 重跑 → 直到 FAIL 0。
完成后只回一句话：`引用 N 处 / 指标 M 类 / lint 结果`。
EOF
)"
  printf '%s' "${tpl//@@ROLE@@/$role}"
  [ -n "$extra" ] && printf '\n%s\n' "$extra"
}

summary() {  # -> "引用 X 处 / 数字 Y / 指标 N 类"
  python3 tools/lint_kb.py "kb/style/角色/$1.md" --json 2>/dev/null | python3 -c '
import json,re,sys
try:
    d = json.load(sys.stdin)[0]
except Exception:
    print("（无 lint 结果）"); raise SystemExit
ms = {re.match(r"M\d(?:_text)?", m).group(0) for m in d["A2"]["metrics"] if re.match(r"M\d", m)}
print("引用 %d 处 / 数字 %d / 指标 %d 类" % (d["A1"]["refs"], d["A2"]["checked"], len(ms)))
'
}

run_one() {
  local role="$1" log="$LOGDIR/$role.log" attempt rc
  : > "$log"
  echo "[$(date +%H:%M:%S)] ▶ $role  启动（model=$MODEL thinking=$THINKING timeout=${TIMEOUT}s）" | tee -a "$log"

  for attempt in 1 2; do
    local extra=""
    if [ "$attempt" -eq 2 ]; then
      [ "$RETRY" -eq 1 ] || break
      extra="$(printf '## 上一次 lint 失败输出（请据此修复；勿改动其他文件）\n```\n%s\n```' "$(tail -60 "$log")")"
    fi

    ( timeout "$TIMEOUT" pi -p --no-session --no-context-files \
        --provider "${MODEL%%/*}" --model "${MODEL#*/}" --thinking "$THINKING" \
        --tools read,bash,edit,write \
        "$(prompt_for "$role" "$extra")" >>"$log" 2>&1 )
    rc=$?

    python3 tools/voice.py --resolve "kb/style/角色/$role.md" >>"$log" 2>&1
    if python3 tools/lint_kb.py "kb/style/角色/$role.md" --json >"$LOGDIR/$role.lint.json" 2>>"$log" \
       && python3 tools/lint_kb.py "kb/style/角色/$role.md" >>"$log" 2>&1; then
      echo "[$(date +%H:%M:%S)] ✅ $role  通过（attempt=$attempt, rc=$rc）  $(summary "$role")"
      return 0
    fi
    echo "[$(date +%H:%M:%S)] ⚠️  $role  attempt=$attempt lint FAIL（rc=$rc）→ 见 $log"
  done
  echo "[$(date +%H:%M:%S)] ❌ $role  未通过（日志：$log）"
  return 1
}

# ── main
echo "角色 ${#ROLES[@]} 个：${ROLES[*]}"
echo "并发 $JOBS${BATCHES:+ · 分组 $BATCHES} · 模型 $MODEL · thinking $THINKING · 重试 $RETRY 次 · 日志 $LOGDIR"

if [ "$DRY" -eq 1 ]; then
  prompt_for "${ROLES[0]}" ""
  exit 0
fi

stage_home
mkdir -p "$LOGDIR"
START_TS="$(date '+%Y-%m-%d %H:%M:%S')"
fail=0

if [ -n "$BATCHES" ]; then
  # 显式分组：--batches 3,3,3,4  → 组内全并行，组间串行
  IFS=',' read -ra SIZES <<< "$BATCHES"
  idx=0; gi=0
  for sz in "${SIZES[@]}"; do
    gi=$((gi+1))
    batch=("${ROLES[@]:idx:sz}"); idx=$((idx+sz))
    [ ${#batch[@]} -eq 0 ] && continue
    echo "[$(date +%H:%M:%S)] ══ 第 $gi 组（${#batch[@]} 并行）：${batch[*]}"
    for role in "${batch[@]}"; do run_one "$role" & done
    wait || true
    echo "[$(date +%H:%M:%S)] ── 第 $gi 组结束"
  done
else
  for role in "${ROLES[@]}"; do
    run_one "$role" &
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
  done
  wait || true
fi
echo
echo "════════ 结果 ════════"
for role in "${ROLES[@]}"; do
  if python3 tools/lint_kb.py "kb/style/角色/$role.md" >/dev/null 2>&1; then
    printf '  ✅ %-6s %s\n' "$role" "$(summary "$role")"
  else
    printf '  ❌ %-6s lint 未通过（%s）\n' "$role" "$LOGDIR/$role.log"; fail=$((fail+1))
  fi
done
echo "通过 $(( ${#ROLES[@]} - fail )) / ${#ROLES[@]}"

# ── 完整性检查：除目标 md 外，工作区不应有别的改动
changed="$(find corpus kb tools config.toml third_party -type f -newermt "$START_TS" 2>/dev/null \
           | grep -v '^kb/style/角色/' | grep -v '__pycache__' || true)"
if [ -n "$changed" ]; then
  echo "⚠️  检测到目标 md 之外的改动（请核查）："; echo "$changed" | sed 's/^/    /'
else
  echo "✅ 完整性：除 kb/style/角色/*.md 外无其他文件改动"
fi
echo "（凭据副本在 $AGENT_HOME，可随时删除：rm -rf $AGENT_HOME）"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
