import subprocess
import re
import yaml

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"
config_file = "config.yaml"

# 匹配 IPv4 和 IPv6
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

# 已记录地址
logged_ips = set()

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

# 只记录新出现的 IP 地址（无其他内容）
with open(log_file, "a", encoding="utf-8") as log:
    for line in proc.stdout:
        line = line.strip()
        if "最佳" in line or "best" in line.lower():
            ips = ipv4_pattern.findall(line) + ipv6_pattern.findall(line)
            for ip in ips:
                if ip not in logged_ips:
                    log.write(ip + "\n")
                    log.flush()
                    print(ip)
                    logged_ips.add(ip)
