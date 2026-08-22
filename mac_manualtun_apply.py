#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mac 专用：把 GitHub 主文件应用到本机 v2rayN（含独立托管的 TUN 运行时 manualTun）。

背景：本机 TUN 由 manualTun/sing-box-tun.json + xray-relay.json 托管（不经 GUI）。
因此除更新 guiNDB.db 外，还需把规则同步进 sing-box-tun.json 并重启 sing-box。

用法（在 Mac 上）：
  python3 mac_manualtun_apply.py            # 拉主文件并应用
  python3 mac_manualtun_apply.py --file ../routing-rules.json
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import base64
from datetime import datetime
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "v2rayN"
DB = APP_SUPPORT / "guiConfigs" / "guiNDB.db"
MANUAL = APP_SUPPORT / "manualTun" / "sing-box-tun.json"
RAW_URL = ("https://raw.githubusercontent.com/422339238/"
           "v2rayn-rule-sync/main/routing-rules.json")
API_URL = ("https://api.github.com/repos/422339238/"
           "v2rayn-rule-sync/contents/routing-rules.json")

MARKER_DOMAINS = [
    "uuyc.163.com", "mofang.163.com", "nrd.nie.163.com", "gameviewer.com",
    "adl.netease.com", "gv.163.com", "fcount-api.webapp.163.com",
    "weiyun.com", "weiyun.qq.com", "tailscale.com", "tailscale.io", "ts.net",
    "bigmodel.cn", "turing.captcha.qcloud.com", "qcloud.com",
]
MARKER_IPS = ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]
MARKER_PROCS = [
    "UURemote", "WeiyunResona", "tailscale-ipn", "io.tailscale.ipn.macsys.network-extension",
]


