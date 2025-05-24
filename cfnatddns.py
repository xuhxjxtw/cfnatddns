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

# 从 config.yaml 读取启动参数
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        arg_list = config.get("args", [])
except Exception as e:
    print(f"配置文件加载失败: {e}")
    exit(1)

# 启动程序
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

# 实时监控输出
for line in proc.stdout:
    line = line.strip()
    ipv4s = ipv4_pattern.findall(line)
    ipv6s = ipv6_pattern.findall(line)

    # 控制台完整打印
    if ipv4s or ipv6s:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {line}")

    # 如果是选择最佳连接，覆盖写入日志（只保留最后一条）
    if "选择最佳连接" in line:
        with open(log_file, "w", encoding="utf-8") as log:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {line}\n"
            log.write(log_entry)
