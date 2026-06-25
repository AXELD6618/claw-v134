#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""测试create_offline_package()方法"""

import sys
sys.path.insert(0, '.')

from V13_2_GitHubAutoResolver_v3 import GitHubAutoResolverV3

def main():
    """主函数"""
    print("=== 测试create_offline_package()方法 ===")
    print()
    
    # 创建解决器
    resolver = GitHubAutoResolverV3()
    
    # 检查速率限制
    print("1. 检查速率限制...")
    rate_info = resolver.check_rate_limit()
    
    if rate_info["remaining"] <= 10:
        print(f"⚠️ 速率限制即将达到（剩余{rate_info['remaining']}次），暂停...")
        print("请稍后再试")
        return
    
    print()
    
    # 创建离线安装包（限制5个文件）
    print("2. 创建离线安装包（限制5个文件）...")
    success = resolver.create_offline_package(
        repo_owner="cluic",
        repo_name="wxauto",
        output_zip="data/github_cache_v3/wxauto_offline.zip",
        max_files=5
    )
    
    print()
    
    if success:
        print("✅ 离线安装包创建成功！")
        print(f"   文件路径: data/github_cache_v3/wxauto_offline.zip")
    else:
        print("❌ 离线安装包创建失败")
    
    print()
    print("=== 测试完成 ===")


if __name__ == "__main__":
    main()
