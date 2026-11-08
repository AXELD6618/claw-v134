# -*- coding: utf-8 -*-
"""
audit_limit_duplication.py — 涨跌停口径重复逻辑【AST 级】审计扫描器（单一判别真相源）

演进：
  v1 三桶(centralized/canonical/suspicious) 靠信号词 → 误报虚高到 322。
  v2 正则判别 genuine_limit vs market_classifier → 收敛到 70，但仍把"权重/阈值里的
     0.30"误算成幅度逻辑，且漏掉用元组前缀/别名函数名的真实幅度函数。
  v3(本版) AST 级识别：以"函数 return 幅度常量 + startswith"为判据，精确区分：
       genuine_limit  真实幅度函数(可带测试迁移)
       inline_limit   内联幅度赋值(如 lim = 0.20 if is_20 else 0.10，需单独精确处理)
       prefixed_input 带市场前缀(bj/sh/sz)的代码，委托 canonical 会行为回退 → 排除
       market_classifier 板块分类器(返回字符串) → 排除
       false_positive 0.30 只是权重/阈值/仓位/随机场景 → 排除
  ast_find_limit_funcs() 由 migrate_limit_logic.py 复用，保证判别口径唯一。
  v4(2026-11-06) 两处反自证修正：
    ① 不再以「含旧字面元组」判为 canonical 已正确——canonical 边界修正(689/92)
      后，这类副本恰恰最容易静默漂移，统一落 suspicious 交 AST 分类；
    ② 内联幅度赋值排除 `meta.get("lim", 0.1)` 这类配置默认值（来自元数据，
      不是硬编码板块幅度判定），消除 intraday_agentic_v2.py 之类的误命中。
    ③ 【安全关键】真值源 research/board_limit.py 自身曾被判为 genuine_limit：
      迁移工具会把 board_limit_pct 替换为调用它自己，造成无限递归、摧毁单一真相源。
      现显式排除；centralized 也改为 AST 真实 ImportFrom 判据，不再靠文本子串自匹配。

用法：
  python research/audit_limit_duplication.py [--json-out PATH]
"""
import os
import sys
import re
import ast
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CENTRALIZED_MARKS = ["research.board_limit", "import board_limit", "from board_limit"]
CANONICAL_TUPLES = ['(“688”, “300”, “301”)', '("688", "300", "301")', "('688', '300', '301')",
                     '(“8”, “4”, “9”)', '("8", "4", "9")', "('8', '4', '9')"]
LIMIT_SIGNALS = ["0.30", "0.20", "limit_pct", "lim_of", "board_limit",
                 'startswith("688"', "startswith('688'", 'startswith("30"',
                 'startswith("8"', "startswith('8'", "== 0.30", "== 0.20"]

LIMIT_VALS = {0.05, 0.10, 0.20, 0.30}
# 带市场前缀的代码字面量（如 "bj430047"/"sh688001"/"sz30"）——委托纯代码 canonical 会行为回退。
# 必须要求"前缀后紧跟数字"，否则 'shift' / 'shape' / 'short' 会被误伤（v3 初版 70/72 误判的教训）。
MARKET_PREFIX = re.compile(r"['\"](?:bj|sh|sz|BJ|SH|SZ)[0-9]")
STARTWITH_OR_SLICE = re.compile(r'startswith|\[:3\]|\[:2\]')
PAT_START688 = re.compile(r'startswith\(\s*[\("\']+688')
PAT_ZT = re.compile(r'round\(\s*prev\s*\*\s*\(?\s*1\s*\+\s*lim')


