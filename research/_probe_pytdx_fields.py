#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：实测 pytdx TdxHq_API 能产出哪些字段，决定脚本侧批量通道能覆盖 34 特征中的哪些。"""
import json
from pytdx.hq import TdxHq_API
from pytdx.config.hosts import hq_hosts


def list_methods():
    ms = [m for m in dir(TdxHq_API) if m.startswith("get_") or "quote" in m.lower() or "info" in m.lower()]
    print("=== TdxHq_API get*/info/quote 方法 ===")
    for m in sorted(ms):
        print("  ", m)


def probe_quotes():
    api = TdxHq_API(heartbeat=True, auto_retry=True)
    for h in hq_hosts:
        name, ip, port = h[0], h[1], h[2]
        try:
            if not api.connect(ip, port, time_out=4):
                continue
            # 沪市 600519 茅台, 深市 000001 平安银行
            q = api.get_security_quotes([(1, "600519"), (0, "000001")])
            api.disconnect()
            if q:
                print("=== get_security_quotes 返回字段（首只）===")
                rec = q[0]
                for k in sorted(rec.keys()):
                    print("   %-16s = %r" % (k, rec[k]))
                print("=== 返回条数 ===", len(q))
                return True
            else:
                print("[probe] get_security_quotes 返回空（可能非交易时段）")
        except Exception as e:
            print("[probe] %s 失败: %s" % (name, e))
            try:
                api.disconnect()
            except Exception:
                pass
            continue
    return False


def probe_transaction():
    """tick 成交（近似主力净流/大单）"""
    api = TdxHq_API(heartbeat=True, auto_retry=True)
    for h in hq_hosts:
        name, ip, port = h[0], h[1], h[2]
        try:
            if not api.connect(ip, port, time_out=4):
                continue
            t = api.get_transaction_data(1, "600519", 0, 10)
            api.disconnect()
            if t:
                print("=== get_transaction_data 字段（首条）===")
                for k in sorted(t[0].keys()):
                    print("   %-12s = %r" % (k, t[0][k]))
            else:
                print("[probe] transaction 空")
            return True
        except Exception as e:
            print("[probe] txn %s: %s" % (name, e))
            try:
                api.disconnect()
            except Exception:
                pass
            continue
    return False


if __name__ == "__main__":
    list_methods()
    ok = probe_quotes()
    if ok:
        probe_transaction()
