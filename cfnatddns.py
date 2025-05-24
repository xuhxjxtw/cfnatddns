import subprocess
import re
import yaml
import requests

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

# 匹配 IPv4 和 IPv6
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

# 当前已记录的 IP
current_ip = None

# 读取配置
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"读取配置失败: {e}")
    exit(1)

# Cloudflare 配置
cf_config = config.get("cloudflare", {})
cf_email = cf_config.get("email")
cf_api_key = cf_config.get("api_key")
cf_zone_id = cf_config.get("zone_id")
cf_record_name = cf_config.get("record_name")

def update_cloudflare_dns(ip, record_type):
    """将 IP 同步到 Cloudflare DNS"""
    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    # 查找 DNS record ID
    list_url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records?type={record_type}&name={cf_record_name}"
    resp = requests.get(list_url, headers=headers).json()

    if not resp["success"] or not resp["result"]:
        print(f"[{record_type}] 获取 DNS 记录失败")
        return False

    record_id = resp["result"][0]["id"]

    # 更新记录
    update_url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records/{record_id}"
    data = {
        "type": record_type,
        "name": cf_record_name,
        "content": ip,
        "ttl": 120,
        "proxied": False
    }

    update_resp = requests.put(update_url, headers=headers, json=data).json()
    if update_resp["success"]:
        print(f"[{record_type}] Cloudflare DNS 更新成功: {ip}")
        return True
    else:
        print(f"[{record_type}] Cloudflare DNS 更新失败:", update_resp)
        return False

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
        ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
        for ip in ips:
            if ip != current_ip:
                current_ip = ip
                record_type = "A" if ipv4_pattern.fullmatch(ip) else "AAAA"
                sync_success = False

                if cf_email and cf_api_key and cf_zone_id and cf_record_name:
                    sync_success = update_cloudflare_dns(ip, record_type)

                # 写日志，第一行是 IP，第二行是状态
                with open(log_file, "w", encoding="utf-8") as log:
                    log.write(ip + "\n")
                    if sync_success:
                        log.write("Cloudflare 已同步\n")
                    else:
                        log.write("Cloudflare 同步失败\n")