def fetch(args):
    if args.file:
        return json.loads(Path(args.file).read_text(encoding="utf-8"))
    req = urllib.request.Request(API_URL, headers={"User-Agent": "v2rayn-rule-sync"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.load(resp)
        data = json.loads(base64.b64decode(meta["content"]).decode("utf-8"))
    except Exception:
        req = urllib.request.Request(RAW_URL, headers={"User-Agent": "v2rayn-rule-sync"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    return data


def split_procs(procs):
    names, paths = [], []
    for p in procs:
        (paths if ("/" in p or "\\" in p) else names).append(p)
    return names, paths


def master_to_singbox(rules):
    """把主文件规则转换成 sing-box direct 规则块（插入在 udp443 阻断之前）。"""
    out = []
    for r in rules:
        outbound = r.get("OutboundTag", "direct")
        if outbound != "direct":
            continue
        if r.get("Domain"):
            suffixes = [d[len("domain:"):] for d in r["Domain"] if d.startswith("domain:")]
            if suffixes:
                out.append({"outbound": "direct", "domain_suffix": suffixes})
        if r.get("Ip"):
            cidrs = [ip for ip in r["Ip"]
                     if not ip.startswith(("geoip:", "geosite:"))]
            if cidrs:
                out.append({"outbound": "direct", "ip_cidr": cidrs})  # geoip:/geosite: 由原配置的 rule_set 处理
        if r.get("Process"):
            names, paths = split_procs(r["Process"])
            if names:
                out.append({"outbound": "direct", "process_name": names})
            if paths:
                out.append({"outbound": "direct", "process_path": paths})
    return out


EMPTY_LIST_KEYS = ("domain_suffix", "ip_cidr", "process_name", "process_path",
                   "domain", "ip", "protocol", "port", "network", "inbound")


def is_managed(rule):
    """托管/异常规则：标记域名/进程/IP，或含空匹配列表（空列表=匹配一切，必须清除）。"""
    text = json.dumps(rule, ensure_ascii=False)
    for m in MARKER_DOMAINS + MARKER_IPS + MARKER_PROCS:
        if m in text:
            return True
    if "Tailscale.app" in text or "UURemote.app" in text or "WeiyunResona.app" in text:
        return True
    for key in EMPTY_LIST_KEYS:
        if isinstance(rule.get(key), list) and not rule[key]:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="本地主文件路径")
    ap.add_argument("--skip-tun-restart", action="store_true")
    args = ap.parse_args()

    rules = fetch(args)
    if not isinstance(rules, list) or not rules:
        sys.exit("主文件格式错误")
    print(f"主文件：{len(rules)} 条规则")

    # 1) 备份
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = APP_SUPPORT / "backups" / f"pi-sync-{stamp}"
    backups.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB, backups / "guiNDB.db")
    shutil.copy2(MANUAL, backups / "sing-box-tun.json")
    print(f"备份：{backups}")

    # 2) 更新数据库（活动路由）
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute("PRAGMA busy_timeout=10000")
        row = con.execute(
            "SELECT Id, Remarks FROM RoutingItem WHERE IsActive=1 ORDER BY Sort LIMIT 1"
        ).fetchone()
        if row is None:
            sys.exit("未找到活动路由规则集")
        with con:
            con.execute(
                "UPDATE RoutingItem SET RuleSet=?, RuleNum=? WHERE Id=?",
                (json.dumps(rules, ensure_ascii=False, separators=(",", ":")),
                 len(rules), row[0]),
            )
        print(f"数据库已更新 → {row[1]}（{len(rules)} 条）")
    finally:
        con.close()

    # 3) 同步 manualTun 配置
    data = json.loads(MANUAL.read_text(encoding="utf-8"))
    route_rules = data.setdefault("route", {}).setdefault("rules", [])
    new_blocks = master_to_singbox(rules)
    route_rules[:] = [r for r in route_rules if not is_managed(r)]
    # 整体去重（保留首次出现）
    seen = set()
    unique = []
    for r in route_rules:
        key = json.dumps(r, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    route_rules[:] = unique
    existing = seen
    new_blocks = [b for b in new_blocks
                  if json.dumps(b, ensure_ascii=False, sort_keys=True) not in existing]
    insert_at = next(
        (i for i, r in enumerate(route_rules)
         if r.get("action") == "reject"
         and "udp" in (r.get("network") or [])
         and 443 in (r.get("port") or [])),
        0,
    )
    route_rules[insert_at:insert_at] = new_blocks
    tmp = MANUAL.with_suffix(".json.pi-new")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(MANUAL)
    print(f"manualTun 配置已更新（规则 {len(route_rules)} 条，插入位置 {insert_at}）")

    # 4) 校验并重启 sing-box
    check = subprocess.run([str(APP_SUPPORT / "bin" / "sing_box" / "sing-box"),
                            "check", "-c", str(MANUAL)], capture_output=True, text=True)
    if check.returncode != 0:
        print("sing-box 配置校验失败：", check.stderr[-800:])
        sys.exit(2)
    if args.skip_tun_restart:
        print("完成（跳过 TUN 重启）。")
        return

    killer = APP_SUPPORT / "binConfigs" / "kill_as_sudo.sh"
    killer.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"SING_BOX=\"{APP_SUPPORT / 'bin' / 'sing_box' / 'sing-box'}\"\n"
        f"CONFIG=\"{MANUAL}\"\n"
        '/usr/bin/pkill -TERM -f "^${SING_BOX} run -c ${CONFIG}( |$)" 2>/dev/null || true\n',
        encoding="utf-8")
    killer.chmod(0o700)
    subprocess.run(["sudo", "-n", str(killer)], capture_output=True)
    killer.unlink(missing_ok=True)
    for _ in range(30):
        if not subprocess.run(["pgrep", "-f", f"sing-box run -c {MANUAL}"],
                              capture_output=True).stdout.strip():
            break
        time.sleep(0.25)

    with open(Path.home() / "Library" / "Logs" / "v2rayN-manual-tun.log", "ab") as logf:
        subprocess.Popen(
            ["sudo", "-n", str(APP_SUPPORT / "bin" / "sing_box" / "sing-box"),
             "run", "-c", str(MANUAL), "--disable-color"],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)

    for _ in range(40):
        if subprocess.run(["pgrep", "-f", f"sing-box run -c {MANUAL}"],
                          capture_output=True).stdout.strip():
            print("TUN 内核已重启。")
            break
        time.sleep(0.25)
    else:
        print("TUN 内核启动失败，请查看 ~/Library/Logs/v2rayN-manual-tun.log")
        sys.exit(3)

    # 5) 冒烟测试
    for name, url in [("UU远程", "https://uuyc.163.com/"),
                      ("腾讯微云", "https://www.weiyun.com/"),
                      ("Google", "https://www.google.com/generate_204")]:
        r = subprocess.run(["curl", "-4LsS", "-o", "/dev/null", "--max-time", "15",
                            "-w", "%{http_code}", url], capture_output=True, text=True)
        print(f"  {name}: {r.stdout or '失败'}")


if __name__ == "__main__":
    main()
