import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import threading
import json
import socket
import requests
import time
import os
import sys # 引入 sys 模块
import logging # 引入 logging 模块

# --- 日志配置 (在所有代码之前生效，独立于 GUI) ---
# 获取程序运行的根目录
if getattr(sys, 'frozen', False):
    # 如果是打包后的 EXE
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 如果是直接运行 Python 脚本
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(BASE_DIR, 'app.log')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json') # 确保配置文件路径正确

logging.basicConfig(
    level=logging.DEBUG, # 设置为 DEBUG 获取最详细的日志
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'), # 输出到文件
        logging.StreamHandler(sys.stdout) # 输出到控制台 (打包时不带 --noconsole 会显示)
    ]
)
# --- 日志配置结束 ---

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith("127.") or ip == "0.0.0.0":
            raise Exception("Loopback or invalid IP detected")
        logging.debug(f"成功获取本地局域网 IP: {ip}")
        return ip
    except Exception as e:
        logging.error(f"获取本地局域网 IP 失败: {e}")
        return None

class App:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.root.title("CF DDNS Listener")
        self.root.geometry("500x300")
        
        # 将日志重定向到 ScrolledText
        self.text = ScrolledText(self.root, state='disabled')
        self.text.pack(expand=True, fill='both')
        self.queue = [] # 用一个队列来线程安全地更新GUI
        self.root.after(100, self._process_log_queue) # 定时处理日志队列

        # 重写 logging 的 handler，使其也输出到 ScrolledText
        self.gui_log_handler = GuiLogHandler(self.queue)
        logging.getLogger().addHandler(self.gui_log_handler)
        
        self.icon = None
        self.log_to_gui(f"程序启动，根目录: {BASE_DIR}")
        self.log_to_gui(f"配置文件路径: {CONFIG_FILE}")
        self.log_to_gui(f"日志文件路径: {LOG_FILE}")
        
        self.start_all()
        self.setup_tray()

    def _process_log_queue(self):
        while self.queue:
            message = self.queue.pop(0)
            self.text.configure(state='normal')
            self.text.insert(tk.END, message)
            self.text.configure(state='disabled')
            self.text.see(tk.END)
        self.root.after(100, self._process_log_queue) # 继续定时检查队列

    def log_to_gui(self, message):
        # 仅用于在GUI初始化后将特定消息发送到GUI
        # 通常情况下，我们直接使用 logging.info/error 等
        formatted_message = f"{time.strftime('%H:%M:%S')} - {message}\n"
        self.queue.append(formatted_message)

    def start_all(self):
        nodes = self.config.get("nodes", {})
        if not nodes:
            logging.warning("配置文件中未找到 'nodes' 配置。请检查 config.json。")
            self.log_to_gui("警告：配置文件中未找到 'nodes' 配置。")
            return

        for name, conf in nodes.items():
            port = conf.get("listen_port")
            if not port:
                logging.warning(f"节点 '{name}' 未配置 'listen_port'。跳过此节点。")
                self.log_to_gui(f"警告：节点 '{name}' 未配置 'listen_port'。")
                continue
            logging.info(f"启动节点 '{name}' 的监听线程...")
            threading.Thread(target=self.listen, args=(name, port, conf), daemon=True).start()

    def listen(self, name, port, conf):
        host_ip = get_local_ip()
        if not host_ip:
            logging.error(f"[{name}] 未能获取有效局域网 IP，监听线程退出。")
            self.log_to_gui(f"[{name}] 未能获取有效局域网 IP，监听失败")
            return
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # 允许重复使用地址
        try:
            logging.info(f"[{name}] 尝试绑定: {host_ip}:{port}")
            s.bind((host_ip, port))
            logging.info(f"[{name}] 成功绑定: {host_ip}:{port}")
        except Exception as e:
            logging.error(f"[{name}] 绑定失败 {host_ip}:{port} 错误: {e}")
            self.log_to_gui(f"[{name}] 绑定失败 {host_ip}:{port} 错误: {e}")
            return
        
        s.listen(5)
        logging.info(f"[{name}] 正在监听: {host_ip}:{port}")
        self.log_to_gui(f"[{name}] 正在监听: {host_ip}:{port}")
        
        while True:
            try:
                conn, addr = s.accept()
                conn.close()
                logging.info(f"[{name}] 收到来自 {addr} 的请求。")
                self.log_to_gui(f"[{name}] 收到请求: {addr}")
                threading.Thread(target=self.update_cf, args=(name, conf), daemon=True).start()
            except Exception as e:
                logging.error(f"[{name}] 监听循环中发生异常: {e}")
                self.log_to_gui(f"[{name}] 监听循环中发生异常: {e}")
                time.sleep(1) # 避免错误循环过快

    def update_cf(self, name, conf):
        # 增加一些错误检查，确保 cloudflare 配置存在
        if "cloudflare" not in conf:
            logging.error(f"[{name}] 配置中缺少 'cloudflare' 部分。")
            self.log_to_gui(f"[{name}] 配置中缺少 'cloudflare' 部分。")
            return
        cf_conf = conf["cloudflare"]
        required_cf_keys = ["email", "api_key", "zone_id", "record_name"]
        if not all(key in cf_conf for key in required_cf_keys):
            logging.error(f"[{name}] Cloudflare 配置不完整，缺少必需字段。")
            self.log_to_gui(f"[{name}] Cloudflare 配置不完整。")
            return

        ip = self.get_public_ip(conf)
        if not ip:
            logging.error(f"[{name}] 获取公网 IP 失败，无法更新 DNS。")
            self.log_to_gui(f"[{name}] 获取公网 IP 失败")
            return
        
        logging.info(f"[{name}] 获取到公网 IP: {ip}")

        headers = {
            "X-Auth-Email": cf_conf["email"],
            "X-Auth-Key": cf_conf["api_key"],
            "Content-Type": "application/json"
        }

        data = {
            "type": "A", # 假设总是 A 记录
            "name": cf_conf["record_name"],
            "content": ip,
            "ttl": 120,
            "proxied": False # 通常 DDNS 不会设置代理
        }

        zone_id = cf_conf["zone_id"]
        record_name = cf_conf["record_name"]

        try:
            # 1. 查询现有 DNS 记录
            logging.info(f"[{name}] 查询 Cloudflare DNS 记录: {record_name}")
            res = requests.get(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={record_name}&type=A",
                headers=headers,
                timeout=10 # 添加超时
            )
            res.raise_for_status() # 检查 HTTP 状态码
            recs = res.json().get("result", [])
            
            record_id = None
            current_ip = None
            for r in recs:
                if r["name"] == record_name and r["type"] == "A":
                    record_id = r["id"]
                    current_ip = r["content"]
                    break

            if not record_id:
                logging.warning(f"[{name}] 找不到现有 DNS 记录 '{record_name}'，尝试创建新记录。")
                # 尝试创建新记录
                create_data = {
                    "type": "A",
                    "name": record_name,
                    "content": ip,
                    "ttl": 120,
                    "proxied": False
                }
                res = requests.post(
                    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                    headers=headers,
                    json=create_data,
                    timeout=10
                )
                res.raise_for_status()
                if res.json().get("success"):
                    logging.info(f"[{name}] 成功创建 DNS 记录 '{record_name}'，IP: {ip}")
                    self.log_to_gui(f"[{name}] 创建成功: {ip}")
                else:
                    logging.error(f"[{name}] 创建 DNS 记录失败: {res.text}")
                    self.log_to_gui(f"[{name}] 创建失败: {res.text}")
                return # 创建成功或失败后返回

            if current_ip == ip:
                logging.info(f"[{name}] IP 地址未变化 ({ip})，无需更新。")
                self.log_to_gui(f"[{name}] IP 未变: {ip}")
                return

            # 2. 更新 DNS 记录
            logging.info(f"[{name}] IP 地址已变化，正在更新 DNS 记录。旧IP: {current_ip}, 新IP: {ip}")
            res = requests.put(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}",
                headers=headers,
                json=data,
                timeout=10 # 添加超时
            )
            res.raise_for_status() # 检查 HTTP 状态码

            if res.json().get("success"):
                logging.info(f"[{name}] DNS 记录更新成功: {ip}")
                self.log_to_gui(f"[{name}] 更新成功: {ip}")
            else:
                logging.error(f"[{name}] DNS 记录更新失败: {res.text}")
                self.log_to_gui(f"[{name}] 更新失败: {res.text}")

        except requests.exceptions.RequestException as e:
            logging.error(f"[{name}] Cloudflare API 请求失败: {e}", exc_info=True)
            self.log_to_gui(f"[{name}] Cloudflare API 错误: {e}")
        except json.JSONDecodeError as e:
            logging.error(f"[{name}] Cloudflare API 响应解析失败: {e}，响应: {res.text if 'res' in locals() else '无响应'}", exc_info=True)
            self.log_to_gui(f"[{name}] Cloudflare 响应解析错误: {e}")
        except Exception as e:
            logging.error(f"[{name}] 更新 Cloudflare DNS 记录时发生未预期异常: {e}", exc_info=True)
            self.log_to_gui(f"[{name}] 更新异常: {e}")

    def get_public_ip(self, conf):
        port = conf.get("listen_port", 0) # 默认为0以防万一
        ip4 = None
        ip6 = None
        
        # 尝试从内网服务获取 IPv4
        if conf["cloudflare"].get("enable_ipv4", True):
            try:
                local_ip = get_local_ip()
                if local_ip:
                    # 注意：这里假设您的内网服务会响应 /ipv4 请求并返回公网 IP
                    url = f"http://{local_ip}:{port}/ipv4" 
                    logging.info(f"尝试从内网服务获取 IPv4: {url}")
                    resp = requests.get(url, timeout=3)
                    if resp.status_code == 200 and resp.text.strip():
                        ip4_candidate = resp.text.strip()
                        if self._is_valid_ipv4(ip4_candidate): # 验证 IP 地址格式
                            ip4 = ip4_candidate
                            logging.info(f"通过内网服务获取 IPv4 成功: {ip4}")
                        else:
                            logging.warning(f"内网服务返回的 IPv4 格式不正确: {ip4_candidate}")
                            raise Exception("内网服务返回格式不正确")
                    else:
                        logging.warning(f"内网服务 IPv4 响应状态码非200或无内容: {resp.status_code}, {resp.text}")
                        raise Exception("内网服务返回异常")
                else:
                    raise Exception("无有效内网IP，无法尝试内网服务")
            except Exception as e:
                logging.warning(f"IPv4 内网获取失败，尝试公网接口: {e}")
                try:
                    ip4_candidate = requests.get("https://4.ipw.cn", timeout=5).text.strip()
                    if self._is_valid_ipv4(ip4_candidate):
                        ip4 = ip4_candidate
                        logging.info(f"通过公网接口获取 IPv4 成功: {ip4}")
                    else:
                        logging.warning(f"公网接口返回的 IPv4 格式不正确: {ip4_candidate}")
                except Exception as e2:
                    logging.error(f"IPv4 公网接口获取失败: {e2}")

        # 尝试从内网服务获取 IPv6
        if conf["cloudflare"].get("enable_ipv6", False):
            try:
                local_ip = get_local_ip() # 再次获取本地IP，虽然可能没变，但确保上下文独立
                if local_ip:
                    url = f"http://{local_ip}:{port}/ipv6"
                    logging.info(f"尝试从内网服务获取 IPv6: {url}")
                    resp = requests.get(url, timeout=3)
                    if resp.status_code == 200 and resp.text.strip():
                        ip6_candidate = resp.text.strip()
                        if self._is_valid_ipv6(ip6_candidate): # 验证 IP 地址格式
                            ip6 = ip6_candidate
                            logging.info(f"通过内网服务获取 IPv6 成功: {ip6}")
                        else:
                            logging.warning(f"内网服务返回的 IPv6 格式不正确: {ip6_candidate}")
                            raise Exception("内网服务返回格式不正确")
                    else:
                        logging.warning(f"内网服务 IPv6 响应状态码非200或无内容: {resp.status_code}, {resp.text}")
                        raise Exception("内网服务返回异常")
                else:
                    raise Exception("无有效内网IP，无法尝试内网服务")
            except Exception as e:
                logging.warning(f"IPv6 内网获取失败，尝试公网接口: {e}")
                try:
                    ip6_candidate = requests.get("https://6.ipw.cn", timeout=5).text.strip()
                    if self._is_valid_ipv6(ip6_candidate):
                        ip6 = ip6_candidate
                        logging.info(f"通过公网接口获取 IPv6 成功: {ip6}")
                    else:
                        logging.warning(f"公网接口返回的 IPv6 格式不正确: {ip6_candidate}")
                except Exception as e2:
                    logging.error(f"IPv6 公网接口获取失败: {e2}")

        if ip4:
            return ip4
        if ip6:
            # 如果配置中 enable_ipv4 和 enable_ipv6 同时为 true，并且两者都成功获取到，
            # 您的代码目前是优先返回 IPv4。如果您希望优先返回 IPv6，需要调整逻辑。
            # Cloudflare DDNS通常会针对A记录(IPv4)和AAAA记录(IPv6)分别更新。
            # 这里的 get_public_ip 返回一个单一IP，意味着您可能需要为IPv4和IPv6分别调用 update_cf。
            # 或者修改 update_cf 来同时处理两种记录。
            # 对于您目前的代码，如果enable_ipv4为True且成功获取，它会直接返回ipv4。
            # 如果您只需要一个，那当前逻辑没问题。
            return ip6 
        
        logging.error("未能获取任何有效的公网 IP 地址。")
        return None

    def _is_valid_ipv4(self, ip_str):
        try:
            socket.inet_pton(socket.AF_INET, ip_str)
            return True
        except socket.error:
            return False

    def _is_valid_ipv6(self, ip_str):
        try:
            socket.inet_pton(socket.AF_INET6, ip_str)
            return True
        except socket.error:
            return False


    def setup_tray(self):
        icon_path = os.path.join(BASE_DIR, "icon.ico") # 确保图标路径正确
        if os.path.exists(icon_path):
            image = Image.open(icon_path)
        else:
            image = Image.new("RGB", (64, 64), "blue")
            logging.warning(f"未找到图标文件: {icon_path}，使用默认蓝色图标。")
        menu = Menu(MenuItem("显示", self.show_window), MenuItem("退出", self.quit_app))
        self.icon = Icon("CFTray", image, "CF DDNS", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon, item):
        self.root.after(0, lambda: self.root.deiconify())

    def quit_app(self, icon, item):
        logging.info("收到退出应用指令。")
        icon.stop()
        self.root.after(0, self.root.destroy)
        # 确保程序彻底退出
        os._exit(0) 

