import sys
import json
import threading
import socket
import requests
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import pystray
from PIL import Image, ImageDraw
import os

CONFIG_FILE = 'config.json'

def get_public_ip(version='ipv4'):
    urls = {
        'ipv4': 'https://api.ipify.org',
        'ipv6': 'https://api6.ipify.org'
    }
    try:
        return requests.get(urls[version], timeout=5).text.strip()
    except:
        return None

def update_cf_dns(cf_config, ip, ip_type, log_func):
    zone_id = cf_config['zone_id']
    record_name = cf_config['record_name']
    email = cf_config['email']
    api_key = cf_config['api_key']

    headers = {
        'X-Auth-Email': email,
        'X-Auth-Key': api_key,
        'Content-Type': 'application/json'
    }

    # 查询 DNS 记录
    try:
        url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type={ip_type}&name={record_name}"
        resp = requests.get(url, headers=headers)
        data = resp.json()
        if not data['success'] or not data['result']:
            log_func(f"[{record_name}] 获取现有记录失败")
            return

        record = data['result'][0]
        record_id = record['id']

        if record['content'] == ip:
            log_func(f"[{record_name}] IP 未变化，跳过")
            return

        update_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
        payload = {
            "type": ip_type,
            "name": record_name,
            "content": ip,
            "ttl": 120,
            "proxied": False
        }
        res = requests.put(update_url, headers=headers, json=payload).json()
        if res['success']:
            log_func(f"[{record_name}] 更新成功: {ip}")
        else:
            log_func(f"[{record_name}] 更新失败: {res}")
    except Exception as e:
        log_func(f"[{record_name}] 更新异常: {e}")

def start_listener(name, port, cf_config, log_func):
    def thread_func():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.listen(5)
            log_func(f"[{name}] 监听中: 127.0.0.1:{port}")
            while True:
                conn, addr = sock.accept()
                conn.close()
                log_func(f"[{name}] 收到触发请求: {addr}")

                if cf_config.get('enable_ipv4', True):
                    ip = get_public_ip('ipv4')
                    if ip:
                        update_cf_dns(cf_config, ip, 'A', log_func)

                if cf_config.get('enable_ipv6', False):
                    ip = get_public_ip('ipv6')
                    if ip:
                        update_cf_dns(cf_config, ip, 'AAAA', log_func)
        except Exception as e:
            log_func(f"[{name}] 监听异常: {e}")
    threading.Thread(target=thread_func, daemon=True).start()

class App:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.root.title("CF NAT DDNS")
        self.root.geometry("600x400")
        self.text = ScrolledText(self.root, state='disabled')
        self.text.pack(expand=True, fill='both')
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.icon = self.make_icon()
        self.tray = pystray.Icon("cf_ddns", self.icon, "CF NAT DDNS", self.menu())
        threading.Thread(target=self.tray.run, daemon=True).start()

        self.start_all()

    def make_icon(self):
        img = Image.new('RGB', (64, 64), color='green')
        d = ImageDraw.Draw(img)
        d.rectangle([16, 16, 48, 48], fill='white')
        return img

    def menu(self):
        return pystray.Menu(
            pystray.MenuItem("打开窗口", lambda: self.root.after(0, self.show_window)),
            pystray.MenuItem("退出", self.exit_app)
        )

    def log(self, msg):
        self.text.configure(state='normal')
        self.text.insert('end', msg + '\n')
        self.text.see('end')
        self.text.configure(state='disabled')

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()

    def exit_app(self):
        self.tray.stop()
        self.root.quit()

    def start_all(self):
        nodes = self.config.get("nodes", {})
        for name, node in nodes.items():
            port = node.get("listen_port")
            cf = node.get("cloudflare")
            if port and cf:
                start_listener(name, port, cf, self.log)

def main():
    if not os.path.exists(CONFIG_FILE):
        print("找不到配置文件 config.json")
        return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    root = tk.Tk()
    app = App(root, config)
    root.mainloop()

if __name__ == '__main__':
    main()
