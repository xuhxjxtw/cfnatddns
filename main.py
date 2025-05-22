
import socketserver
import threading
import requests
import json
import os
import re

CONFIG_PATH = "config.txt"

def load_config():
    config = {}
    if not os.path.exists(CONFIG_PATH):
        print("配置文件不存在")
        return config
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                config[k.strip()] = v.strip()
    return config

def extract_ip(data, record_type):
    try:
        json_obj = json.loads(data)
        ip = json_obj.get("best") or json_obj.get("ip")
        if ip:
            return ip
    except:
        pass
    if record_type == "A":
        match = re.search(r"(\d{1,3}\.){3}\d{1,3}", data)
    else:
        match = re.search(r"([0-9a-fA-F:]{2,})", data)
    return match.group(0) if match else None

def update_cloudflare(zone_id, record_id, dns_name, ip, record_type, token):
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    body = {
        "type": record_type,
        "name": dns_name,
        "content": ip,
        "ttl": 120,
        "proxied": False
    }
    response = requests.put(url, headers=headers, json=body)
    print(f"[{dns_name}] [{record_type}] => {ip} 上传结果: {response.status_code}, {response.text}")

class CFHandler(socketserver.BaseRequestHandler):
    def handle(self):
        port = self.server.server_address[1]
        data = self.request.recv(2048).decode("utf-8").strip()
        print(f"[{port}] 收到数据: {data}")
        config = load_config()
        prefix = f"PORT_{port}_"
        try:
            zone_id = config[prefix + "ZONE_ID"]
            record_id = config[prefix + "RECORD_ID"]
            dns_name = config[prefix + "DNS_NAME"]
            record_type = config.get(prefix + "TYPE", "AAAA")
            token = config["API_TOKEN"]
            ip = extract_ip(data, record_type)
            if ip:
                update_cloudflare(zone_id, record_id, dns_name, ip, record_type, token)
            else:
                print(f"[{port}] 无有效 IP")
        except KeyError as e:
            print(f"[{port}] 缺少配置项: {e}")

def start_listener(ports):
    for port in ports:
        server = socketserver.ThreadingTCPServer(("127.0.0.1", port), CFHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"监听 127.0.0.1:{port}")

if __name__ == "__main__":
    config = load_config()
    ports = [int(k.split("_")[1]) for k in config if k.startswith("PORT_") and k.endswith("_ZONE_ID")]
    if not ports:
        print("未在 config.txt 中找到端口配置")
    else:
        print("开始监听...")
        start_listener(ports)
        input("按 Enter 键退出...")
