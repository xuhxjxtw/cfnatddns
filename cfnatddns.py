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

# 构造参数
args = [
    exe_name,
    f"-colo={config.get('colo', 'HKG')}",
    f"-port={config.get('port', 8443)}",
    f"-addr={config.get('addr', '0.0.0.0:1236')}",
    f"-ips={config.get('ips', 6)}",
    f"-delay={config.get('delay', 300)}"
]

# Cloudflare 配置
cf_config = config.get("cloudflare", {})
cf_email = cf_config.get("email")
cf_api_key = cf_config.get("api_key")
cf_zone_id = cf_config.get("zone_id")
cf_record_name = cf_config.get("record_name")

def update_cf_dns(ip):
    if not all([cf_email, cf_api_key, cf_zone_id, cf_record_name]):
        print("Cloudflare 配置不完整，跳过同步")
        return

    record_type = "A" if ":" not in ip else "AAAA"

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    try:
        # 查询记录 ID
        r = requests.get(
            f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records",
            headers=headers,
            params={"type": record_type, "name": cf_record_name}
        )
        result = r.json()
        if not result["success"] or not result["result"]:
            print(f"[{record_type}] 查询 DNS 记录失败: {result.get('errors')}")
            return

        record_id = result["result"][0]["id"]

        # 更新记录
        data = {
            "type": record_type,
            "name": cf_record_name,
            "content": ip,
            "ttl": 120,
            "proxied": False
        }
        r = requests.put(
            f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records/{record_id}",
            headers=headers,
            json=data
        )
        if r.status_code == 200 and r.json().get("success"):
            print(f"[{record_type}] Cloudflare DNS 已更新为: {ip}")
        else:
            print(f"[{record_type}] DNS 更新失败: {r.text}")
    except Exception as e:
        print(f"[{record_type}] 更新 DNS 出错: {e}")

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
    print(line)  # 控制台完整输出
    if "最佳" in line or "best" in line.lower():
        ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
        for ip in ips:
            if ip != current_ip:
                # 覆盖写入最新 IP
                with open(log_file, "w", encoding="utf-8") as log:
                    log.write(ip + "\n")
                current_ip = ip
                update_cf_dns(ip)
