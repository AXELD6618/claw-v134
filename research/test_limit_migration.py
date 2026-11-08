# -*- coding: utf-8 -*-
"""
test_limit_migration.py — 迁移工具【独立验收方】（评测独立于生产, 生产者不得自证）

严格性：本测试不采信 migrate_limit_logic.build_patch 声称的 status 是否正确，
而是自己从【原始文件】和【补丁文本】里抽取函数、独立 exec、独立计算向量数值来复核：

  1) test_patch_matches_canonical : 每个"补丁"的新函数在全部板块向量上 == board_limit_pct
                                    （证明真正收口到单一真相源，而非形状上的 import）
  2) test_st_branches_preserved   : 每个含 ST 分支的补丁，ST 场景 new==old==0.05
                                    （证明 ST 路径不回退，保留原判定语义）
  3) test_no_unexpected_change    : old!=new 的差异代码必须全部落在板块纠错白名单
                                    （证明没有"顺手改了别的行为"）
  4) test_all_patches_compile     : 所有补丁文本 py_compile 通过, 且无 FAIL
  5) test_audit_counts_sane       : 数字契约 genuine_limit << 322（审计精确化未回退）

setUpClass 刷新审计 JSON 一次（重跑 audit_limit_duplication.main），反映当前已迁移状态。

用法：.venv/Scripts/python.exe research/test_limit_migration.py
"""
import os
import sys
import io
import ast
import json
import contextlib
import unittest
import py_compile
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import research.migrate_limit_logic as M
import research.audit_limit_duplication as AUD
from research.board_limit import board_limit_pct


def _func_seg(text, name):
    """从文本里按函数名抽取 FunctionDef 源码段（独立验收用，不用生产定位器）。"""
    tree = ast.parse(text)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n, ast.get_source_segment(text, n)
    return None, None


def _invoke(func, code, name, args, code_param, name_param):
    """独立验收方按参数语义构造调用（与生产工具同语义，但由验收方自己组装）。"""
    call = {a: "" for a in args}
    call[code_param] = code
    if name_param and name_param in call:
        call[name_param] = name
    elif len(args) >= 2:
        other = [a for a in args if a != code_param]
        if other:
            call[other[0]] = name
    return func(**call)


