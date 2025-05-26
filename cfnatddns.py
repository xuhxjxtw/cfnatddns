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

# -------------------- 配置读取 --------------------
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"[错误] 配置读取失败: {e}")
    exit(1)

# Cloudflare 配置
cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")
sync_count = config.get("sync_count", 2)

# -------------------- IP 工具函数 --------------------
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

def load_ip_log():
    ip_dict = {"A": [], "AAAA": []}
    if not os.path.exists(log_file):
        return ip_dict
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            ip = line.strip()
            record_type = get_ip_type(ip)
            if record_type:
                ip_dict[record_type].append(ip)
    return ip_dict

def save_ip_log(ip_dict):
    with open(log_file, "w", encoding="utf-8") as f:
        for ip in ip_dict["A"] + ip_dict["AAAA"]:
            f.write(ip + "\n")

def update_cf_dns(ip):
    record_type = get_ip_type(ip)
    if not record_type:
        print(f"[跳过] 非法 IP: {ip}")
        return

    ip_dict = load_ip_log()
    if ip in ip_dict[record_type]:
        print(f"[跳过] {record_type} IP 已存在: {ip}")
        return

    ip_dict[record_type].append(ip)
    if len(ip_dict[record_type]) > sync_count:
        removed = ip_dict[record_type].pop(0)
        print(f"[日志] 超出数量，移除最旧 IP: {removed}")

    save_ip_log(ip_dict)

    # Cloudflare 同步
    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"

    try:
        # 删除原有记录
        resp = requests.get(url, headers=headers, params={"type": record_type, "name": cf_record_name})
        for record in resp.json().get("result", []):
            requests.delete(f"{url}/{record['id']}", headers=headers)

        # 添加当前 IP 列表
        for ip_item in ip_dict[record_type]:
            data = {
                "type": record_type,
                "name": cf_record_name,
                "content": ip_item,
                "ttl": 1,
                "proxied": False
            }
            resp = requests.post(url, headers=headers, json=data)
            if resp.json().get("success"):
                print(f"[{record_type}] 添加成功: {ip_item}")
            else:
                print(f"[{record_type}] 添加失败: {resp.json()}")
    except Exception as e:
        print(f"[{record_type}] Cloudflare 更新异常: {e}")

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

# -------------------- 托盘图标 --------------------
console_hwnd = win32console.GetConsoleWindow()

def toggle_console():
    if win32gui.IsWindowVisible(console_hwnd):
        win32gui.ShowWindow(console_hwnd, win32con.SW_HIDE)
    else:
        win32gui.ShowWindow(console_hwnd, win32con.SW_SHOW)

def on_show_hide(icon, item):
    toggle_console()

def on_exit(icon, item):
    icon.stop()
    try:
        proc.terminate()
    except Exception:
        pass
    os._exit(0)

def tray_icon():
    try:
        image = Image.open("icon.ico")
    except Exception as e:
        print(f"[错误] 无法加载托盘图标: {e}")
        return

    menu = (
        item('显示/隐藏', on_show_hide),
        item('控制台退出', on_exit)
    )
    icon = pystray.Icon("cfnat", image, "cfnat", menu)
    icon.run()

tray_thread = threading.Thread(target=tray_icon, daemon=True)
tray_thread.start()

# -------------------- 实时日志处理 --------------------
for line in proc.stdout:
    line = line.strip()
    print(line)

    if "最佳" in line or "best" in line.lower():
        ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
        for ip in ips:
            if ":" in ip and ip.count(":") == 2 and ip.replace(":", "").isdigit():
                continue
            update_cf_dns(ip)
