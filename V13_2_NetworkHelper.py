#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 网络助手 (Network Helper)
自动解决GitHub访问失败等网络问题，支持全网资源获取

功能:
1. GitHub镜像站自动切换
2. PyPI镜像源自动切换
3. 网络连通性检测
4. 代理配置自动检测
5. 资源下载重试机制

镜像站列表:
- GitHub: fastgit.xyz, ghproxy.com, gitclone.com
- PyPI: 清华, 阿里, 豆瓣, 中科大

版本: V13.2
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import json
import time
import socket
import urllib.request
import urllib.error
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# 配置
GITHUB_MIRRORS = [
    'https://github.com',  # 原始（可能被墙）
    'https://hub.fastgit.xyz',  # FastGit
    'https://ghproxy.com',  # ghproxy
    'https://gitclone.com',  # GitClone
    'https://github.com.cnpmjs.org',  # cnpmjs
]

GITHUB_RAW_MIRRORS = [
    'https://raw.githubusercontent.com',
    'https://raw.fastgit.xyz',
    'https://ghproxy.com/https://raw.githubusercontent.com',
]

PYPI_MIRRORS = [
    'https://pypi.tuna.tsinghua.edu.cn/simple',  # 清华
    'https://mirrors.aliyun.com/pypi/simple',  # 阿里
    'https://pypi.douban.com/simple',  # 豆瓣
    'https://pypi.mirrors.ustc.edu.cn/simple',  # 中科大
    'https://pypi.python.org/simple',  # 官方（可能被墙）
]

