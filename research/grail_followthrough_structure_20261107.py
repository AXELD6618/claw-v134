# -*- coding: utf-8 -*-
"""
grail_followthrough_structure_20261107.py — 全市场「续涨结构」只读实证脚本

目的：回答「当前验收标签（追 T+1 涨停）是否与业务目标（后续 >=2 日续涨）结构冲突」。
以 backtest_cache/daily/*.csv 全量前复权日线构造 (code, T) 基准样本，按 T+1 涨幅分档，
量化 P(T+2 逐日收涨)/P(T+3 逐日收涨)/P(续涨>=2日) 与收益均值/中位数，
对「涨停透支假设」做两比例 z 检验 + 比值 CI + bootstrap CI，给板块/时段稳健性切片、
反向检验、ST/边界敏感性、以及 3 个多日路径候选标签的 2x2 列联表。

严格只读：不写任何文件、不调用主程序、不重跑 OOS、不改任何生产代码/产物。
输出：stdout 一段 JSON（全部实数，无占位）。

数据契约：
  backtest_cache/daily/*.csv，列序 日期,开盘,收盘,最高,最低,成交量
  （非 OHLC：index0=日期 index1=开盘 index2=收盘 index3=最高 index4=最低 index5=成交量）
  涨停判定统一 research/board_limit.py::is_limit_up（Decimal ROUND_HALF_UP 到分）。
  基准样本：T 日非涨停可买（is_limit_up(code, prev_close, close_T) == False）
            且按【全市场交易日历】T 之后连续 3 个交易日该股均有行（缺档判 NA）。

方法：DIVERGE 只可能落在涨停价窗口 [X-0.012, X+0.002]（X=prev*(1+lim)），
  窗口内精确调用 is_limit_up，窗口外所有阈值同侧、is_limit_up ≡ (price >= X) 数学等价，
  并每 997 个窗口外样本抽样调用真实 is_limit_up 验证 mismatch=0。

用法：
  E:/WorkBuddy_dot_workbuddy/Claw/.venv/Scripts/python.exe -B \
      E:/WorkBuddy_dot_workbuddy/Claw/research/grail_followthrough_structure_20261107.py
"""
from __future__ import annotations
import os
import sys
import glob
import json
import math
from array import array
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

REPO = 'E:/WorkBuddy_dot_workbuddy/Claw'
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from research.board_limit import board_limit_pct, is_limit_up

DAILY = os.path.join(REPO, 'backtest_cache', 'daily')
# 可选 smoke test：FT_MAX_FILES>0 时只取前 N 个文件（默认 0 = 全量）
MAX_FILES = int(os.environ.get('FT_MAX_FILES', '0') or '0')

# 非个股 / selftest 残留（经只读探查确认）：
#   880548/880948/880964 = 通达信板块指数（千点级点位、254 行、2025-09-23~2026-10-15）
#   920491               = board_limit.py selftest 残留（仅 3 个交易日、价格恒 10.00）
EXCLUDE = {'880548', '880948', '880964', '920491'}

ST_BOUND_LO, ST_BOUND_HI = 0.045, 0.055     # ST 5% 板边界（敏感性用）
ST_MAXRET, ST_MINHITS = 0.06, 3             # 疑似 ST 启发式阈值
BOOT_B, BOOT_SEED = 5000, 20261107
Z95 = 1.959963984540054
WIN_LO, WIN_HI = 0.012, 0.002

try:
    import numpy as np
    HAS_NUMPY = hasattr(np.random, 'default_rng')
except Exception:
    HAS_NUMPY = False

RET_EDGES = [0.0, 0.01, 0.02, 0.03, 0.05, 0.07, 0.095, 0.19]
RET_LABELS = ['<=0', '(0,1%]', '(1%,2%]', '(2%,3%]', '(3%,5%]',
              '(5%,7%]', '(7%,9.5%]', '(9.5%,19%]', '>19%']
BANDS = ['A', 'B', 'C', 'D']
SUBS = ['D_flatdown', 'C1_0to2', 'C2_2to5', 'B_5toZT', 'A_zt']
LIMTAGS = ['lim0.10', 'lim0.20', 'lim0.30']
ERAS = ['pre2024', 'post2024']


def rbucket(r):
    for i, e in enumerate(RET_EDGES):
        if r <= e:
            return RET_LABELS[i]
    return RET_LABELS[-1]


def med(a):
    n = len(a)
    if n == 0:
        return None
    s = sorted(a)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def wilson(k, n, z=Z95):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [c - h, c + h]