class TestLimitMigration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with contextlib.redirect_stdout(io.StringIO()):
            AUD.main()  # 刷新审计 JSON 一次
        cls.audit = json.load(open(os.path.join(HERE, "limit_duplication_audit.json"),
                                   encoding="utf-8"))
        cls.targets = [m["path"] for m in cls.audit["migration_targets"]
                       if m["strategy"] == "replace_limit_func_with_board_limit_pct"]
        # 预生成所有补丁，供各测试用例共享
        cls.patches = {}
        for rel in cls.targets:
            new_txt, info = M.build_patch(os.path.join(ROOT, rel))
            cls.patches[rel] = (new_txt, info)

    def _old_and_new(self, rel):
        """独立抽取旧(源文件)/新(补丁)函数段。"""
        txt = open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()
        new_txt, info = self.patches[rel]
        fname = info.get("func_name")
        if not fname:
            return None
        old_node, old_seg = _func_seg(txt, fname)
        new_node, new_seg = _func_seg(new_txt, fname)
        if not old_seg or not new_seg:
            return None
        args = [a.arg for a in old_node.args.args]
        cp = info.get("code_param", args[0] if args else "code")
        np_ = info.get("name_param")
        ns = {"board_limit_pct": board_limit_pct, "is_limit_up": None}
        ns2 = {"board_limit_pct": board_limit_pct, "is_limit_up": None}
        exec(old_seg, ns)
        exec(new_seg, ns2)
        # 返回 (旧函数, 新函数, args, code_param, name_param, 新函数源码)
        return ns[fname], ns2[fname], args, cp, np_, new_seg

    def _compile(self, text):
        d = tempfile.mkdtemp(prefix="migtest_")
        try:
            p = os.path.join(d, "c.py")
            open(p, "w", encoding="utf-8").write(text)
            py_compile.compile(p, doraise=True)
            return True
        except Exception:
            return False
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _assert_converged_if_no_patches(self, checked, label):
        """某验收类别没有【适用补丁】时不判失败。

        2026-11-06 状态机修正：旧版在 checked=0 时一律要求"所有补丁都非可写"，
        但 test_st_branches_preserved 只适用于 ST_AWARE_FIX —— 当本批只有 SAFE_FIX
        时 checked=0 完全正常，旧版却误判为"状态机异常"并否决整批。
        现在区分三种情况：
          ① 本类别 checked>0        → 已逐向量复核，直接通过；
          ② 存在其他类别可写补丁    → 本类别不适用，非收敛态，通过；
          ③ 全部非可写              → 迁移已收敛干净，通过。
        唯一仍判失败的是出现 FAIL（真实生产失败，绝不掩盖）。
        """
        if checked > 0:
            return
        statuses = [info.get("status") for _, info in self.patches.values()]
        self.assertNotIn("FAIL", statuses, "%s: 存在 FAIL 状态，验收否决" % label)
        writable_n = sum(1 for s in statuses if s in M.AUTO_WRITABLE)
        if writable_n:
            print("[gate] %s: 本类别 0 补丁（另有 %d 个其他类别可写补丁，非收敛态）"
                  % (label, writable_n))
        else:
            print("[gate] %s: 0 patch —— 已收敛干净(剩余 %d 全 MANUAL), 验收通过"
                  % (label, len(self.patches)))

    def test_patch_matches_canonical(self):
        """补丁新函数在全部板块向量上必须 == board_limit_pct（独立复核, 不采信 status）。"""
        checked = 0
        for rel, (new_txt, info) in self.patches.items():
            if info.get("status") not in M.AUTO_WRITABLE:
                continue
            got = self._old_and_new(rel)
            self.assertIsNotNone(got, "无法独立抽取函数: %s" % rel)
            _, nf, args, cp, np_, new_seg = got
            # 关键不变量：委托必须用真实 code 参数（防 lp(n,c) 参数颠倒退化）
            self.assertIn("board_limit_pct(%s)" % cp, new_seg,
                          "委托未使用 code_param: %s" % rel)
            for c in M.TEST_CODES:
                self.assertEqual(_invoke(nf, c, "", args, cp, np_), board_limit_pct(c),
                                 "补丁板块向量 != canonical: %s code=%s" % (rel, c))
            checked += 1
        self._assert_converged_if_no_patches(checked, "canonical-match")
        if checked:
            print("[gate] canonical-match checked %d patches" % checked)

    def test_st_branches_preserved(self):
        """含 ST 分支的补丁：ST 场景 new==old==0.05，且新函数源码仍含 ST 判定。"""
        checked = 0
        for rel, (new_txt, info) in self.patches.items():
            if info.get("status") != "ST_AWARE_FIX":
                continue
            got = self._old_and_new(rel)
            self.assertIsNotNone(got)
            of, nf, args, cp, np_, new_seg = got
            # 新函数必须仍保留 ST 判定语句（防止退化成简单替换丢分支）
            self.assertTrue(M.ST_IN_BODY.search(new_seg),
                            "ST_AWARE 补丁丢失 ST 分支: %s" % rel)
            for c, nm in M.ST_VECTORS:
                o = _invoke(of, c, nm, args, cp, np_)
                n = _invoke(nf, c, nm, args, cp, np_)
                # 2026-11-06：ST 期望不再一律 0.05，改以 canonical 规则为准
                # （主板 5% / 双创 20% / 北交所 30%）。旧副本若 ST 语义本身不符，
                # 验收方应否决该补丁、要求人工修文件，而不是放宽验收标准。
                exp_st = board_limit_pct(c, is_st=True)
                self.assertEqual(o, exp_st,
                                 "旧函数 ST 场景与 canonical 不符: %s %s old=%s exp=%s" % (rel, c, o, exp_st))
                self.assertEqual(n, o, "ST 路径回退: %s %s old=%s new=%s" % (rel, c, o, n))
            checked += 1
        self._assert_converged_if_no_patches(checked, "ST-preserved")
        if checked:
            print("[gate] ST-preserved checked %d patches" % checked)

    def test_no_unexpected_change(self):
        """old!=new 的差异代码必须全部属于板块纠错白名单（无意外行为变更）。"""
        for rel, (new_txt, info) in self.patches.items():
            if info.get("status") not in ("SAFE_FIX", "ST_AWARE_FIX", "AUTO_SAFE"):
                continue
            diffs = info.get("diff_codes", [])
            bad = [c for c in diffs if c not in M.BOARD_FIX_CODES]
            self.assertEqual(bad, [], "非板块纠错的意外差异: %s %s" % (rel, bad))

    def test_all_patches_compile(self):
        """所有可写补丁 py_compile 通过；工具不得产生 FAIL。"""
        compiled = 0
        for rel, (new_txt, info) in self.patches.items():
            st = info.get("status")
            self.assertNotEqual(st, "FAIL", "build_patch FAIL: %s %s" % (rel, info.get("reason")))
            if st in M.AUTO_WRITABLE and new_txt:
                self.assertTrue(self._compile(new_txt), "补丁 py_compile 失败: %s" % rel)
                compiled += 1
        print("[gate] compiled %d patches OK" % compiled)

    def test_audit_counts_sane(self):
        """数字契约：genuine_limit 远小于早期误估的 322；允许 0（收敛态）。

        旧版 assertGreater(targets, 0) 把「全部收敛」误判为回归失败；
        收敛态改判：centralized>0 且 suspicious 策略里不再有待迁移幅度函数。
        """
        self.assertLess(len(self.targets), 322)
        if len(self.targets) == 0:
            cent_raw = self.audit["buckets"].get("centralized", [])
            cent = len(cent_raw) if isinstance(cent_raw, (list, dict)) else int(cent_raw)
            self.assertGreater(cent, 0,
                               "genuine=0 但 centralized=0，疑似扫描失败而非真收敛")
            strategies = {m["strategy"] for m in self.audit["migration_targets"]}
            self.assertNotIn("replace_limit_func_with_board_limit_pct", strategies,
                             "genuine=0 但策略清单仍含待迁移项，审计自相矛盾")
            print("[gate] genuine_limit=0 —— 已收敛干净(centralized=%d)" % cent)
        else:
            print("[gate] genuine_limit=%d (<322)" % len(self.targets))


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestLimitMigration)
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)
