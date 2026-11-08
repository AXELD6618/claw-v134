# -*- coding: utf-8 -*-
"""
research/purged_cv.py — Purged K-Fold + Embargo（消除标签重叠泄漏）

López de Prado《Advances in Financial Machine Learning》第 7 章的标准做法：
  - Purge：训练集中剔除"标签时间区间与测试区间重叠"的样本
  - Embargo：在测试区间之后再禁用一个缓冲带，防序列自相关泄漏
提供 sklearn 兼容的 PurgedKFold（有 split/get_n_splits），以及底层索引函数。
run: python -m research.purged_cv --selftest
"""
from __future__ import annotations
import numpy as np


def purged_kfold_indices(n_samples, n_splits=5, embargo=0, t1=None):
    """生成 (train_idx, test_idx)。

    t1: 可选，长度 n_samples，表示每个样本"标签结束的时间点"(整数位置/序号)。
        若为 None，则退化为"按样本位置 purge + embargo"（适用于 iid 对齐场景）。
    embargo: 整数，测试区间之后禁用的样本数。
    """
    n_samples = int(n_samples)
    idx = np.arange(n_samples)
    folds = np.array_split(idx, n_splits)
    t1_arr = None if t1 is None else np.asarray(t1)
    for te in folds:
        if len(te) == 0:
            continue
        test_start, test_end = int(te[0]), int(te[-1])
        zone_end = test_end + int(embargo)
        if t1_arr is None:
            purge = (idx >= test_start) & (idx <= zone_end)
        else:
            # 样本 i 的标签区间 [i, t1[i]] 与测试区间 [test_start, test_end] 重叠 -> 剔除
            purge = (t1_arr >= test_start) & (idx <= zone_end)
        train_idx = idx[~purge]
        train_idx = np.setdiff1d(train_idx, te, assume_unique=False)
        yield train_idx, te


class PurgedKFold:
    def __init__(self, n_splits=5, embargo=0):
        self.n_splits = n_splits
        self.embargo = embargo

    def get_n_splits(self, X=None, y=None, t1=None):
        return self.n_splits

    def split(self, X, y=None, t1=None):
        n = len(X)
        for tr, te in purged_kfold_indices(n, self.n_splits, self.embargo, t1):
            yield tr, te


def _selftest():
    print("[selftest] n=20, 4 folds, embargo=2")
    cov = 0
    for k, (tr, te) in enumerate(purged_kfold_indices(20, 4, embargo=2)):
        overlap = set(tr.tolist()) & set(te.tolist())
        print(f"  fold{k}: train={len(tr)} test={list(te)} train∩test={len(overlap)}")
        assert len(overlap) == 0, "train/test 重叠!"
        cov += len(te)
    print("  覆盖样本数:", cov, "(应=20)")
    print("[selftest] 带 t1 标签区间 purge")
    t1 = np.array([i + 3 if i % 3 == 0 else i for i in range(20)])  # 部分样本标签跨3步
    for tr, te in purged_kfold_indices(20, 4, embargo=1, t1=t1):
        # 断言：无训练样本标签区间伸入测试区间
        if len(te) == 0:
            continue
        ts, tend = te[0], te[-1]
        violate = sum(1 for i in tr if t1[i] >= ts and i <= tend)
        print(f"  test=[{ts},{tend}] train={len(tr)} 越界样本={violate}")
        assert violate == 0
    print("[selftest] PASS")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python -m research.purged_cv --selftest")
