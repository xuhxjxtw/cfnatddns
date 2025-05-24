import subprocess
import os
import time
import datetime

# --- 配置部分 ---
LAUNCHER_NAME = "cmd_tray-HKG.exe"
IP_FILE_NAME = "ips-v4"
LOG_FILE_NAME = "cfnat_ips.log"
MAX_WAIT_SECONDS = 180  # 最多等待 180 秒生成 IP 文件

def launch_gui_launcher(launcher_path):
    """启动 cmd_tray-HKG 启动器"""
    print(f"启动启动器：{launcher_path}")
    try:
        subprocess.Popen([launcher_path], cwd=os.path.dirname(launcher_path))
        print("启动器已启动。")
    except Exception as e:
        print(f"启动失败：{e}")
        exit(1)

def wait_for_ip_file(ip_file_path, timeout=180):
    """等待 IP 文件生成"""
    print(f"等待 IP 文件：{ip_file_path}")
    start_time = time.time()
    while not os.path.exists(ip_file_path):
        if time.time() - start_time > timeout:
            print(f"等待超时：{timeout} 秒未生成 {ip_file_path}")
            return False
        time.sleep(2)
    return True

def extract_ips(ip_file_path):
    """读取并返回唯一 IP 列表"""
    with open(ip_file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    ips = sorted(set(line.strip() for line in lines if line.strip()))
    return ips

def log_ips(ips, log_file_path):
    """写入日志"""
    with open(log_file_path, 'a', encoding='utf-8') as log_file:
        log_file.write(f"\n[{datetime.datetime.now()}] --- 提取到 {len(ips)} 个 IP ---\n")
        for ip in ips:
            log_file.write(ip + '\n')
        log_file.write(f"[{datetime.datetime.now()}] --- 结束 ---\n")
    print(f"已写入 {len(ips)} 个 IP 到日志文件：{log_file_path}")

# --- 主程序入口 ---
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    launcher_path = os.path.join(script_dir, LAUNCHER_NAME)
    ip_file_path = os.path.join(script_dir, IP_FILE_NAME)
    log_file_path = os.path.join(script_dir, LOG_FILE_NAME)

    # 步骤 1：启动启动器
    launch_gui_launcher(launcher_path)

    # 步骤 2：等待 IP 文件并读取
    if wait_for_ip_file(ip_file_path, MAX_WAIT_SECONDS):
        ip_list = extract_ips(ip_file_path)
        if ip_list:
            print(f"成功提取 {len(ip_list)} 个 IP。")
            log_ips(ip_list, log_file_path)
        else:
            print("IP 文件存在但没有有效 IP。")
    else:
        print("未能找到 IP 文件，终止。")
