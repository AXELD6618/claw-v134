# -*- coding: utf-8 -*-
"""回归锁（2026-11-06 二次升级）：生产关键文件必须真正委托涨跌停幅度单一真相源。

旧版缺陷（已修正）：只检查 6 个文件里是否出现字面元组 ("688","300","301") / ("8","4","9")，
等于把生产链路锁死在内联副本上——canonical 一旦修正边界（689 CDR、900 B 股），
生产文件反而留在旧规则。这属于"伪单一真相源"。

现在改为生产者不得自证的独立验收：
  1. 每个文件必须 import research.board_limit，且不得再出现旧内联幅度元组；
  2. 从磁盘读源码、AST 抽取目标函数、注入 canonical 后隔离 exec；
  3. 用真实板块向量（含 689/900/920）逐一实跑该函数，与 canonical 比对数值；
  4. gen_lianban_watchlist 额外锁定涨停判定走 is_limit_up。

运行：.venv/Scripts/python.exe research/test_board_limit.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from research import board_limit as bl  # 真值

# 生产关键文件 → 该文件对外提供幅度函数的名字
PROD_FUNCS = {
    "unified_tail_pick.py": "_lim",
    "research/evolution_reweight.py": "_limit_pct",
    "gen_lianban_watchlist.py": "lim_of",
    "evening_review.py": "_limit_pct",
    "backtest_strategy.py": "board_limit",
    "evolution_review.py": "limit_of",
}
# 板块向量：北交所 / 科创板(含CDR) / 创业板 / 主板 / 沪市B股
VECTORS = ["920491", "921000", "830799", "430047", "688001", "689009",
           "300750", "301269", "600519", "000001", "002594", "900901"]
# 旧内联幅度元组，出现即视为回退
LEGACY_TUPLES = ['("688", "300", "301")', '("8", "4", "9")']


def _find_func_src(path, func_name):
    txt = open(path, encoding="utf-8").read()
    tree = ast.parse(txt)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return txt, ast.get_source_segment(txt, node)
    return txt, None


def test_canonical():
    bl.selftest()  # 真值函数矩阵（含 ST / CDR / B 股断言）
    problems = []
    checked = 0
    for rel, fname in PROD_FUNCS.items():
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            problems.append("%s 文件缺失" % rel)
            continue
        txt, seg = _find_func_src(path, fname)
        if seg is None:
            problems.append("%s 未找到函数 %s" % (rel, fname))
            continue
        if "from research.board_limit import" not in txt:
            problems.append("%s 未委托单一真相源(缺 import)" % rel)
        for legacy in LEGACY_TUPLES:
            if legacy in txt:
                problems.append("%s 仍内联旧幅度元组 %s" % (rel, legacy))
        if rel == "gen_lianban_watchlist.py" and "if is_limit_up(" not in txt:
            problems.append("%s 涨停判定未走 is_limit_up" % rel)
        # 独立验收：不看注释/字符串，实际执行该函数比数值
        ns = {"board_limit_pct": bl.board_limit_pct, "is_limit_up": bl.is_limit_up}
        try:
            exec(seg, ns)
            fn = ns.get(fname)
            assert callable(fn), "函数不可调用"
            for code in VECTORS:
                got, exp = fn(code), bl.board_limit_pct(code)
                if got != exp:
                    problems.append("%s.%s(%s)=%s != canonical %s" % (rel, fname, code, got, exp))
                checked += 1
        except Exception as e:
            problems.append("%s 独立 exec 失败: %r" % (rel, e))
    assert not problems, "生产关键文件口径验收失败:\n  " + "\n  ".join(problems)
    print("生产关键文件口径一致(%d 个 / %d 个向量实跑): %s"
          % (len(PROD_FUNCS), checked, list(PROD_FUNCS)))
    return True


if __name__ == "__main__":
    test_canonical()
