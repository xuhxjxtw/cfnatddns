import subprocess
import re
import yaml
import requests
import time
import os

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

# 匹配 IPv4 和 IPv6 地址（不含端口）
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"?([a-fA-F0-9:]+)?")

# 当前已记录的 IP
current_ip = None

# 读取配置
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"读取配置失败: {e}")
    exit(1)

# Cloudflare 信息
cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")

def update_cf_dns(ip):
    if not (cf_email and cf_api_key and cf_zone_id and cf_record_name):
        print("Cloudflare 配置不完整，跳过 DNS 更新。")
        return

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    # 判断记录类型
    record_type = "A" if ":" not in ip else "AAAA"

    # 查询现有记录
    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records?name={cf_record_name}&type={record_type}"
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        result = r.json()
        records = result.get("result", [])
        record_id = records[0]["id"] if records else None
    except Exception as e:
        print(f"[{record_type}] 查询 DNS 记录失败: {e}")
        return

    # 准备更新请求
    data = {
        "type": record_type,
        "name": cf_record_name,
        "content": ip,
        "ttl": 120,
        "proxied": False
    }

    if record_id:
        update_url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records/{record_id}"
        method = requests.put
    else:
        update_url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"
        method = requests.post

    try:
        resp = method(update_url, headers=headers, json=data)
        result = resp.json()
        if result.get("success"):
            print(f"[{record_type}] Cloudflare DNS 已更新: {ip}")
        else:
            print(f"[{record_type}] DNS 更新失败: {result}")
    except Exception as e:
        print(f"[{record_type}] DNS 更新请求失败: {e}")

# 构造参数
args = [
    exe_name,
    f"-colo={config.get('colo', 'HKG')}",
    f"-port={config.get('port', 8443)}",
    f"-addr={config.get('addr', '0.0.0.0:1236')}",
    f"-ips={config.get('ips', 6)}",
    f"-delay={config.get('delay', 300)}"
]

# 启动进程
try:
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1
    )
except Exception as e:
    print(f"启动失败: {e}")
    exit(1)

# 实时监控输出
for line in proc.stdout:
    line = line.strip()
    print(line)

    if "最佳" in line or "best" in line.lower():
        raw_ips = ipv4_pattern.findall(line) + [match[0] for match in ipv6_pattern.findall(line)]
        cleaned_ips = []

        for ip in raw_ips:
            if ":" in ip:
                ip = ip.strip("[]").split("]:")[0] if "]" in ip else ip.split(":")
                if isinstance(ip, list) and len(ip) > 1:
                    ip = ":".join(ip[:-1])
            cleaned_ips.append(ip)

        for ip in cleaned_ips:
            if ip != current_ip:
                with open(log_file, "w", encoding="utf-8") as log:
                    log.write(ip + "\n")
                current_ip = ip
                update_cf_dns(ip)
