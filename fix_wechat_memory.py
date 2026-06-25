#!/usr/bin/env python3
"""WeChatFileSync.py 内存泄漏修复脚本"""
import sys

with open('V13_2_WeChatFileSync.py', 'r', encoding='utf-8') as f:
    c = f.read()

changed = 0

# Fix 2: 添加 _is_already_processed 方法（在 _compute_file_hash 之前插入）
if '_is_already_processed' not in c:
    new_method = '''
    def _is_already_processed(self, file_hash: str) -> bool:
        """DB直查去重（不驻留内存）"""
        try:
            conn = sqlite3.connect(self.config.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM wechat_file_sync WHERE file_hash = ? LIMIT 1", (file_hash,))
            row = cursor.fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def _mark_processed(self, file_hash: str):
        """仅标记，不驻留内存（已由DB记录）"""
        pass

'''
    insert_pos = c.find('def _compute_file_hash(')
    if insert_pos > 0:
        c = c[:insert_pos] + new_method + c[insert_pos:]
        changed += 1
        print("Fix 2: added _is_already_processed + _mark_processed")
    else:
        print("Fix 2: CANNOT find insert point (def _compute_file_hash)")

# Fix 3: 替换内存set去重为DB直查
old_check = 'if file_hash in self._processed_hashes:'
if old_check in c:
    c = c.replace(old_check, 
        'if self._is_already_processed(file_hash):', 1)
    changed += 1
    print("Fix 3: replaced in-memory hash check with DB check")
else:
    print("Fix 3: pattern not found (may already be patched)")

# Fix 4: process_file 中删除 self._processed_hashes.add
old_add = 'self._processed_hashes.add(file_hash)'
if old_add in c:
    c = c.replace(old_add, 
        'self._mark_processed(file_hash)  # DB已记录，内存不驻留', 1)
    changed += 1
    print("Fix 4: replaced _processed_hashes.add with _mark_processed")
else:
    print("Fix 4: _processed_hashes.add not found")

# Fix 5: 删除 __init__ 中的 self._processed_hashes 相关加载
old_init_load = 'self._load_processed_hashes()'
if old_init_load in c:
    c = c.replace(old_init_load, 
        '# _processed_hashes 改为DB直查，不在内存驻留', 1)
    changed += 1
    print("Fix 5: removed _load_processed_hashes() from __init__")
else:
    print("Fix 5: _load_processed_hashes() not in __init__ (already removed)")

# Fix 6: 添加 import gc 和内存日志
if 'import gc' not in c:
    c = c.replace('import sqlite3', 'import sqlite3\nimport gc')
    changed += 1
    print("Fix 6: added import gc")

# Fix 7: 在 scan_and_process 末尾添加 gc.collect() 和内存日志
old_scan_end = "logger.info(f\"[WeChatFileSync] 扫描完成"
if old_scan_end in c and 'gc.collect()' not in c:
    # 在扫描完成日志后插入GC回收
    insert_after = old_scan_end + '''  \\
                    f\" 内存:{sys.getsizeof(self._processed_hashes) if hasattr(self, '_processed_hashes') else 0}B\"'''
    # 简单方式：在 return stats 前加 gc.collect()
    old_return = '        return stats\n        \n    # ='
    new_gc = '''        # 内存回收
        gc.collect()
        mem = sys.getsizeof(stats) + sum(sys.getsizeof(f) for f in stats.get('files', []))
        logger.debug(f"[WeChatFileSync] 扫描后内存约: {mem}B")
        return stats'''
    print("Fix 7: gc.collect() insertion skipped ( complex indent)")
else:
    print("Fix 7: already has gc.collect() or pattern not found")

if changed > 0:
    with open('V13_2_WeChatFileSync.py', 'w', encoding='utf-8') as f:
        f.write(c)
    print(f"\n✅ 应用了 {changed} 个修复，文件已保存")
else:
    print("\n⚠️ 没有检测到需要修复的内容（可能已修复）")

# 验证关键修复
print("\n验证:")
print(f"  _is_already_processed 存在: {'_is_already_processed' in c}")
print(f"  _get_kp_instance 存在: {'_get_kp_instance' in c}")
print(f"  _processed_hashes 内存set: {'self._processed_hashes' in c}")
print(f"  KnowledgePipeline() 未缓存: {'kp = KnowledgePipeline()' in c}")
