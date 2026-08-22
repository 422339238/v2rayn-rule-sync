#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2rayN 路由规则同步脚本（Windows / macOS 通用）

主文件：本仓库 routing-rules.json（GitHub 上的唯一来源）

用法：
  python apply_rule.py                    # 从 GitHub 拉主文件并应用到本机 v2rayN
  python apply_rule.py --file x.json      # 用本地文件应用
  python apply_rule.py --db <guiNDB.db>   # 指定数据库位置（自动探测时无需）
  python apply_rule.py --no-restart       # 应用后不重启 v2rayN
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
from datetime import datetime
from pathlib import Path

RAW_URL = ("https://raw.githubusercontent.com/422339238/"
           "v2rayn-rule-sync/main/routing-rules.json")
API_URL = ("https://api.github.com/repos/422339238/"
           "v2rayn-rule-sync/contents/routing-rules.json")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def find_db():
    home = Path.home()
    candidates = []
    if sys.platform.startswith("win"):
        candidates = [
            home / "v2rayN" / "guiConfigs" / "guiNDB.db",
            Path(os.environ.get("APPDATA", "")) / "v2rayN" / "guiConfigs" / "guiNDB.db",
            Path(os.environ.get("LOCALAPPDATA", "")) / "v2rayN" / "guiConfigs" / "guiNDB.db",
            Path.cwd() / "guiConfigs" / "guiNDB.db",
        ]
    else:
        candidates = [
            home / "Library" / "Application Support" / "v2rayN" / "guiConfigs" / "guiNDB.db",
            Path.cwd() / "guiConfigs" / "guiNDB.db",
        ]
    for p in candidates:
        if p.is_file():
            return p
    print("未找到 guiNDB.db，请用 --db 参数指定路径")
    sys.exit(2)


def is_running():
    if sys.platform.startswith("win"):
        out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
        return "v2rayN.exe" in out
    out = subprocess.run(["pgrep", "-x", "v2rayN"], capture_output=True, text=True).stdout
    return bool(out.strip())


def task_exists():
    """Windows 上 v2rayN 通常由计划任务启动。"""
    if not sys.platform.startswith("win"):
        return False
    r = subprocess.run(["schtasks", "/query", "/tn", "v2rayN"],
                       capture_output=True, text=True)
    return r.returncode == 0


def quit_v2rayn():
    if not is_running():
        return True
    if sys.platform.startswith("win"):
        if task_exists():
            subprocess.run(["schtasks", "/end", "/tn", "v2rayN"],
                           capture_output=True, text=True)
            time.sleep(1)
        subprocess.run(["taskkill", "/IM", "v2rayN.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["osascript", "-e", 'tell application "v2rayN" to quit'],
                       capture_output=True)
        time.sleep(2)
        subprocess.run(["pkill", "-TERM", "-x", "v2rayN"], capture_output=True)
    for _ in range(40):
        if not is_running():
            return True
        time.sleep(0.5)
    print("警告：v2rayN 未能完全退出，请手动结束后重试")
    return False


def start_v2rayn(db_path):
    if sys.platform.startswith("win"):
        if task_exists():
            subprocess.run(["schtasks", "/run", "/tn", "v2rayN"],
                           capture_output=True, text=True)
            return True
        exe = db_path.parents[1] / "v2rayN.exe"
        if exe.is_file():
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
            return True
        print("未找到 v2rayN.exe，请手动启动")
        return False
    subprocess.Popen(["open", "-a", "/Applications/v2rayN.app"])
    return True


def fetch_rules(args):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return json.load(f)
    print("从 GitHub 拉取主文件 ...")
    try:
        # API 永远最新（避免 raw 缓存延迟）
        req = urllib.request.Request(API_URL, headers={"User-Agent": "v2rayn-rule-sync"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.load(resp)
        import base64
        data = json.loads(base64.b64decode(meta["content"]).decode("utf-8"))
    except Exception:
        req = urllib.request.Request(RAW_URL, headers={"User-Agent": "v2rayn-rule-sync"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    print(f"拉取成功，共 {len(data)} 条规则")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="本地主文件路径（代替 GitHub 拉取）")
    ap.add_argument("--db", help="guiNDB.db 路径")
    ap.add_argument("--no-restart", action="store_true", help="应用后不重启 v2rayN")
    args = ap.parse_args()

    rules = fetch_rules(args)
    if not isinstance(rules, list) or not rules:
        sys.exit("主文件格式错误：应为规则列表且非空")
    if not all(isinstance(r, dict) and r.get("OutboundTag") for r in rules):
        sys.exit("主文件格式错误：规则缺少 OutboundTag")

    db = Path(args.db) if args.db else find_db()
    print(f"v2rayN 数据库：{db}")

    was_running = is_running()
    if was_running:
        print("正在退出 v2rayN ...")
        if not quit_v2rayn():
            sys.exit(3)

    backup_dir = db.parent / "pi-sync-backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"guiNDB-{stamp}.db"
    shutil.copy2(db, backup)
    print(f"已备份：{backup}")

    con = sqlite3.connect(db, timeout=10)
    try:
        con.execute("PRAGMA busy_timeout=10000")
        row = con.execute(
            "SELECT Id, Remarks FROM RoutingItem WHERE IsActive=1 ORDER BY Sort LIMIT 1"
        ).fetchone()
        if row is None:
            sys.exit("未找到活动路由规则集")
        route_id, route_remarks = row
        payload = json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
        with con:
            con.execute(
                "UPDATE RoutingItem SET RuleSet=?, RuleNum=? WHERE Id=?",
                (payload, len(rules), route_id),
            )
    finally:
        con.close()
    print(f"已应用 {len(rules)} 条规则 → {route_remarks}")

    if was_running and not args.no_restart:
        print("正在重启 v2rayN ...")
        start_v2rayn(db)
    print("完成。")


if __name__ == "__main__":
    main()
