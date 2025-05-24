import subprocess
import re
import time
import os
import datetime
import glob

LOG_FILE_NAME = "cfnat_ips.log"

def get_cf_ips_from_proxy_output(proxy_command, max_ips=20, timeout_seconds=120):
    ips_found = set()
    process = None
    start_time = time.time()

    ip_pattern = re.compile(
        r'\b(?:'
        r'(?:\d{1,3}\.){3}\d{1,3}'
        r'|'
        r'(?:[0-9a-fA-F]{1,4}:){1,7}[0-9a-fA-F]{1,4}'
        r'|'
        r'::(?:[0-9a-fA-F]{1,4}){1,7}'
        r'|'
        r'[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6}::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?'
        r')\b'
    )

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(script_dir, LOG_FILE_NAME)
    
    log_file = None
    try:
        log_file = open(log_path, 'a', encoding='utf-8')
        log_file.write(f"\n[{datetime.datetime.now()}] --- 开始收集 Cloudflare IP --- \n")
        log_file.flush()
        print(f"日志将写入到: {log_path}")
    except Exception as e:
        print(f"错误：无法打开日志文件 {log_path}，将只在控制台输出。错误信息: {e}")
        log_file = None

    def log_message(message, to_file=True):
        print(message)
        if to_file and log_file:
            log_file.write(message + "\n")
            log_file.flush()

    log_message(f"尝试启动代理程序：{' '.join(proxy_command)}")

    try:
        process = subprocess.Popen(
            proxy_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            bufsize=1,
            cwd=script_dir
        )

        log_message(f"开始监听代理服务的输出，最多等待 {timeout_seconds} 秒，或直到收集到 {max_ips} 个IP...")

        while True:
            if time.time() - start_time > timeout_seconds:
                log_message(f"已达到最大等待时间 {timeout_seconds} 秒，停止收集。")
                break

            if len(ips_found) >= max_ips:
                log_message(f"已收集到 {max_ips} 个不同的IP地址，停止收集。")
                break

            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    log_message("代理程序已退出。")
                    break
                else:
                    time.sleep(0.1)
                    continue

            line = line.strip()
            matches = ip_pattern.findall(line)
            for ip_match in matches:
                if ip_match and ip_match not in ips_found:
                    log_message(f"发现 IP: {ip_match}")
                    ips_found.add(ip_match)

    except FileNotFoundError:
        log_message(f"错误：找不到代理启动器。请确认 '{proxy_command[0]}' 存在。")
    except Exception as e:
        log_message(f"运行或解析过程中发生错误: {e}")
    finally:
        if process and process.poll() is None:
            log_message("终止代理程序。")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log_message("强制杀死代理程序。")
                process.kill()
        
        log_message("IP 收集完成。")
        log_message(f"--- Cloudflare IP 列表 (共 {len(ips_found)} 个) ---")
        if ips_found:
            for ip in sorted(ips_found):
                log_message(ip)
        else:
            log_message("未获取到任何 Cloudflare IP 地址。")
        log_message(f"[{datetime.datetime.now()}] --- 收集结束 --- \n")
        
        if log_file:
            log_file.close()

    return list(ips_found)


if __name__ == "__main__":
    proxy_launcher_pattern = "cmd_tray-*.exe"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    matching_files = glob.glob(os.path.join(script_dir, proxy_launcher_pattern))

    if not matching_files:
        print(f"错误：在当前目录下未找到匹配 '{proxy_launcher_pattern}' 的代理启动器文件。")
        exit(1)

    proxy_launcher_path = matching_files[0]

    if not os.path.isfile(proxy_launcher_path):
        print(f"错误：代理启动器文件不存在：{proxy_launcher_path}")
        exit(1)

    proxy_command = [proxy_launcher_path]  # 使用完整路径更可靠

    print(f"已找到代理启动器：{proxy_launcher_path}")
    print(f"将使用命令：{proxy_command} 启动代理程序。")

    get_cf_ips_from_proxy_output(proxy_command, max_ips=50, timeout_seconds=180)
