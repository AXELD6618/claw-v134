# -*- coding: utf-8 -*-
"""
limitup_caliber_shadow_20261106.py — 涨停判定口径「影子回放」（只读决策备忘录配套脚本）

目的：对生产链路里散落的比例阈值构造「收口后等价替换」的影子实现
（统一走 research/board_limit.py::is_limit_up），回放 backtest_cache/daily/*.csv
全量日线，逐行量化判定分歧实数，供 GO/NO-GO 决策。

覆盖行位：
  unified_tail_pick.py:812 / 830 / 836 / 854 / 855 / 856
  edge_brief.py:126 / 127 / 137 / 141 / 174
  limitup_base.py:161-166（含 167-175 连板 run-length）
  research/grail_cohort_20261102.py:51 / 66

严格只读：不写任何文件、不调用主程序、不重跑 OOS、不改任何生产代码。
输出：stdout 一段 JSON（全部实数，无占位）。

方法：DIVERGE 只可能落在所有原判定阈值与影子阈值的并集窗口内
  [X - max(0.005*prev, 0.011, 0.010, 0.005) - 0.002, X + 0.002]（X = prev*(1+lim)）。
窗口内对 close/high/low 精确调用 is_limit_up；窗口外所有阈值同侧、判定数学等价，
并每 1001 条抽样调用真实 is_limit_up 验证 mismatch=0。跌停侧单独窗口对称处理。

用法：
  E:/WorkBuddy_dot_workbuddy/Claw/.venv/Scripts/python.exe \
      E:/WorkBuddy_dot_workbuddy/Claw/research/limitup_caliber_shadow_20261106.py
"""
from __future__ import annotations
import os
import sys
import glob
import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

REPO = 'E:/WorkBuddy_dot_workbuddy/Claw'
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from research.board_limit import board_limit_pct, is_limit_up

DAILY = os.path.join(REPO, 'backtest_cache', 'daily')
NEAR_ZT_PT = 2.5                      # edge_brief.py:151
COHORT_DATES = ('2026-10-29', '2026-10-30', '2026-11-02')

LINE_IDS = ['U830', 'U836', 'U855', 'U856',
            'E127', 'E137', 'E141',
            'L162_164', 'L165', 'L166', 'G51', 'G66']


