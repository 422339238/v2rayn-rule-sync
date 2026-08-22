# v2rayn-rule-sync

v2rayN 路由规则统一来源仓库：**在一个地方改规则，所有设备统一使用**。

## 原理

- `routing-rules.json` 是本仓库里唯一的规则主文件（v2rayN 路由规则格式，即 v2rayN「路由设置 → 导出规则」的文件格式）。
- 所有设备（Windows / macOS 的 v2rayN）从一个同一地址拉取：`https://raw.githubusercontent.com/422339238/v2rayn-rule-sync/main/routing-rules.json`
- 当前主文件内容：`V4-绕过大陆(Whitelist)` 规则集（11 条），包含：
  - UU远程 / 腾讯微云直连（域名 + 进程名，macOS + Windows）
  - Tailscale 直连（域名 + IP 段 + 进程名，macOS + Windows 进程名变体）
  - GLM抢单直连（bigmodel.cn / qcloud.com）
  - 阻断 udp443、代理 Google、绕过局域网 IP/域名、绕过中国公共 DNS IP/域名、绕过中国 IP/域名

## 设备应用（两选一）

### 方法一：v2rayN 内置「导入规则(URL)」——推荐，无需脚本

在每台设备上：

1. v2rayN → 设置 → 路由设置
2. 选中规则集 → 设置 → **导入规则(URL)**
3. 粘贴上面 raw URL → 下载 → 选「全部替换」→ 确定

v2rayN 会自动更新数据库并重载内核，规则即生效。

### 方法二：同步脚本（可自动化）

需要 Python 3（Windows/macOS 均可；v2rayN 需已安装）：

```bash
# 拉取主文件并应用到本机（自动退出/重启 v2rayN，先备份数据库）
python apply_rule.py

# 断网时用本地文件
python apply_rule.py --file routing-rules.json
```

数据库自动备份在 `guiConfigs/pi-sync-backups/`。

## 修改规则的流程（一次修改，处处生效）

1. 在任意一台设备的 v2rayN GUI 里：路由设置 → 修改/新增规则
2. 路由设置 → 导出规则（文件）→ 得到规则列表 JSON
3. 用该文件覆盖本仓库的 `routing-rules.json`，`git push`
4. 其他设备按上面「设备应用」拉取一次即可

也可以直接编辑本仓库的 JSON（每项含 `Id`、`OutboundTag`、`Domain`/`Ip`/`Process`、`Remarks` 等字段）。

## 关于服务器节点（如果你也想统一）

- 节点建议改用 **v2rayN 订阅**：只维护一个订阅地址，所有设备填同一个订阅 URL，v2rayN 自动更新。
- 手工节点：在来源设备上右键导出分享链接，目标设备「导入剪贴板 / 扫描二维码」。

## 回滚

- 脚本方式：每台设备应用前自动备份 `guiNDB-<时间>.db`。
- GUI 方式：v2rayN 路由设置里也可手动改回 / 重新导入旧版本文件。
