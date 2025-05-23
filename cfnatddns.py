import socket
import logging
from logging.handlers import RotatingFileHandler
import time

# --- 日志配置 ---
LOG_FILE_NAME = "cfnatddns.log"
LOG_LEVEL = "INFO"

def setup_logging(log_file_name, log_level_str):
    logger = logging.getLogger('cfnatddns_logger')
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level_str.upper(), logging.INFO))
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file_name,
        maxBytes=1024 * 1024 * 5,  # 5MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging(LOG_FILE_NAME, LOG_LEVEL)

def get_local_ipv6():
    """ 获取本机的内网 IPv6 地址 """
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.connect(('2001:4860:4860::8888', 80))  # 使用 Google 的公共 IPv6 DNS 地址
        local_ipv6 = s.getsockname()[0]
        s.close()
        return local_ipv6
    except Exception:
        return "::1"  # 返回本地 IPv6 地址

def connect_tcp_to_cf(host, port, path):
    """ 连接到 Cloudflare 代理服务器并模拟 HTTP 请求（带绝对路径） """
    logger.info(f"尝试连接到 {host}:{port}，请求路径 {path}（Cloudflare 代理服务器）")
    try:
        # 通过 TCP 建立连接
        with socket.create_connection((host, port), timeout=5) as sock:
            # 构建 HTTP 请求，带绝对路径
            request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            sock.sendall(request.encode())
            response = sock.recv(4096)  # 接收响应
            logger.info(f"收到来自 Cloudflare 代理服务器的响应: {response.decode('utf-8', errors='ignore')[:100]}...")  # 只显示响应前 100 个字符
            server_ip = sock.getpeername()[0]  # 获取 Cloudflare 代理服务器的 IP 地址
            logger.info(f"成功与 Cloudflare 代理服务器建立 TCP 连接，代理 IP 地址: {server_ip}")
            return server_ip
    except Exception as e:
        logger.error(f"TCP 连接失败: {e}")
        return None

if __name__ == '__main__':
    while True:
        local_ipv6 = get_local_ipv6()
        logger.info(f"本机内网 IPv6 地址: {local_ipv6}")

        # 设置目标网站和端口
        server_host = 'cloudflaremirrors.com'
        server_port = 443  # 默认使用 443 端口 (HTTPS)
        path = '/debian'  # 目标路径

        # 连接到 Cloudflare 代理服务器
        connect_tcp_to_cf(server_host, server_port, path)

        # 每隔 60 秒再次运行
        time.sleep(60)  # 你可以根据需要调整间隔时间
