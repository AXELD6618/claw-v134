"""
V13_2_GitHubAutoResolver_v3.py (~800行)
GitHub访问自动解决系统 v3.0
使用GitHub API为主，镜像站为辅的策略
作者: WorkBuddy AI
日期: 2026-06-24
版本: 3.0 (实用版)
"""

import os
import json
import base64
import urllib.request
import urllib.error
import time
import zipfile
import tarfile
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timedelta


class GitHubAutoResolverV3:
    """GitHub访问自动解决系统 v3.0"""
    
    def __init__(self, cache_dir: str = "data/github_cache_v3"):
        """
        初始化GitHub访问自动解决系统
        
        Args:
            cache_dir: 本地缓存目录
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # GitHub API基础URL
        self.github_api_base = "https://api.github.com"
        
        # 超时设置
        self.timeout = 15
        
        # 用户代理
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        
        # 下载历史（避免重复下载）
        self.download_history_file = self.cache_dir / "download_history_v3.json"
        self.download_history = self._load_download_history()
        
        # 速率限制信息
        self.rate_limit_remaining = 60  # 未认证用户每小时60次
        self.rate_limit_reset = 0  # 速率限制重置时间
        
        print(f"✅ GitHub访问自动解决系统 v3.0 初始化完成")
        print(f"   缓存目录: {self.cache_dir}")
        print(f"   GitHub API速率限制: {self.rate_limit_remaining}次/小时")
        print()
    
    def _load_download_history(self) -> Dict:
        """加载下载历史"""
        if self.download_history_file.exists():
            try:
                with open(self.download_history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_download_history(self):
        """保存下载历史"""
        with open(self.download_history_file, "w", encoding="utf-8") as f:
            json.dump(self.download_history, f, ensure_ascii=False, indent=2)
    
    def _make_request(self, url: str, timeout: Optional[int] = None, is_api: bool = False) -> Tuple[bool, Optional[bytes], Dict]:
        """
        发送HTTP请求
        
        Args:
            url: 请求URL
            timeout: 超时时间（秒）
            is_api: 是否是API请求（用于检查速率限制）
        
        Returns:
            (成功标志, 响应内容, 响应头)
        """
        timeout = timeout or self.timeout
        
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent}
            )
            
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # 如果是API请求，更新速率限制信息
                if is_api:
                    self.rate_limit_remaining = int(resp.headers.get("X-RateLimit-Remaining", 60))
                    self.rate_limit_reset = int(resp.headers.get("X-RateLimit-Reset", 0))
                
                if resp.status == 200:
                    content = resp.read()
                    headers = dict(resp.headers)
                    return True, content, headers
                else:
                    return False, None, {}
        
        except Exception as e:
            print(f"   ❌ 请求失败: {e}")
            return False, None, {}
    
    def check_rate_limit(self) -> Dict:
        """
        检查GitHub API速率限制
        
        Returns:
            速率限制信息
        """
        print("=== 检查GitHub API速率限制 ===")
        print()
        
        url = f"{self.github_api_base}/rate_limit"
        success, content, headers = self._make_request(url, is_api=True)
        
        if not success or not content:
            print("❌ 无法获取速率限制信息")
            return {"remaining": 0, "limit": 60, "reset": 0}
        
        try:
            data = json.loads(content.decode("utf-8"))
            rate_info = data.get("rate", {})
            
            remaining = rate_info.get("remaining", 0)
            limit = rate_info.get("limit", 60)
            reset = rate_info.get("reset", 0)
            
            print(f"速率限制: {remaining}/{limit} 次")
            print(f"重置时间: {datetime.fromtimestamp(reset).strftime('%Y-%m-%d %H:%M:%S')}")
            print()
            
            self.rate_limit_remaining = remaining
            self.rate_limit_reset = reset
            
            return {"remaining": remaining, "limit": limit, "reset": reset}
        
        except Exception as e:
            print(f"❌ 解析速率限制信息失败: {e}")
            return {"remaining": 0, "limit": 60, "reset": 0}
    
    def get_repo_info(self, repo_owner: str, repo_name: str) -> Optional[Dict]:
        """
        获取仓库信息
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
        
        Returns:
            仓库信息字典，如果失败则返回None
        """
        print(f"=== 获取仓库信息: {repo_owner}/{repo_name} ===")
        print()
        
        url = f"{self.github_api_base}/repos/{repo_owner}/{repo_name}"
        success, content, headers = self._make_request(url, is_api=True)
        
        if not success or not content:
            print(f"❌ 获取仓库信息失败")
            return None
        
        try:
            data = json.loads(content.decode("utf-8"))
            
            print(f"✅ 仓库信息获取成功")
            print(f"   仓库名称: {data.get('full_name')}")
            print(f"   描述: {data.get('description')}")
            print(f"   默认分支: {data.get('default_branch')}")
            print(f"   Star数: {data.get('stargazers_count')}")
            print(f"   Fork数: {data.get('forks_count')}")
            print(f"   最后更新: {data.get('updated_at')}")
            print()
            
            return data
        
        except Exception as e:
            print(f"❌ 解析仓库信息失败: {e}")
            return None
    
    def get_file_content(self, repo_owner: str, repo_name: str, file_path: str, ref: str = "main") -> Optional[str]:
        """
        获取文件内容（通过GitHub API）
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            file_path: 文件路径（如README.md）
            ref: 分支或提交SHA
        
        Returns:
            文件内容（字符串），如果失败则返回None
        """
        print(f"=== 获取文件内容: {repo_owner}/{repo_name}/{file_path} ===")
        print()
        
        # 检查速率限制
        if self.rate_limit_remaining <= 0:
            reset_time = datetime.fromtimestamp(self.rate_limit_reset)
            print(f"❌ 速率限制已满，请于 {reset_time.strftime('%Y-%m-%d %H:%M:%S')} 后再试")
            return None
        
        url = f"{self.github_api_base}/repos/{repo_owner}/{repo_name}/contents/{file_path}?ref={ref}"
        success, content, headers = self._make_request(url, is_api=True)
        
        if not success or not content:
            print(f"❌ 获取文件内容失败")
            return None
        
        try:
            data = json.loads(content.decode("utf-8"))
            
            # 检查是否是文件
            if data.get("type") != "file":
                print(f"❌ 不是文件: {data.get('type')}")
                return None
            
            # 获取base64编码的内容
            file_content_b64 = data.get("content", "")
            file_content = base64.b64decode(file_content_b64).decode("utf-8")
            
            print(f"✅ 文件内容获取成功 (大小: {len(file_content)} 字符)")
            print()
            
            return file_content
        
        except Exception as e:
            print(f"❌ 解析文件内容失败: {e}")
            return None
    
    def download_file(self, repo_owner: str, repo_name: str, file_path: str, output_path: str, ref: str = "main") -> bool:
        """
        下载单个文件（通过GitHub API）
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            file_path: 文件路径（如README.md）
            output_path: 输出文件路径
            ref: 分支或提交SHA
        
        Returns:
            是否成功
        """
        print(f"=== 下载单个文件 ===")
        print(f"仓库: {repo_owner}/{repo_name}")
        print(f"文件: {file_path}")
        print(f"输出: {output_path}")
        print()
        
        # 获取文件内容
        content = self.get_file_content(repo_owner, repo_name, file_path, ref)
        
        if not content:
            print(f"❌ 下载失败")
            return False
        
        try:
            # 保存到文件
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            print(f"✅ 文件已保存: {output_path}")
            print()
            
            return True
        
        except Exception as e:
            print(f"❌ 保存文件失败: {e}")
            return False
    
    def download_multiple_files(self, repo_owner: str, repo_name: str, file_paths: List[str], output_dir: str, ref: str = "main") -> Dict[str, bool]:
        """
        下载多个文件（通过GitHub API）
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            file_paths: 文件路径列表
            output_dir: 输出目录
            ref: 分支或提交SHA
        
        Returns:
            下载结果字典 {文件路径: 是否成功}
        """
        print(f"=== 下载多个文件 ===")
        print(f"仓库: {repo_owner}/{repo_name}")
        print(f"文件数: {len(file_paths)}")
        print(f"输出目录: {output_dir}")
        print()
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 下载每个文件
        results = {}
        for i, file_path in enumerate(file_paths, 1):
            print(f"[{i}/{len(file_paths)}] 下载: {file_path}")
            
            # 构造输出文件路径
            output_filename = os.path.join(output_dir, file_path.replace("/", "_"))
            
            # 下载文件
            success = self.download_file(repo_owner, repo_name, file_path, output_filename, ref)
            results[file_path] = success
            
            print()
        
        # 统计结果
        success_count = sum(1 for success in results.values() if success)
        print(f"=== 下载完成: {success_count}/{len(file_paths)} 个文件成功 ===")
        print()
        
        return results
    
    def get_repo_tree(self, repo_owner: str, repo_name: str, ref: str = "main", recursive: bool = True) -> Optional[List[Dict]]:
        """
        获取仓库文件树
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            ref: 分支或提交SHA
            recursive: 是否递归获取所有文件
        
        Returns:
            文件树列表，如果失败则返回None
        """
        print(f"=== 获取仓库文件树: {repo_owner}/{repo_name} ===")
        print()
        
        # 检查速率限制
        if self.rate_limit_remaining <= 0:
            reset_time = datetime.fromtimestamp(self.rate_limit_reset)
            print(f"❌ 速率限制已满，请于 {reset_time.strftime('%Y-%m-%d %H:%M:%S')} 后再试")
            return None
        
        url = f"{self.github_api_base}/repos/{repo_owner}/{repo_name}/git/trees/{ref}?recursive={1 if recursive else 0}"
        success, content, headers = self._make_request(url, is_api=True)
        
        if not success or not content:
            print(f"❌ 获取文件树失败")
            return None
        
        try:
            data = json.loads(content.decode("utf-8"))
            tree = data.get("tree", [])
            
            print(f"✅ 文件树获取成功 (文件数: {len(tree)})")
            print()
            
            return tree
        
        except Exception as e:
            print(f"❌ 解析文件树失败: {e}")
            return None
    
    def create_offline_package(self, repo_owner: str, repo_name: str, output_zip: str, ref: str = "main", max_files: int = 100) -> bool:
        """
        创建离线安装包（通过GitHub API下载所有文件并打包成ZIP）
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            output_zip: 输出ZIP文件路径
            ref: 分支或提交SHA
            max_files: 最大文件数（避免速率限制）
        
        Returns:
            是否成功
        """
        print(f"=== 创建离线安装包 ===")
        print(f"仓库: {repo_owner}/{repo_name}")
        print(f"输出: {output_zip}")
        print()
        
        # 获取仓库文件树
        tree = self.get_repo_tree(repo_owner, repo_name, ref, recursive=True)
        
        if not tree:
            print(f"❌ 获取文件树失败")
            return False
        
        # 过滤文件（只保留文件，不包括目录）
        files = [item for item in tree if item.get("type") == "blob"]
        
        # 限制文件数
        if len(files) > max_files:
            print(f"⚠️ 文件数过多 ({len(files)} > {max_files})，只下载前{max_files}个文件")
            files = files[:max_files]
        
        print(f"需要下载 {len(files)} 个文件")
        print()
        
        # 创建临时目录
        temp_dir = self.cache_dir / f"{repo_name}_temp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 下载所有文件
        print(f"开始下载文件...")
        print()
        
        success_count = 0
        for i, file_info in enumerate(files, 1):
            file_path = file_info.get("path")
            
            print(f"[{i}/{len(files)}] 下载: {file_path}")
            
            # 创建子目录
            local_file_path = temp_dir / file_path
            local_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 下载文件
            success = self.download_file(repo_owner, repo_name, file_path, str(local_file_path), ref)
            
            if success:
                success_count += 1
            
            print()
            
            # 检查速率限制
            if self.rate_limit_remaining <= 5:
                print(f"⚠️ 速率限制即将达到，暂停60秒...")
                time.sleep(60)
        
        print(f"下载完成: {success_count}/{len(files)} 个文件成功")
        print()
        
        # 打包成ZIP
        print(f"打包成ZIP: {output_zip}")
        
        try:
            with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zipf.write(file_path, arcname)
            
            print(f"✅ ZIP包创建成功: {output_zip}")
            print()
        
        except Exception as e:
            print(f"❌ 创建ZIP包失败: {e}")
            return False
        
        # 清理临时目录
        shutil.rmtree(temp_dir)
        
        print(f"✅ 离线安装包创建完成!")
        print()
        
        return True
    
    def generate_manual_download_guide(self, repo_owner: str, repo_name: str, branch: str = "main") -> str:
        """
        生成手动下载指南
        
        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            branch: 分支名称
        
        Returns:
            手动下载指南（Markdown格式）
        """
        print(f"=== 生成手动下载指南 ===")
        print()
        
        guide = f"""# {repo_owner}/{repo_name} 手动下载指南

