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

# -------------------- 脚本退出清理 --------------------
def cleanup_on_exit():
    if os.path.exists(log_file):
        try:
            os.remove(log_file)
            print("[清理] 已删除日志文件")
        except Exception as e:
            print(f"[清理] 删除日志文件失败: {e}")

atexit.register(cleanup_on_exit)

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

cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")

ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

# -------------------- Cloudflare 同步函数 --------------------
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

    try:
        # 获取所有当前域名的记录（所有类型）
        params_all = {"name": cf_record_name}
        resp_all = requests.get(url, headers=headers, params=params_all)
        result_all = resp_all.json()

        if not result_all.get("success"):
            print(f"[查询] 获取记录失败: {result_all}")
            return

        records = result_all.get("result", [])
        found = False

        # 删除所有该域名下非当前类型的记录 + 当前类型的旧 IP
        for record in records:
            r_type = record["type"]
            r_content = record["content"]
            r_id = record["id"]
            if r_type == record_type:
                if r_content == ip:
                    found = True
                    continue
            del_url = f"{url}/{r_id}"
            try:
                requests.delete(del_url, headers=headers)
                print(f"[清除] 删除 {r_type} 记录: {r_content}")
            except Exception as e:
                print(f"[清除] 删除失败: {e}")

        if found:
            print(f"[{record_type}] 当前 IP 已存在，无需更新: {ip}")
            return

        # 添加新记录
        create_data = {
            "type": record_type,
            "name": cf_record_name,
            "content": ip,
            "ttl": 1,
            "proxied": False
        }
        create_resp = requests.post(url, headers=headers, json=create_data)
        create_result = create_resp.json()
        if create_result.get("success"):
            print(f"[同步] 添加 {record_type} IP 成功: {ip}")
        else:
            print(f"[同步] 添加 {record_type} IP 失败: {create_result}")

    except Exception as e:
        print(f"[{record_type}] 更新过程异常: {e}")

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

# -------------------- 系统托盘图标 --------------------
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
    tray_title = os.path.basename(sys.argv[0])
    icon = pystray.Icon("cfnat", image, tray_title, menu)
    icon.run()

tray_thread = threading.Thread(target=tray_icon, daemon=True)
tray_thread.start()

# -------------------- 实时日志监控 --------------------
for line in proc.stdout:
    line = line.strip()
    print(line)

    if "最佳" in line or "best" in line.lower():
        ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
        for ip in ips:
            if ":" in ip and ip.count(":") == 2 and ip.replace(":", "").isdigit():
                continue
            if ip != current_ip:
                with open(log_file, "w", encoding="utf-8") as log:
                    log.write(ip + "\n")
                current_ip = ip
                print(f"[更新] 检测到新 IP: {ip}")
                update_cf_dns(ip)
