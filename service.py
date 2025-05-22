import threading
import time

def run_ddns_loop():
    while True:
        print("正在执行 DDNS 更新...")
        # TODO: 替换为您的实际 Cloudflare DDNS 逻辑
        time.sleep(60)

def start_service():
    thread = threading.Thread(target=run_ddns_loop, daemon=True)
    thread.start()
