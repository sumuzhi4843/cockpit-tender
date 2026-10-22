#!/bin/bash
# 安装 Chromium 浏览器（用于江苏政府采购网 CDP 抓取）
# 用法：sudo bash setup_chromium.sh

set -e

echo "=== 安装 Chromium 浏览器 ==="

# 检查是否已安装
if command -v chromium-browser &>/dev/null || command -v chromium &>/dev/null; then
    echo "Chromium 已安装，跳过"
    exit 0
fi

# 方法 1: apt 安装（Ubuntu/Debian）
if command -v apt-get &>/dev/null; then
    echo "尝试 apt 安装 chromium-browser（snap 版）…"
    # snap 版可能不支持 headless，改用 ppa 或直接下载
    if grep -q 'Ubuntu\|Debian' /etc/os-release 2>/dev/null; then
        # 添加 PPA 获取非 snap 版
        add-apt-repository -y ppa:xtradeb/apps 2>/dev/null || true
        apt-get update -qq 2>/dev/null || true
        apt-get install -y chromium 2>&1 | tail -5
        if command -v chromium &>/dev/null; then
            echo "✓ Chromium 安装成功: $(chromium --version)"
            exit 0
        fi
    fi
fi

# 方法 2: 通过 npm puppeteer 安装（自带 Chromium）
if command -v npx &>/dev/null; then
    echo "尝试通过 puppeteer 安装 Chromium…"
    npx -y puppeteer browsers install chrome 2>&1 | tail -5
    echo "Puppeteer Chromium 安装完成"
    echo "可通过 CHROMIUM_PATH 环境变量指定路径使用"
    exit 0
fi

# 方法 3: 直接下载 Chromium 二进制
echo "尝试直接下载 Chromium 二进制…"
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    # 从国内镜像下载
    CHROMIUM_URL="https://storage.googleapis.com/chromium-browser-snapshots/Linux_x64"
    LAST_CHANGE=$(curl -s "$CHROMIUM_URL/LAST_CHANGE" 2>/dev/null)
    if [ -n "$LAST_CHANGE" ]; then
        echo "下载 Chromium r$LAST_CHANGE…"
        cd /tmp
        curl -sL "$CHROMIUM_URL/$LAST_CHANGE/chrome-linux.zip" -o chrome.zip
        unzip -q chrome.zip -d /opt/chromium
        ln -sf /opt/chromium/chrome-linux/chrome /usr/local/bin/chromium
        rm -f chrome.zip
        echo "✓ Chromium 安装成功: $(/usr/local/bin/chromium --version)"
    fi
fi

# 检查是否安装成功
if ! command -v chromium &>/dev/null && ! command -v chromium-browser &>/dev/null; then
    echo ""
    echo "⚠ 自动安装失败，请手动安装："
    echo ""
    echo "  Ubuntu: sudo apt install chromium-browser"
    echo "  macOS:  brew install --cask chromium"
    echo "  Docker: 使用 browserless/chrome 镜像"
    echo ""
    echo "或设置 CHROMIUM_PATH 环境变量指向已安装的浏览器："
    echo "  export CHROMIUM_PATH=/path/to/chromium"
    echo ""
    exit 1
fi
