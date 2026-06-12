#!/bin/bash
# deploy_persistence.sh — 一键部署三节点持久化
# 运行方式: bash symphony-framework/scripts/deploy_persistence.sh

set -e
# SSH credentials loaded from external file (not committed to git)
if [ -f "$SCRIPT_DIR/.deploy_env" ]; then
    source "$SCRIPT_DIR/.deploy_env"
else
    echo "❌ Missing .deploy_env (see .deploy_env.example)"
    exit 1
fi
SSH_5060="sshpass -p '2129482' ssh -o StrictHostKeyChecking=no administrator@192.168.1.100"
SCP_5060="sshpass -p '2129482' scp -o StrictHostKeyChecking=no"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 节点持久化部署 ==="
echo ""

# ========== R1 (192.168.1.36 Mac mini) ==========
echo "📡 [R1] 部署 launchd plist..."

# 先确认 R1 可达
if ! ping -c 1 -W 2 192.168.1.36 &>/dev/null; then
    echo "❌ R1 (192.168.1.36) 不可达，跳过"
else
    # 复制启动脚本
    $SCP_R1 "$SCRIPT_DIR/start_r1_full.sh" finance@192.168.1.36:~/opensymphony/scripts/start_r1_full.sh
    
    # 复制 plist
    $SCP_R1 "$SCRIPT_DIR/com.opensymphony.r1.plist" finance@192.168.1.36:/tmp/com.opensymphony.r1.plist
    
    # 安装 plist
    $SSH_R1 bash -s << 'REMOTE_R1'
set -e

# 确保 start_r1_full.sh 可执行
chmod +x ~/opensymphony/scripts/start_r1_full.sh

# 停掉手动启动的旧进程
pkill -f "opensymphony.kernel" 2>/dev/null || true
pkill -f "start_r1_full" 2>/dev/null || true
sleep 2

# 安装 launchd
cp /tmp/com.opensymphony.r1.plist ~/Library/LaunchAgents/com.opensymphony.r1.plist
launchctl unload ~/Library/LaunchAgents/com.opensymphony.r1.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.opensymphony.r1.plist

echo "✅ R1 launchd 已安装并启动"
echo "   日志: /tmp/opensymphony_r1.log"
echo "   管理: launchctl unload/load ~/Library/LaunchAgents/com.opensymphony.r1.plist"
REMOTE_R1
    
    echo "✅ R1 完成"
fi

echo ""

# ========== 5060Ti (192.168.1.100 Windows) ==========
echo "🖥️ [5060Ti] 部署 NSSM 服务..."

if ! ping -c 1 -W 2 192.168.1.100 &>/dev/null; then
    echo "❌ 5060Ti (192.168.1.100) 不可达，跳过"
else
    # 复制 NSSM 启动脚本
    $SCP_5060 "$SCRIPT_DIR/start_svc_nssm.bat" administrator@192.168.1.100:C:/Users/Administrator/symphony/scripts/start_svc_nssm.bat
    
    # 下载 NSSM（如果不存在）+ 安装服务
    $SSH_5060 bash -c 'cmd /c "C:\\Users\\Administrator\\symphony\\scripts\\install_nssm.bat"' 2>/dev/null || {
        # 如果 bash 不可用，直接用 cmd
        $SSH_5060 cmd /c "C:\\Users\\Administrator\\symphony\\scripts\\install_nssm.bat" 2>/dev/null || true
    }
    
    echo "⚠️  5060Ti NSSM 需要手动安装（见下方说明）"
    echo "   如果自动安装失败，请 SSH 到 5060Ti 执行："
    echo "   1. 下载 nssm.exe 到 C:\\Tools\\"
    echo "   2. C:\\Tools\\nssm.exe install OpenSymphony C:\\Users\\Administrator\\symphony\\scripts\\start_svc_nssm.bat"
    echo "   3. C:\\Tools\\nssm.exe set OpenSymphony AppDirectory C:\\Users\\Administrator\\symphony"
    echo "   4. C:\\Tools\\nssm.exe set OpenSymphony DisplayName OpenSymphony"
    echo "   5. C:\\Tools\\nssm.exe set OpenSymphony Start SERVICE_AUTO_START"
    echo "   6. C:\\Tools\\nssm.exe set OpenSymphony AppStdout C:\\Users\\Administrator\\symphony\\logs\\stdout.log"
    echo "   7. C:\\Tools\\nssm.exe set OpenSymphony AppStderr C:\\Users\\Administrator\\symphony\\logs\\stderr.log"
    echo "   8. C:\\Tools\\nssm.exe set OpenSymphony AppRotateFiles 1"
    echo "   9. net start OpenSymphony"
fi

echo ""
echo "=== 部署完成 ==="
echo "R0 (本机): 无需配置"
echo "R1: launchd KeepAlive+RunAtLoad"
echo "5060Ti: NSSM 服务（手动或自动安装）"