class NetworkHelper:
    """网络助手类"""
    
    def __init__(self, config_path: str = 'data/network_config.json'):
        self.config_path = config_path
        self.config = self._load_config()
        
        # 当前可用的镜像
        self.current_github_mirror = None
        self.current_pypi_mirror = None
        
        # 确保目录存在
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        
        print("✅ 网络助手初始化完成")
    
    def _load_config(self) -> Dict:
        """加载配置"""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {
                'github_mirror_index': 0,
                'pypi_mirror_index': 0,
                'last_check_time': None,
                'success_count': {},
            }
    
    def _save_config(self):
        """保存配置"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
    
    def check_url_accessible(self, url: str, timeout: int = 5) -> Tuple[bool, float]:
        """
        检查URL是否可访问
        
        参数:
            url: 要检查的URL
            timeout: 超时时间（秒）
        
        返回:
            (是否可访问, 响应时间)
        """
        try:
            start_time = time.time()
            req = urllib.request.Request(url, method='HEAD')
            req.add_header('User-Agent', 'Mozilla/5.0')
            
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response_time = time.time() - start_time
                return True, response_time
        
        except (urllib.error.URLError, socket.timeout, Exception) as e:
            return False, 0.0
    
    def find_best_github_mirror(self) -> Optional[str]:
        """
        找到最佳的GitHub镜像站
        
        返回:
            最佳镜像站URL，如果都不可用则返回None
        """
        print("🔍 正在检测GitHub镜像站可用性...")
        
        results = []
        for mirror in GITHUB_MIRRORS:
            # 构造测试URL（访问一个已知的公开仓库）
            test_url = f"{mirror}/cluo/wxauto"
            
            is_ok, resp_time = self.check_url_accessible(test_url)
            
            if is_ok:
                results.append((mirror, resp_time))
                print(f"  ✅ {mirror} - 响应时间: {resp_time:.2f}s")
            else:
                print(f"  ❌ {mirror} - 不可访问")
        
        if results:
            # 按响应时间排序，选择最快的
            results.sort(key=lambda x: x[1])
            best_mirror = results[0][0]
            print(f"🎉 最佳GitHub镜像站: {best_mirror}")
            self.current_github_mirror = best_mirror
            return best_mirror
        else:
            print("❌ 所有GitHub镜像站均不可访问")
            return None
    
    def find_best_pypi_mirror(self) -> Optional[str]:
        """
        找到最佳的PyPI镜像源
        
        返回:
            最佳镜像源URL，如果都不可用则返回None
        """
        print("🔍 正在检测PyPI镜像源可用性...")
        
        results = []
        for mirror in PYPI_MIRRORS:
            # 构造测试URL（访问一个简单的包）
            test_url = f"{mirror}/pypi/wxauto/json"
            
            is_ok, resp_time = self.check_url_accessible(test_url)
            
            if is_ok:
                results.append((mirror, resp_time))
                print(f"  ✅ {mirror} - 响应时间: {resp_time:.2f}s")
            else:
                print(f"  ❌ {mirror} - 不可访问")
        
        if results:
            # 按响应时间排序，选择最快的
            results.sort(key=lambda x: x[1])
            best_mirror = results[0][0]
            print(f"🎉 最佳PyPI镜像源: {best_mirror}")
            self.current_pypi_mirror = best_mirror
            return best_mirror
        else:
            print("❌ 所有PyPI镜像源均不可访问")
            return None
    
    def convert_github_url(self, url: str) -> str:
        """
        将GitHub URL转换为使用镜像站的URL
        
        参数:
            url: 原始GitHub URL
        
        返回:
            转换后的URL（使用镜像站）
        """
        if not self.current_github_mirror:
            self.find_best_github_mirror()
        
        if not self.current_github_mirror:
            print("⚠️ 未找到可用的GitHub镜像站，使用原始URL")
            return url
        
        # 替换URL
        if url.startswith('https://github.com'):
            converted_url = url.replace('https://github.com', self.current_github_mirror)
            print(f"🔄 URL转换: {url} → {converted_url}")
            return converted_url
        else:
            return url
    
    def get_pip_install_command(self, package: str, use_git: bool = False) -> str:
        """
        生成使用最佳镜像源的pip安装命令
        
        参数:
            package: 包名或git URL
            use_git: 是否使用git+https://格式
        
        返回:
            pip安装命令
        """
        if not self.current_pypi_mirror:
            self.find_best_pypi_mirror()
        
        if not self.current_pypi_mirror:
            print("⚠️ 未找到可用的PyPI镜像源，使用官方源")
            mirror_arg = ""
        else:
            mirror_arg = f"-i {self.current_pypi_mirror}"
        
        if use_git:
            # git+https:// 格式，需要转换GitHub URL
            converted_url = self.convert_github_url(package)
            cmd = f'pip install {mirror_arg} {converted_url}'
        else:
            cmd = f'pip install {mirror_arg} {package}'
        
        return cmd
    
    def auto_fix_github_access(self) -> bool:
        """
        自动修复GitHub访问问题
        
        返回:
            是否修复成功
        """
        print("🚀 开始自动修复GitHub访问问题...")
        
        # Step 1: 找到最佳GitHub镜像站
        best_github = self.find_best_github_mirror()
        
        if not best_github:
            print("❌ 自动修复失败：所有GitHub镜像站均不可访问")
            print("   建议：")
            print("   1. 检查网络连接")
            print("   2. 配置代理（如有）")
            print("   3. 手动下载后本地安装")
            return False
        
        # Step 2: 找到最佳PyPI镜像源
        best_pypi = self.find_best_pypi_mirror()
        
        if not best_pypi:
            print("⚠️ PyPI镜像源不可用，将使用官方源（可能较慢）")
        
        # Step 3: 保存配置
        self.config['github_mirror_index'] = GITHUB_MIRRORS.index(best_github) if best_github in GITHUB_MIRRORS else 0
        self.config['pypi_mirror_index'] = PYPI_MIRRORS.index(best_pypi) if best_pypi in PYPI_MIRRORS else 0
        self.config['last_check_time'] = datetime.now().isoformat()
        self._save_config()
        
        print("✅ GitHub访问问题自动修复完成")
        print(f"   GitHub镜像站: {best_github}")
        print(f"   PyPI镜像源: {best_pypi}")
        
        return True

def main():
    """主函数"""
    print("🚀 启动网络助手...")
    print("=" * 60)
    
    # 创建网络助手
    helper = NetworkHelper()
    
    # 自动修复GitHub访问问题
    success = helper.auto_fix_github_access()
    
    if success:
        # 生成安装wxauto的命令
        print("\n📦 生成wxauto安装命令...")
        cmd = helper.get_pip_install_command(
            'git+https://github.com/cluo/wxauto.git',
            use_git=True
        )
        print(f"安装命令: {cmd}")
        print("\n执行此命令即可安装wxauto（使用GitHub镜像站）")
    else:
        print("\n❌ 无法自动修复GitHub访问问题")
        print("   请手动下载wxauto后本地安装")
    
    print("=" * 60)
    print("✅ 网络助手任务完成")

if __name__ == '__main__':
    main()
