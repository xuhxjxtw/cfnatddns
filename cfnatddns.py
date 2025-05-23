# cfnatddns.py

import subprocess
import threading
import queue
import time
import os
import sys
import logging
from logging.handlers import RotatingFileHandler

# --- 全局路径和硬编码配置 ---
# 获取脚本或打包后的exe所在的目录
base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

# 硬编码待启动程序路径
PROGRAM_TO_START_RELATIVE_PATH = "cfnat_winGUI-LAX/cmd_tray-LAX.exe"

# 日志配置
DEFAULT_LOG_FILE_NAME = "cfnatddns.log"
DEFAULT_LOG_LEVEL = "INFO" # 默认控制台和文件都显示INFO及以上，可改为 "DEBUG" 查看更详细的程序输出

# --- 日志配置函数 ---
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
    
    log_file_path = os.path.join(base_dir, log_file_name)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO))
    logger.addHandler(console_handler)

    # 文件处理器 (带文件轮转)
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=1024 * 1024 * 5,  # 5 MB
        backupCount=5,             # 最多保留 5 个备份文件
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG) # 文件日志通常记录更详细的 DEBUG 信息，包括原始程序输出
    logger.addHandler(file_handler)

    return logger

# 初始化日志器
logger = setup_logging(DEFAULT_LOG_FILE_NAME, DEFAULT_LOG_LEVEL)
logger.info("日志系统已初始化。所有配置参数已硬编码在脚本中。")


# --- 内部状态管理 ---
output_queue = queue.Queue()
stop_event = threading.Event()
main_program_process = None 

def get_full_program_path(relative_path):
    """
    根据相对路径获取待启动可执行文件的完整路径。
    """
    full_path = os.path.join(base_dir, relative_path)
    logger.debug(f"计算待启动程序的完整路径: {full_path}")
    return full_path

def enqueue_output(out, q):
    """
    从子进程的标准输出中读取行，解码并放入队列。
    会尝试多种编码以兼容 Windows 环境。
    """
    logger.debug("开始读取主程序进程输出线程...")
    while not stop_event.is_set():
        try:
            line = out.readline()
            if not line: # EOF
                logger.debug("主程序进程输出 EOF。")
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
                # 直接将原始程序输出记录到日志，不再解析
                logger.debug(f"[Program raw output] {decoded_line}") 
        except ValueError as e:
            if not stop_event.is_set():
                logger.error(f"读取程序输出管道时发生错误: {e}")
            break
        except Exception as e:
            logger.exception(f"读取输出时发生未知错误: {e}") 
            break
    logger.debug("读取主程序进程输出线程已停止。")
    out.close()

def start_main_program_and_log_output():
    """
    启动 cmd_tray-LAX.exe 进程，并将其所有输出记录到日志。
    """
    global main_program_process

    # 获取 cmd_tray-LAX.exe 的完整路径
    PROGRAM_TO_START_FULL_PATH = get_full_program_path(PROGRAM_TO_START_RELATIVE_PATH)

    logger.info(f"准备启动主程序。目标路径: {PROGRAM_TO_START_FULL_PATH}")
    if not os.path.exists(PROGRAM_TO_START_FULL_PATH):
        logger.critical(f"错误: 找不到主程序可执行文件。请检查路径: {PROGRAM_TO_START_FULL_PATH}。")
        return False # 启动失败
    
    if not os.path.isfile(PROGRAM_TO_START_FULL_PATH):
        logger.critical(f"错误: 路径 {PROGRAM_TO_START_FULL_PATH} 不是一个有效的文件。")
        return False # 启动失败

    # 检查 cmd_tray.yaml 文件是否存在 (此文件应与 cmd_tray-LAX.exe 在同一目录下)
    yaml_path = os.path.join(os.path.dirname(PROGRAM_TO_START_FULL_PATH), "cmd_tray.yaml")
    if not os.path.exists(yaml_path):
        logger.warning(f"警告: 未找到 'cmd_tray.yaml' 文件在 {yaml_path}。请确保它与 cmd_tray-LAX.exe 在同一目录下。主程序可能无法正常启动其子进程。")
    else:
        logger.info(f"找到 'cmd_tray.yaml' 文件: {yaml_path}")

    try:
        # 启动 cmd_tray-LAX.exe 进程，不带任何额外参数
        command = [PROGRAM_TO_START_FULL_PATH]
        logger.info(f"执行主程序命令 (不带参数): {' '.join(command)}")

        main_program_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # 合并标准错误到标准输出，确保捕获所有日志
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0 
        )
        logger.info(f"主程序 '{os.path.basename(PROGRAM_TO_START_FULL_PATH)}' 已成功启动。")
    except FileNotFoundError:
        logger.critical(f"无法找到或启动主程序。请确保文件存在且有执行权限: {PROGRAM_TO_START_FULL_PATH}")
        return False
    except PermissionError:
        logger.critical(f"没有足够的权限执行主程序。请尝试以管理员身份运行脚本: {PROGRAM_TO_START_FULL_PATH}")
        return False
    except Exception as e:
        logger.critical(f"启动主程序时发生未知错误: {e}")
        logger.exception("启动主程序的详细错误信息:") 
        return False
    
    return True # 启动成功

