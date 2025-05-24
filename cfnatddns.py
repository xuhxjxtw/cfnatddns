import subprocess
import re
import yaml
import os
from datetime import datetime

exe_name = "cfnat-windows-amd64.exe"
config_file = "config.yaml"
log_file = "cfnat_log.txt"

ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

# 加载参数
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        arg_list = config.get("args", [])
except Exception as e:
    print(f"配置文件加载失败: {e}")
    exit(1)

# 检查可执行文件
if not os.path.exists(exe_name):
    print(f"{exe_name} 不存在")
    exit(1)

# 启动进程
try:
    proc = subprocess.Popen(
        [exe_name] + arg_list,
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

# 实时读取并筛选输出
for line in proc.stdout:
    line = line.strip()

    # 打印含 IP 的行
    if re.search(ipv4_pattern, line) or re.search(ipv6_pattern, line):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {line}")

    # 日志只保存“选择最佳连接”
    if "选择最佳连接" in line:
        with open(log_file, "w", encoding="utf-8") as log:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.write(f"[{timestamp}] {line}\n")
