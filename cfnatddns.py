import subprocess
import re
import yaml
import requests
import ipaddress
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import sys
import os

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")
current_ip = None

try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    tk.messagebox.showerror("错误", f"配置读取失败: {e}")
    sys.exit(1)

cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")

def get_ip_type(ip):
    try:
        return "A" if ipaddress.ip_address(ip).version == 4 else "AAAA"
    except ValueError:
        return None

def update_cf_dns(ip):
    record_type = get_ip_type(ip)
    if not record_type:
        return
    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }
    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"
    params = {"type": record_type, "name": cf_record_name}
    try:
        resp = requests.get(url, headers=headers, params=params).json()
        if not resp.get("success"): return
        record_id = resp["result"][0]["id"]
        data = {
            "type": record_type,
            "name": cf_record_name,
            "content": ip,
            "ttl": 120,
            "proxied": False
        }
        requests.put(f"{url}/{record_id}", headers=headers, json=data)
    except Exception:
        pass

def run_process():
    global current_ip
    args = [
        exe_name,
        f"-colo={config.get('colo', 'HKG')}",
        f"-port={config.get('port', 8443)}",
        f"-addr={config.get('addr', '0.0.0.0:1236')}",
        f"-ips={config.get('ips', 6)}",
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
        for line in proc.stdout:
            if not line.strip():
                continue
            log_to_window(line.strip())
            if "最佳" in line or "best" in line.lower():
                ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
                for ip in ips:
                    if ":" in ip and ip.count(":") == 2 and ip.replace(":", "").isdigit():
                        continue
                    if ip != current_ip:
                        with open(log_file, "w", encoding="utf-8") as log:
                            log.write(ip + "\n")
                        current_ip = ip
                        log_to_window(f"[更新] 检测到新 IP: {ip}")
                        update_cf_dns(ip)
    except Exception as e:
        log_to_window(f"[错误] 启动失败: {e}")

def log_to_window(text):
    log_text.configure(state="normal")
    log_text.insert("end", text + "\n")
    log_text.see("end")
    log_text.configure(state="disabled")

def create_image():
    image = Image.new('RGB', (64, 64), color=(50, 150, 255))
    d = ImageDraw.Draw(image)
    d.rectangle((16, 16, 48, 48), fill="white")
    return image

def quit_app(icon, item):
    icon.stop()
    os._exit(0)

def show_window(icon, item):
    root.after(0, root.deiconify)

def hide_window():
    root.withdraw()
    icon = pystray.Icon("cfnat", create_image(), "CF NAT", menu=(
        item("打开窗口", show_window),
        item("退出", quit_app)
    ))
    threading.Thread(target=icon.run, daemon=True).start()

# GUI 初始化
root = tk.Tk()
root.title("CF NAT 日志监控")
root.geometry("600x400")
root.protocol("WM_DELETE_WINDOW", hide_window)

log_text = ScrolledText(root, state="disabled", wrap="word")
log_text.pack(fill="both", expand=True, padx=10, pady=10)

btn = tk.Button(root, text="最小化到托盘", command=hide_window)
btn.pack(pady=5)

# 启动后台线程
threading.Thread(target=run_process, daemon=True).start()

root.mainloop()