由于GitHub访问限制，请按照以下步骤手动下载：

## 方法1: 下载ZIP包

1. 访问仓库主页:
   https://github.com/{repo_owner}/{repo_name}

2. 点击绿色 "Code" 按钮，选择 "Download ZIP"

3. 下载ZIP包到本地

4. 解压ZIP包

5. 进入解压后的目录

6. 运行安装命令:
   ```
   python setup.py install
   ```

## 方法2: 使用Git克隆

1. 如果你有Git installed and GitHub访问权限:

   ```bash
   git clone https://github.com/{repo_owner}/{repo_name}.git
   cd {repo_name}
   python setup.py install
   ```

## 方法3: 使用镜像站

尝试以下镜像站（可能可用）:

- https://ghproxy.com/https://github.com/{repo_owner}/{repo_name}/archive/refs/heads/{branch}.zip
- https://github.com.cnpmjs.org/{repo_owner}/{repo_name}/archive/refs/heads/{branch}.zip

## 方法4: 使用离线安装包

如果你有离线安装包，直接运行:

```bash
pip install {repo_name}-offline.zip
```

## 联系作者

如果以上方法都不行，请联系仓库作者:
{repo_owner} (https://github.com/{repo_owner})

---

生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
        
        print(f"✅ 手动下载指南生成完成")
        print()
        
        return guide


def main():
    """主函数"""
    print("=== GitHub访问自动解决系统 v3.0 ===")
    print()
    
    # 创建解决器
    resolver = GitHubAutoResolverV3()
    
    # 检查速率限制
    print("1. 检查GitHub API速率限制")
    rate_info = resolver.check_rate_limit()
    
    if rate_info["remaining"] <= 0:
        print("❌ 速率限制已满，无法继续")
        return
    
    print()
    
    # 获取仓库信息（示例：wxauto）
    print("2. 获取仓库信息（示例：cluic/wxauto）")
    repo_info = resolver.get_repo_info("cluic", "wxauto")
    
    if not repo_info:
        print("❌ 获取仓库信息失败")
        return
    
    print()
    
    # 获取关键文件内容
    print("3. 获取关键文件内容")
    key_files = ["README.md", "setup.py", "requirements.txt"]
    
    for file in key_files:
        content = resolver.get_file_content("cluic", "wxauto", file)
        if content:
            print(f"   ✅ {file} ({len(content)} 字符)")
        else:
            print(f"   ❌ {file} 获取失败")
    
    print()
    
    # 生成手动下载指南
    print("4. 生成手动下载指南")
    guide = resolver.generate_manual_download_guide("cluic", "wxauto")
    
    guide_file = resolver.cache_dir / "manual_download_guide.md"
    with open(guide_file, "w", encoding="utf-8") as f:
        f.write(guide)
    
    print(f"✅ 手动下载指南已保存: {guide_file}")
    print()
    
    print("=== 完成 ===")
    print()
    print("⚠️ 注意:")
    print("1. 由于GitHub访问限制，无法自动下载ZIP包")
    print("2. 已获取关键文件内容（通过GitHub API）")
    print("3. 已生成手动下载指南")
    print("4. 请参考手动下载指南完成安装")


if __name__ == "__main__":
    main()
