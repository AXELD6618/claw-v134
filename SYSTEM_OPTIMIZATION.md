# V13.4 系统资源优化方案

## 核心策略：云端为主，本地为辅

V13.4采用**云端优先架构**：所有数据采集、因子计算、圣杯选股在GitHub Actions云端执行。
本地WorkBuddy仅负责：拉取结果 → 展示 → 告警。

---

## 一、本地资源开销评估

| 组件 | 内存占用 | CPU占用 | 说明 |
|------|---------|---------|------|
| WorkBuddy (基础) | ~200MB | <5% | 空闲时 |
| WorkBuddy (自动化触发) | ~400MB | 10-30% | 执行自动化时 |
| local_sync.py daemon | ~50MB | <2% | 5分钟一次git pull |
| Chrome/Edge (多标签) | 500-2000MB | 5-30% | 最大开销来源 |

**优化前预估总占用: 4-6GB RAM / 20-50% CPU**
**优化后预估总占用: 1-2GB RAM / 5-15% CPU**

---

## 二、立即执行的优化措施

### 1. 关闭原有重型自动化 → 切换为薄客户端模式

原有30+自动化每15分钟触发一次，每次加载完整上下文+执行TDX查询。
切换后仅保留3个薄客户端自动化：

```
✅ 保留：[天眼] 09:25 集合竞价快照 — 改为仅拉取云端数据
✅ 保留：[天眼] 14:30 T5圣杯 — 改为从cloud_outputs/读取信号
✅ 保留：🛡️ V13.4 系统守护者 — 改为检查云端健康状态
⏸️ 暂停：所有T0/T1/T3/T4/NIGHT/BATTLE自动化 — 云端执行
⏸️ 暂停：所有M55/M70校准自动化 — 云端执行
```

### 2. Windows系统级优化

```powershell
# 关闭Windows Search索引（节省500MB-1GB内存）
Stop-Service WSearch -Force
Set-Service WSearch -StartupType Disabled

# 关闭SysMain (Superfetch)（节省200-500MB内存）
Stop-Service SysMain -Force
Set-Service SysMain -StartupType Disabled

# 设置电源计划为"高性能"（防止CPU降频）
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

# 关闭视觉效果（节省GPU/CPU）
SystemPropertiesPerformance.exe /disable
```

### 3. Chrome/Edge优化

```
- 关闭非必要标签页（保留最多3个）
- 禁用硬件加速：chrome://settings → 系统 → 关闭"使用硬件加速"
- 安装The Great Suspender扩展（自动休眠非活跃标签）
- 清除缓存：chrome://settings/clearBrowserData
```

### 4. 启动项清理

```
任务管理器 → 启动 → 禁用所有非必要启动项：
  - 微信（如不需要实时消息）
  - 企业微信
  - OneDrive
  - Steam/游戏平台
  - 各种更新助手
```

---

## 三、WorkBuddy专项优化

### 自动化瘦身（已完成）

所有管道自动化已技能化：自动化Prompt从80行压缩为1行 `Skill: v134-pipeline-t5`

### 上下文控制

- 每次自动化session保持<15轮工具调用
- 避免加载大型skill文件（>500行）
- 使用文件驱动状态传递，而非LLM上下文传递

### 磁盘清理

```powershell
# 清理WorkBuddy缓存
Remove-Item "$env:USERPROFILE\.workbuddy\Claw\data\*_cache\*" -Recurse -Force
Remove-Item "$env:USERPROFILE\.workbuddy\Claw\__pycache__" -Recurse -Force
Remove-Item "$env:USERPROFILE\.workbuddy\Claw\logs\*" -Recurse -Force

# 清理Windows临时文件
cleanmgr /sagerun:1
```

---

## 四、出差期间推荐配置

### 电脑开机时的运行模式

| 进程 | 状态 | 说明 |
|------|------|------|
| WorkBuddy | ✅ 运行 | 核心，必须保持 |
| Chrome (1个标签) | ✅ 运行 | WorkBuddy Web UI |
| 微信 | ⚠️ 可选 | 如需接收推送 |
| 其他所有应用 | ❌ 关闭 | 节省资源 |

### 资源分配优先级

```
1. WorkBuddy进程 — 最高优先级（保障自动化触发）
2. Python子进程 — 正常优先级（local_sync.py daemon）
3. Chrome — 低于正常优先级（仅UI展示）
4. 其他 — 最低优先级
```

---

## 五、监控指标

| 指标 | 正常值 | 告警值 |
|------|--------|--------|
| 内存使用 | <3GB | >6GB |
| CPU使用率 | <15% | >50% |
| 磁盘空闲 | >10GB | <5GB |
| WorkBuddy进程存活 | ✅ | ❌ |

---

## 六、应急恢复

如果电脑卡顿/资源不足：

```powershell
# 一键清理内存
Get-Process | Where-Object {$_.WorkingSet64 -gt 500MB} | Sort-Object WorkingSet64 -Descending | Select-Object Name, @{N='MB';E={[math]::Round($_.WorkingSet64/1MB)}}

# 重启WorkBuddy（保留云端数据）
taskkill /F /IM WorkBuddy.exe
Start-Process "$env:LOCALAPPDATA\Programs\WorkBuddy\WorkBuddy.exe"
```

---

**核心原则：云端干活，本地喝茶。GitHub Actions在云端全天候运行，本地电脑只是一个"遥控器"。**
