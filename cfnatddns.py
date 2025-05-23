# cfnatddns.py

import subprocess
import re
import threading
import queue
import time
import os
import sys
import json # 导入 json 模块

# --- 全局路径和配置加载 ---
# 获取脚本或打包后的exe所在的目录
base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

# 配置文件路径
CONFIG_FILE_NAME = "config.json"
CONFIG_FILE_PATH = os.path.join(base_dir, CONFIG_FILE_NAME)

def load_config():
    """
    加载配置文件，如果文件不存在或加载失败，则使用默认配置。
    默认 cfnat 监听端口为 1234。
    """
    default_config = {
        "cfnat_listen_addr": "0.0.0.0",
        "cfnat_listen_port": 1234,  # <-- 默认值已改为 1234
        "min_valid_ips": 10,
        "latency_threshold_ms": 300,
        "log_file_name": "ip.ddns.txt",
        "cfnat_exe_relative_path": "cfnat_winGUI-LAX/cfnat-windows-amd64.exe"
    }

    config = {}
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"成功加载配置文件: {CONFIG_FILE_PATH}")
            # 合并默认配置，确保所有键都存在
            for key, default_value in default_config.items():
                if key not in config:
                    config[key] = default_value
                    print(f"配置文件中缺少键 '{key}'，使用默认值: {default_value}")
        else:
            print(f"配置文件未找到: {CONFIG_FILE_PATH}，将使用默认配置。")
            config = default_config
            # 可以选择在这里创建默认配置文件，方便用户修改
            # with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            #     json.dump(default_config, f, indent=2, ensure_ascii=False)
            # print(f"已创建默认配置文件: {CONFIG_FILE_PATH}")

    except json.JSONDecodeError as e:
        print(f"错误: 配置文件 {CONFIG_FILE_PATH} 格式无效: {e}。将使用默认配置。")
        config = default_config
    except Exception as e:
        print(f"加载配置文件时发生未知错误: {e}。将使用默认配置。")
        config = default_config
    
    return config

# 加载配置
CONFIG = load_config()

# --- 从配置中获取值 ---
CFNAT_EXE_PATH = os.path.join(base_dir, CONFIG["cfnat_exe_relative_path"])
CFNAT_LISTEN_ADDR = CONFIG["cfnat_listen_addr"]
CFNAT_LISTEN_PORT = CONFIG["cfnat_listen_port"] # 从加载的配置中获取端口
MIN_VALID_IPS = CONFIG["min_valid_ips"]
LATENCY_THRESHOLD_MS = CONFIG["latency_threshold_ms"]
LOG_FILE_PATH = os.path.join(base_dir, CONFIG["log_file_name"])

# 提取 IP 和延迟的正则表达式 (保持不变)
IP_LATENCY_PATTERN = re.compile(r"地址: (\[?[\da-fA-F.:]+\]?):\d+ 延迟: (\d+) ms")

# --- 内部状态管理 --- (保持不变)
output_queue = queue.Queue()
found_ips_data = {}
stop_event = threading.Event()
cfnat_process = None

def enqueue_output(out, q):
    """
    从子进程的标准输出中读取行，解码并放入队列。
    会尝试多种编码以兼容 Windows 环境。
    """
    print("开始读取 cfnat 进程输出...")
    while not stop_event.is_set():
        try:
            line = out.readline()
            if not line: # EOF
                break
            
            decoded_line = ""
            try:
                # 优先尝试 UTF-8
                decoded_line = line.decode('utf-8').strip()
            except UnicodeDecodeError:
                try:
                    # 其次尝试 GBK (Windows 常用中文编码)
                    decoded_line = line.decode('gbk', errors='ignore').strip()
                except UnicodeDecodeError:
                    # 如果仍然失败，尝试其他编码或忽略错误
                    decoded_line = line.decode(sys.getdefaultencoding(), errors='ignore').strip()

            if decoded_line:
                q.put(decoded_line)
        except ValueError as e:
            # 管道关闭或其他读取错误
            if not stop_event.is_set():
                print(f"读取 cfnat 输出管道时发生错误: {e}")
            break
        except Exception as e:
            print(f"读取输出时发生未知错误: {e}")
            break
    print("读取 cfnat 进程输出线程已停止。")
    out.close()

