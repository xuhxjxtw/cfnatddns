import subprocess
import re
import yaml
import requests
import ipaddress
import os
import shutil
import tempfile
import sys
import atexit
import signal
import threading
from PIL import Image
import pystray
from pystray import MenuItem as item
import win32gui
import win32con
import win32console

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"
current_ip = None

# -------------------- 清理旧的 _MEIxxxx 临时目录 --------------------
def cleanup_mei_dirs():
    temp_dir = tempfile.gettempdir()
    current_dir = getattr(sys, '_MEIPASS', None)

    for item in os.listdir(temp_dir):
        path = os.path.join(temp_dir, item)
        if item.startswith("_MEI") and os.path.isdir(path):
            if current_dir and os.path.abspath(path) == os.path.abspath(current_dir):
                continue
            try:
                shutil.rmtree(path)
                print(f"[清理] 删除残留: {path}")
            except Exception as e:
                print(f"[跳过] 删除失败 {path}: {e}")

cleanup_mei_dirs()

# -------------------- Ctrl+C 信号处理 --------------------
def signal_handler(sig, frame):
    print("\n[退出] 收到中断信号，正在退出...")
    try:
        proc.terminate()
    except Exception:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# -------------------- 读取配置 --------------------
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"[错误] 配置读取失败: {e}")
    exit(1)

cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")
sync_count = config.get("sync_count", 1)

# -------------------- IP 工具 --------------------
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

# -------------------- IP 缓存初始化与保存 --------------------
ip_cache = {"A": [], "AAAA": []}

def load_ip_log():
    if not os.path.exists(log_file):
        return
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            ip = line.strip()
            rtype = get_ip_type(ip)
            if rtype and ip not in ip_cache[rtype]:
                ip_cache[rtype].append(ip)
    ip_cache["A"] = ip_cache["A"][:sync_count]
    ip_cache["AAAA"] = ip_cache["AAAA"][:sync_count]

def save_ip_log():
    with open(log_file, "w", encoding="utf-8") as f:
        for rtype in ["A", "AAAA"]:
            for ip in ip_cache[rtype]:
                f.write(ip + "\n")

load_ip_log()

# -------------------- Cloudflare 同步函数 --------------------
def update_cf_dns(ip):
    record_type = get_ip_type(ip)
    if not record_type:
        print(f"[跳过] 非法 IP 地址: {ip}")
        return

    other_type = "AAAA" if record_type == "A" else "A"

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"

    # 删除其他类型记录
    try:
        del_params = {"type": other_type, "name": cf_record_name}
        del_resp = requests.get(url, headers=headers, params=del_params)
        del_result = del_resp.json()
        if del_result.get("success"):
            for record in del_result.get("result", []):
                record_id = record["id"]
                del_url = f"{url}/{record_id}"
                requests.delete(del_url, headers=headers)
                print(f"[清除] 已删除旧 {other_type} 记录: {record['content']}")
    except Exception as e:
        print(f"[{other_type}] 删除异常: {e}")

    # 当前类型记录，先获取现有
    try:
        params = {"type": record_type, "name": cf_record_name}
        resp = requests.get(url, headers=headers, params=params)
        result = resp.json()
        existing = result.get("result", []) if result.get("success") else []

        existing_ips = {r["content"]: r["id"] for r in existing}
        desired_ips = ip_cache[record_type]

        # 删除不在列表里的旧 IP
        for ip_val, r_id in existing_ips.items():
            if ip_val not in desired_ips:
                requests.delete(f"{url}/{r_id}", headers=headers)
                print(f"[同步] 删除多余 {record_type} IP: {ip_val}")

        # 添加或更新期望 IP
        for ip_val in desired_ips:
            if ip_val in existing_ips:
                continue
            data = {
                "type": record_type,
                "name": cf_record_name,
                "content": ip_val,
                "ttl": 1,
                "proxied": False
            }
            create_resp = requests.post(url, headers=headers, json=data)
            create_result = create_resp.json()
            if create_result.get("success"):
                print(f"[同步] 添加 {record_type} IP 成功: {ip_val}")
            else:
                print(f"[同步] 添加 {record_type} IP 失败: {create_result}")
    except Exception as e:
        print(f"[{record_type}] 同步异常: {e}")

# -------------------- 启动 cfnat 子进程 --------------------
args = [exe_name]
optional_args = {
    "colo": "-colo=",
    "port": "-port=",
    "addr": "-addr=",
    "ips": "-ips=",
    "delay": "-delay=",
    "ipnum": "-ipnum=",
    "num": "-num=",
    "task": "-task="
}
for key, flag in optional_args.items():
    value = config.get(key)
    if value is not None:
        args.append(f"{flag}{value}")

try:
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1
    )
except Exception as e:
    print(f"[错误] 启动失败: {e}")
    exit(1)

# -------------------- 托盘图标控制台 --------------------
console_hwnd = win32console.GetConsoleWindow()

def toggle_console():
    if win32gui.IsWindowVisible(console_hwnd):
        win32gui.ShowWindow(console_hwnd, win32con.SW_HIDE)
    else:
        win32gui.ShowWindow(console_hwnd, win32con.SW_SHOW)

def on_show_hide(icon, item): toggle_console()
def on_exit(icon, item):
    icon.stop()
    try:
        proc.terminate()
    except Exception: pass
    os._exit(0)

def tray_icon():
    try:
        image = Image.open("icon.ico")
    except Exception as e:
        print(f"[错误] 无法加载托盘图标: {e}")
        return

    menu = (item('显示/隐藏', on_show_hide), item('退出', on_exit))
    icon = pystray.Icon("cfnat", image, "cfnat", menu)
    icon.run()

tray_thread = threading.Thread(target=tray_icon, daemon=True)
tray_thread.start()

# -------------------- 实时监听输出并更新 IP --------------------
for line in proc.stdout:
    line = line.strip()
    print(line)

    if "最佳" in line or "best" in line.lower():
        ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
        for ip in ips:
            if ":" in ip and ip.count(":") == 2 and ip.replace(":", "").isdigit():
                continue
            rtype = get_ip_type(ip)
            if rtype and ip not in ip_cache[rtype]:
                ip_cache[rtype].insert(0, ip)
                ip_cache[rtype] = ip_cache[rtype][:sync_count]
                save_ip_log()
                print(f"[更新] 检测到新 {rtype} IP: {ip}")
                update_cf_dns(ip)
