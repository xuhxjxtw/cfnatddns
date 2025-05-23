# cfnatddns.py

import subprocess
import re
import threading
import queue
import time
import os
import sys
import json
import glob
import logging
from logging.handlers import RotatingFileHandler

# --- 全局路径和配置加载 ---
# 获取脚本或打包后的exe所在的目录
base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

# 配置文件路径
CONFIG_FILE_NAME = "config.json"
CONFIG_FILE_PATH = os.path.join(base_dir, CONFIG_FILE_NAME)

# --- 日志配置 (在加载配置之前初始化，以便记录加载过程中的问题) ---
# 默认日志文件和级别
DEFAULT_LOG_FILE_NAME = "cfnatddns.log"
DEFAULT_LOG_LEVEL = "INFO" # 默认控制台和文件都显示INFO及以上

def setup_logging(log_file_name=DEFAULT_LOG_FILE_NAME, log_level_str=DEFAULT_LOG_LEVEL):
    """
    设置日志记录器，包括控制台输出和文件输出。
    """
    logger = logging.getLogger('cfnatddns_logger')
    logger.setLevel(logging.DEBUG) # 主logger设置为最低级别，确保所有消息都能被处理器处理

    # 清除旧的处理器，避免重复添加
    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO)) # 从字符串获取日志级别
    logger.addHandler(console_handler)

    # 文件处理器 (带文件轮转)
    log_file_path = os.path.join(base_dir, log_file_name)
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=1024 * 1024 * 5,  # 5 MB
        backupCount=5,             # 最多保留 5 个备份文件
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG) # 文件日志通常记录更详细的 DEBUG 信息
    logger.addHandler(file_handler)

    return logger

# 在加载配置前先设置一个基本日志器，以便记录加载过程
logger = setup_logging()

def load_config():
    """
    加载配置文件，如果文件不存在或加载失败，则使用默认配置。
    默认 cfnat 监听端口为 1234。
    """
    default_config = {
        "cfnat_listen_addr": "0.0.0.0",
        "cfnat_listen_port": 1234,
        "min_valid_ips": 10,
        "latency_threshold_ms": 300,
        "log_file_name": DEFAULT_LOG_FILE_NAME, # 默认日志文件名
        "log_level": DEFAULT_LOG_LEVEL,         # 默认日志级别
        "cfnat_exe_relative_path_pattern": "cfnat_winGUI-LAX/cmd_tray-*.exe"
    }

    config = {}
    try:
        if os.path.exists(CONFIG_FILE_PATH):
            logger.info(f"尝试加载配置文件: {CONFIG_FILE_PATH}")
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
            logger.info(f"成功加载配置文件: {CONFIG_FILE_PATH}")
            # 合并默认配置，确保所有键都存在
            for key, default_value in default_config.items():
                if key not in config:
                    config[key] = default_value
                    logger.warning(f"配置文件中缺少键 '{key}'，使用默认值: {default_value}")
        else:
            logger.warning(f"配置文件未找到: {CONFIG_FILE_PATH}，将使用默认配置。")
            config = default_config
            # 可以在这里选择创建默认配置文件，方便用户修改
            # try:
            #     with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            #         json.dump(default_config, f, indent=2, ensure_ascii=False)
            #     logger.info(f"已创建默认配置文件: {CONFIG_FILE_PATH}")
            # except IOError as e:
            #     logger.error(f"创建默认配置文件失败: {e}")

    except json.JSONDecodeError as e:
        logger.error(f"错误: 配置文件 {CONFIG_FILE_PATH} 格式无效: {e}。将使用默认配置。")
        config = default_config
    except Exception as e:
        logger.error(f"加载配置文件时发生未知错误: {e}。将使用默认配置。")
        config = default_config
    
    return config

# 加载配置
CONFIG = load_config()

# 根据加载的配置重新配置日志器 (特别是日志级别和文件名)
logger = setup_logging(CONFIG["log_file_name"], CONFIG["log_level"])
logger.info("日志系统已根据配置文件重新配置。")

# --- 从配置中获取值 ---
CFNAT_LISTEN_ADDR = CONFIG["cfnat_listen_addr"]
CFNAT_LISTEN_PORT = CONFIG["cfnat_listen_port"]
MIN_VALID_IPS = CONFIG["min_valid_ips"]
LATENCY_THRESHOLD_MS = CONFIG["latency_threshold_ms"]
# LOG_FILE_PATH 已经在 setup_logging 中处理，这里用于 write_ips_to_log_file

# 提取 IP 和延迟的正则表达式 (保持不变)
IP_LATENCY_PATTERN = re.compile(r"地址: (\[?[\da-fA-F.:]+\]?):\d+ 延迟: (\d+) ms")

# --- 内部状态管理 --- (保持不变)
output_queue = queue.Queue()
found_ips_data = {}
stop_event = threading.Event()
cfnat_process = None

