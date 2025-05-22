import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import threading
import json
import socket
import requests
import time
import os
from pystray import Icon, Menu, MenuItem
from PIL import Image

CONFIG_FILE = 'config.json'

class App:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.root.title("CF DDNS Listener")
        self.root.geometry("500x300")
        self.text = ScrolledText(self.root, state='disabled')
        self.text.pack(expand=True, fill='both')
        self.icon = None
        self.start_all()
        self.setup_tray()

    def log(self, message):
        self.text.configure(state='normal')
        self.text.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
        self.text.configure(state='disabled')
        self.text.see(tk.END)

    def start_all(self):
        nodes = self.config.get("nodes", {})
        for name, conf in nodes.items():
            port = conf.get("listen_port")
            threading.Thread(target=self.listen, args=(name, port, conf), daemon=True).start()
            self.log(f"[{name}] 监听中: 127.0.0.1:{port}")

    def listen(self, name, port, conf):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.listen(5)
        while True:
            conn, addr = s.accept()
            conn.close()
            self.log(f"[{name}] 收到请求: {addr}")
            threading.Thread(target=self.update_cf, args=(name, conf), daemon=True).start()

    def update_cf(self, name, conf):
        ip = self.get_public_ip(conf)
        if not ip:
            self.log(f"[{name}] 获取公网 IP 失败")
            return

        headers = {
            "X-Auth-Email": conf["cloudflare"]["email"],
            "X-Auth-Key": conf["cloudflare"]["api_key"],
            "Content-Type": "application/json"
        }

        data = {
            "type": "A",
            "name": conf["cloudflare"]["record_name"],
            "content": ip,
            "ttl": 120,
            "proxied": False
        }

        zone_id = conf["cloudflare"]["zone_id"]
        record_name = conf["cloudflare"]["record_name"]

        try:
            # 获取 record_id
            res = requests.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records", headers=headers)
            recs = res.json().get("result", [])
            record_id = next((r["id"] for r in recs if r["name"] == record_name), None)
            if not record_id:
                self.log(f"[{name}] 找不到 DNS 记录: {record_name}")
                return

            # 更新
            res = requests.put(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}",
                headers=headers,
                json=data
            )

            if res.json().get("success"):
                self.log(f"[{name}] 更新成功: {ip}")
            else:
                self.log(f"[{name}] 更新失败: {res.text}")

        except Exception as e:
            self.log(f"[{name}] 异常: {e}")

    def get_public_ip(self, conf):
        if conf["cloudflare"].get("enable_ipv4", True):
            try:
                return requests.get("https://api.ipify.org").text
            except:
                return None
        return None

    def setup_tray(self):
        if os.path.exists("icon.ico"):
            image = Image.open("icon.ico")
        else:
            image = Image.new("RGB", (64, 64), "blue")
        menu = Menu(MenuItem("显示", self.show_window), MenuItem("退出", self.quit_app))
        self.icon = Icon("CFTray", image, "CF DDNS", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon, item):
        self.root.after(0, lambda: self.root.deiconify())

    def quit_app(self, icon, item):
        icon.stop()
        self.root.after(0, self.root.destroy)

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

if __name__ == "__main__":
    root = tk.Tk()
    config = load_config()
    app = App(root, config)
    root.mainloop()
