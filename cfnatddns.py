import subprocess
import re
from datetime import datetime

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"

# IPv4 和 IPv6 正则匹配
ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ipv6_pattern = re.compile(r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b")

try:
    proc = subprocess.Popen(
        [exe_name],
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

with open(log_file, "a", encoding="utf-8") as log:
    log.write(f"\n\n--- 日志开始于 {datetime.now()} ---\n")
    for line in proc.stdout:
        line = line.strip()
        print(line)  # 全部输出打印到终端

        # 只写入包含“选择最佳连接”的行
        if "选择最佳连接" in line:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {line}\n"
            log.write(log_entry)
            log.flush()
