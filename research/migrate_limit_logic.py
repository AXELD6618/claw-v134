# -*- coding: utf-8 -*-
"""
migrate_limit_logic.py — 涨跌停幅度逻辑中央集权化迁移工具（带测试护栏, 禁盲改）

设计目标（对齐 HOST_PLAYBOOK / 用户授权"带测试逐一审, 禁盲改"）：
  把 audit_limit_duplication.py 标记的 genuine_limit 文件里的"涨跌停幅度重复逻辑"
  收口为单一真相源 research.board_limit.board_limit_pct，且每一步都有回归护栏。

四类状态（2026-11-06 归因升级，全部有【可验证不变量】，非人工判断）：
  AUTO_SAFE    : 旧函数 == 新函数（纯重构，行为不变）
  SAFE_FIX     : 函数体无 ST 分支；old!=new 的差异【全部】落在板块纠错集合
                 (北交所920/921/830/430、双创688/300/301：旧误判0.10→正确0.30/0.20)；
                 且 new==canonical。行为变更方向=修正错误口径，无回退面。
  ST_AWARE_FIX : 函数体含 ST 前置分支；替换时【保留 ST 分支原文】，仅把板块部分委托 canonical；
                 验证向量含 ST 场景，严格断言 new==old（ST 路径不回退）。
  MANUAL       : 无法隔离 exec / 差异非板块纠错 / ST 分支无法保留 → 不自动改。

安全模型：
  - 只处理 audit 标记的 genuine_limit；market_classifier / weak / zt_expr 一律不自动改。
  - 替换方式：AST 定位"含幅度分支的函数"，保留原函数签名；ST_AWARE 保留其 ST 判定语句原文。
  - 行为护栏：隔离命名空间 exec 旧/新函数（不 import 整文件，避免顶层副作用），
    板块向量断言 new==canonical；ST 向量断言 new==old。
  - 语法护栏：py_compile 失败即回滚（--apply 模式还原 .bak）。
  - --apply 默认只写 有护栏的三类(AUTO_SAFE/SAFE_FIX/ST_AWARE_FIX)；MANUAL 永远跳过。
    --only-auto-safe 可进一步收紧为仅 AUTO_SAFE。

用法：
  python research/migrate_limit_logic.py --mode plan
  python research/migrate_limit_logic.py --mode dry-apply [--batch 0] [--out-dir /tmp/mig]
  python research/migrate_limit_logic.py --mode apply    [--batch 0] [--only-auto-safe]
"""
import os
import sys
import ast
import re
import json
import shutil
import argparse
import py_compile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 板块向量：北交所(920/921/83/43) / 双创(688/689CDR/300/301) / 主板 / 中小板 / 沪B(900)
# 2026-11-06 补充 689009 与 900901：canonical 边界修正后，旧副本在这两个代码上
# 的系统性差异必须被向量捕获，否则迁移工具会把「行为纠错」误判为「行为不变」。
TEST_CODES = ["920491", "921000", "830799", "430047",
              "688001", "300750", "301269", "689009", "900901",
              "600519", "000001", "002594"]
# 已知"错误口径→正确口径"的板块纠错代码集合（old!=new 只允许落在这些代码上）
BOARD_FIX_CODES = {"920491", "921000", "830799", "430047",
                   "688001", "300750", "301269", "689009", "900901"}
# ST 场景验证向量：(代码, 含ST的name)。仅对函数体含 ST 分支的文件启用。
ST_VECTORS = [("600519", "ST 某某"), ("000001", "*ST 测试"), ("920491", "ST北交")]
# 函数体内"ST 前置分支"识别
ST_IN_BODY = re.compile(r"0\.05|\bST\b|is_st|\*ST")

sys.path.insert(0, ROOT)
from research.board_limit import board_limit_pct  # 行为护栏的参照真值
from research import audit_limit_duplication as AUD  # 单一判别真相源(AST 级)

# 有护栏、可自动写入的状态
AUTO_WRITABLE = {"AUTO_SAFE", "SAFE_FIX", "ST_AWARE_FIX"}


def read_text(path):
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return None