def write_ips_to_log_file(ips_list, file_path):
    """
    将优选 IP 列表写入指定的日志文件。
    每个 IP 占一行，格式为：IP地址,延迟ms
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f: # 'w' 模式会清空文件再写入
            for ip, latency in ips_list:
                f.write(f"{ip},{latency}\n")
        print(f"\n优选 IP 已写入文件: {file_path}")
    except IOError as e:
        print(f"错误: 无法写入日志文件 {file_path}: {e}")

def start_cfnat_and_process_output():
    """
    启动 cfnat 进程，并实时解析其标准输出以提取优选 IP。
    """
    global cfnat_process, found_ips_data # 声明使用全局变量

    print(f"正在尝试启动 cfnat 进程: {CFNAT_EXE_PATH}")
    if not os.path.exists(CFNAT_EXE_PATH):
        print(f"错误: 找不到 cfnat.exe，请检查路径: {CFNAT_EXE_PATH}")
        return []

    try:
        # 启动 cfnat 进程
        # creationflags=subprocess.CREATE_NO_WINDOW 用于在 Windows 上隐藏控制台窗口
        cfnat_process = subprocess.Popen(
            [CFNAT_EXE_PATH, f"-addr={CFNAT_LISTEN_ADDR}:{CFNAT_LISTEN_PORT}"], # 传递监听地址参数
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # 合并标准错误到标准输出，确保捕获所有日志
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0 # 仅在 Windows 上隐藏窗口
        )
        print("cfnat 进程已成功启动。")
    except FileNotFoundError:
        print(f"错误: 无法找到或启动 cfnat.exe。请确保文件存在且有执行权限: {CFNAT_EXE_PATH}")
        return []
    except Exception as e:
        print(f"启动 cfnat 进程时发生未知错误: {e}")
        return []

    # 启动一个线程来读取 cfnat 的输出
    output_reader_thread = threading.Thread(target=enqueue_output, args=(cfnat_process.stdout, output_queue))
    output_reader_thread.daemon = True # 设置为守护线程，主程序退出时它也会退出
    output_reader_thread.start()

    print("开始监听 cfnat 输出，请稍候...")
    
    # 主循环：从队列中获取并处理 cfnat 的输出行
    try:
        while not stop_event.is_set():
            try:
                line = output_queue.get(timeout=0.5) # 设置超时，以便检查 stop_event
                # print(f"[cfnat output] {line}") # 调试时可以取消注释，查看原始日志
                
                match = IP_LATENCY_PATTERN.search(line)
                if match:
                    ip = match.group(1).strip('[]') # 移除 IPv6 地址的方括号
                    latency = int(match.group(2))

                    # 检查 IP 是否符合优选条件 (从 CONFIG 中获取)
                    if latency <= CONFIG["latency_threshold_ms"]:
                        if ip not in found_ips_data:
                            found_ips_data[ip] = (latency, time.time()) # 存储延迟和发现时间
                            print(f"发现优选 IP: {ip}, 延迟: {latency} ms (当前已找到 {len(found_ips_data)} 个)")
                            
                            # 如果找到足够数量的 IP (从 CONFIG 中获取)，可以考虑停止 cfnat
                            if len(found_ips_data) >= CONFIG["min_valid_ips"]:
                                # 您可以在这里添加逻辑来通知 DDNS 客户端更新 IP
                                # 例如：notify_ddns_service(ip)
                                pass # 目前只是打印信息

            except queue.Empty:
                # 队列为空，检查 cfnat 进程状态
                pass
            
            # 检查 cfnat 进程是否仍在运行
            if cfnat_process.poll() is not None:
                print("cfnat 进程已退出。")
                stop_event.set() # 通知所有线程停止
                break # 退出循环
            
            time.sleep(0.01) # 短暂休眠，避免 CPU 占用过高
            
    except KeyboardInterrupt:
        print("\n用户中断：正在停止 cfnat 进程和相关线程...")
    except Exception as e:
        print(f"主处理循环中发生错误: {e}")
    finally:
        # 确保进程和线程被清理
        stop_event.set() # 设置停止事件，通知读取线程退出
        if cfnat_process and cfnat_process.poll() is None: # 如果进程仍在运行
            try:
                print("正在尝试终止 cfnat 进程...")
                cfnat_process.terminate() # 尝试优雅终止
                cfnat_process.wait(timeout=5) # 等待进程终止
                if cfnat_process.poll() is None: # 如果还未终止，则强制杀死
                    print("cfnat 进程未响应终止请求，强制杀死...")
                    cfnat_process.kill()
            except Exception as e:
                print(f"终止 cfnat 进程时发生错误: {e}")

        if output_reader_thread.is_alive():
            output_reader_thread.join(timeout=2) # 等待读取线程完成

    # 准备返回优选 IP 列表
    # 将字典转换为列表，并按延迟排序
    final_ips_list = sorted([(ip, data[0]) for ip, data in found_ips_data.items()], key=lambda x: x[1])
    return final_ips_list

if __name__ == "__main__":
    print("--- Cloudflare DDNS 优选 IP 工具 (基于 cfnat 输出解析) ---")
    
    # 检查 cfnat.exe 路径是否存在，并打印其绝对路径
    absolute_cfnat_path = os.path.abspath(CFNAT_EXE_PATH)
    print(f"配置的 cfnat.exe 路径: {absolute_cfnat_path}")
    if not os.path.exists(absolute_cfnat_path):
        print(f"错误: 在指定路径未找到 cfnat.exe。请检查 'cfnat_winGUI-LAX' 文件夹及其内容是否与 '{os.path.dirname(absolute_cfnat_path)}' 同级。")
        sys.exit(1) # 退出程序

    print(f"cfnat 将监听本地地址: {CONFIG['cfnat_listen_addr']}:{CONFIG['cfnat_listen_port']}")
    print(f"将寻找延迟 <= {CONFIG['latency_threshold_ms']} ms 的 IP。")
    print(f"在找到至少 {CONFIG['min_valid_ips']} 个 IP 后，程序将继续运行并监控。")

    # 执行主函数
    preferred_ips = start_cfnat_and_process_output()

    print("\n--- 优选 Cloudflare IP 报告 ---")
    if preferred_ips:
        for i, (ip, latency) in enumerate(preferred_ips):
            print(f"{i+1}. IP: {ip}, 延迟: {latency} ms")
        
        # 将优选 IP 写入文件
        write_ips_to_log_file(preferred_ips, LOG_FILE_PATH)
    else:
        print("未找到任何优选 IP。请检查 cfnat 运行情况或网络连接。")
        # 即使没有找到，也清空或创建一个空文件
        write_ips_to_log_file([], LOG_FILE_PATH) 

    print("程序已退出。")
