import subprocess
import re
import yaml
import requests
import ipaddress
import os
import sys
import threading
import signal

import tkinter as tk
from PIL import Image, ImageTk
from pystray import Icon, MenuItem, Menu

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

current_ip = None
proc = None

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

def update_cf_dns(ip, cf_conf):
    record_type = get_ip_type(ip)
    if not record_type:
        print(f"[跳过] 无效 IP 地址: {ip}")
        return

    headers = {
        "X-Auth-Email": cf_conf.get("email"),
        "X-Auth-Key": cf_conf.get("api_key"),
        "Content-Type": "application/json"
    }

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_conf.get('zone_id')}/dns_records"
    params = {"type": record_type, "name": cf_conf.get("record_name")}

    try:
        resp = requests.get(url, headers=headers, params=params)
        result = resp.json()

        if not result.get("success"):
            print(f"[{record_type}] 查询 DNS 记录失败: {result}")
            return

        records = result.get("result", [])
        if not records:
            print(f"[{record_type}] 找不到 DNS 记录: {cf_conf.get('record_name')}")
            return

        record_id = records[0]["id"]

        update_url = f"{url}/{record_id}"
        data = {
            "type": record_type,
            "name": cf_conf.get("record_name"),
            "content": ip,
            "ttl": 120,
            "proxied": False
        }

        update_resp = requests.put(update_url, headers=headers, json=data)
        update_result = update_resp.json()

        if update_result.get("success"):
            print(f"[{record_type}] Cloudflare DNS 已更新: {ip}")
        else:
            print(f"[{record_type}] DNS 更新失败: {update_result}")

    except Exception as e:
        print(f"[{record_type}] 更新异常: {e}")

def start_process(cf_conf):
    global current_ip, proc
    args = [
        exe_name,
        f"-colo={cf_conf.get('colo', 'HKG')}",
        f"-port={cf_conf.get('port', 8443)}",
        f"-addr={cf_conf.get('addr', '0.0.0.0:1236')}",
        f"-ips={cf_conf.get('ips', 6)}",
        f"-delay={cf_conf.get('delay', 300)}"
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
        print(f"启动失败: {e}")
        return

    for line in proc.stdout:
        line = line.strip()
        print(line)
        if "最佳" in line or "best" in line.lower():
            ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
            for ip in ips:
                if ip != current_ip:
                    with open(log_file, "w", encoding="utf-8") as log:
                        log.write(ip + "\n")
                    current_ip = ip
                    update_cf_dns(ip, cf_conf)

def on_exit(icon, item):
    if proc:
        proc.terminate()
    icon.stop()
    root.quit()

def toggle_window(icon, item):
    if root.state() == 'normal':
        root.withdraw()
    else:
        root.deiconify()

# 读取配置
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"读取配置失败: {e}")
    sys.exit(1)

cf_conf = config.get("cloudflare", {})

# 设置 GUI 隐藏窗口 + 托盘图标
root = tk.Tk()
root.withdraw()
root.title("CFNAT Monitor")
root.protocol("WM_DELETE_WINDOW", lambda: on_exit(icon, None))

image = Image.open("icon.ico")
icon = Icon("cfnat", image, "CFNAT", menu=Menu(
    MenuItem("显示/隐藏", toggle_window),
    MenuItem("退出", on_exit)
))

# 启动托盘图标
threading.Thread(target=icon.run, daemon=True).start()

# 启动主逻辑
threading.Thread(target=start_process, args=(cf_conf,), daemon=True).start()

# 保持窗口主循环
root.mainloop()
