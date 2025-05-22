import socket
import threading
import requests
import time
import logging
import sys
import os

# 设置日志输出（控制台和文件）
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("cfddns.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

CONFIG_FILE = 'config.txt'
CF_API_TOKEN = 'your_token_here'
CF_ZONE_ID = 'your_zone_id_here'
CF_API_BASE = f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records"

domain_map = {}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logging.error("找不到 config.txt 配置文件")
        sys.exit(1)

    with open(CONFIG_FILE, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 3:
                continue
            port, domain, rtype = parts
            domain_map[int(port)] = {'domain': domain, 'type': rtype.upper()}


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
        logging.warning(f"[{domain}] 没找到匹配的记录")
    except Exception as e:
        logging.error(f"[{domain}] 请求异常: {e}")


def listen_on_port(port, domain, rtype):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', port))
        sock.listen(5)
        logging.info(f"监听端口 {port} 成功: {domain} ({rtype})")
    except Exception as e:
        logging.error(f"监听端口 {port} 失败: {e}")
        return

    while True:
        try:
            conn, _ = sock.accept()
            data = conn.recv(1024).decode().strip()
            conn.close()
            if data:
                logging.info(f"接收到 {port} 上的 IP: {data}")
                update_cf(domain, data, rtype)
        except Exception as e:
            logging.warning(f"端口 {port} 异常: {e}")


if __name__ == '__main__':
    load_config()
    for port, info in domain_map.items():
        t = threading.Thread(target=listen_on_port, args=(port, info['domain'], info['type']))
        t.daemon = True
        t.start()

    logging.info("所有端口已开始监听，程序常驻运行中...")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("程序被中断退出")
