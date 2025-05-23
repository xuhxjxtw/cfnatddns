import os
import sys
import json
import socket
import threading
import time
import requests
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

CONFIG_PATH = 'config.json'
IP_CHECK_INTERVAL = 300  # 秒（5 分钟）

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def get_public_ip(ipv6=False):
    try:
        url = 'https://api64.ipify.org' if ipv6 else 'https://api.ipify.org'
        return requests.get(url, timeout=10).text.strip()
    except Exception as e:
        logging.warning(f'获取 {"IPv6" if ipv6 else "IPv4"} 地址失败: {e}')
        return None

class CloudflareAPI:
    def __init__(self, config):
        self.zone_id = config['zone_id']
        self.record_id_v4 = config.get('record_id_v4')
        self.record_id_v6 = config.get('record_id_v6')
        self.record_name = config['record_name']
        self.api_token = config['api_token']
        self.enable_ipv4 = config.get('enable_ipv4', True)
        self.enable_ipv6 = config.get('enable_ipv6', False)

    def update_dns_record(self, ip, record_type):
        record_id = self.record_id_v4 if record_type == 'A' else self.record_id_v6
        if not record_id:
            return

        url = f'https://api.cloudflare.com/client/v4/zones/{self.zone_id}/dns_records/{record_id}'
        headers = {
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        }
        data = {
            'type': record_type,
            'name': self.record_name,
            'content': ip,
            'ttl': 300,
            'proxied': False
        }

        try:
            resp = requests.put(url, headers=headers, json=data, timeout=10)
            if resp.ok:
                logging.info(f'{self.record_name} ({record_type}) 更新为 {ip}')
            else:
                logging.warning(f'{self.record_name} ({record_type}) 更新失败: {resp.text}')
        except Exception as e:
            logging.warning(f'更新 {record_type} 记录异常: {e}')

class DNSUpdateMonitor(threading.Thread):
    def __init__(self, nodes):
        super().__init__(daemon=True)
        self.nodes = nodes
        self.last_ip_v4 = None
        self.last_ip_v6 = None

    def run(self):
        while True:
            ip_v4 = get_public_ip()
            ip_v6 = get_public_ip(ipv6=True)

            for name, node in self.nodes.items():
                cf_cfg = node.get('cloudflare')
                if not cf_cfg:
                    continue

                api = CloudflareAPI(cf_cfg)

                if api.enable_ipv4 and ip_v4 and ip_v4 != self.last_ip_v4:
                    api.update_dns_record(ip_v4, 'A')

                if api.enable_ipv6 and ip_v6 and ip_v6 != self.last_ip_v6:
                    api.update_dns_record(ip_v6, 'AAAA')

            self.last_ip_v4 = ip_v4
            self.last_ip_v6 = ip_v6
            time.sleep(IP_CHECK_INTERVAL)

# 示例 HTTP 代理（简化）
class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"代理正常运行中")

def start_http_proxy(port):
    server = HTTPServer(('0.0.0.0', port), ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info(f'HTTP 代理启动，监听端口 {port}')

def main():
    config = load_config()
    nodes = config.get('nodes', {})

    # 启动每个节点的本地代理
    for name, node in nodes.items():
        port = node.get('listen_port', 8080)
        start_http_proxy(port)

    # 启动 Cloudflare DDNS 线程
    DNSUpdateMonitor(nodes).start()

    # 主线程保持运行
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == '__main__':
    main()
