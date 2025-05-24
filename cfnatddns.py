import subprocess
import re
import yaml
import requests
import ipaddress
import threading
import os
import sys
import pystray
from PIL import Image
import win32gui
import win32con

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

# 匹配 IPv4 和 IPv6 地址
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

current_ip = None

# 读取配置文件
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
    params = {"type": record_type, "name": cf_record_name}

    try:
        resp = requests.get(url, headers=headers, params=params)
        result = resp.json()

        if not result.get("success"):
            print(f"[{record_type}] 查询 DNS 记录失败: {result}")
            return

        records = result.get("result", [])
        if not records:
            print(f"[{record_type}] 找不到 DNS 记录: {cf_record_name}")
            return

        record_id = records[0]["id"]

        update_url = f"{url}/{record_id}"
        data = {
            "type": record_type,
            "name": cf_record_name,
            "content": ip,
            "ttl": 120,
            "proxied": False
        }

        update_resp = requests.put(update_url, headers=headers, json=data)
        update_result = update_resp.json()

        if update_result.get("success"):
            print(f"[{record_type}] Cloudflare DNS 更新成功: {ip}")
        else:
            print(f"[{record_type}] Cloudflare DNS 更新失败: {update_result}")

    except Exception as e:
        print(f"[{record_type}] 更新过程异常: {e}")

# 获取主窗口句柄
def find_main_window(title):
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd) and title in win32gui.GetWindowText(hwnd):
            extra.append(hwnd)
    hwnds = []
    win32gui.EnumWindows(callback, hwnds)
    return hwnds[0] if hwnds else None

def hide_window(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

def show_window(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

def tray_thread(hwnd):
    icon_path = os.path.join(os.path.dirname(sys.argv[0]), "icon.ico")
    image = Image.open(icon_path)

    def on_toggle(icon, item):
        if win32gui.IsWindowVisible(hwnd):
            hide_window(hwnd)
        else:
            show_window(hwnd)

    def on_exit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("显示/隐藏窗口", on_toggle),
        pystray.MenuItem("退出", on_exit)
    )
    tray_icon = pystray.Icon("cfnat", image, "cfnat", menu)
    tray_icon.run()

# 启动进程
try:
    proc = subprocess.Popen(
        [
            exe_name,
            f"-colo={config.get('colo', 'HKG')}",
            f"-port={config.get('port', 8443)}",
            f"-addr={config.get('addr', '0.0.0.0:1236')}",
            f"-ips={config.get('ips', 6)}",
            f"-delay={config.get('delay', 300)}"
        ],
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

# 托盘线程启动
window_title = "python"  # 或你自己脚本运行时的窗口标题
main_hwnd = find_main_window(window_title)
if main_hwnd:
    threading.Thread(target=tray_thread, args=(main_hwnd,), daemon=True).start()

# 实时输出读取
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
