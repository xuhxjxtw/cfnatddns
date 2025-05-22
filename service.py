import requests
import time
import json
import socket

def get_local_ip(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            return sock.recv(1024).decode().strip()
    except Exception:
        return None

def update_cloudflare_dns(record_info, new_ip):
    url = f"https://api.cloudflare.com/client/v4/zones/{record_info['zone_id']}/dns_records/{record_info['record_id']}"
    headers = {
        "Authorization": f"Bearer {record_info['api_token']}",
        "Content-Type": "application/json"
    }
    data = {
        "type": "A",
        "name": record_info["domain"],
        "content": new_ip,
        "ttl": 1,
        "proxied": True
    }
    response = requests.put(url, headers=headers, json=data)
    return response.ok

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def load_port_config():
    with open("port_config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def run_monitor():
    config = load_config()
    port_config = load_port_config()
    interval = config.get("check_interval", 10)
    last_ips = {}

    while True:
        for item in port_config:
            port = item["port"]
            current_ip = get_local_ip(port)
            if current_ip and last_ips.get(port) != current_ip:
                print(f"[{port}] IP changed to {current_ip}, updating DNS...")
                if update_cloudflare_dns(item, current_ip):
                    print(f"[{port}] DNS updated successfully.")
                    last_ips[port] = current_ip
                else:
                    print(f"[{port}] Failed to update DNS.")
        time.sleep(interval)
