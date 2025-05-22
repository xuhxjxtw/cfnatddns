import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import threading
import json
import socket
import requests
import time
import os
import sys
import logging
from tkinter import messagebox

# 禁用托盘图标功能，设置为 False
# 之前遇到 NameError: name 'Image' is not defined 错误，
# 暂时禁用此功能以确保核心 DDNS 更新功能正常。
HAS_TRAY_ICON = False 

# --- 日志配置 (在所有代码之前生效，独立于 GUI) ---
# 获取程序运行的根目录
if getattr(sys, 'frozen', False):
    # 如果是打包后的 EXE，获取 EXE 所在的目录
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 如果是直接运行 Python 脚本，获取脚本所在的目录
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 定义日志文件和配置文件的完整路径
LOG_FILE = os.path.join(BASE_DIR, 'app.log')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

# 配置日志系统，将日志输出到文件和控制台
logging.basicConfig(
    level=logging.DEBUG, # 设置为 DEBUG 获取最详细的日志输出
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'), # 将日志写入到 app.log 文件
        logging.StreamHandler(sys.stdout) # 将日志输出到控制台 (打包时不带 --noconsole 会显示)
    ]
)
logging.info(f"程序启动，根目录: {BASE_DIR}")
logging.info(f"配置文件路径: {CONFIG_FILE}")
logging.info(f"日志文件路径: {LOG_FILE}")
# --- 日志配置结束 ---

