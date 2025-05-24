import subprocess
from datetime import datetime

exe_name = "cfnat-windows-amd64.exe"
log_file = "cfnat_log.txt"

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
    log.write(f"\n--- 日志开始于 {datetime.now()} ---\n")
    for line in proc.stdout:
        if "选择最佳连接" in line:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {line}"
            log.write(log_entry)
            log.flush()
            print(log_entry, end="")
