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
    
    # 确保 log_file_name 是一个完整路径，即使它只有文件名部分
    log_file_path = os.path.join(base_dir, log_file_name)


    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO)) # 从字符串获取日志级别
    logger.addHandler(console_handler)

    # 文件处理器 (带文件轮转)
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
        "cfnat_listen_port": 1234, # 默认端口为 1234
        "min_valid_ips": 10,
        "latency_threshold_ms": 300,
        "log_file_name": DEFAULT_LOG_FILE_NAME, # 默认日志文件名
        "log_level": DEFAULT_LOG_LEVEL,         # 默认日志级别
        # 现在指向第一个启动的程序 cmd_tray-LAX.exe
        "cfnat_exe_relative_path": "cfnat_winGUI-LAX/cmd_tray-LAX.exe" 
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
# 直接从配置中获取 cfnat exe 的相对路径 (现在是 cmd_tray-LAX.exe)
CFNAT_EXE_RELATIVE_PATH = CONFIG["cfnat_exe_relative_path"] 
CFNAT_LISTEN_ADDR = CONFIG["cfnat_listen_addr"]
CFNAT_LISTEN_PORT = CONFIG["cfnat_listen_port"]
MIN_VALID_IPS = CONFIG["min_valid_ips"]
LATENCY_THRESHOLD_MS = CONFIG["latency_threshold_ms"]
LOG_FILE_PATH = os.path.join(base_dir, CONFIG["log_file_name"]) # 确保这个路径用于 write_ips_to_log_file

# 提取 IP 和延迟的正则表达式 (保持不变)
# 这个正则表达式是用来匹配 cfnat-windows-amd64.exe 的输出格式的
IP_LATENCY_PATTERN = re.compile(r"地址: (\[?[\da-fA-F.:]+\]?):\d+ 延迟: (\d+) ms")

# --- 内部状态管理 --- (保持不变)
output_queue = queue.Queue()
found_ips_data = {}
stop_event = threading.Event()
cfnat_process = None # 这里将是 cmd_tray-LAX.exe 的进程

def get_full_cfnat_exe_path(relative_path):
    """
    根据相对路径获取 cfnat 可执行文件的完整路径。
    """
    full_path = os.path.join(base_dir, relative_path)
    logger.debug(f"计算待启动程序的完整路径: {full_path}")
    return full_path

