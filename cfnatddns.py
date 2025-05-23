import requests
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

def get_local_ipv4():
    """ 获取本机的 IPv4 地址 """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))  # 任意外部地址，只用于获取本地 IPv4
        local_ipv4 = s.getsockname()[0]
        s.close()
        return local_ipv4
    except Exception:
        return "127.0.0.1"  # 如果无法获取，返回默认 IPv4 地址

def send_http_proxy_request(url, proxy):
    """ 通过 HTTP 代理发送请求 """
    try:
        logger.info(f"通过 HTTP 代理 {proxy} 访问 {url}")
        response = requests.get(url, proxies=proxy)
        logger.info(f"响应代码: {response.status_code}")
        logger.info(f"响应内容: {response.text[:100]}...")  # 只显示前 100 个字符
        return response
    except Exception as e:
        logger.error(f"请求失败: {e}")
        return None

if __name__ == '__main__':
    while True:
        local_ipv4 = get_local_ipv4()
        logger.info(f"本机内网 IPv4 地址: {local_ipv4}")

        # 设置目标网站和代理
        server_host = 'cloudflaremirrors.com'
        path = '/debian'  # 目标路径
        url = f"http://{server_host}{path}"

        # 设置代理，这里使用示例代理，你需要替换成你实际使用的代理服务器
        proxy = {
            "http": f"http://{local_ipv4}:1234",  # 本机 IP 和端口 1234
            "https": f"http://{local_ipv4}:1234",  # 本机 IP 和端口 1234
        }

        # 通过代理发送 HTTP 请求
        send_http_proxy_request(url, proxy)

        # 每 10 秒清理一次日志
        time.sleep(10)