def two_prop_z(k1, n1, k2, n2):
    if n1 == 0 or n2 == 0:
        return None, None
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None, None
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2.0))


def ratio_ci(k1, n1, k2, n2):
    p1, p2 = k1 / n1, k2 / n2
    if p1 <= 0 or p2 <= 0:
        return [None, None]
    se = math.sqrt((1 - p1) / (n1 * p1) + (1 - p2) / (n2 * p2))
    base = math.log(p1 / p2)
    return [math.exp(base - Z95 * se), math.exp(base + Z95 * se)]


def diff_ci(k1, n1, k2, n2):
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return [p1 - p2 - Z95 * se, p1 - p2 + Z95 * se]


def boot(k1, n1, k2, n2):
    if not HAS_NUMPY or n1 == 0 or n2 == 0:
        return None

    def f(x):
        v = float(x)
        return v if math.isfinite(v) else None

    p1, p2 = k1 / n1, k2 / n2
    rng = np.random.default_rng(BOOT_SEED)
    b1 = rng.binomial(n1, p1, size=BOOT_B) / n1
    b2 = rng.binomial(n2, p2, size=BOOT_B) / n2
    d = b1 - b2
    with np.errstate(divide='ignore', invalid='ignore'):
        r = b1 / np.where(b2 > 0, b2, np.nan)
    return {
        'B': BOOT_B, 'seed': BOOT_SEED,
        'diff_ci95': [f(np.percentile(d, 2.5)), f(np.percentile(d, 97.5))],
        'ratio_ci95': [f(np.nanpercentile(r, 2.5)), f(np.nanpercentile(r, 97.5))],
    }


def phi4(a, b, c, d):
    den = math.sqrt(float((a + b) * (c + d) * (a + c) * (b + d)))
    return (a * d - b * c) / den if den > 0 else None


def empty():
    return {'n': 0, 'k2': 0, 'k3': 0, 'kft': 0, 'ke': 0,
            's2': 0.0, 's3': 0.0, 's1': 0.0}


