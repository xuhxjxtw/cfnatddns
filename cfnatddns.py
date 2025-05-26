import subprocess
import re
import yaml
import requests
import ipaddress
import os
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
ip_cache = {"A": [], "AAAA": []}

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

# -------------------- 配置读取 --------------------
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"[错误] 配置读取失败: {e}")
    exit(1)

sync_count = config.get("sync_count", 1)
cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")

# -------------------- IP 工具函数 --------------------
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

def update_cf_dns(ip):
    record_type = get_ip_type(ip)
    if not record_type:
        print(f"[跳过] 非法 IP 地址: {ip}")
        return

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"

    # 获取当前记录
    try:
        params = {"type": record_type, "name": cf_record_name}
        resp = requests.get(url, headers=headers, params=params)
        result = resp.json()

        if not result.get("success"):
            print(f"[{record_type}] 查询 DNS 记录失败: {result}")
            return

        current_records = result.get("result", [])
        current_ips = [r["content"] for r in current_records]

        # 删除多余的记录
        for record in current_records:
            if record["content"] not in ip_cache[record_type]:
                del_url = f"{url}/{record['id']}"
                requests.delete(del_url, headers=headers)
                print(f"[清理] 删除 Cloudflare 上旧 IP: {record['content']}")

        # 添加新 IP（如果不在 Cloudflare 上）
        for cached_ip in ip_cache[record_type]:
            if cached_ip not in current_ips:
                create_data = {
                    "type": record_type,
                    "name": cf_record_name,
                    "content": cached_ip,
                    "ttl": 1,
                    "proxied": False
                }
                resp = requests.post(url, headers=headers, json=create_data)
                result = resp.json()
                if result.get("success"):
                    print(f"[{record_type}] 添加成功: {cached_ip}")
                else:
                    print(f"[{record_type}] 添加失败: {result}")
    except Exception as e:
        print(f"[{record_type}] 同步异常: {e}")

# -------------------- IP 缓存加载 --------------------
def load_ip_cache():
    if not os.path.exists(log_file):
        return
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            ip = line.strip()
            rtype = get_ip_type(ip)
            if rtype and ip not in ip_cache[rtype]:
                ip_cache[rtype].append(ip)

load_ip_cache()

# -------------------- 托盘图标 --------------------
console_hwnd = win32console.GetConsoleWindow()

def toggle_console():
    if win32gui.IsWindowVisible(console_hwnd):
        win32gui.ShowWindow(console_hwnd, win32con.SW_HIDE)
    else:
        win32gui.ShowWindow(console_hwnd, win32con.SW_SHOW)

def on_show_hide(icon, item): toggle_console()
def on_exit(icon, item):
    icon.stop()
    try: proc.terminate()
    except: pass
    os._exit(0)

def tray_icon():
    try:
        image = Image.open("icon.ico")
    except Exception as e:
        print(f"[错误] 无法加载托盘图标: {e}")
        return
    menu = (item('显示/隐藏', on_show_hide), item('控制台退出', on_exit))
    icon = pystray.Icon("cfnat", image, "cfnat", menu)
    icon.run()

threading.Thread(target=tray_icon, daemon=True).start()

# -------------------- 启动子进程 --------------------
args = [exe_name]
optional_args = {
    "colo": "-colo=", "port": "-port=", "addr": "-addr=", "ips": "-ips=",
    "delay": "-delay=", "ipnum": "-ipnum=", "num": "-num=", "task": "-task="
}
for key, flag in optional_args.items():
    value = config.get(key)
    if value is not None:
        args.append(f"{flag}{value}")

try:
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="ignore", bufsize=1)
except Exception as e:
    print(f"[错误] 启动失败: {e}")
    exit(1)

# -------------------- 实时日志监控 --------------------
def save_ip_log():
    with open(log_file, "w", encoding="utf-8") as f:
        for rtype in ["A", "AAAA"]:
            for ip in ip_cache[rtype]:
                f.write(ip + "\n")

for line in proc.stdout:
    line = line.strip()
    print(line)
    if "最佳" in line or "best" in line.lower():
        ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
        for ip in ips:
            rtype = get_ip_type(ip)
            if not rtype:
                continue
            if ip not in ip_cache[rtype]:
                ip_cache[rtype].insert(0, ip)
                ip_cache[rtype] = ip_cache[rtype][:sync_count]
                print(f"[更新] 新 IP 加入缓存: {ip}")
                save_ip_log()
                update_cf_dns(ip)