def find_limit_func(txt):
    """用审计器的 AST 判据定位幅度函数，返回 (node, code_param, name_param, args)。

    关键：不再假设首参是 code。`lp(n, c)` 这类参数顺序颠倒的函数，由
    AUD._detect_params 依据 `.startswith` 接收者识别真正的 code 参数。
    带市场前缀(bj/sh/sz+数字)的函数已被审计器排除，这里再拒一次做纵深防御。
    """
    found = AUD.ast_find_limit_funcs(txt)
    if not found:
        return None
    node, code_param, name_param = found[0]
    seg = ast.get_source_segment(txt, node) or ""
    if AUD.MARKET_PREFIX.search(seg):
        return None  # 纵深防御：前缀输入不迁移
    args = [a.arg for a in node.args.args]
    return node, code_param, name_param, args


def find_limit_func_node(txt):
    """兼容旧调用：仅返回 FunctionDef 节点。"""
    r = find_limit_func(txt)
    return r[0] if r else None


def st_if_stmt_lines(txt, node):
    """抽取函数体内 ST 判定 If 语句的原始行(含缩进)，用于 ST_AWARE 保留分支。"""
    out = []
    try:
        lines = txt.splitlines(keepends=True)
    except Exception:
        return out
    for stmt in node.body:
        if isinstance(stmt, ast.If):
            seg = ast.get_source_segment(txt, stmt) or ""
            if ST_IN_BODY.search(seg):
                for ln in range(stmt.lineno, stmt.end_lineno + 1):
                    out.append(lines[ln - 1])
    return out


def inject_import(txt):
    """在最后一个顶层 import 行之后注入 `from research.board_limit import ...`；
    若已存在则原样返回。"""
    if "research.board_limit" in txt:
        return txt, False
    try:
        tree = ast.parse(txt)
    except Exception:
        return txt, False
    last_imp = -1
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_imp = node.end_lineno
    lines = txt.splitlines(keepends=True)
    inject_line = "from research.board_limit import board_limit_pct, is_limit_up\n"
    if last_imp is not None and last_imp <= len(lines):
        out = lines[:last_imp] + [inject_line] + lines[last_imp:]
    else:
        if lines and lines[0].startswith("#!"):
            out = [lines[0], inject_line] + lines[1:]
        else:
            out = [inject_line] + lines
    return "".join(out), True


def _invoke(func, code, name, args, code_param, name_param):
    """按【参数语义】构造调用，而非按位置假设首参是 code。

    lp(n, c) 这类函数：code 应放在 c 的位置、name 放在 n 的位置；
    若盲用首参当 code，会把 name 传进 board 判定，静默算错。
    """
    if not args:
        return func()
    call = {}
    for a in args:
        call[a] = ""                      # 默认空 name
    call[code_param] = code
    if name_param and name_param in call:
        call[name_param] = name
    elif len(args) >= 2:
        # 第二个形参不是显式 name_param 时，也给它 name（兼容 ST 判定）
        other = [a for a in args if a != code_param]
        if other:
            call[other[0]] = name
    return func(**call)


