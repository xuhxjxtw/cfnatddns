import sys
import json
import threading
import socket
import requests
from PyQt5 import QtWidgets, QtGui
from PyQt5.QtWidgets import QSystemTrayIcon, QMenu, QAction

CONFIG_FILE = 'config.json'

def get_public_ip(ip_version='ipv4'):
    urls = {
        'ipv4': 'https://api.ipify.org',
        'ipv6': 'https://api6.ipify.org'
    }
    try:
        return requests.get(urls[ip_version], timeout=5).text
    except Exception as e:
        print(f"获取公网 {ip_version} 地址失败: {e}")
        return None

def update_dns_record(cf_config, ip, ip_version='A'):
    import time
    headers = {
        'Authorization': f"Bearer {cf_config.get('api_token') or cf_config.get('api_key')}",
        'Content-Type': 'application/json'
    }
    zone_id = cf_config['zone_id']
    record_name = cf_config['record_name']
    # 先获取 DNS 记录 ID
    url_get = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type={ip_version}&name={record_name}"
    try:
        r = requests.get(url_get, headers=headers, timeout=10)
        r.raise_for_status()
        records = r.json()
        if not records['success'] or len(records['result']) == 0:
            print(f"未找到 DNS 记录 {record_name} 类型 {ip_version}")
            return
        record = records['result'][0]
        record_id = record['id']
        current_ip = record['content']
        if current_ip == ip:
            print(f"IP 未变化，跳过更新: {ip}")
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
            print(f"成功更新 {record_name} {ip_version} 记录为 {ip}")
        else:
            print(f"更新失败: {r2.text}")
    except Exception as e:
        print(f"更新 DNS 记录失败: {e}")

def listen_on_port(name, port, cf_config):
    def handler():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', port))
            s.listen()
            print(f"{name} 监听端口 {port} 已启动")
            while True:
                conn, addr = s.accept()
                with conn:
                    print(f"{name} 收到连接来自 {addr}")
                    ip_version = 'A' if cf_config.get('enable_ipv4', True) else 'AAAA'
                    ip_type = 'ipv4' if ip_version == 'A' else 'ipv6'
                    ip = get_public_ip(ip_type)
                    if ip:
                        update_dns_record(cf_config, ip, ip_version)
    threading.Thread(target=handler, daemon=True).start()

class SystemTrayApp(QtWidgets.QSystemTrayIcon):
    def __init__(self, icon, parent=None):
        super(SystemTrayApp, self).__init__(icon, parent)
        self.setToolTip('CF NAT DDNS')
        menu = QMenu(parent)
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(QtWidgets.qApp.quit)
        menu.addAction(exit_action)
        self.setContextMenu(menu)

def main():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    nodes = config.get('nodes', {})
    for name, node in nodes.items():
        port = node.get('listen_port')
        cf_config = node.get('cloudflare')
        if port and cf_config:
            listen_on_port(name, port, cf_config)
    app = QtWidgets.QApplication(sys.argv)
    tray_icon = SystemTrayApp(QtGui.QIcon("icon.ico"))
    tray_icon.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
