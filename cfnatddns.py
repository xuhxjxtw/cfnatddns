import subprocess
import re
from datetime import datetime

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"

ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

try:
    proc = subprocess.Popen(
        [exe_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",      # 强制使用 UTF-8 解码
        errors="ignore",       # 忽略非法字符
        bufsize=1
    )
except Exception as e:
    print(f"启动失败: {e}")
    exit(1)

with open(log_file, "a", encoding="utf-8") as log:
    log.write(f"\n\n--- 日志开始于 {datetime.now()} ---\n")
    for line in proc.stdout:
        line = line.strip()
        ipv4s = ipv4_pattern.findall(line)
        ipv6s = ipv6_pattern.findall(line)

        if ipv4s or ipv6s:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {line}\n"
            log.write(log_entry)
            log.flush()
            print(log_entry, end="")