def exch_px(prev, lim):
    """收口后涨跌停价：round(prev*(1+lim), 2)，ROUND_HALF_UP 到分。
    lim 取负即跌停价（board_limit.py 仅提供 is_limit_up，无 is_limit_down，
    故跌停影子用同一舍入式对称实现，并在报告中列为接口缺口）。"""
    return float((Decimal(str(prev)) * Decimal(str(1.0 + lim)))
                 .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def blank():
    return {'n': 0, 'div': 0, 'orig_only': 0, 'shadow_only': 0, 'examples': []}


def upd(s, orig, shadow, ctx, cap=3):
    s['n'] += 1
    if orig != shadow:
        s['div'] += 1
        if orig and not shadow:
            s['orig_only'] += 1
        else:
            s['shadow_only'] += 1
        if len(s['examples']) < cap:
            s['examples'].append(dict(ctx, orig=bool(orig), shadow=bool(shadow)))


def runlen(flags):
    out, c = [], 0
    for v in flags:
        c = c + 1 if v else 0
        out.append(c)
    return out


def main():
    fs = sorted(glob.glob(os.path.join(DAILY, '*.csv')))
    S = {k: blank() for k in LINE_IDS}
    N = 0
    exact_calls = 0
    outer = 0
    verify_n = 0
    verify_mis = 0
    px_changed = 0        # :854 / :126 / :174 涨停价数值会变的 (code,date)
    gap_changed = 0       # :174 gap 数值变化
    near_changed = 0      # :174 NEAR_ZT_PT 分类变化
    cntB_dist, cntA_dist = Counter(), Counter()
    cand_B = cand_A = cand_lost = cand_gain = 0
    exec_B = exec_A = exec_changed = 0
    board_len_changed = 0
    cohort = {'n': 0, 'lup30_div': 0, 'ldn30_div': 0, 'lup02_div': 0,
              'cohortA_orig': 0, 'cohortA_shadow': 0, 'cohortA_changed': 0,
              'cohortB_orig': 0, 'cohortB_shadow': 0,
              'relay_cont_orig': 0, 'relay_cont_shadow': 0,
              'relay_broke_orig': 0, 'relay_broke_shadow': 0,
              'examples': []}

    for f in fs:
        code = os.path.splitext(os.path.basename(f))[0]
        lim = board_limit_pct(code)
        rows = []
        try:
            with open(f, encoding='utf-8', errors='ignore') as fh:
                raw = fh.read().splitlines()
        except OSError:
            continue
        for line in raw[1:]:
            p = line.split(',')
            if len(p) < 5:
                continue
            try:
                c = float(p[2])
                h = float(p[3])
                lw = float(p[4])
            except ValueError:
                continue
            if c <= 0:
                continue
            rows.append((p[0].strip(), c, h, lw))
        rows.sort(key=lambda x: x[0])
        if len(rows) < 2:
            continue
        m = {r[0]: r[1] for r in rows}
        zB, zA = [], []

        for i in range(1, len(rows)):
            dt = rows[i][0]
            prev = rows[i - 1][1]
            close, high, low = rows[i][1], rows[i][2], rows[i][3]
            X = prev * (1 + lim)
            Xd = prev * (1 - lim)
            thr = lim - 0.005
            ztB = (close / prev - 1.0) >= thr              # 比例阈值（U830/U836/E127/L162-164）
            ztC = close >= X - 0.005                        # G51 元容差无 round
            atztB = close >= X - 0.011                      # U856 / E141
            yiziB = low >= X - 0.011                        # U855 / E137
            highB = (high / prev - 1.0) >= thr              # L166 炸板 / G66 触板代理
            zhabanB = highB and (not ztB)
            zdB = (close / prev - 1.0) <= -thr              # L165 跌停
            gapB = (X - close) / prev * 100.0               # E174
            nearB = gapB <= NEAR_ZT_PT
            if abs(X * 100.0 - round(X * 100.0)) > 1e-9:
                px_changed += 1

            # 候选窗口：覆盖所有原判定阈值与影子阈值
            maxd = max(0.005 * prev, 0.011, 0.010, 0.005)
            lo_up, hi_up = X - maxd - 0.002, X + 0.002
            cand_up = any(lo_up <= q <= hi_up for q in (close, high, low))
            lo_dn, hi_dn = Xd - 0.002, Xd + 0.005 * prev + 0.012
            cand_dn = lo_dn <= close <= hi_dn

            if cand_up:
                ztA = is_limit_up(code, prev, close)
                yiziA = is_limit_up(code, prev, low)
                highA = is_limit_up(code, prev, high)
                atztA = ztA
                zhabanA = highA and (not ztA)
                xp = exch_px(prev, lim)
                gapA = (xp - close) / prev * 100.0
                nearA = gapA <= NEAR_ZT_PT
                exact_calls += 3
            else:
                cmn = close >= X
                ztA = cmn
                atztA = cmn
                yiziA = low >= X
                highA = high >= X
                zhabanA = highA and (not cmn)
                xp = X
                gapA = gapB
                nearA = nearB
                outer += 1
                if outer % 1001 == 0:
                    verify_n += 1
                    if is_limit_up(code, prev, close) != ztB:
                        verify_mis += 1
                    if is_limit_up(code, prev, high) != highB:
                        verify_mis += 1
            if cand_dn:
                zdA = close <= exch_px(prev, -lim) + 0.005
            else:
                zdA = zdB

            ctx = {'code': code, 'date': dt, 'prev': round(prev, 6),
                   'close': round(close, 6), 'high': round(high, 6), 'low': round(low, 6)}
            upd(S['U830'], ztB, ztA, ctx)
            upd(S['U836'], ztB, ztA, ctx)
            upd(S['E127'], ztB, ztA, ctx)
            upd(S['L162_164'], ztB, ztA, ctx)
            upd(S['G51'], ztC, ztA, ctx)
            upd(S['G66'], highB, highA, ctx)
            upd(S['L165'], zdB, zdA, ctx)
            upd(S['L166'], zhabanB, zhabanA, ctx)
            if ztB:
                # :830 通过后才会执行 :855/:856/:137
                upd(S['U855'], yiziB, yiziA, ctx)
                upd(S['U856'], atztB, atztA, ctx)
                upd(S['E137'], yiziB, yiziA, ctx)
            else:
                # :127 为 False 才会执行 :141（TOUCHED 分支）
                upd(S['E141'], atztB, atztA, ctx)
            if gapB != gapA:
                gap_changed += 1
            if nearB != nearA:
                near_changed += 1
            zB.append(ztB)
            zA.append(ztA)
            N += 1

        cB, cA = runlen(zB), runlen(zA)
        for k in range(len(cB)):
            cntB_dist[cB[k]] += 1
            cntA_dist[cA[k]] += 1
            if cB[k] != cA[k]:
                board_len_changed += 1
            if cB[k] >= 2:
                cand_B += 1
            if cA[k] >= 2:
                cand_A += 1
            if cB[k] >= 2 and cA[k] < 2:
                cand_lost += 1
            if cB[k] < 2 and cA[k] >= 2:
                cand_gain += 1
            if cB[k] >= 3:
                exec_B += 1
            if cA[k] >= 3:
                exec_A += 1
            if (cB[k] >= 3) != (cA[k] >= 3):
                exec_changed += 1

        # research/grail_cohort_20261102.py:51/66 的三日 cohort 影子回放
        if all(d in m for d in COHORT_DATES):
            c29, c30, c02 = m[COHORT_DATES[0]], m[COHORT_DATES[1]], m[COHORT_DATES[2]]
            lup30 = c30 >= c29 * (1 + lim) - 0.005
            ldn30 = c30 <= c29 * (1 - lim) + 0.005
            lup02 = c02 >= c30 * (1 + lim) - 0.005
            norm30 = (not lup30) and (not ldn30)
            rose02 = c02 > c30
            lup30A = is_limit_up(code, c29, c30)
            ldn30A = c30 <= exch_px(c29, -lim) + 0.005
            lup02A = is_limit_up(code, c30, c02)
            norm30A = (not lup30A) and (not ldn30A)
            oA, oB = norm30 and lup02, norm30 and rose02
            oRC, oRB = lup30 and lup02, lup30 and (not lup02)
            sA, sB = norm30A and lup02A, norm30A and rose02
            sRC, sRB = lup30A and lup02A, lup30A and (not lup02A)
            cohort['n'] += 1
            cohort['lup30_div'] += int(lup30 != lup30A)
            cohort['ldn30_div'] += int(ldn30 != ldn30A)
            cohort['lup02_div'] += int(lup02 != lup02A)
            cohort['cohortA_orig'] += int(oA)
            cohort['cohortA_shadow'] += int(sA)
            cohort['cohortA_changed'] += int(oA != sA)
            cohort['cohortB_orig'] += int(oB)
            cohort['cohortB_shadow'] += int(sB)
            cohort['relay_cont_orig'] += int(oRC)
            cohort['relay_cont_shadow'] += int(sRC)
            cohort['relay_broke_orig'] += int(oRB)
            cohort['relay_broke_shadow'] += int(sRB)
            if (lup30 != lup30A or lup02 != lup02A or ldn30 != ldn30A) and len(cohort['examples']) < 3:
                cohort['examples'].append({'code': code, 'c29': c29, 'c30': c30, 'c02': c02,
                                           'lup30': bool(lup30), 'lup30A': bool(lup30A),
                                           'lup02': bool(lup02), 'lup02A': bool(lup02A)})

    lines = dict(S)
    lines['U812'] = {'n': len(fs), 'div': 0,
                     'note': '幅度 _lim(code) 已委托 board_limit_pct（unified_tail_pick.py:36/140-142），零分歧'}
    lines['U854'] = {'n': N, 'div': px_changed,
                     'note': '涨停价 无round(prev*(1+lim)) → round(...,2) 的数值变化数（非 bool 判定）'}
    lines['E126'] = {'n': N, 'div': px_changed,
                     'note': '涨停价 无round → round 的数值变化数（与 U854 同源）'}
    lines['E174'] = {'n': N, 'div': gap_changed,
                     'note': 'near_zt_gap 的 gap 数值变化数；NEAR_ZT_PT=2.5 分类变化另见 price_field.near_changed'}
    lines['L167_175'] = {'n': N, 'div': board_len_changed,
                         'note': '连板 run-length cons 变化数（limitup_base.py:167-175）'}

    out = {
        'sample': {'n_csv': len(fs),
                   'n_bak': len(glob.glob(os.path.join(DAILY, '*.csv.bak'))),
                   'pairs': N},
        'method': {'exact_is_limit_up_calls': exact_calls,
                   'outer_math_equiv': outer,
                   'verify_sample': verify_n,
                   'verify_mismatch': verify_mis,
                   'window': 'lo=X-max(0.005*prev,0.011,0.010,0.005)-0.002, hi=X+0.002'},
        'lines': lines,
        'price_field': {'px_changed': px_changed, 'gap_changed': gap_changed,
                        'near_changed': near_changed},
        'tb': {'board_len_changed': board_len_changed,
               'cntB_dist': {str(k): v for k, v in sorted(cntB_dist.items())},
               'cntA_dist': {str(k): v for k, v in sorted(cntA_dist.items())},
               'cand_ge2_orig': cand_B, 'cand_ge2_shadow': cand_A,
               'cand_lost': cand_lost, 'cand_gain': cand_gain,
               'exec_ge3_orig': exec_B, 'exec_ge3_shadow': exec_A,
               'exec_ge3_changed': exec_changed},
        'cohort': cohort,
    }
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