def main():
    fs = sorted(glob.glob(os.path.join(DAILY, '*.csv')))
    if MAX_FILES > 0:
        fs = fs[:MAX_FILES]
    all_rows = {}
    master_set = set()
    n_excluded = 0
    n_data_rows = 0
    n_bad = 0

    for f in fs:
        code = os.path.splitext(os.path.basename(f))[0]
        if code in EXCLUDE:
            n_excluded += 1
            continue
        rows = []
        with open(f, encoding='utf-8', errors='ignore') as fh:
            next(fh, None)
            for line in fh:
                p = line.split(',')
                if len(p) < 3:
                    n_bad += 1
                    continue
                try:
                    c = float(p[2])
                except ValueError:
                    n_bad += 1
                    continue
                if c <= 0:
                    n_bad += 1
                    continue
                d = p[0].strip()
                rows.append((d, c))
                master_set.add(d)
                n_data_rows += 1
        if len(rows) >= 2:
            rows.sort(key=lambda x: x[0])
            all_rows[code] = rows

    master = sorted(master_set)
    midx = {d: i for i, d in enumerate(master)}
    years = sorted({d[:4] for d in master})

    # 疑似 ST 启发式（主板 0.10 板）：历史从未出现 >6% 单日涨幅，且 >=3 次落在 5% 板边界。
    st_like = set()
    for code, rows in all_rows.items():
        if board_limit_pct(code) != 0.10:
            continue
        mx, n5 = 0.0, 0
        for i in range(1, len(rows)):
            pv, c = rows[i - 1][1], rows[i][1]
            if pv <= 0:
                continue
            r = c / pv - 1
            if r > mx:
                mx = r
            if ST_BOUND_LO <= r <= ST_BOUND_HI:
                n5 += 1
        if mx <= ST_MAXRET and n5 >= ST_MINHITS:
            st_like.add(code)

    ACC = {}

    def acc(key):
        a = ACC.get(key)
        if a is None:
            a = empty()
            if key and key[0] == 'band':   # 仅主四档存收益数组以算 median（内存约束）
                a['a2'] = array('d')
                a['a3'] = array('d')
                a['a1'] = array('d')
            ACC[key] = a
        return a

    def add(key, up2, up3, ft2, r2, r3, ret1, entry):
        a = acc(key)
        a['n'] += 1
        a['k2'] += int(up2)
        a['k3'] += int(up3)
        a['kft'] += int(ft2)
        a['ke'] += int(entry)
        a['s2'] += r2
        a['s3'] += r3
        a['s1'] += ret1
        if 'a2' in a:
            a['a2'].append(r2)
            a['a3'].append(r3)
            a['a1'].append(ret1)

    LAB = {k: defaultdict(int) for k in ('L1_FT2', 'L2_FT2_anti_zt', 'L3_FT2_mild')}
    examples = {b: [] for b in BANDS}

    N = 0
    n_T_no_prev = len(all_rows)          # 每票首行无 prev，无法判 T 日涨停
    n_T_limitup = 0
    n_no_forward = 0
    n_gap_na = 0
    exact_calls = 0
    outer0 = outer1 = 0
    outer_cnt = 0
    verify_n = 0
    verify_mis = 0
    tdates = set()
    n_stclean = 0
    n_bdclean = 0
    n_boundary = 0

    for code, rows in all_rows.items():
        lim = board_limit_pct(code)
        limtag = 'lim%.2f' % lim
        rmap = {d: c for d, c in rows}
        for i in range(1, len(rows)):
            dt0 = rows[i][0]
            pi = midx.get(dt0)
            if pi is None:
                continue
            if pi + 3 >= len(master):
                n_no_forward += 1
                continue
            m1, m2, m3 = master[pi + 1], master[pi + 2], master[pi + 3]
            c1, c2, c3 = rmap.get(m1), rmap.get(m2), rmap.get(m3)
            if c1 is None or c2 is None or c3 is None:
                n_gap_na += 1
                continue

            prev = rows[i - 1][1]
            c0 = rows[i][1]
            if min(prev, c0, c1, c2, c3) <= 0:
                continue

            X0 = prev * (1 + lim)
            X1 = c0 * (1 + lim)
            cand0 = (X0 - WIN_LO <= c0 <= X0 + WIN_HI)
            cand1 = (X1 - WIN_LO <= c1 <= X1 + WIN_HI)
            if cand0:
                zt_T = is_limit_up(code, prev, c0)
                exact_calls += 1
            else:
                zt_T = (c0 >= X0)
                outer0 += 1
            if cand1:
                zt_1 = is_limit_up(code, c0, c1)
                exact_calls += 1
            else:
                zt_1 = (c1 >= X1)
                outer1 += 1

            if (not cand0) or (not cand1):
                outer_cnt += 1
                if outer_cnt % 997 == 0:
                    verify_n += 1
                    if (not cand0) and is_limit_up(code, prev, c0) != zt_T:
                        verify_mis += 1
                    if (not cand1) and is_limit_up(code, c0, c1) != zt_1:
                        verify_mis += 1

            if zt_T:
                n_T_limitup += 1
                continue

            ret1 = c1 / c0 - 1
            if zt_1:
                band, sub = 'A', 'A_zt'
            elif ret1 >= 0.05:
                band, sub = 'B', 'B_5toZT'
            elif ret1 > 0.02:
                band, sub = 'C', 'C2_2to5'
            elif ret1 > 0:
                band, sub = 'C', 'C1_0to2'
            else:
                band, sub = 'D', 'D_flatdown'
            rb = rbucket(ret1)
            era = 'pre2024' if dt0 < '2024-01-01' else 'post2024'
            year = dt0[:4]

            up2 = c2 > c1
            up3 = c3 > c2
            ft2 = up2 and up3
            r2 = c2 / c1 - 1
            r3 = c3 / c2 - 1
            entry = ret1 > 0

            N += 1
            tdates.add(dt0)
            keys = [
                ('all',),
                ('band', band),
                ('sub', sub),
                ('lim_band', limtag, band),
                ('era_band', era, band),
                ('year_band', year, band),
                ('lim_era_band', limtag, era, band),
                ('ret_bucket', rb),
            ]
            for k in keys:
                add(k, up2, up3, ft2, r2, r3, ret1, entry)
            if code not in st_like:
                n_stclean += 1
                add(('stclean_band', band), up2, up3, ft2, r2, r3, ret1, entry)
            if ST_BOUND_LO <= ret1 <= ST_BOUND_HI:
                n_boundary += 1
            else:
                n_bdclean += 1
                add(('bdclean_band', band), up2, up3, ft2, r2, r3, ret1, entry)

            g = bool(zt_1)
            L1 = bool(entry and up2 and up3)
            L2 = bool((not g) and entry and up2 and up3)
            L3 = bool((0 < ret1 < 0.05) and up2 and up3)
            for name, lp in (('L1_FT2', L1), ('L2_FT2_anti_zt', L2), ('L3_FT2_mild', L3)):
                d = LAB[name]
                if lp and g:
                    d['a'] += 1
                elif lp and not g:
                    d['b'] += 1
                elif not lp and g:
                    d['c'] += 1
                else:
                    d['d'] += 1

            if len(examples[band]) < 3:
                examples[band].append({
                    'code': code, 'date_T': dt0, 'lim': lim,
                    'prev': round(prev, 6), 'c0': round(c0, 6), 'c1': round(c1, 6),
                    'c2': round(c2, 6), 'c3': round(c3, 6),
                    'ret1': round(ret1, 6), 'band': band,
                    'up2': bool(up2), 'up3': bool(up3), 'ft2': bool(ft2),
                })

    def summarize(key):
        a = ACC.get(key)
        if a is None or a['n'] == 0:
            return {'n': 0}
        n = a['n']
        return {
            'n': n,
            'k_ft2': a['kft'],
            'p_up2': a['k2'] / n,
            'p_up3': a['k3'] / n,
            'p_ft2': a['kft'] / n,
            'p_entry': a['ke'] / n,
            'mean_r2': a['s2'] / n,
            'median_r2': med(a['a2']) if 'a2' in a else None,
            'mean_r3': a['s3'] / n,
            'median_r3': med(a['a3']) if 'a3' in a else None,
            'mean_ret1': a['s1'] / n,
            'median_ret1': med(a['a1']) if 'a1' in a else None,
        }

    def compare(kA, kC):
        a, c = ACC.get(kA), ACC.get(kC)
        if not a or not c or a['n'] == 0 or c['n'] == 0:
            return None
        k1, n1, k2, n2 = a['kft'], a['n'], c['kft'], c['n']
        p1, p2 = k1 / n1, k2 / n2
        z, pv = two_prop_z(k1, n1, k2, n2)
        return {
            'n_A': n1, 'n_C': n2, 'k_A': k1, 'k_C': k2,
            'p_A': p1, 'p_C': p2,
            'ratio_A_over_C': (p1 / p2) if p2 > 0 else None,
            'diff_A_minus_C': p1 - p2,
            'z': z, 'p_two_sided': pv,
            'wilson_A': wilson(k1, n1), 'wilson_C': wilson(k2, n2),
            'ratio_ci95_deltamethod': ratio_ci(k1, n1, k2, n2),
            'diff_ci95_wald': diff_ci(k1, n1, k2, n2),
            'bootstrap': boot(k1, n1, k2, n2),
        }

    bands_out = {b: summarize(('band', b)) for b in BANDS}
    sub_out = {s: summarize(('sub', s)) for s in SUBS}
    bucket_out = {r: summarize(('ret_bucket', r)) for r in RET_LABELS}

    # 核心判据：A vs C（温和上涨 0~5%）；另给 A vs (B+C) 与 A vs 全样本
    bc = empty()
    for b in ('B', 'C'):
        x = ACC.get(('band', b))
        if x:
            bc['n'] += x['n']
            bc['kft'] += x['kft']
    ACC[('merged', 'BC')] = bc
    allacc = ACC.get(('all',)) or empty()

    overdraft = {
        'A_vs_C': compare(('band', 'A'), ('band', 'C')),
        'A_vs_BC': compare(('band', 'A'), ('merged', 'BC')),
        'A_vs_all': {
            'n_A': ACC.get(('band', 'A'), empty())['n'],
            'p_A': (ACC.get(('band', 'A'), empty())['kft'] / ACC.get(('band', 'A'), empty())['n'])
                   if ACC.get(('band', 'A'), empty())['n'] else None,
            'n_all': allacc['n'],
            'p_all': allacc['kft'] / allacc['n'] if allacc['n'] else None,
        },
    }

    # 稳健性切片
    by_lim = {t: compare(('lim_band', t, 'A'), ('lim_band', t, 'C')) for t in LIMTAGS}
    by_era = {e: compare(('era_band', e, 'A'), ('era_band', e, 'C')) for e in ERAS}
    by_year = {y: compare(('year_band', y, 'A'), ('year_band', y, 'C')) for y in years}
    by_lim_era = {}
    for t in LIMTAGS:
        for e in ERAS:
            by_lim_era[t + '|' + e] = compare(('lim_era_band', t, e, 'A'),
                                              ('lim_era_band', t, e, 'C'))
    reversals = []
    for grpname, grp in (('by_lim', by_lim), ('by_era', by_era),
                         ('by_year', by_year), ('by_lim_era', by_lim_era)):
        for tag, c in grp.items():
            if c and c['diff_A_minus_C'] is not None and c['diff_A_minus_C'] > 0:
                reversals.append({'slice': grpname + ':' + tag,
                                  'n_A': c['n_A'], 'n_C': c['n_C'],
                                  'p_A': c['p_A'], 'p_C': c['p_C'],
                                  'diff': c['diff_A_minus_C'], 'z': c['z']})

    # 反向检验：续涨概率最高的档
    def best_of(keys):
        best, bp = None, -1
        for k in keys:
            a = ACC.get(k)
            if a and a['n'] >= 200:
                p = a['kft'] / a['n']
                if p > bp:
                    bp, best = p, k
        return {'key': list(best) if best else None, 'p_ft2': bp if best else None,
                'n': ACC[best]['n'] if best else 0}

    reverse = {
        'best_band': best_of([('band', b) for b in BANDS]),
        'best_sub': best_of([('sub', s) for s in SUBS]),
        'best_ret_bucket': best_of([('ret_bucket', r) for r in RET_LABELS]),
    }

    st_sens = {
        'n_st_like_codes': len(st_like),
        'st_like_examples': sorted(st_like)[:10],
        'n_stclean': n_stclean,
        'stclean_A_vs_C': compare(('stclean_band', 'A'), ('stclean_band', 'C')),
        'n_boundary_removed': n_boundary,
        'n_bdclean': n_bdclean,
        'bdclean_A_vs_C': compare(('bdclean_band', 'A'), ('bdclean_band', 'C')),
    }

    labels = {}
    for name, d in LAB.items():
        a, b, c, dd = d['a'], d['b'], d['c'], d['d']
        tot = a + b + c + dd
        p_pos = (a + b) / tot if tot else None
        p_grail = (a + c) / tot if tot else None
        labels[name] = {
            'a_label_and_grail': a, 'b_label_and_no_grail': b,
            'c_no_label_and_grail': c, 'd_neither': dd,
            'n': tot, 'pos_rate': p_pos,
            'p_grail_given_label': a / (a + b) if (a + b) else None,
            'p_grail_given_no_label': c / (c + dd) if (c + dd) else None,
            'p_label_given_grail': a / (a + c) if (a + c) else None,
            'p_label_given_no_grail': b / (b + dd) if (b + dd) else None,
            'grail_base_rate': p_grail,
            'phi': phi4(a, b, c, dd),
        }

    out = {
        'meta': {
            'repo': REPO, 'daily': DAILY,
            'csv_columns': '日期,开盘,收盘,最高,最低,成交量 (index2=收盘, 非OHLC)',
            'truth_source': 'research/board_limit.py::is_limit_up (Decimal ROUND_HALF_UP)',
            'excluded_codes': sorted(EXCLUDE),
        },
        'sample': {
            'n_csv': len(fs), 'n_excluded_files': n_excluded,
            'n_codes_effective': len(all_rows), 'n_data_rows': n_data_rows, 'n_bad_rows': n_bad,
            'master_days': len(master), 'date_min': master[0] if master else None,
            'date_max': master[-1] if master else None, 'years': years,
            'N': N, 'n_T_no_prev': n_T_no_prev, 'n_T_limitup': n_T_limitup,
            'n_no_forward': n_no_forward, 'n_gap_na': n_gap_na,
            'T_date_min': min(tdates) if tdates else None,
            'T_date_max': max(tdates) if tdates else None,
            'n_T_distinct_dates': len(tdates),
        },
        'bands': bands_out,
        'sub_bands': sub_out,
        'ret_bucket': bucket_out,
        'overdraft': overdraft,
        'robustness': {
            'by_lim': by_lim, 'by_era': by_era, 'by_year': by_year,
            'by_lim_era': by_lim_era, 'reversals': reversals,
        },
        'reverse': reverse,
        'st_sensitivity': st_sens,
        'labels': labels,
        'examples': examples,
        'method': {
            'window': '[X-0.012, X+0.002], X=prev*(1+lim)',
            'exact_is_limit_up_calls': exact_calls,
            'outer_equiv_T': outer0, 'outer_equiv_T1': outer1,
            'verify_sample': verify_n, 'verify_mismatch': verify_mis,
            'has_numpy': HAS_NUMPY, 'bootstrap_B': BOOT_B, 'bootstrap_seed': BOOT_SEED,
            'st_heuristic': '主板0.10板: max(单日涨幅)<=0.06 且 ret in [0.045,0.055] 天数>=3',
            'boundary_sensitivity': '剔除 T+1 涨幅落在 [0.045,0.055] 的样本',
            'entry_profit_proxy': 'ret1 = close_T1/close_T - 1 > 0 (close_T 代理 14:50 买入价)',
            'ft2_definition': 'up2 (c2>c1) AND up3 (c3>c2); 逐日收涨, 非累计收益',
        },
    }
    print(json.dumps(out, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
