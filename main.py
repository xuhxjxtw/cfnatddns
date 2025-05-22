import sys
import json
import threading
import socket
import requests
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import pystray
from PIL import Image, ImageDraw

CONFIG_FILE = 'config.json'

def get_public_ip(ip_version='ipv4'):
    urls = {
        'ipv4': 'https://api.ipify.org',
        'ipv6': 'https://api6.ipify.org'
    }
    try:
        return requests.get(urls[ip_version], timeout=5).text
    except Exception as e:
        return f"获取公网 {ip_version} 地址失败: {e}"

def update_dns_record(cf_config, ip, ip_version='A', log_func=None):
    headers = {
        'Authorization': f"Bearer {cf_config.get('api_token') or cf_config.get('api_key')}",
        'Content-Type': 'application/json'
    }
    zone_id = cf_config['zone_id']
    record_name = cf_config['record_name']

    url_get = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type={ip_version}&name={record_name}"
    try:
        r = requests.get(url_get, headers=headers, timeout=10)
        r.raise_for_status()
        records = r.json()
        if not records['success'] or len(records['result']) == 0:
            msg = f"未找到 DNS 记录 {record_name} 类型 {ip_version}"
            if log_func:
                log_func(msg)
            return
        record = records['result'][0]
        record_id = record['id']
        current_ip = record['content']
        if current_ip == ip:
            msg = f"IP 未变化，跳过更新: {ip}"
            if log_func:
                log_func(msg)
            return
        url_update = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
        data = {
            "type": ip_version,
            "name": record_name,
            "content": ip,
            "ttl": 120,
            "proxied": False
        }
        r2 = requests.put(url_update, headers=headers, json=data, timeout=10)
        r2.raise_for_status()
        if r2.json().get('success'):
            msg = f"成功更新 {record_name} {ip_version} 记录为 {ip}"
        else:
            msg = f"更新失败: {r2.text}"
        if log_func:
            log_func(msg)
    except Exception as e:
        msg = f"更新 DNS 记录失败: {e}"
        if log_func:
            log_func(msg)

def listen_on_port(name, port, cf_config, log_func=None):
    def handler():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', port))
            s.listen()
            if log_func:
                log_func(f"{name} 监听端口 {port} 已启动")
            while True:
                conn, addr = s.accept()
                with conn:
                    if log_func:
                        log_func(f"{name} 收到连接来自 {addr}")
                    ip_version = 'A' if cf_config.get('enable_ipv4', True) else 'AAAA'
                    ip_type = 'ipv4' if ip_version == 'A' else 'ipv6'
                    ip = get_public_ip(ip_type)
                    if ip and log_func:
                        log_func(f"获取公网 IP: {ip}")
                    if ip:
                        update_dns_record(cf_config, ip, ip_version, log_func)
    threading.Thread(target=handler, daemon=True).start()

class App:
    def __init__(self, root):
        self.root = root
        root.title("CF NAT DDNS 状态")
        root.geometry('600x400')

        self.text = ScrolledText(root, state='disabled')
        self.text.pack(expand=True, fill='both')

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.icon = self.create_image()
        self.tray_icon = pystray.Icon("cf_nat_ddns", self.icon, "CF NAT DDNS", self.create_menu())
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def create_image(self):
        image = Image.new('RGB', (64, 64), color='blue')
        d = ImageDraw.Draw(image)
        d.rectangle([16,16,48,48], fill='white')
        return image

    def create_menu(self):
        return pystray.Menu(
            pystray.MenuItem('显示窗口', self.show_window),
            pystray.MenuItem('退出', self.exit_app)
        )

    def log(self, msg):
        self.text.configure(state='normal')
        self.text.insert('end', msg + '\n')
        self.text.see('end')
        self.text.configure(state='disabled')

    def hide_window(self):
        self.root.withdraw()

    def show_window(self, icon=None, item=None):
        self.root.deiconify()
        self.root.after(0, self.root.focus_force)

    def exit_app(self, icon=None, item=None):
        self.tray_icon.stop()
        self.root.quit()

def main():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    root = tk.Tk()
    app = App(root)

    nodes = config.get('nodes', {})
    for name, node in nodes.items():
        port = node.get('listen_port')
        cf_config = node.get('cloudflare')
        if port and cf_config:
            listen_on_port(name, port, cf_config, app.log)

    root.mainloop()

if __name__ == '__main__':
    main()
