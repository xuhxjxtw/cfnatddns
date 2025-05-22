
import socket
import threading
import requests
import time
import os
from tkinter import messagebox
from pystray import Icon, Menu, MenuItem
from PIL import Image

CONFIG_PATH = 'config.txt'
SECRET_PATH = 'config_secret.txt'

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    config = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) == 3:
            port, domain, rtype = parts
            config.append((int(port), domain, rtype.upper()))
    return config

def load_secrets():
    with open(SECRET_PATH, 'r', encoding='utf-8') as f:
        lines = f.read().strip().splitlines()
    return lines[0], lines[1]

def update_dns(domain, ip, rtype, token, zone_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    list_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    params = {"type": rtype, "name": domain}
    r = requests.get(list_url, headers=headers, params=params)
    data = r.json()
    if not data['success'] or not data['result']:
        print(f"[Cloudflare] 记录不存在：{domain}")
        return
    record_id = data['result'][0]['id']
    update_url = f"{list_url}/{record_id}"
    record = {"type": rtype, "name": domain, "content": ip, "ttl": 60}
    r = requests.put(update_url, headers=headers, json=record)
    if r.status_code == 200:
        print(f"[Cloudflare] 更新成功：{domain} -> {ip}")
    else:
        print(f"[Cloudflare] 更新失败：{r.text}")

def listener(port, domain, rtype, token, zone_id):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', port))
    s.listen(5)
    print(f"[监听] 端口 {port} 对应域名 {domain}")
    while True:
        conn, addr = s.accept()
        ip = conn.recv(1024).decode().strip()
        print(f"[接收] {port} -> {ip}")
        update_dns(domain, ip, rtype, token, zone_id)
        conn.close()

def start_all():
    token, zone_id = load_secrets()
    config = load_config()
    for port, domain, rtype in config:
        threading.Thread(target=listener, args=(port, domain, rtype, token, zone_id), daemon=True).start()

def on_exit(icon, item):
    icon.stop()
    os._exit(0)

def on_hide(icon, item):
    import ctypes
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

def tray():
    image = Image.open("cloudflare_icon.ico")
    menu = Menu(MenuItem("隐藏控制台", on_hide), MenuItem("退出", on_exit))
    Icon("cf_ddns", image, "CF DDNS", menu).run()

if __name__ == "__main__":
    start_all()
    threading.Thread(target=tray, daemon=True).start()
    while True:
        time.sleep(1)
