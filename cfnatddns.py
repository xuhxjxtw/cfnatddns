import subprocess
import re
import os
from datetime import datetime

# 设置程序名和日志文件名
exe_name = "cmd_tray-HKG.exe"
log_file = "cfnat_log.txt"

# 正则匹配 IPv4 和 IPv6
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b([a-fA-F0-9:]{2,})\b")

# 打开 EXE 程序
proc = subprocess.Popen(
    [exe_name],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
    universal_newlines=True
)

# 开始实时监控并记录日志
with open(log_file, "a", encoding="utf-8") as log:
    log.write(f"\n\n--- 日志开始于 {datetime.now()} ---\n")
    for line in proc.stdout:
        line = line.strip()
        ipv4s = ipv4_pattern.findall(line)
        ipv6s = ipv6_pattern.findall(line)
        if ipv4s or ipv6s:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.write(f"[{timestamp}] {line}\n")
            log.flush()
        print(line)

# 提示：请确保 cmd_tray-HKG.exe 在同一目录下且为可执行文件。
