#!/bin/bash
# wxauto 手动安装辅助脚本
# 使用方法：
# 1. 手动从 https://github.com/cluo/wxauto 下载ZIP
# 2. 将ZIP文件放到此脚本同目录下
# 3. 运行此脚本：bash install_wxauto.sh

echo "🚀 开始安装wxauto（手动模式）..."

# 查找wxauto ZIP文件
ZIP_FILE=$(ls wxauto*.zip 2>/dev/null | head -1)

if [ -z "$ZIP_FILE" ]; then
    echo "❌ 未找到wxauto ZIP文件"
    echo "   请先手动下载：https://github.com/cluo/wxauto"
    echo "   然后将ZIP文件放到此脚本同目录下"
    exit 1
fi

echo "✅ 找到ZIP文件: $ZIP_FILE"

# 解压
echo "📦 解压中..."
unzip -q "$ZIP_FILE" -d wxauto_temp

if [ $? -ne 0 ]; then
    echo "❌ 解压失败"
    exit 1
fi

echo "✅ 解压完成"

# 查找解压后的目录
WXAUTO_DIR=$(ls -d wxauto_temp/wxauto-* 2>/dev/null | head -1)

if [ -z "$WXAUTO_DIR" ]; then
    echo "❌ 未找到wxauto目录"
    exit 1
fi

echo "✅ 找到wxauto目录: $WXAUTO_DIR"

# 安装
echo "📥 安装中..."
"$PYTHON_EXE" -m pip install "$WXAUTO_DIR"

if [ $? -ne 0 ]; then
    echo "❌ 安装失败"
    exit 1
fi

echo "✅ wxauto安装成功"

# 清理
echo "🧹 清理临时文件..."
rm -rf wxauto_temp

echo "🎉 wxauto手动安装完成！"
echo "   可以运行 V13_2_WeChatRealtimeSync.py 测试"