def enqueue_output(out, q):
    """
    从子进程的标准输出中读取行，解码并放入队列。
    会尝试多种编码以兼容 Windows 环境。
    """
    logger.debug("开始读取待启动程序进程输出线程...")
    while not stop_event.is_set():
        try:
            line = out.readline()
            if not line: # EOF
                logger.debug("待启动程序进程输出 EOF。")
                break
            
            decoded_line = ""
            try:
                decoded_line = line.decode('utf-8').strip()
            except UnicodeDecodeError:
                try:
                    decoded_line = line.decode('gbk', errors='ignore').strip()
                except UnicodeDecodeError:
                    decoded_line = line.decode(sys.getdefaultencoding(), errors='ignore').strip()
                    logger.warning(f"无法以 UTF-8 或 GBK 解码程序输出，使用默认编码。原始: {line[:50]}...")

            if decoded_line:
                q.put(decoded_line)
                logger.debug(f"将程序输出行加入队列: {decoded_line}")
        except ValueError as e:
            if not stop_event.is_set():
                logger.error(f"读取程序输出管道时发生错误: {e}")
            break
        except Exception as e:
            logger.exception(f"读取输出时发生未知错误: {e}") # 使用 exception 记录完整堆栈
            break
    logger.debug("读取待启动程序进程输出线程已停止。")
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
    启动 cmd_tray-LAX.exe 进程，并实时解析其标准输出以提取优选 IP。
    """
    global cfnat_process, found_ips_data # 声明使用全局变量

    # 获取 cmd_tray-LAX.exe 的完整路径
    PROGRAM_TO_START_PATH = get_full_cfnat_exe_path(CFNAT_EXE_RELATIVE_PATH)

    logger.info(f"准备启动主程序。目标路径: {PROGRAM_TO_START_PATH}")
    if not os.path.exists(PROGRAM_TO_START_PATH):
        logger.critical(f"错误: 找不到主程序可执行文件。请检查路径: {PROGRAM_TO_START_PATH} 和 config.json 中的 'cfnat_exe_relative_path' 配置。")
        return []
    
    if not os.path.isfile(PROGRAM_TO_START_PATH):
        logger.critical(f"错误: 路径 {PROGRAM_TO_START_PATH} 不是一个有效的文件。请检查配置。")
        return []

    try:
        # 启动 cmd_tray-LAX.exe 进程
        # 注意：这里传递的 -addr 参数是给 cmd_tray-LAX.exe 的，如果它不接受这个参数，可能会有问题
        # 如果 cmd_tray-LAX.exe 不需要这些参数，或者它有自己的方式传递给 cfnat-windows-amd64.exe，
        # 则这里需要移除参数。
        # 假设 cmd_tray-LAX.exe 会处理或转发这些参数，或者只是启动 cfnat-windows-amd64.exe 
        # 且 cfnat-windows-amd64.exe 默认就监听 1238 或 1234。
        command = [PROGRAM_TO_START_PATH, f"-addr={CFNAT_LISTEN_ADDR}:{CFNAT_LISTEN_PORT}"]
        logger.info(f"执行主程序命令: {' '.join(command)}")

        cfnat_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # 合并标准错误到标准输出，确保捕获所有日志
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0 # 仅在 Windows 上隐藏窗口
        )
        logger.info(f"主程序 '{os.path.basename(PROGRAM_TO_START_PATH)}' 已成功启动。")
    except FileNotFoundError:
        logger.critical(f"无法找到或启动主程序。请确保文件存在且有执行权限: {PROGRAM_TO_START_PATH}")
        return []
    except PermissionError:
        logger.critical(f"没有足够的权限执行主程序。请尝试以管理员身份运行脚本: {PROGRAM_TO_START_PATH}")
        return []
    except Exception as e:
        logger.critical(f"启动主程序时发生未知错误: {e}")
        logger.exception("启动主程序的详细错误信息:") # 记录完整堆栈
        return []

    # 启动一个线程来读取主程序的输出 (预期是 cfnat-windows-amd64.exe 转发的输出)
    output_reader_thread = threading.Thread(target=enqueue_output, args=(cfnat_process.stdout, output_queue), name="Main_Program_Output_Reader")
    output_reader_thread.daemon = True # 设置为守护线程，主程序退出时它也会退出
    output_reader_thread.start()

    logger.info("开始监听主程序输出 (预期为 cfnat-windows-amd64.exe 的 IP 信息)，请稍候...")
    
    # 主循环：从队列中获取并处理输出行
    try:
        while not stop_event.is_set():
            try:
                line = output_queue.get(timeout=0.5) # 设置超时，以便检查 stop_event
                logger.debug(f"[Program raw output] {line}") # 记录原始程序输出
                
                match = IP_LATENCY_PATTERN.search(line)
                if match:
                    ip = match.group(1).strip('[]') # 移除 IPv6 地址的方括号
                    latency = int(match.group(2))

                    # 检查 IP 是否符合优选条件 (从 CONFIG 中获取)
                    if latency <= CONFIG["latency_threshold_ms"]:
                        if ip not in found_ips_data:
                            found_ips_data[ip] = (latency, time.time()) # 存储延迟和发现时间
                            logger.info(f"发现优选 IP: {ip}, 延迟: {latency} ms (当前已找到 {len(found_ips_data)} 个)")
                            
                            if len(found_ips_data) >= CONFIG["min_valid_ips"]:
                                logger.debug(f"已找到 {len(found_ips_data)} 个 IP，达到最小要求 {CONFIG['min_valid_ips']} 个。")
                                pass 

            except queue.Empty:
                logger.debug("输出队列为空，等待新的程序输出。")
                pass
            except Exception as e:
                logger.exception(f"处理程序输出行时发生错误: {e}. 行: {line}")

            # 检查主程序进程是否仍在运行
            if cfnat_process.poll() is not None:
                logger.warning(f"主程序 '{os.path.basename(PROGRAM_TO_START_PATH)}' 进程已退出。")
                stop_event.set() # 通知所有线程停止
                break # 退出循环
            
            time.sleep(0.01) # 短暂休眠，避免 CPU 占用过高
            
    except KeyboardInterrupt:
        logger.info("用户中断 (Ctrl+C)：正在停止主程序进程和相关线程...")
    except Exception as e:
        logger.exception(f"主处理循环中发生错误: {e}")
    finally:
        # 确保进程和线程被清理
        logger.info("开始清理主程序进程和相关线程...")
        stop_event.set() # 设置停止事件，通知读取线程退出
        if cfnat_process and cfnat_process.poll() is None: # 如果进程仍在运行
            try:
                logger.info("正在尝试终止主程序进程...")
                cfnat_process.terminate() # 尝试优雅终止
                cfnat_process.wait(timeout=5) # 等待进程终止
                if cfnat_process.poll() is None: # 如果还未终止，则强制杀死
                    logger.warning("主程序进程未响应终止请求，强制杀死...")
                    cfnat_process.kill()
            except Exception as e:
                logger.error(f"终止主程序进程时发生错误: {e}")
                logger.exception("终止主程序进程的详细错误信息:")

        if output_reader_thread.is_alive():
            logger.debug("等待主程序输出读取线程停止...")
            output_reader_thread.join(timeout=2) # 等待读取线程完成
            if output_reader_thread.is_alive():
                logger.warning("主程序输出读取线程未能及时停止。")
        logger.info("主程序进程和相关线程清理完成。")

    # 准备返回优选 IP 列表
    final_ips_list = sorted([(ip, data[0]) for ip, data in found_ips_data.items()], key=lambda x: x[1])
    return final_ips_list

if __name__ == "__main__":
    logger.info("--- Cloudflare DDNS 优选 IP 工具 (基于 cfnat 输出解析) 启动 ---")
    
    # 打印通过配置文件加载的 cfnat exe 路径
    logger.info(f"配置的待启动主程序路径: {CONFIG['cfnat_exe_relative_path']}")

    logger.info(f"cfnat (通过主程序启动) 将监听本地地址: {CONFIG['cfnat_listen_addr']}:{CONFIG['cfnat_listen_port']}")
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
        logger.warning("未找到任何优选 IP。请检查主程序运行情况、网络连接，或其子进程 (cfnat-windows-amd64.exe) 是否有正确输出。")
        # 即使没有找到，也清空或创建一个空文件
        write_ips_to_log_file([], LOG_FILE_PATH) 

    logger.info("程序已退出。")
