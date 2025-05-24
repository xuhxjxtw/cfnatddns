import subprocess
import shlex
import yaml
import os
from datetime import datetime

config_file = "config.yaml"
log_file = "cfnat_log.txt"
debug_log = "debug_log.txt"

ipv4_pattern = r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
ipv6_pattern = r"\b(?:[a-fA-F0-9]{1,4}:){2,7}[a-fA-F0-9]{1,4}\b"

# 加载 YAML 配置
try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        cmd_line = config.get("cmd", "").strip()
        if not cmd_line:
            raise ValueError("cmd 配置为空")
except Exception as e:
    print(f"配置文件加载失败: {e}")
    with open(debug_log, "a", encoding="utf-8") as dbg:
        dbg.write(f"[{datetime.now()}] 配置加载失败: {e}\n")
    exit(1)

# 拆分命令为列表
cmd_args = shlex.split(cmd_line)

# 日志初始化
with open(debug_log, "a", encoding="utf-8") as dbg:
    dbg.write(f"\n\n[{datetime.now()}] 启动命令: {cmd_line}\n")

# 启动子进程
try:
    proc = subprocess.Popen(
        cmd_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1
    )
except Exception as e:
    print(f"启动失败: {e}")
    with open(debug_log, "a", encoding="utf-8") as dbg:
        dbg.write(f"[{datetime.now()}] 启动失败: {e}\n")
    exit(1)

# 处理输出
for line in proc.stdout:
    line = line.strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 打印含 IP 的信息
    if re.search(ipv4_pattern, line) or re.search(ipv6_pattern, line):
        print(f"[{timestamp}] {line}")

    # 记录完整日志
    with open(debug_log, "a", encoding="utf-8") as dbg:
        dbg.write(f"[{timestamp}] {line}\n")

    # 保存最佳连接记录
    if "选择最佳连接" in line:
        with open(log_file, "w", encoding="utf-8") as log:
            log.write(f"[{timestamp}] {line}\n")
