# cfnatddns.py

import subprocess
import threading
import time
import os
import sys
import logging
from logging.handlers import RotatingFileHandler

# --- 硬编码路径和日志设置 ---
# 获取脚本或打包后的exe所在的目录
base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

# 待启动的程序路径（相对于脚本所在目录）
# 确保 'cfnat_winGUI-LAX' 文件夹和 'cmd_tray-LAX.exe' 在正确的位置
PROGRAM_TO_START_RELATIVE_PATH = "cfnat_winGUI-LAX/cmd_tray-LAX.exe"

# 日志文件名
LOG_FILE_NAME = "cfnatddns.log"
# 日志级别，设置为 INFO 可以在控制台看到主要的启动信息，
# 文件日志会记录所有 DEBUG 级别的程序原始输出
LOG_LEVEL = "INFO" 

# --- 日志配置函数 ---
def setup_logging(log_file_name, log_level_str):
    logger = logging.getLogger('cfnatddns_logger')
    logger.setLevel(logging.DEBUG) # 确保所有消息都能被处理器处理

    # 清除旧的处理器，防止重复添加
    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)
    
    log_file_path = os.path.join(base_dir, log_file_name)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    # 控制台日志
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO))
    logger.addHandler(console_handler)

    # 文件日志（带轮转）
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=1024 * 1024 * 5,  # 5 MB
        backupCount=5,             # 5个备份文件
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG) # 文件日志记录所有DEBUG信息，包括原始程序输出
    logger.addHandler(file_handler)

    return logger

# 初始化日志系统
logger = setup_logging(LOG_FILE_NAME, LOG_LEVEL)

# 全局变量用于进程管理
main_program_process = None
stop_event = threading.Event()

def enqueue_output(out_stream):
    """
    从子进程的标准输出中读取行，并直接写入日志。
    """
    logger.debug("开始读取主程序进程输出线程...")
    while not stop_event.is_set():
        try:
            line = out_stream.readline()
            if not line: # EOF
                logger.debug("主程序进程输出 EOF。")
                break
            
            # 尝试解码，优先UTF-8，其次GBK，最后系统默认
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
                # 直接将原始程序输出记录到日志，级别为 DEBUG
                logger.debug(f"[Program raw output] {decoded_line}") 
        except ValueError as e:
            if not stop_event.is_set():
                logger.error(f"读取程序输出管道时发生错误: {e}")
            break
        except Exception as e:
            logger.exception(f"读取输出时发生未知错误: {e}") 
            break
    logger.debug("读取主程序进程输出线程已停止。")
    out_stream.close()

def start_and_monitor_program():
    """
    启动 cmd_tray-LAX.exe 并监控其输出。
    """
    global main_program_process

    full_program_path = os.path.join(base_dir, PROGRAM_TO_START_RELATIVE_PATH)

    logger.info(f"准备启动主程序: {full_program_path}")

    if not os.path.exists(full_program_path):
        logger.critical(f"错误: 找不到主程序可执行文件。请检查路径: {full_program_path}。")
        return False
    
    if not os.path.isfile(full_program_path):
        logger.critical(f"错误: 路径 {full_program_path} 不是一个有效的文件。")
        return False

    # 检查 cmd_tray.yaml 是否存在，作为提示
    yaml_path = os.path.join(os.path.dirname(full_program_path), "cmd_tray.yaml")
    if not os.path.exists(yaml_path):
        logger.warning(f"警告: 未找到 'cmd_tray.yaml' 文件在 {yaml_path}。请确保它与 cmd_tray-LAX.exe 在同一目录下。主程序可能无法正常启动其子进程。")
    else:
        logger.info(f"找到 'cmd_tray.yaml' 文件: {yaml_path}")

    try:
        # 启动 cmd_tray-LAX.exe，不带任何参数
        # 它会自己处理其内部的 cfnat-windows-amd64.exe 启动逻辑
        command = [full_program_path]
        logger.info(f"执行命令: {' '.join(command)}")

        main_program_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # 合并错误输出到标准输出，确保所有日志都被捕获
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0 # 仅在Windows上隐藏窗口
        )
        logger.info(f"主程序 '{os.path.basename(full_program_path)}' 已成功启动。PID: {main_program_process.pid}")
        return True
    except FileNotFoundError:
        logger.critical(f"无法找到或启动主程序。请确保文件存在且有执行权限: {full_program_path}")
        return False
    except PermissionError:
        logger.critical(f"没有足够的权限执行主程序。请尝试以管理员身份运行此脚本。")
        return False
    except Exception as e:
        logger.critical(f"启动主程序时发生未知错误: {e}")
        logger.exception("启动主程序的详细错误信息:")
        return False

if __name__ == "__main__":
    logger.info("--- Cloudflare DDNS 监控启动 ---")
    logger.info(f"将启动主程序: {PROGRAM_TO_START_RELATIVE_PATH}")
    logger.info(f"所有程序输出将记录到日志文件: {LOG_FILE_NAME}")

    # 尝试启动主程序
    if not start_and_monitor_program():
        logger.critical("主程序启动失败，请检查日志。")
        sys.exit(1) # 如果启动失败，直接退出

    # 启动一个线程来持续读取主程序的输出并记录到日志
    output_reader_thread = threading.Thread(
        target=enqueue_output, 
        args=(main_program_process.stdout,), 
        name="Main_Program_Output_Reader"
    )
    output_reader_thread.daemon = True # 设置为守护线程，主程序退出时它也会自动退出
    output_reader_thread.start()

    logger.info("开始持续监控主程序运行状态和输出...")
    
    try:
        while not stop_event.is_set():
            # 仅检查主程序是否仍在运行
            if main_program_process.poll() is not None:
                logger.warning(f"主程序 '{os.path.basename(PROGRAM_TO_START_RELATIVE_PATH)}' 进程已退出。退出代码: {main_program_process.poll()}")
                stop_event.set() 
                break 
            
            time.sleep(1) # 每秒检查一次，减少CPU占用
            
    except KeyboardInterrupt: # 用户按 Ctrl+C
        logger.info("用户中断 (Ctrl+C)：正在停止主程序进程和相关线程...")
    except Exception as e:
        logger.exception(f"主监控循环中发生错误: {e}")
    finally:
        logger.info("开始清理主程序进程和相关线程...")
        stop_event.set() # 通知所有线程停止
        
        # 尝试优雅终止主程序进程
        if main_program_process and main_program_process.poll() is None:
            try:
                logger.info("正在尝试终止主程序进程...")
                main_program_process.terminate() 
                main_program_process.wait(timeout=5)
                if main_program_process.poll() is None:
                    logger.warning("主程序进程未响应终止请求，强制杀死...")
                    main_program_process.kill()
            except Exception as e:
                logger.error(f"终止主程序进程时发生错误: {e}")
                logger.exception("终止主程序进程的详细错误信息:")

        # 等待输出读取线程完成
        if output_reader_thread.is_alive():
            logger.debug("等待主程序输出读取线程停止...")
            output_reader_thread.join(timeout=2)
            if output_reader_thread.is_alive():
                logger.warning("主程序输出读取线程未能及时停止。")
        logger.info("主程序进程和相关线程清理完成。")

    logger.info("程序已退出。")
