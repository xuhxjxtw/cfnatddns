import json
import requests
import time
import socket
import threading
from pathlib import Path

CONFIG_PATH = Path("config.json")
GET_IPV4_URL = "https://4.ipw.cn"
GET_IPV6_URL = "https://6.ipw.cn"
UPDATE_INTERVAL = 300  # 每5分钟

def get_local_ip():
    """尝试获取内网IP"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip

def get_public_ip(ipv6=False):
    try:
        url = GET_IPV6_URL if ipv6 else GET_IPV4_URL
        resp = requests.get(url, timeout=5)
        return resp.text.strip()
    except Exception as e:
        print(f"获取 {'IPv6' if ipv6 else 'IPv4'} 失败: {e}")
        return None

def update_dns_record(email, api_key, zone_id, record_name, ip, record_type):
    headers = {
        "X-Auth-Email": email,
        "X-Auth-Key": api_key,
        "Content-Type": "application/json",
    }
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    # 获取记录ID
    records = requests.get(url, headers=headers).json()
    record_id = None
    for r in records.get("result", []):
        if r["name"] == record_name and r["type"] == record_type:
            record_id = r["id"]
            break
    if not record_id:
        print(f"找不到记录: {record_name} ({record_type})")
        return
    data = {
        "type": record_type,
        "name": record_name,
        "content": ip,
        "ttl": 120,
        "proxied": False
    }
    r = requests.put(f"{url}/{record_id}", headers=headers, json=data)
    print(f"更新 {record_type} {record_name} 为 {ip}: {r.status_code}")

def worker(node_name, config):
    port = config["listen_port"]
    cf = config["cloudflare"]
    ipv4_enabled = cf.get("enable_ipv4", True)
    ipv6_enabled = cf.get("enable_ipv6", False)
    while True:
        local_ip = get_local_ip()
        print(f"[{node_name}] 本地IP: {local_ip}:{port}")

        if ipv4_enabled:
            ip4 = get_public_ip(False)
            if ip4:
                update_dns_record(cf["email"], cf["api_key"], cf["zone_id"], cf["record_name"], ip4, "A")

        if ipv6_enabled:
            ip6 = get_public_ip(True)
            if ip6:
                update_dns_record(cf["email"], cf["api_key"], cf["zone_id"], cf["record_name"], ip6, "AAAA")

        time.sleep(UPDATE_INTERVAL)

def main():
    if not CONFIG_PATH.exists():
        print("缺少 config.json 文件")
        return
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    nodes = config.get("nodes", {})
    for name, cfg in nodes.items():
        threading.Thread(target=worker, args=(name, cfg), daemon=True).start()

    # 保持主线程运行
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