def _const_limit(node):
    """若 AST 节点是幅度常量(0.05/0.10/0.20/0.30)则返回 float，否则 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        v = float(node.value)
        return v if v in LIMIT_VALS else None
    return None


def _collect_limit_constants(node):
    """收集函数体内 return/IfExp 里出现的幅度常量集合。"""
    vals = set()
    for s in ast.walk(node):
        if isinstance(s, ast.Return) and s.value is not None:
            v = _const_limit(s.value)
            if v is not None:
                vals.add(v)
            elif isinstance(s.value, ast.IfExp):
                for sub in (s.value.body, s.value.orelse):
                    vv = _const_limit(sub)
                    if vv is not None:
                        vals.add(vv)
    return vals


def _detect_params(node):
    """识别幅度函数里哪个形参是 code、哪个是 name(ST 判定用)。

    依据：`.startswith` 的接收者 → code；`"ST" in X` 的 X → name。
    兼容 str(code) / c=str(code) 等间接写法（回退到首参）。
    """
    args = [a.arg for a in node.args.args]
    code_param = args[0] if args else "code"
    name_param = None
    for s in ast.walk(node):
        if isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute) \
                and s.func.attr == "startswith":
            recv = s.func.value
            cand = None
            if isinstance(recv, ast.Name) and recv.id in args:
                cand = recv.id
            elif isinstance(recv, ast.Call) and recv.args and isinstance(recv.args[0], ast.Name) \
                    and recv.args[0].id in args:
                cand = recv.args[0].id
            if cand:
                code_param = cand
                break
    for s in ast.walk(node):
        if isinstance(s, ast.Compare) and isinstance(s.left, ast.Constant) \
                and isinstance(s.left.value, str) and "ST" in s.left.value.upper():
            for c in s.comparators:
                if isinstance(c, ast.Name) and c.id in args and c.id != code_param:
                    name_param = c.id
    if name_param is None:
        for a in args:
            if a != code_param:
                name_param = a
                break
    return code_param, name_param


def _is_limit_func(node, txt):
    """该函数是否返回涨跌停幅度常量且用前缀判定（不含 ST 特例判定）。"""
    seg = ast.get_source_segment(txt, node) or ""
    if not STARTWITH_OR_SLICE.search(seg):
        return False
    vals = _collect_limit_constants(node)
    return len(vals) >= 2 or (vals and 0.05 in vals)


def ast_find_limit_funcs(txt, include_prefixed=False):
    """AST 级识别真实幅度函数（供迁移工具复用的唯一判据）。

    返回 [(node, code_param, name_param), ...]。
    默认排除带市场前缀(bj/sh/sz+数字)的函数——它们输入格式不同，委托纯代码
    canonical 会行为回退；include_prefixed=True 时只返回这类（供审计分类用）。
    """
    try:
        tree = ast.parse(txt)
    except Exception:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not _is_limit_func(node, txt):
            continue
        seg = ast.get_source_segment(txt, node) or ""
        prefixed = bool(MARKET_PREFIX.search(seg))
        if prefixed != include_prefixed:
            continue
        cp, np_ = _detect_params(node)
        out.append((node, cp, np_))
    return out


def _has_inline_limit_assign(txt):
    """识别内联幅度赋值(lim/lp/limit = 0.20 if ... else 0.10 之类)。

    两处都要扫：①函数体内；②模块顶层（如 _dryrun_firsttrain.py 在 for 循环里
    `lim = 0.20 if is_20 else 0.10`，不在任何 FunctionDef 内——v3 初版只扫函数体
    导致它被误分为 market_classifier，漏掉一个训练标签污染缺陷）。
    """
    try:
        tree = ast.parse(txt)
    except Exception:
        return False
    names = {"lim", "lp", "limit", "limit_pct", "limit_ratio", "board_limit", "zt_lim"}

    def _assign_is_limit(s):
        if not isinstance(s, ast.Assign):
            return False
        tgt = [t.id for t in s.targets if isinstance(t, ast.Name)]
        if not any(n in names for n in tgt):
            return False
        # 配置/元数据读取的默认值（meta.get("lim", 0.1)）不是内联板块幅度逻辑，
        # 它由上游数据决定，不能据此判定该文件复制了涨跌停幅度规则。
        if isinstance(s.value, ast.Call) and isinstance(s.value.func, ast.Attribute) \
                and s.value.func.attr in ("get", "setdefault"):
            return False
        return any(_const_limit(x) is not None for x in ast.walk(s.value))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.Module)):
            continue
        seg = ast.get_source_segment(txt, node) or "" if not isinstance(node, ast.Module) else txt
        if not STARTWITH_OR_SLICE.search(seg):
            continue
        for s in ast.walk(node):
            if _assign_is_limit(s):
                return True
    return False


def ast_classify(txt):
    """对 suspicious 文件做 AST 级分类，返回 (tags, strategy)。"""
    funcs = ast_find_limit_funcs(txt)                       # 纯代码幅度函数
    prefixed_funcs = ast_find_limit_funcs(txt, include_prefixed=True)  # 带市场前缀幅度函数
    has_688 = bool(PAT_START688.search(txt))
    has_zt = bool(PAT_ZT.search(txt))

    if funcs:
        return ["genuine_limit"], "replace_limit_func_with_board_limit_pct"
    if prefixed_funcs:
        return ["prefixed_input"], "exclude_prefixed_input"  # 确实存在带前缀的幅度函数, 禁止迁移
    if _has_inline_limit_assign(txt):
        return ["inline_limit"], "inline_limit_manual_review"  # 内联赋值, 单独精确处理
    if has_zt:
        return ["zt_expr"], "replace_zt_expr_with_is_limit_up"
    if has_688:
        return ["market_classifier"], "exclude_market_classifier"
    return ["false_positive"], "exclude_false_positive"       # 权重/阈值/仓位/随机场景


def _imports_canonical(txt):
    """以 AST 判断是否有真实 import，避免工具自身源码里的字符串常量造成自匹配。"""
    try:
        tree = ast.parse(txt)
    except Exception:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("research.board_limit", "board_limit"):
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("research.board_limit", "board_limit"):
                    return True
    return False


def scan_file(path):
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return None
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if rel == "research/board_limit.py":
        # 单一真相源本身不是副本：绝不能进入迁移目标（否则会被替换成自调用递归）
        return "centralized"
    if _imports_canonical(txt):
        return "centralized"
    # v4：旧版在此把「含旧字面元组」的文件直接判为 canonical(已正确)，等于用旧口径
    # 给自己发合格证。canonical 边界改动后这类文件会静默漂移；现统一落 suspicious，
    # 由 ast_classify 独立判别是真幅度函数、分类器还是误命中。
    if any(s in txt for s in LIMIT_SIGNALS):
        return "suspicious"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default=os.path.join(HERE, "limit_duplication_audit.json"))
    args = ap.parse_args()

    buckets = {"centralized": [], "canonical": [], "suspicious": []}
    total_py = 0
    skip_dirs = {".venv", "__pycache__", ".git", "node_modules", "backtest_cache"}
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            total_py += 1
            b = scan_file(os.path.join(root, fn))
            if b:
                buckets[b].append(os.path.relpath(os.path.join(root, fn), ROOT).replace(os.sep, "/"))
    for k in buckets:
        buckets[k].sort()

    migration_targets, weak_signals = [], []
    for rel in buckets["suspicious"]:
        try:
            txt = open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        tags, strat = ast_classify(txt)
        migration_targets.append({"path": rel, "tags": tags, "strategy": strat})
        if strat in ("exclude_false_positive",):
            weak_signals.append(rel)
    migration_targets.sort(key=lambda x: x["path"])
    weak_signals.sort()

    strat_counts = {}
    for m in migration_targets:
        strat_counts[m["strategy"]] = strat_counts.get(m["strategy"], 0) + 1
    genuine = strat_counts.get("replace_limit_func_with_board_limit_pct", 0)

    print("=== 涨跌停口径重复逻辑审计（AST 精确版）===")
    print("扫描 .py 文件总数: %d" % total_py)
    print("  已中央集权(import board_limit): %d" % len(buckets["centralized"]))
    print("  内联正确(规范元组):            %d" % len(buckets["canonical"]))
    print("  待审查(含 limit 信号, 非上述):  %d" % len(buckets["suspicious"]))
    print("    真实幅度函数(genuine_limit, 可带测试迁移): %d" % genuine)
    for s, c in sorted(strat_counts.items(), key=lambda x: -x[1]):
        print("      %-42s %d" % (s, c))
    print("  → 真正可带测试自动迁移的 = %d 文件" % genuine)
    print("")
    print("--- 迁移策略分布 ---")
    for s, c in sorted(strat_counts.items(), key=lambda x: -x[1]):
        print("  %-45s %d" % (s, c))

    out = {"total_py": total_py, "buckets": buckets,
           "migration_targets": migration_targets,
           "weak_signals": weak_signals,
           "strategy_counts": strat_counts,
           "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nJSON 已写: %s" % args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