if __name__ == "__main__":
    logger.info("--- Cloudflare DDNS 优选 IP 工具 (日志监控版) 启动 ---")
    
    logger.info(f"配置的待启动主程序路径 (硬编码): {PROGRAM_TO_START_RELATIVE_PATH}")
    logger.info(f"主程序 (cmd_tray-LAX.exe) 将根据其自身配置 (cmd_tray.yaml) 来启动 cfnat-windows-amd64.exe。")
    logger.info(f"脚本将从主程序标准输出中监听所有信息，并记录到日志文件中。")
    logger.info(f"日志文件: {DEFAULT_LOG_FILE_NAME}, 日志级别: {DEFAULT_LOG_LEVEL}")

    if not start_main_program_and_log_output():
        logger.critical("主程序启动失败，请检查之前的错误日志。")
        sys.exit(1) # 启动失败则退出

    # 启动一个线程来读取主程序的输出 
    output_reader_thread = threading.Thread(target=enqueue_output, args=(main_program_process.stdout, output_queue), name="Main_Program_Output_Reader")
    output_reader_thread.daemon = True # 设置为守护线程
    output_reader_thread.start()

    logger.info("开始持续监控主程序输出并记录到日志...")
    
    # 主循环：持续监控主程序进程状态
    try:
        while not stop_event.is_set():
            # 这里不再从队列取数据，因为 enqueue_output 已经直接记录到日志了
            # 我们只需要检查主程序是否仍在运行
            if main_program_process.poll() is not None:
                logger.warning(f"主程序 '{os.path.basename(PROGRAM_TO_START_FULL_PATH)}' 进程已退出。")
                stop_event.set() # 通知所有线程停止
                break # 退出循环
            
            time.sleep(1) # 每秒检查一次主程序状态，减少CPU占用
            
    except KeyboardInterrupt:
        logger.info("用户中断 (Ctrl+C)：正在停止主程序进程和相关线程...")
    except Exception as e:
        logger.exception(f"主处理循环中发生错误: {e}")
    finally:
        # 确保进程和线程被清理
        logger.info("开始清理主程序进程和相关线程...")
        stop_event.set() # 设置停止事件，通知读取线程退出
        if main_program_process and main_program_process.poll() is None: # 如果进程仍在运行
            try:
                logger.info("正在尝试终止主程序进程...")
                main_program_process.terminate() # 尝试优雅终止
                main_program_process.wait(timeout=5) # 等待进程终止
                if main_program_process.poll() is None: # 如果还未终止，则强制杀死
                    logger.warning("主程序进程未响应终止请求，强制杀死...")
                    main_program_process.kill()
            except Exception as e:
                logger.error(f"终止主程序进程时发生错误: {e}")
                logger.exception("终止主程序进程的详细错误信息:")

        if output_reader_thread.is_alive():
            logger.debug("等待主程序输出读取线程停止...")
            output_reader_thread.join(timeout=2) # 等待读取线程完成
            if output_reader_thread.is_alive():
                logger.warning("主程序输出读取线程未能及时停止。")
        logger.info("主程序进程和相关线程清理完成。")

    logger.info("程序已退出。")
