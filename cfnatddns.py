import subprocess
import re
import yaml
import os
from datetime import datetime

exe_name = "cfnat-windows-amd64.exe"
config_file = "config.yaml"
log_file = "cfnat_log.txt"
full_log_file = "cfnat_full_log.txt"

ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        arg_list = config.get("args", [])
except Exception as e:
    print(f"配置文件加载失败: {e}")
    input("按回车退出...")
    exit(1)

if not os.path.exists(exe_name):
    print(f"{exe_name} 不存在")
    input("按回车退出...")
    exit(1)

print("启动命令:", " ".join([exe_name] + arg_list))

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
    input("按回车退出...")
    exit(1)

with open(full_log_file, "a", encoding="utf-8") as full_log:
    for line in proc.stdout:
        line = line.strip()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full_log.write(f"[{timestamp}] {line}\n")
        full_log.flush()

        print(f"[{timestamp}] {line}")

        if "选择最佳连接" in line:
            with open(log_file, "w", encoding="utf-8") as log:
                log.write(f"[{timestamp}] {line}\n")