def find_cfnat_exe_path(pattern):
    """
    根据模式在 base_dir 中查找 cfnat 可执行文件的路径。
    """
    full_pattern = os.path.join(base_dir, pattern)
    logger.debug(f"正在根据模式查找 cfnat 可执行文件: {full_pattern}")
    matches = glob.glob(full_pattern)
    
    if matches:
        chosen_path = matches[0]
        logger.info(f"根据模式 '{pattern}' 找到 cfnat 可执行文件: {chosen_path}")
        return chosen_path
    else:
        logger.error(f"错误: 未找到匹配模式 '{pattern}' 的 cfnat 可执行文件。")
        return None

def enqueue_output(out, q):
    """
    从子进程的标准输出中读取行，解码并放入队列。
    会尝试多种编码以兼容 Windows 环境。
    """
    logger.debug("开始读取 cfnat 进程输出线程...")
    while not stop_event.is_set():
        try:
            line = out.readline()
            if not line: # EOF
                logger.debug("cfnat 进程输出 EOF。")
                break
            
            decoded_line = ""
            try:
                decoded_line = line.decode('utf-8').strip()
            except UnicodeDecodeError:
                try:
                    decoded_line = line.decode('gbk', errors='ignore').strip()
                except UnicodeDecodeError:
                    decoded_line = line.decode(sys.getdefaultencoding(), errors='ignore').strip()
                    logger.warning(f"无法以 UTF-8 或 GBK 解码 cfnat 输出，使用默认编码。原始: {line[:50]}...")

            if decoded_line:
                q.put(decoded_line)
                logger.debug(f"将 cfnat 输出行加入队列: {decoded_line}")
        except ValueError as e:
            if not stop_event.is_set():
                logger.error(f"读取 cfnat 输出管道时发生错误: {e}")
            break
        except Exception as e:
            logger.exception(f"读取输出时发生未知错误: {e}") # 使用 exception 记录完整堆栈
            break
    logger.debug("读取 cfnat 进程输出线程已停止。")
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
        logger.info(f"优选 IP 已写入文件: {file_path}")
    except IOError as e:
        logger.error(f"错误: 无法写入日志文件 {file_path}: {e}")
    except Exception as e:
        logger.exception(f"写入 IP 文件时发生未知错误: {e}")