def build_patch(source_path):
    """对单个 genuine_limit 文件生成补丁。返回 (new_text, info_dict)。
    info.status ∈ {AUTO_SAFE, SAFE_FIX, ST_AWARE_FIX, MANUAL, FAIL}。"""
    txt = read_text(source_path)
    if txt is None:
        return None, {"status": "FAIL", "reason": "read_error"}
    found = find_limit_func(txt)
    if found is None:
        return None, {"status": "MANUAL", "reason": "no_limit_func_found"}
    node, code_param, name_param, args = found
    seg = ast.get_source_segment(txt, node)
    if not seg:
        return None, {"status": "MANUAL", "reason": "source_segment_empty"}

    # 构造新函数：保留 def 签名行 + (ST_AWARE 时)保留 ST 分支原文
    #             + 委托 board_limit_pct(<真实 code 参数>)
    def_line = seg.splitlines()[0].rstrip()
    st_lines = st_if_stmt_lines(txt, node)
    has_st = bool(st_lines)
    body_parts = [def_line]
    if has_st:
        body_parts.append("".join(st_lines).rstrip("\n"))
    body_parts.append("    return board_limit_pct(%s)" % code_param)
    new_func = "\n".join(body_parts) + "\n"

    # 行为护栏：隔离 exec 旧/新函数（不 import 整文件，规避顶层副作用）
    ns = {"board_limit_pct": board_limit_pct, "is_limit_up": None}
    ns2 = {"board_limit_pct": board_limit_pct, "is_limit_up": None}
    try:
        exec(seg, ns)
        old_func = ns.get(node.name)
        exec(new_func, ns2)
        new_func_obj = ns2.get(node.name)
        if not (callable(old_func) and callable(new_func_obj)):
            return None, {"status": "MANUAL", "reason": "func_not_callable"}
    except Exception as e:
        return None, {"status": "MANUAL", "reason": "exec_error", "behavior": {"err": repr(e)}}

    inv = lambda f, c, nm: _invoke(f, c, nm, args, code_param, name_param)

    # 板块向量：new 必须 == canonical
    old_vals, new_vals = {}, {}
    try:
        for c in TEST_CODES:
            old_vals[c] = inv(old_func, c, "")
            new_vals[c] = inv(new_func_obj, c, "")
    except Exception as e:
        return None, {"status": "MANUAL", "reason": "old_func_exec_error",
                      "behavior": {"err": repr(e)}}
    canon = {c: board_limit_pct(c) for c in TEST_CODES}
    new_equals_canon = all(new_vals.get(c) == canon.get(c) for c in TEST_CODES)
    if not new_equals_canon:
        return None, {"status": "MANUAL", "reason": "new_not_canonical",
                      "behavior": {"new": new_vals, "canon": canon}}

    diff_codes = [c for c in TEST_CODES if old_vals.get(c) != new_vals.get(c)]
    all_board_fix = all(c in BOARD_FIX_CODES for c in diff_codes)
    if diff_codes and not all_board_fix:
        return None, {"status": "MANUAL", "reason": "non_board_diff",
                      "behavior": {"diff_codes": diff_codes}}

    # ST 向量护栏：ST 路径必须严格 new==old（保留 ST 判定，不回退）
    st_preserved = None
    if has_st:
        st_preserved = True
        for c, nm in ST_VECTORS:
            try:
                o = inv(old_func, c, nm)
                n = inv(new_func_obj, c, nm)
            except Exception:
                st_preserved = False
                break
            if o != n:
                st_preserved = False
                break
        if not st_preserved:
            return None, {"status": "MANUAL", "reason": "st_branch_not_preserved"}

    # 状态判定
    if has_st:
        status = "ST_AWARE_FIX"
    elif not diff_codes:
        status = "AUTO_SAFE"
    elif all_board_fix:
        status = "SAFE_FIX"
    else:
        return None, {"status": "MANUAL", "reason": "unclassified"}

    new_txt, _ = inject_import(txt)
    new_txt = new_txt.replace(seg, new_func, 1)
    info = {"status": status, "func_name": node.name, "code_param": code_param,
            "name_param": name_param, "args": args,
            "has_st": has_st, "st_preserved": st_preserved,
            "diff_codes": diff_codes,
            "old_vals": old_vals, "new_vals": new_vals, "canon": canon}
    return new_txt, info