def get_local_ip():
    """
    尝试获取本地局域网 IP 地址，排除回环地址和无效地址。
    通过连接一个外部地址来获取本地网卡用于出站连接的 IP。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)) # 连接一个外部地址 (Google DNS) 以获取本地IP
        ip = s.getsockname()[0] # 获取本地连接的 IP 地址
        s.close()
        # 检查获取到的 IP 是否是回环地址或无效地址
        if ip.startswith("127.") or ip == "0.0.0.0":
            logging.warning(f"检测到回环或无效本地IP: {ip}")
            raise Exception("Loopback or invalid IP detected")
        logging.debug(f"成功获取本地局域网 IP: {ip}")
        return ip
    except Exception as e:
        logging.error(f"获取本地局域网 IP 失败: {e}")
        return None

# 自定义 Logging Handler，将日志消息发送到 Tkinter GUI
class GuiLogHandler(logging.Handler):
    """
    一个自定义的日志处理程序，用于将日志消息线程安全地添加到 Tkinter ScrolledText 控件的队列中。
    Tkinter UI 操作必须在主线程进行，所以通过队列进行通信。
    """
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        # 格式化日志记录
        msg = self.format(record)
        # 将格式化后的消息添加到队列
        self.queue.append(f"{msg}\n")

class App:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.root.title("CF DDNS Listener")
        self.root.geometry("600x400") # 设置窗口大小
        
        # 创建 ScrolledText 控件用于显示日志
        self.text = ScrolledText(self.root, state='disabled', wrap='word') # 自动换行
        self.text.pack(expand=True, fill='both', padx=10, pady=10) # 填充整个窗口，并设置边距
        
        self.queue = [] # 用于存储待显示的日志消息队列
        # 定时器，每 100 毫秒调用 _process_log_queue 来更新 GUI
        self.root.after(100, self._process_log_queue)

        # 将自定义的 GuiLogHandler 添加到根日志器中
        # 确保只添加一次，避免重复日志显示在 GUI 上
        if not any(isinstance(h, GuiLogHandler) for h in logging.getLogger().handlers):
            self.gui_log_handler = GuiLogHandler(self.queue)
            logging.getLogger().addHandler(self.gui_log_handler)
        
        self.icon = None # 托盘图标对象，此处因 HAS_TRAY_ICON=False 而不再使用

        logging.info("GUI 初始化完成。")
        self.log_to_gui(f"程序启动，根目录: {BASE_DIR}")
        self.log_to_gui(f"配置文件路径: {CONFIG_FILE}")
        self.log_to_gui(f"日志文件路径: {LOG_FILE}")
        
        self.start_all()
        # 托盘图标功能已禁用，所以不调用 setup_tray()
        # if HAS_TRAY_ICON:
        #     self.setup_tray()
        # else:
        #     logging.warning("托盘图标功能已禁用。")

    def _process_log_queue(self):
        """
        定时从队列中取出日志消息并更新 GUI 的 ScrolledText 控件。
        这是 Tkinter 线程安全的更新 UI 的方式。
        """
        while self.queue:
            message = self.queue.pop(0) # 从队列头部取出消息
            self.text.configure(state='normal') # 允许编辑 ScrolledText
            self.text.insert(tk.END, message) # 在文本末尾插入消息
            self.text.configure(state='disabled') # 再次禁用编辑
            self.text.see(tk.END) # 滚动到文本末尾，显示最新消息
        self.root.after(100, self._process_log_queue) # 继续定时检查队列

    def log_to_gui(self, message):
        """
        将消息添加到 GUI 日志队列。
        此方法主要用于在 GUI 初始化后，将特定消息发送到 GUI。
        通常情况下，我们直接使用 logging.info/error 等标准日志方法。
        """
        formatted_message = f"{time.strftime('%H:%M:%S')} - {message}\n"
        self.queue.append(formatted_message)

    def start_all(self):
        """
        遍历配置文件中的所有节点，并为每个节点启动监听线程和周期性更新线程。
        """
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
            # 启动监听线程，daemon=True 确保主程序退出时线程也会退出
            threading.Thread(target=self.listen, args=(name, port, conf), daemon=True).start()
            
            # 关键：立即启动一个更新线程，确保程序启动后就会立即进行一次 DNS 更新
            logging.info(f"[{name}] 立即启动首次 DNS 更新。")
            threading.Thread(target=self.update_cf, args=(name, conf), daemon=True).start()


    def listen(self, name, port, conf):
        """
        监听指定端口的连接，并在收到请求时触发 DNS 更新。
        同时包含周期性更新逻辑。
        """
        host_ip = get_local_ip()
        if not host_ip:
            logging.error(f"[{name}] 未能获取有效局域网 IP，监听线程退出。")
            self.log_to_gui(f"[{name}] 未能获取有效局域网 IP，监听失败")
            return
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # 允许重复使用地址，避免重启时端口被占用
        try:
            logging.info(f"[{name}] 尝试绑定: {host_ip}:{port}")
            s.bind((host_ip, port))
            logging.info(f"[{name}] 成功绑定: {host_ip}:{port}")
        except Exception as e:
            logging.error(f"[{name}] 绑定失败 {host_ip}:{port} 错误: {e}")
            self.log_to_gui(f"[{name}] 绑定失败 {host_ip}:{port} 错误: {e}")
            return
        
        s.listen(5) # 允许最多 5 个挂起连接
        logging.info(f"[{name}] 正在监听: {host_ip}:{port}")
        self.log_to_gui(f"[{name}] 正在监听: {host_ip}:{port}")
        
        last_periodic_update_time = time.time() # 记录上次周期性更新的时间戳
        # 定义周期性更新间隔 (例如：300秒 = 5分钟，可根据需求调整)
        # 注意：Cloudflare 有 API 限速，不要设置太短，避免触发限速
        periodic_update_interval = 300 

        while True:
            try:
                # 尝试接受连接，设置一个短超时，避免一直阻塞
                s.settimeout(1.0) # 设置 accept 的超时时间为 1 秒
                conn, addr = s.accept() # 接受传入连接
                conn.close() # 立即关闭连接，只作为触发器
                logging.info(f"[{name}] 收到来自 {addr} 的请求。触发 DNS 更新。")
                self.log_to_gui(f"[{name}] 收到请求: {addr}，触发更新。")
                # 收到请求后，启动新线程进行 DNS 更新，避免阻塞监听
                threading.Thread(target=self.update_cf, args=(name, conf), daemon=True).start()
                last_periodic_update_time = time.time() # 收到请求并更新后重置周期计时
            except socket.timeout:
                # 如果 accept 超时，说明在 1 秒内没有收到连接，继续检查是否需要周期性更新
                pass
            except Exception as e:
                logging.error(f"[{name}] 监听循环中发生异常: {e}", exc_info=True)
                self.log_to_gui(f"[{name}] 监听循环中发生异常: {e}")
                time.sleep(1) # 避免错误循环过快，减轻CPU负担

            # 周期性检查和更新
            if time.time() - last_periodic_update_time > periodic_update_interval:
                logging.info(f"[{name}] 达到周期更新时间 ({periodic_update_interval}秒)，触发 DNS 更新。")
                self.log_to_gui(f"[{name}] 达到周期更新时间，触发 DNS 更新。")
                threading.Thread(target=self.update_cf, args=(name, conf), daemon=True).start()
                last_periodic_update_time = time.time() # 更新后重置计时


    def update_cf(self, name, conf):
        """获取公网IP并更新Cloudflare DNS记录。"""
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

        # 获取公网 IP (直接从公共服务获取，不再尝试内网服务)
        ip = self.get_public_ip_from_public_service(conf)
        if not ip:
            logging.error(f"[{name}] 获取公网 IP 失败，无法更新 DNS。")
            self.log_to_gui(f"[{name}] 获取公网 IP 失败")
            return
        
        logging.info(f"[{name}] 获取到公网 IP: {ip}")

        headers = {
            "X-Auth-Email": cf_conf["email"],
            "X-Auth-Key": cf_conf["api_key"], # 使用 API Key
            "Content-Type": "application/json"
        }

        # Cloudflare DNS 记录 API 的基础 URL
        base_url = f"https://api.cloudflare.com/client/v4/zones/{cf_conf['zone_id']}/dns_records"
        record_name = cf_conf["record_name"]

        try:
            # 1. 查询现有 DNS 记录
            logging.info(f"[{name}] 查询 Cloudflare DNS 记录: {record_name}")
            # 明确指定查询类型为 A 记录 (IPv4)
            res = requests.get(
                f"{base_url}?name={record_name}&type=A", 
                headers=headers,
                timeout=10 # 设置请求超时
            )
            res.raise_for_status() # 如果 HTTP 状态码不是 2xx，则抛出 HTTPError 异常
            recs = res.json().get("result", []) # 解析 JSON 响应，获取 DNS 记录列表
            
            record_id = None
            current_ip = None
            # 遍历记录，找到匹配的 A 记录
            for r in recs:
                if r["name"] == record_name and r["type"] == "A":
                    record_id = r["id"]
                    current_ip = r["content"]
                    break

            if not record_id:
                logging.warning(f"[{name}] 找不到现有 DNS 记录 '{record_name}'，尝试创建新记录。")
                # 如果记录不存在，则尝试创建新记录
                create_data = {
                    "type": "A",
                    "name": record_name,
                    "content": ip,
                    "ttl": 120, # TTL (Time To Live)，120秒
                    "proxied": cf_conf.get("proxied", False) # 是否通过 Cloudflare 代理，默认不代理
                }
                res = requests.post(
                    base_url, # 创建记录使用 POST 请求到基础 URL
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
                return # 创建成功或失败后返回，不再继续更新

            if current_ip == ip:
                logging.info(f"[{name}] IP 地址未变化 ({ip})，无需更新。")
                self.log_to_gui(f"[{name}] IP 未变: {ip}")
                return

            # 2. 更新 DNS 记录
            logging.info(f"[{name}] IP 地址已变化，正在更新 DNS 记录。旧IP: {current_ip}, 新IP: {ip}")
            update_data = {
                "type": "A",
                "name": record_name,
                "content": ip,
                "ttl": 120,
                "proxied": cf_conf.get("proxied", False)
            }
            res = requests.put(
                f"{base_url}/{record_id}", # 更新记录使用 PUT 请求到特定记录 ID 的 URL
                headers=headers,
                json=update_data,
                timeout=10
            )
            res.raise_for_status()

            if res.json().get("success"):
                logging.info(f"[{name}] DNS 记录更新成功: {ip}")
                self.log_to_gui(f"[{name}] 更新成功: {ip}")
            else:
                logging.error(f"[{name}] DNS 记录更新失败: {res.text}")
                self.log_to_gui(f"[{name}] 更新失败: {res.text}")

        except requests.exceptions.RequestException as e:
            # 捕获所有 requests 相关的异常（网络问题、超时、HTTP 状态码错误等）
            logging.error(f"[{name}] Cloudflare API 请求失败: {e}", exc_info=True)
            self.log_to_gui(f"[{name}] Cloudflare API 错误: {e}")
        except json.JSONDecodeError as e:
            # 捕获 JSON 解析错误，如果 Cloudflare 返回了非 JSON 格式的错误响应
            logging.error(f"[{name}] Cloudflare API 响应解析失败: {e}，响应: {res.text if 'res' in locals() else '无响应'}", exc_info=True)
            self.log_to_gui(f"[{name}] Cloudflare 响应解析错误: {e}")
        except Exception as e:
            # 捕获其他所有未预期的异常
            logging.error(f"[{name}] 更新 Cloudflare DNS 记录时发生未预期异常: {e}", exc_info=True)
            self.log_to_gui(f"[{name}] 更新异常: {e}")

    def get_public_ip_from_public_service(self, conf):
        """
        只从公共 IP 查询接口获取公网 IP 地址。
        """
        ip4 = None
        ip6 = None
        
        if conf["cloudflare"].get("enable_ipv4", True):
            try:
                # 备用公网 IP 获取接口 (IPv4)
                ip4_candidate = requests.get("https://4.ipw.cn", timeout=5).text.strip()
                if self._is_valid_ipv4(ip4_candidate):
                    ip4 = ip4_candidate
                    logging.info(f"通过公网接口获取 IPv4 成功: {ip4}")
                else:
                    logging.warning(f"公网接口返回的 IPv4 格式不正确: '{ip4_candidate}'")
            except Exception as e:
                logging.error(f"IPv4 公网接口获取失败: {e}")

        if conf["cloudflare"].get("enable_ipv6", False):
            try:
                # 备用公网 IP 获取接口 (IPv6)
                ip6_candidate = requests.get("https://6.ipw.cn", timeout=5).text.strip()
                if self._is_valid_ipv6(ip6_candidate):
                    ip6 = ip6_candidate
                    logging.info(f"通过公网接口获取 IPv6 成功: {ip6}")
                else:
                    logging.warning(f"公网接口返回的 IPv6 格式不正确: '{ip6_candidate}'")
            except Exception as e:
                logging.error(f"IPv6 公网接口获取失败: {e}")

        if ip4:
            return ip4
        if ip6:
            # 如果同时启用了 IPv4 和 IPv6，并且两者都成功获取，
            # 这里的逻辑是优先返回 IPv4 (如果 ip4 存在)。
            # 如果 ip4 不存在但 ip6 存在，则返回 ip6。
            # Cloudflare DDNS通常会针对 A 记录 (IPv4) 和 AAAA 记录 (IPv6) 分别更新。
            # 这里的 get_public_ip 只返回一个 IP，意味着您可能需要为 IPv4 和 IPv6 分别调用 update_cf。
            return ip6 
        
        logging.error("未能获取任何有效的公网 IP 地址。")
        return None

    def _is_valid_ipv4(self, ip_str):
        """验证字符串是否为有效的 IPv4 地址。"""
        try:
            socket.inet_pton(socket.AF_INET, ip_str)
            return True
        except socket.error:
            return False

    def _is_valid_ipv6(self, ip_str):
        """验证字符串是否为有效的 IPv6 地址。"""
        try:
            socket.inet_pton(socket.AF_INET6, ip_str)
            return True
        except socket.error:
            return False

    def show_window(self, icon=None, item=None):
        """显示主窗口。"""
        self.root.after(0, lambda: self.root.deiconify())

    def quit_app(self, icon=None, item=None):
        """退出应用程序。"""
        logging.info("收到退出应用指令。")
        self.root.after(0, self.root.destroy)
        os._exit(0) 

def load_config():
    """加载配置文件，包含错误处理。"""
    logging.info(f"尝试加载配置文件: {CONFIG_FILE}")
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # 抛出 FileNotFoundError，由主程序入口捕获并显示消息框
        raise FileNotFoundError(f"未找到配置文件！请确保 '{CONFIG_FILE}' 存在于 EXE 相同目录下。")
    except json.JSONDecodeError as e:
        # 抛出 JSONDecodeError，由主程序入口捕获并显示消息框
        raise json.JSONDecodeError(f"配置文件 '{CONFIG_FILE}' 格式不正确，请检查 JSON 语法。", e.doc, e.pos)
    except Exception as e:
        # 抛出其他未知异常
        raise Exception(f"加载配置文件时发生未知错误: {e}")

if __name__ == "__main__":
    # 提前创建 Tkinter 根窗口，以便在配置文件加载失败时显示错误消息框
    root = tk.Tk()
    root.withdraw() # 启动时先隐藏窗口，直到 App 初始化完成或显示错误

    app_config = None
    try:
        app_config = load_config() # 尝试加载配置文件
        logging.info("配置文件加载成功！")
    except FileNotFoundError as e:
        logging.critical(f"严重错误：{e} 程序将退出。")
        messagebox.showerror("错误", str(e)) # 显示错误消息框
        sys.exit(1) # 立即退出程序
    except json.JSONDecodeError as e:
        logging.critical(f"严重错误：{e} 程序将退出。", exc_info=True)
        messagebox.showerror("错误", str(e))
        sys.exit(1)
    except Exception as e:
        logging.critical(f"严重错误：{e} 程序将退出。", exc_info=True)
        messagebox.showerror("错误", str(e))
        sys.exit(1)

    # 如果配置文件加载成功，则继续启动 GUI 和主程序逻辑
    try:
        app = App(root, app_config)
        # 当用户点击窗口关闭按钮时，隐藏窗口到托盘而不是关闭程序
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw()) 
        root.mainloop() # 启动 Tkinter 事件循环
    except Exception as e:
        logging.critical(f"程序 GUI 或 主循环启动时发生严重错误: {e}", exc_info=True)
        messagebox.showerror("严重错误", f"程序启动时发生严重错误！请查看日志文件 '{LOG_FILE}'。\n错误信息：{e}")
        sys.exit(1)
