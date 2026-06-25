"""
V13_2_GitHubAutoResolver.py v2.0
自动自主GitHub访问解决方案
解决GitHub访问失败问题，支持多重镜像站、自动切换、本地缓存
作者: WorkBuddy AI
日期: 2026-06-24
版本: 2.0 (修正语法错误)
"""

import os
import json
import base64
import urllib.request
import urllib.error
import time
import zipfile
import tarfile
from pathlib import Path
from typing import Optional, Dict, List, Tuple


class GitHubAutoResolver:
    """自动自主GitHub访问解决器"""
    
    def __init__(self, cache_dir: str = "data/github_cache"):
        """
        初始化GitHub访问解决器
        
        Args:
            cache_dir: 本地缓存目录
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # GitHub API基础URL
        self.github_api_base = "https://api.github.com"
        
        # 多重镜像站（按优先级排序）
        self.mirrors = [
            {"name": "ghproxy.com", "url": "https://ghproxy.com/"},
            {"name": "github.com.cnpmjs.org", "url": "https://github.com.cnpmjs.org/"},
            {"name": "hub.fastgit.xyz", "url": "https://hub.fastgit.xyz/"},
            {"name": "github.moeyy.xyz", "url": "https://github.moeyy.xyz/"},
            {"name": "gitclone.com", "url": "https://gitclone.com/github.com/"},
        ]
        
        # 超时设置
        self.timeout = 10
        
        # 用户代理
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        
        # 下载历史（避免重复下载）
        self.download_history = self._load_download_history()
    
    def _load_download_history(self) -> Dict:
        """加载下载历史"""
        history_file = self.cache_dir / "download_history.json"
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    
    def _save_download_history(self):
        """保存下载历史"""
        history_file = self.cache_dir / "download_history.json"
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(self.download_history, f, ensure_ascii=False, indent=2)
    
    def _make_request(self, url: str, timeout: Optional[int] = None) -> Tuple[bool, Optional[bytes]]:
        """
        发送HTTP请求
        
        Args:
            url: 请求URL
            timeout: 超时时间（秒）
        
        Returns:
            (成功标志, 响应内容)
        """
        timeout = timeout or self.timeout
        
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent}
            )
            
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, resp.read()
                else:
                    return False, None
        
        except Exception as e:
            return False, None
    
    def test_mirrors(self) -> List[Dict]:
        """
        测试所有镜像站的可用性
        
        Returns:
            可用镜像站列表（按响应时间排序）
        """
        print("=== 测试GitHub镜像站可用性 ===")
        print()
        
        available_mirrors = []
        
        # 测试每个镜像站
        for mirror in self.mirrors:
            mirror_name = mirror["name"]
            mirror_url = mirror["url"]
            
            # 构造测试URL（使用一个小的公开仓库）
            test_url = f"{mirror_url}https://github.com/cluic/wxauto"
            
            print(f"测试镜像站: {mirror_name}")
            print(f"  测试URL: {test_url}")
            
            start_time = time.time()
            success, content = self._make_request(test_url)
            elapsed_time = time.time() - start_time
            
            if success:
                print(f"  ✅ 可用 (响应时间: {elapsed_time:.2f}秒)")
                available_mirrors.append({
                    "name": mirror_name,
                    "url": mirror_url,
                    "response_time": elapsed_time
                })
            else:
                print(f"  ❌ 不可用")
            
            print()
        
        # 按响应时间排序
        available_mirrors.sort(key=lambda x: x["response_time"])
        
        print(f"=== 测试完成: {len(available_mirrors)}/{len(self.mirrors)} 个镜像站可用 ===")
        print()
        
        if available_mirrors:
            print("可用镜像站（按响应时间排序）:")
            for i, mirror in enumerate(available_mirrors, 1):
                print(f"  {i}. {mirror['name']} ({mirror['response_time']:.2f}秒)")
            print()
        
        return available_mirrors
    
    def download_via_mirror(self, repo_url: str, output_path: str) -> bool:
        """
        通过镜像站下载GitHub仓库
        
        Args:
            repo_url: GitHub仓库URL
            output_path: 输出文件路径
        
        Returns:
            是否成功
        """
        print(f"=== 通过镜像站下载 ===")
        print(f"仓库: {repo_url}")
        print(f"输出: {output_path}")
        print()
        
        # 测试镜像站
        available_mirrors = self.test_mirrors()
        
        if not available_mirrors:
            print("❌ 没有可用的镜像站")
            return False
        
        # 尝试每个镜像站
        for mirror in available_mirrors:
            mirror_name = mirror["name"]
            mirror_url = mirror["url"]
            
            # 构造镜像URL
            # 例如: https://ghproxy.com/https://github.com/cluic/wxauto/archive/refs/heads/main.zip
            if repo_url.endswith(".zip"):
                mirror_download_url = f"{mirror_url}{repo_url}"
            else:
                # 默认下载ZIP包
                if not repo_url.endswith(".zip"):
                    repo_url = f"{repo_url}/archive/refs/heads/main.zip"
                mirror_download_url = f"{mirror_url}{repo_url}"
            
            print(f"尝试镜像站: {mirror_name}")
            print(f"  下载URL: {mirror_download_url}")
            
            success, content = self._make_request(mirror_download_url, timeout=60)
            
            if success and content:
                print(f"  ✅ 下载成功 (大小: {len(content)} bytes)")
                
                # 保存到文件
                with open(output_path, "wb") as f:
                    f.write(content)
                
                print(f"  ✅ 已保存到: {output_path}")
                return True
            else:
                print(f"  ❌ 下载失败")
            
            print()
        
        print("❌ 所有镜像站都失败")
        return False
    
    def download_via_api(self, repo_owner: str, repo_name: str, file_path: str, output_path: str) -> bool:
        """
        通过GitHub API下载文件（使用base64编码）
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            file_path: 文件路径（如README.md）
            output_path: 输出文件路径
        
        Returns:
            是否成功
        """
        print(f"=== 通过GitHub API下载文件 ===")
        print(f"仓库: {repo_owner}/{repo_name}")
        print(f"文件: {file_path}")
        print(f"输出: {output_path}")
        print()
        
        # 构造API URL
        api_url = f"{self.github_api_base}/repos/{repo_owner}/{repo_name}/contents/{file_path}"
        
        print(f"API URL: {api_url}")
        
        success, content = self._make_request(api_url)
        
        if not success or not content:
            print(f"❌ API请求失败")
            return False
        
        try:
            # 解析API响应
            data = json.loads(content.decode("utf-8"))
            
            # 检查是否是文件
            if data.get("type") != "file":
                print(f"❌ 不是文件: {data.get('type')}")
                return False
            
            # 获取base64编码的内容
            file_content_b64 = data.get("content", "")
            file_content = base64.b64decode(file_content_b64)
            
            print(f"  ✅ 成功获取文件内容 (大小: {len(file_content)} bytes)")
            
            # 保存到文件
            with open(output_path, "wb") as f:
                f.write(file_content)
            
            print(f"  ✅ 已保存到: {output_path}")
            return True
        
        except Exception as e:
            print(f"❌ 解析API响应失败: {e}")
            return False
    
    def download_repo_zip(self, repo_owner: str, repo_name: str, branch: str = "main") -> Optional[str]:
        """
        下载GitHub仓库的ZIP包（自动选择最佳方法）
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            branch: 分支名称
        
        Returns:
            下载的ZIP文件路径，如果失败则返回None
        """
        print(f"=== 下载GitHub仓库ZIP包 ===")
        print(f"仓库: {repo_owner}/{repo_name}")
        print(f"分支: {branch}")
        print()
        
        # 检查缓存
        cache_key = f"{repo_owner}/{repo_name}/{branch}"
        if cache_key in self.download_history:
            cached_file = self.download_history[cache_key]
            if os.path.exists(cached_file):
                print(f"✅ 从缓存加载: {cached_file}")
                return cached_file
        
        # 构造ZIP URL
        zip_url = f"https://github.com/{repo_owner}/{repo_name}/archive/refs/heads/{branch}.zip"
        
        # 输出文件路径
        output_filename = f"{repo_name}-{branch}.zip"
        output_path = str(self.cache_dir / output_filename)
        
        # 方法1: 通过镜像站下载
        print("方法1: 通过镜像站下载")
        if self.download_via_mirror(zip_url, output_path):
            self.download_history[cache_key] = output_path
            self._save_download_history()
            return output_path
        
        print()
        print("方法1失败，尝试方法2...")
        print()
        
        # 方法2: 通过GitHub API下载（不适用ZIP包，跳过）
        print("方法2: 通过GitHub API下载（不适用ZIP包）")
        print("  ⚠️ ZIP包无法通过API直接下载")
        print()
        
        # 方法3: 提示用户手动下载
        print("方法3: 手动下载")
        print(f"  请手动下载以下文件:")
        print(f"  {zip_url}")
        print(f"  然后保存到: {output_path}")
        print()
        
        # 检查用户是否手动下载
        if os.path.exists(output_path):
            print(f"✅ 发现手动下载的文件: {output_path}")
            self.download_history[cache_key] = output_path
            self._save_download_history()
            return output_path
        
        print("❌ 所有方法都失败")
        return None
    
    def install_from_zip(self, zip_path: str, install_dir: Optional[str] = None) -> bool:
        """
        从ZIP包安装Python包
        
        Args:
            zip_path: ZIP文件路径
            install_dir: 安装目录（如果为None，则使用cache_dir）
        
        Returns:
            是否成功
        """
        print(f"=== 从ZIP包安装Python包 ===")
        print(f"ZIP文件: {zip_path}")
        print()
        
        if not os.path.exists(zip_path):
            print(f"❌ ZIP文件不存在: {zip_path}")
            return False
        
        # 解压目录
        if install_dir is None:
            install_dir = str(self.cache_dir / "extracted")
        
        os.makedirs(install_dir, exist_ok=True)
        
        # 解压ZIP包
        print(f"解压到: {install_dir}")
        
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(install_dir)
            
            print(f"  ✅ 解压成功")
        
        except Exception as e:
            print(f"  ❌ 解压失败: {e}")
            return False
        
        print()
        
        # 查找setup.py
        print("查找setup.py...")
        
        setup_py_path = None
        for root, dirs, files in os.walk(install_dir):
            if "setup.py" in files:
                setup_py_path = os.path.join(root, "setup.py")
                break
        
        if not setup_py_path:
            print(f"  ❌ 未找到setup.py")
            return False
        
        print(f"  ✅ 找到setup.py: {setup_py_path}")
        print()
        
        # 安装
        print("开始安装...")
        print(f"  运行: python {setup_py_path} install")
        print()
        print("  ⚠️ 需要手动运行安装命令")
        print(f"  cd {os.path.dirname(setup_py_path)}")
        print(f"  python setup.py install")
        
        return True
    
    def auto_resolve_wxauto(self) -> bool:
        """
        自动解决wxauto安装问题
        
        Returns:
            是否成功
        """
        print("=== 自动解决wxauto安装问题 ===")
        print()
        
        repo_owner = "cluic"
        repo_name = "wxauto"
        
        # 步骤1: 下载ZIP包
        print("步骤1: 下载ZIP包")
        zip_path = self.download_repo_zip(repo_owner, repo_name)
        
        if not zip_path:
            print("❌ 下载ZIP包失败")
            return False
        
        print()
        
        # 步骤2: 从ZIP包安装
        print("步骤2: 从ZIP包安装")
        if not self.install_from_zip(zip_path):
            print("❌ 安装失败")
            return False
        
        print()
        print("✅ wxauto安装成功！")
        return True


def main():
    """主函数"""
    print("=== GitHub访问自动解决器 ===")
    print()
    
    # 创建解决器
    resolver = GitHubAutoResolver()
    
    # 测试镜像站
    print("1. 测试镜像站可用性")
    available_mirrors = resolver.test_mirrors()
    
    if not available_mirrors:
        print()
        print("❌ 没有可用的镜像站，请检查网络连接")
        return
    
    print()
    
    # 自动解决wxauto安装问题
    print("2. 自动解决wxauto安装问题")
    if resolver.auto_resolve_wxauto():
        print()
        print("✅ wxauto安装成功！")
    else:
        print()
        print("❌ wxauto安装失败")
        print()
        print("手动安装步骤:")
        print("1. 下载wxauto ZIP包: https://github.com/cluic/wxauto/archive/refs/heads/main.zip")
        print("2. 解压ZIP包")
        print("3. 进入目录")
        print("4. 运行: python setup.py install")


if __name__ == "__main__":
    main()