def compile_check(text, path_hint):
    """把 text 写到临时文件并 py_compile，返回 (ok, msg)。"""
    d = tempfile.mkdtemp(prefix="migcompile_")
    tmp = os.path.join(d, "check.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        py_compile.compile(tmp, doraise=True)
        return True, "ok"
    except Exception as e:
        return False, repr(e)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def load_targets(audit_json, batch, only_strategy):
    data = json.load(open(audit_json, encoding="utf-8"))
    targets = [m for m in data["migration_targets"]
              if m["strategy"] == "replace_limit_func_with_board_limit_pct"]
    if only_strategy and only_strategy != "replace_limit_func_with_board_limit_pct":
        targets = [m for m in data["migration_targets"] if m["strategy"] == only_strategy]
    targets.sort(key=lambda x: x["path"])
    if batch and batch > 0:
        n = 50
        start = (batch - 1) * n
        targets = targets[start:start + n]
    return targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-json", default=os.path.join(HERE, "limit_duplication_audit.json"))
    ap.add_argument("--mode", choices=["plan", "dry-apply", "apply"], default="plan")
    ap.add_argument("--batch", type=int, default=0, help="0=全部; >0=第N批(每批50)")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "migration_dryrun"))
    ap.add_argument("--backup-suffix", default=".bak")
    ap.add_argument("--report", default=os.path.join(HERE, "limit_migration_report.json"))
    ap.add_argument("--only-auto-safe", action="store_true",
                   help="收紧：--apply 仅写 AUTO_SAFE(行为不变)，不写 SAFE_FIX/ST_AWARE_FIX")
    ap.add_argument("--include-fix-review", action="store_true",
                   help="[兼容旧命令, 现已默认启用] 保留参数避免宿主脚本报错")
    a = ap.parse_args()

    writable = {"AUTO_SAFE"} if a.only_auto_safe else set(AUTO_WRITABLE)
    targets = load_targets(a.audit_json, a.batch, None)
    print("[migrate] mode=%s targets=%d writable=%s" % (a.mode, len(targets), sorted(writable)))

    if a.mode == "plan":
        print("计划：以下 %d 个 genuine_limit 文件将逐个生成补丁 + 行为护栏验证" % len(targets))
        for m in targets:
            print("  %s" % m["path"])
        return 0

    os.makedirs(a.out_dir, exist_ok=True)
    report = {"mode": a.mode, "batch": a.batch, "writable": sorted(writable), "results": []}
    counts = {"AUTO_SAFE": 0, "SAFE_FIX": 0, "ST_AWARE_FIX": 0,
              "MANUAL": 0, "FAIL": 0, "ROLLBACK": 0}

    for m in targets:
        rel = m["path"]
        src = os.path.join(ROOT, rel)
        new_txt, info = build_patch(src)
        status = info.get("status", "FAIL")
        rec = {"path": rel, "status": status, "reason": info.get("reason", ""),
               "func_name": info.get("func_name"), "has_st": info.get("has_st"),
               "diff_codes": info.get("diff_codes")}

        # 安全闸门：只有"有护栏的可写状态"才在 --apply 下写源；其余一律跳过/不碰。
        # 2026-11-06 报告诚实性修正：旧版把非可写状态【包括 FAIL】一律覆写成 MANUAL，
        # 并抹掉 build_patch 给出的真实 reason（exec_error / non_board_diff / ...），
        # 等于报告自证合格、掩盖真实失败。现在只标注未写入，状态与原因保持原样。
        if a.mode == "apply" and status not in writable:
            rec["gate"] = "not_written"

        if status in writable:
            ok, msg = compile_check(new_txt, rel)
            if not ok:
                rec["status"] = "FAIL"
                rec["reason"] = "compile_error:" + msg
            elif a.mode == "apply":
                bak = src + a.backup_suffix
                if not os.path.exists(bak):
                    shutil.copy2(src, bak)
                with open(src, "w", encoding="utf-8") as f:
                    f.write(new_txt)
                try:
                    py_compile.compile(src, doraise=True)
                except Exception as e:
                    shutil.copy2(bak, src)  # 回滚
                    rec["status"] = "ROLLBACK"
                    rec["reason"] = "compile_error_after_write:" + repr(e)
            else:  # dry-apply: 写临时副本
                d = os.path.join(a.out_dir, os.path.dirname(rel))
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(a.out_dir, rel), "w", encoding="utf-8") as f:
                    f.write(new_txt)
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        report["results"].append(rec)
        print("  %-12s %s" % (rec["status"], rel))

    written = counts["AUTO_SAFE"] + counts["SAFE_FIX"] + counts["ST_AWARE_FIX"]
    report["summary"] = {"written": written, "counts": counts}
    with open(a.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n=== 迁移汇总 (mode=%s) ===" % a.mode)
    print("  有护栏可写状态命中: AUTO_SAFE=%d SAFE_FIX=%d ST_AWARE_FIX=%d"
          % (counts["AUTO_SAFE"], counts["SAFE_FIX"], counts["ST_AWARE_FIX"]))
    print("  MANUAL=%d  FAIL=%d  ROLLBACK=%d" % (counts["MANUAL"], counts["FAIL"], counts["ROLLBACK"]))
    print("  报告: %s" % a.report)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
