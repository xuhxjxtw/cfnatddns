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

# 待启动的程序完整路径
# 直接构建绝对路径，避免相对路径的歧义
# 假设 cmd_tray-LAX.exe 就在 base_dir/cfnat_winGUI-LAX/ 目录下
PROGRAM_TO_START_FULL_PATH = os.path.join(base_dir, "cfnat_winGUI-LAX", "cmd_tray-LAX.exe")


# 日志文件名
LOG_FILE_NAME = "cfnatddns.log"
LOG_LEVEL = "INFO"

# --- 日志配置函数 ---
def setup_logging(log_file_name, log_level_str):
    logger = logging.getLogger('cfnatddns_logger')
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    log_file_path = os.path.join(base_dir, log_file_name)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO))
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=1024 * 1024 * 5,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging(LOG_FILE_NAME, LOG_LEVEL)

main_program_process = None
stop_event = threading.Event()

def enqueue_output(out_stream):
    logger.debug("开始读取主程序进程输出线程...")
    while not stop_event.is_set():
        try:
            line = out_stream.readline()
            if not line:
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
    global main_program_process

    logger.info(f"准备启动主程序: {PROGRAM_TO_START_FULL_PATH}")

    if not os.path.exists(PROGRAM_TO_START_FULL_PATH):
        logger.critical(f"错误: 找不到主程序可执行文件。请检查路径: {PROGRAM_TO_START_FULL_PATH}。")
        return False

    if not os.path.isfile(PROGRAM_TO_START_FULL_PATH):
        logger.critical(f"错误: 路径 {PROGRAM_TO_START_FULL_PATH} 不是一个有效的文件。")
        return False

    yaml_path = os.path.join(os.path.dirname(PROGRAM_TO_START_FULL_PATH), "cmd_tray.yaml")
    if not os.path.exists(yaml_path):
        logger.warning(f"警告: 未找到 'cmd_tray.yaml' 文件在 {yaml_path}。请确保它与 cmd_tray-LAX.exe 在同一目录下。主程序可能无法正常启动其子进程。")
    else:
        logger.info(f"找到 'cmd_tray.yaml' 文件: {yaml_path}")

    try:
        command = [PROGRAM_TO_START_FULL_PATH]
        logger.info(f"执行命令: {' '.join(command)}")

        main_program_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        logger.info(f"主程序 '{os.path.basename(PROGRAM_TO_START_FULL_PATH)}' 已成功启动。PID: {main_program_process.pid}")
        return True
    except FileNotFoundError:
        logger.critical(f"无法找到或启动主程序。请确保文件存在且有执行权限: {PROGRAM_TO_START_FULL_PATH}")
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
    logger.info(f"将启动主程序: {PROGRAM_TO_START_FULL_PATH}")
    logger.info(f"所有程序输出将记录到日志文件: {LOG_FILE_NAME}")

    if not start_and_monitor_program():
        logger.critical("主程序启动失败，请检查日志。")
        sys.exit(1)

    output_reader_thread = threading.Thread(
        target=enqueue_output,
        args=(main_program_process.stdout,),
        name="Main_Program_Output_Reader"
    )
    output_reader_thread.daemon = True
    output_reader_thread.start()

    logger.info("开始持续监控主程序运行状态和输出...")

    try:
        while not stop_event.is_set():
            if main_program_process.poll() is not None:
                logger.warning(f"主程序 '{os.path.basename(PROGRAM_TO_START_FULL_PATH)}' 进程已退出。退出代码: {main_program_process.poll()}")
                stop_event.set()
                break

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("用户中断 (Ctrl+C)：正在停止主程序进程和相关线程...")
    except Exception as e:
        logger.exception(f"主监控循环中发生错误: {e}")
    finally:
        logger.info("开始清理主程序进程和相关线程...")
        stop_event.set()

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

        if output_reader_thread.is_alive():
            logger.debug("等待主程序输出读取线程停止...")
            output_reader_thread.join(timeout=2)
            if output_reader_thread.is_alive():
                logger.warning("主程序输出读取线程未能及时停止。")
        logger.info("主程序进程和相关线程清理完成。")

    logger.info("程序已退出。")
