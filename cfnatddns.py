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

# Cloudflare 配置
cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")

# Telegram 配置
tg_conf = config.get("telegram", {})
tg_bot_token = tg_conf.get("bot_token")
tg_chat_id = tg_conf.get("chat_id")

# -------------------- IP 工具函数 --------------------
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

# -------------------- Telegram 通知 --------------------
def send_telegram_message(message):
    if not tg_bot_token or not tg_chat_id:
        print("[通知] 未配置 Telegram，跳过发送")
        return
    url = f"https://api.telegram.org/bot{tg_bot_token}/sendMessage"
    data = {
        "chat_id": tg_chat_id,
        "text": message
    }
    try:
        resp = requests.post(url, data=data)
        if resp.status_code != 200:
            print(f"[通知] Telegram 发送失败: {resp.text}")
        else:
            print("[通知] Telegram 消息已发送")
    except Exception as e:
        print(f"[通知] 发送失败: {e}")

# -------------------- 更新 Cloudflare DNS --------------------
def update_cf_dns(ip):
    record_type = get_ip_type(ip)
    if not record_type:
        print(f"[跳过] 非法 IP 地址: {ip}")
        return

    other_type = "AAAA" if record_type == "A" else "A"
    colo_name = config.get("colo", "未知")

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"

    # 删除旧类型记录
    try:
        del_params = {"type": other_type, "name": cf_record_name}
        del_resp = requests.get(url, headers=headers, params=del_params)
        del_result = del_resp.json()
        if del_result.get("success"):
            for record in del_result.get("result", []):
                record_id = record["id"]
                del_url = f"{url}/{record_id}"
                d = requests.delete(del_url, headers=headers)
                print(f"[清除] 删除旧 {other_type} 记录: {record['content']}")
    except Exception as e:
        print(f"[{other_type}] 删除过程异常: {e}")

    # 更新或创建当前类型记录
    try:
        params = {"type": record_type, "name": cf_record_name}
        resp = requests.get(url, headers=headers, params=params)
        result = resp.json()

        if not result.get("success"):
            print(f"[{record_type}] 查询失败: {result}")
            return

        records = result.get("result", [])
        if records:
            record_id = records[0]["id"]
            update_url = f"{url}/{record_id}"
            data = {
                "type": record_type,
                "name": cf_record_name,
                "content": ip,
                "ttl": 1,
                "proxied": False
            }
            update_resp = requests.put(update_url, headers=headers, json=data)
            if update_resp.json().get("success"):
                print(f"[{record_type}] DNS 更新成功: {ip}")
                send_telegram_message(f"{colo_name}: {ip} 已同步到 Cloudflare")
        else:
            data = {
                "type": record_type,
                "name": cf_record_name,
                "content": ip,
                "ttl": 1,
                "proxied": False
            }
            create_resp = requests.post(url, headers=headers, json=data)
            if create_resp.json().get("success"):
                print(f"[{record_type}] DNS 创建成功: {ip}")
                send_telegram_message(f"{colo_name}: {ip} 已添加到 Cloudflare")
    except Exception as e:
        print(f"[{record_type}] 更新异常: {e}")

# -------------------- 启动 cfnat 子进程 --------------------
args = [
    exe_name,
    f"-colo={config.get('colo', 'HKG')}",
    f"-port={config.get('port', 8443)}",
    f"-addr={config.get('addr', '0.0.0.0:1234')}",
    f"-ips={config.get('ips', 4)}",
    f"-delay={config.get('delay', 300)}"
]

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
    icon = pystray.Icon("cfnat", image, "cfnat", menu)
    icon.run()

tray_thread = threading.Thread(target=tray_icon, daemon=True)
tray_thread.start()

# -------------------- 实时日志解析 --------------------
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