def start_cfnat_and_process_output():
    """
    启动 cfnat 进程，并实时解析其标准输出以提取优选 IP。
    """
    global cfnat_process, found_ips_data # 声明使用全局变量

    # 在这里动态查找 cfnat exe 的实际路径
    CFNAT_EXE_RUNTIME_PATH = find_cfnat_exe_path(CONFIG["cfnat_exe_relative_path_pattern"])

    if not CFNAT_EXE_RUNTIME_PATH:
        logger.critical("无法找到 cfnat 可执行文件，程序无法启动。请检查配置和文件是否存在。")
        return []
    
    logger.info(f"正在尝试启动 cfnat 进程: {CFNAT_EXE_RUNTIME_PATH}")
    if not os.path.exists(CFNAT_EXE_RUNTIME_PATH): # 再次确认文件是否存在
        logger.critical(f"找到的 cfnat.exe 路径无效或文件不存在: {CFNAT_EXE_RUNTIME_PATH}")
        return []

    try:
        # 启动 cfnat 进程
        # creationflags=subprocess.CREATE_NO_WINDOW 用于在 Windows 上隐藏控制台窗口
        cfnat_process = subprocess.Popen(
            [CFNAT_EXE_RUNTIME_PATH, f"-addr={CFNAT_LISTEN_ADDR}:{CFNAT_LISTEN_PORT}"], # 传递监听地址参数
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # 合并标准错误到标准输出，确保捕获所有日志
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0 # 仅在 Windows 上隐藏窗口
        )
        logger.info("cfnat 进程已成功启动。")
    except FileNotFoundError: # 虽然上面已经检查，但以防万一
        logger.critical(f"无法找到或启动 cfnat.exe。请确保文件存在且有执行权限: {CFNAT_EXE_RUNTIME_PATH}")
        return []
    except Exception as e:
        logger.critical(f"启动 cfnat 进程时发生未知错误: {e}")
        logger.exception("启动 cfnat 进程的详细错误信息:") # 记录完整堆栈
        return []

    # 启动一个线程来读取 cfnat 的输出
    output_reader_thread = threading.Thread(target=enqueue_output, args=(cfnat_process.stdout, output_queue), name="CFNAT_Output_Reader")
    output_reader_thread.daemon = True # 设置为守护线程，主程序退出时它也会退出
    output_reader_thread.start()

    logger.info("开始监听 cfnat 输出，请稍候...")
    
    # 主循环：从队列中获取并处理 cfnat 的输出行
    try:
        while not stop_event.is_set():
            try:
                line = output_queue.get(timeout=0.5) # 设置超时，以便检查 stop_event
                logger.debug(f"[cfnat raw output] {line}") # 记录原始 cfnat 输出
                
                match = IP_LATENCY_PATTERN.search(line)
                if match:
                    ip = match.group(1).strip('[]') # 移除 IPv6 地址的方括号
                    latency = int(match.group(2))

                    # 检查 IP 是否符合优选条件 (从 CONFIG 中获取)
                    if latency <= CONFIG["latency_threshold_ms"]:
                        if ip not in found_ips_data:
                            found_ips_data[ip] = (latency, time.time()) # 存储延迟和发现时间
                            logger.info(f"发现优选 IP: {ip}, 延迟: {latency} ms (当前已找到 {len(found_ips_data)} 个)")
                            
                            # 如果找到足够数量的 IP (从 CONFIG 中获取)，可以考虑停止 cfnat
                            if len(found_ips_data) >= CONFIG["min_valid_ips"]:
                                logger.debug(f"已找到 {len(found_ips_data)} 个 IP，达到最小要求 {CONFIG['min_valid_ips']} 个。")
                                # 您可以在这里添加逻辑来通知 DDNS 客户端更新 IP
                                # 例如：notify_ddns_service(ip)
                                pass 

            except queue.Empty:
                # 队列为空，检查 cfnat 进程状态
                logger.debug("输出队列为空，等待新的 cfnat 输出。")
                pass
            except Exception as e:
                logger.exception(f"处理 cfnat 输出行时发生错误: {e}. 行: {line}")

            # 检查 cfnat 进程是否仍在运行
            if cfnat_process.poll() is not None:
                logger.warning("cfnat 进程已退出。")
                stop_event.set() # 通知所有线程停止
                break # 退出循环
            
            time.sleep(0.01) # 短暂休眠，避免 CPU 占用过高
            
    except KeyboardInterrupt:
        logger.info("用户中断 (Ctrl+C)：正在停止 cfnat 进程和相关线程...")
    except Exception as e:
        logger.exception(f"主处理循环中发生错误: {e}")
    finally:
        # 确保进程和线程被清理
        logger.info("开始清理 cfnat 进程和相关线程...")
        stop_event.set() # 设置停止事件，通知读取线程退出
        if cfnat_process and cfnat_process.poll() is None: # 如果进程仍在运行
            try:
                logger.info("正在尝试终止 cfnat 进程...")
                cfnat_process.terminate() # 尝试优雅终止
                cfnat_process.wait(timeout=5) # 等待进程终止
                if cfnat_process.poll() is None: # 如果还未终止，则强制杀死
                    logger.warning("cfnat 进程未响应终止请求，强制杀死...")
                    cfnat_process.kill()
            except Exception as e:
                logger.error(f"终止 cfnat 进程时发生错误: {e}")
                logger.exception("终止 cfnat 进程的详细错误信息:")

        if output_reader_thread.is_alive():
            logger.debug("等待 cfnat 输出读取线程停止...")
            output_reader_thread.join(timeout=2) # 等待读取线程完成
            if output_reader_thread.is_alive():
                logger.warning("cfnat 输出读取线程未能及时停止。")
        logger.info("cfnat 进程和相关线程清理完成。")

    # 准备返回优选 IP 列表
    # 将字典转换为列表，并按延迟排序
    final_ips_list = sorted([(ip, data[0]) for ip, data in found_ips_data.items()], key=lambda x: x[1])
    return final_ips_list

if __name__ == "__main__":
    logger.info("--- Cloudflare DDNS 优选 IP 工具 (基于 cfnat 输出解析) 启动 ---")
    
    # 打印通过配置文件加载的 cfnat exe 模式
    logger.info(f"配置的 cfnat.exe 查找模式: {CONFIG['cfnat_exe_relative_path_pattern']}")

    logger.info(f"cfnat 将监听本地地址: {CONFIG['cfnat_listen_addr']}:{CONFIG['cfnat_listen_port']}")
    logger.info(f"将寻找延迟 <= {CONFIG['latency_threshold_ms']} ms 的 IP。")
    logger.info(f"在找到至少 {CONFIG['min_valid_ips']} 个 IP 后，程序将继续运行并监控。")

    # 执行主函数
    preferred_ips = start_cfnat_and_process_output()

    logger.info("--- 优选 Cloudflare IP 报告 ---")
    if preferred_ips:
        for i, (ip, latency) in enumerate(preferred_ips):
            logger.info(f"{i+1}. IP: {ip}, 延迟: {latency} ms")
        
        # 将优选 IP 写入文件
        write_ips_to_log_file(preferred_ips, LOG_FILE_PATH)
    else:
        logger.warning("未找到任何优选 IP。请检查 cfnat 运行情况或网络连接。")
        # 即使没有找到，也清空或创建一个空文件
        write_ips_to_log_file([], LOG_FILE_PATH) 

    logger.info("程序已退出。")
