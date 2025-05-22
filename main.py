import socket
import threading
import requests
import time
import logging

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 从配置文件读取端口与域名映射
CONFIG_FILE = 'config.txt'

# 你的 Cloudflare API Token、Zone ID
CF_API_TOKEN = 'your_token_here'
CF_ZONE_ID = 'your_zone_id_here'
CF_API_BASE = f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records"

# 存储端口对应配置
domain_map = {}

# 读取配置文件
def load_config():
    with open(CONFIG_FILE, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 3:
                continue
            port, domain, rtype = parts
            domain_map[int(port)] = {'domain': domain, 'type': rtype.upper()}

# 更新 Cloudflare DNS 记录
def update_cf(domain, ip, rtype):
    headers = {
        'Authorization': f'Bearer {CF_API_TOKEN}',
        'Content-Type': 'application/json'
    }
    params = {
        'name': domain,
        'match': 'all'
    }
    try:
        r = requests.get(CF_API_BASE, headers=headers, params=params)
        records = r.json()
        for rec in records['result']:
            if rec['type'] == rtype:
                record_id = rec['id']
                data = {
                    'type': rtype,
                    'name': domain,
                    'content': ip,
                    'ttl': 1,
                    'proxied': False
                }
                res = requests.put(f"{CF_API_BASE}/{record_id}", headers=headers, json=data)
                if res.status_code == 200:
                    logging.info(f"[{domain}] DNS 更新成功: {ip}")
                else:
                    logging.warning(f"[{domain}] 更新失败: {res.text}")
                return
        logging.warning(f"[{domain}] 找不到匹配的记录")
    except Exception as e:
        logging.error(f"[{domain}] Cloudflare 请求失败: {e}")

# 监听端口线程
def listen_on_port(port, domain, rtype):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', port))
    sock.listen(5)
    logging.info(f"监听中: 127.0.0.1:{port} → {domain} ({rtype})")

    while True:
        conn, addr = sock.accept()
        data = conn.recv(1024).decode().strip()
        conn.close()
        if data:
            logging.info(f"收到来自端口 {port} 的 IP: {data}")
            update_cf(domain, data, rtype)

# 主入口
if __name__ == '__main__':
    load_config()
    for port, info in domain_map.items():
        t = threading.Thread(target=listen_on_port, args=(port, info['domain'], info['type']))
        t.daemon = True
        t.start()

    logging.info("所有端口监听线程已启动，程序常驻运行中...")
    while True:
        time.sleep(3600)
