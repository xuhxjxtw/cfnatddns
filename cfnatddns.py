import subprocess
import re
import yaml
import requests
import ipaddress

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

# 提取 IPv4 和 IPv6（包括带中括号和端口的 IPv6 地址）
ip_extract_pattern = re.compile(r"(?:(?P<ipv6>[a-fA-F0-9:]+)|\b(?P<ipv4>(?:\d{1,3}\.){3}\d{1,3})\b)")

current_ip = None

# 读取配置
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"读取配置失败: {e}")
    exit(1)

# Cloudflare 配置
cf_conf = config.get("cloudflare", {})
cf_email = cf_conf.get("email")
cf_api_key = cf_conf.get("api_key")
cf_zone_id = cf_conf.get("zone_id")
cf_record_name = cf_conf.get("record_name")

def get_ip_type(ip):
    try:
        ip_obj = ipaddress.ip_address(ip)
        return "A" if ip_obj.version == 4 else "AAAA"
    except ValueError:
        return None

def update_cf_dns(ip):
    record_type = get_ip_type(ip)
    if not record_type:
        print(f"[跳过] 无效 IP 地址: {ip}")
        return

    headers = {
        "X-Auth-Email": cf_email,
        "X-Auth-Key": cf_api_key,
        "Content-Type": "application/json"
    }

    url = f"https://api.cloudflare.com/client/v4/zones/{cf_zone_id}/dns_records"
    params = {"type": record_type, "name": cf_record_name}

    try:
        resp = requests.get(url, headers=headers, params=params)
        result = resp.json()

        if not result.get("success"):
            print(f"[{record_type}] 查询 DNS 记录失败: {result}")
            return

        records = result.get("result", [])
        if not records:
            print(f"[{record_type}] 找不到 DNS 记录: {cf_record_name}")
            return

        record_id = records[0]["id"]

        update_url = f"{url}/{record_id}"
        data = {
            "type": record_type,
            "name": cf_record_name,
            "content": ip,
            "ttl": 120,
            "proxied": False
        }

        update_resp = requests.put(update_url, headers=headers, json=data)
        update_result = update_resp.json()

        if update_result.get("success"):
            print(f"[{record_type}] Cloudflare DNS 已更新: {ip}")
        else:
            print(f"[{record_type}] DNS 更新失败: {update_result}")

    except Exception as e:
        print(f"[{record_type}] 更新异常: {e}")

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
        # 提取 IP
        matches = ip_extract_pattern.finditer(line)
        for match in matches:
            ip = match.group("ipv6") or match.group("ipv4")
            if ip and ip != current_ip:
                try:
                    # 确保是合法 IP（过滤无效数据）
                    ipaddress.ip_address(ip)
                    with open(log_file, "w", encoding="utf-8") as log:
                        log.write(ip + "\n")
                    current_ip = ip
                    update_cf_dns(ip)
                except ValueError:
                    continue