# 自定义 Logging Handler，将日志消息发送到 Tkinter GUI
class GuiLogHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        msg = self.format(record)
        self.queue.append(f"{msg}\n")

# --- 主程序入口 (添加了错误处理) ---
def load_config():
    # 这里的 CONFIG_FILE 已经包含 BASE_DIR 了
    logging.info(f"尝试加载配置文件: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

if __name__ == "__main__":
    # --- 在启动 Tkinter 之前捕获配置文件加载错误 ---
    app_config = None
    try:
        app_config = load_config()
        logging.info("配置文件加载成功！")
    except FileNotFoundError:
        logging.critical(f"严重错误：未找到配置文件！请确保 '{CONFIG_FILE}' 存在于 EXE 相同目录下。程序将退出。")
        # 弹出一个简单的错误框（Tkinter未完全初始化，可能无法显示复杂GUI）
        tk.messagebox.showerror("错误", f"未找到配置文件！请确保 '{CONFIG_FILE}' 存在于 EXE 相同目录下。")
        sys.exit(1) # 立即退出程序
    except json.JSONDecodeError as e:
        logging.critical(f"严重错误：配置文件 '{CONFIG_FILE}' 格式不正确，请检查 JSON 语法：{e}。程序将退出。", exc_info=True)
        tk.messagebox.showerror("错误", f"配置文件格式不正确！请检查 '{CONFIG_FILE}' 的 JSON 语法。错误信息：{e}")
        sys.exit(1) # 立即退出程序
    except Exception as e:
        logging.critical(f"严重错误：加载配置文件时发生未知错误: {e}。程序将退出。", exc_info=True)
        tk.messagebox.showerror("错误", f"加载配置文件时发生未知错误！错误信息：{e}")
        sys.exit(1) # 立即退出程序
    # --- 配置文件加载错误处理结束 ---

    # 如果配置文件加载成功，则继续启动 GUI
    root = tk.Tk()
    try:
        app = App(root, app_config)
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw()) # 隐藏窗口到托盘而不是关闭
        root.mainloop()
    except Exception as e:
        logging.critical(f"程序 GUI 或主循环启动时发生严重错误: {e}", exc_info=True)
        tk.messagebox.showerror("严重错误", f"程序启动时发生严重错误！请查看日志文件 '{LOG_FILE}'。\n错误信息：{e}")
        sys.exit(1)
