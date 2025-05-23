import socket
import ssl
import logging
from logging.handlers import RotatingFileHandler

# --- 日志配置 ---
LOG_FILE_NAME = "cfnatddns.log"
LOG_LEVEL = "INFO"

def setup_logging(log_file_name, log_level_str):
    # 创建日志记录器
    logger = logging.getLogger('cfnatddns_logger')
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    # 设置日志文件路径
    log_file_path = log_file_name
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    # 控制台日志
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO))
    logger.addHandler(console_handler)

    # 文件日志
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=1024 * 1024 * 5,  # 5MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging(LOG_FILE_NAME, LOG_LEVEL)

def get_local_ip():
    """ 获取本机的内网 IP 地址 """
    hostname = socket.gethostname()  # 获取主机名
    local_ip = socket.gethostbyname(hostname)  # 获取内网 IP 地址
    return local_ip

def get_server_ip(host, port=1234):
    logger.info(f"开始与本机 IP {host}:{port} 建立 TLS 连接并获取 IP 地址...")
    context = ssl.create_default_context()

    try:
        # 建立与目标服务器的 TLS 连接
        with socket.create_connection((host, port)) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                # 获取服务器的 IP 地址
                server_ip = ssock.getpeername()[0]
                logger.info(f"成功与 {host} 建立 TLS 连接，目标服务器 IP 地址是: {server_ip}")
                return server_ip
    except Exception as e:
        logger.error(f"与服务器 {host} 建立 TLS 连接时发生错误: {e}")
        return None

# 获取本机的内网 IP 地址
local_ip = get_local_ip()
logger.info(f"本机内网 IP 地址: {local_ip}")

# 与本机的 TLS 连接
get_server_ip(local_ip)
