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

# 移除 pystray 和 PIL 的导入，因为我们将禁用托盘图标功能
# from pystray import Icon, Menu, MenuItem
# from PIL import Image

# 禁用托盘图标功能，设置为 False
HAS_TRAY_ICON = False 

# --- 日志配置 (在所有代码之前生效，独立于 GUI) ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(BASE_DIR, 'app.log')
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.info(f"程序启动，根目录: {BASE_DIR}")
logging.info(f"配置文件路径: {CONFIG_FILE}")
logging.info(f"日志文件路径: {LOG_FILE}")
# --- 日志配置结束 ---

def get_local_ip():
    """尝试获取本地局域网IP地址，排除回环地址和无效地址。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith("127.") or ip == "0.0.0.0":
            logging.warning(f"检测到回环或无效本地IP: {ip}")
            raise Exception("Loopback or invalid IP detected")
        logging.debug(f"成功获取本地局域网 IP: {ip}")
        return ip
    except Exception as e:
        logging.error(f"获取本地局域网 IP 失败: {e}")
        return None

class GuiLogHandler(logging.Handler):
    """将日志消息添加到 Tkinter ScrolledText 控件的队列中。"""
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        msg = self.format(record)
        self.queue.append(f"{msg}\n")

class App:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.root.title("CF DDNS Listener")
        self.root.geometry("600x400")
        
        self.text = ScrolledText(self.root, state='disabled', wrap='word')
        self.text.pack(expand=True, fill='both', padx=10, pady=10)
        
        self.queue = []
        self.root.after(100, self._process_log_queue)

        if not any(isinstance(h, GuiLogHandler) for h in logging.getLogger().handlers):
            self.gui_log_handler = GuiLogHandler(self.queue)
            logging.getLogger().addHandler(self.gui_log_handler)
        
        self.icon = None # 托盘图标对象，此处不再使用

        logging.info("GUI 初始化完成。")
        self.log_to_gui(f"程序启动，根目录: {BASE_DIR}")
        self.log_to_gui(f"配置文件路径: {CONFIG_FILE}")
        self.log_to_gui(f"日志文件路径: {LOG_FILE}")
        
        self.start_all()
        # 移除 setup_tray() 调用
        # if HAS_TRAY_ICON:
        #     self.setup_tray()
        # else:
        #     logging.warning("托盘图标功能已禁用。")

    def _process_log_queue(self):
        """定时从队列中取出日志并更新 GUI。"""
        while self.queue:
            message = self.queue.pop(0)
            self.text.configure(state='normal')
            self.text.insert(tk.END, message)
            self.text.configure(state='disabled')
            self.text.see(tk.END)
        self.root.after(100, self._process_log_queue)

    def log_to_gui(self, message):
        """将消息添加到 GUI 日志队列。"""
        formatted_message = f"{time.strftime('%H:%M:%S')} - {message}\n"
        self.queue.append(formatted_message)

    def start_all(self):
        """遍历配置文件中的节点并启动监听线程。"""
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
        """监听指定端口的连接，并在收到请求时触发 DNS 更新。"""
        host_ip = get_local_ip()
        if not host_ip:
            logging.error(f"[{name}] 未能获取有效局域网 IP，监听线程退出。")
            self.log_to_gui(f"[{name}] 未能获取有效局域网 IP，监听失败")
            return
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
                logging.error(f"[{name}] 监听循环中发生异常: {e}", exc_info=True)
                self.log_to_gui(f"[{name}] 监听循环中发生异常: {e}")
                time.sleep(1)

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

        base_url = f"https://api.cloudflare.com/client/v4/zones/{cf_conf['zone_id']}/dns_records"
        record_name = cf_conf["record_name"]

        try:
            # 1. 查询现有 DNS 记录
            logging.info(f"[{name}] 查询 Cloudflare DNS 记录: {record_name}")
            res = requests.get(
                f"{base_url}?name={record_name}&type=A", 
                headers=headers,
                timeout=10
            )
            res.raise_for_status()
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
                create_data = {
                    "type": "A",
                    "name": record_name,
                    "content": ip,
                    "ttl": 120,
                    "proxied": cf_conf.get("proxied", False)
                }
                res = requests.post(
                    base_url,
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
                return

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
                f"{base_url}/{record_id}",
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
            logging.error(f"[{name}] Cloudflare API 请求失败: {e}", exc_info=True)
            self.log_to_gui(f"[{name}] Cloudflare API 错误: {e}")
        except json.JSONDecodeError as e:
            logging.error(f"[{name}] Cloudflare API 响应解析失败: {e}，响应: {res.text if 'res' in locals() else '无响应'}", exc_info=True)
            self.log_to_gui(f"[{name}] Cloudflare 响应解析错误: {e}")
        except Exception as e:
            logging.error(f"[{name}] 更新 Cloudflare DNS 记录时发生未预期异常: {e}", exc_info=True)
            self.log_to_gui(f"[{name}] 更新异常: {e}")

    def get_public_ip(self, conf):
        """尝试从内网服务或公网接口获取公网IP地址。"""
        port = conf.get("listen_port", 0)
        ip4 = None
        ip6 = None
        
        if conf["cloudflare"].get("enable_ipv4", True):
            try:
                local_ip = get_local_ip()
                if local_ip:
                    url = f"http://{local_ip}:{port}/ipv4" 
                    logging.info(f"尝试从内网服务获取 IPv4: {url}")
                    resp = requests.get(url, timeout=3)
                    if resp.status_code == 200 and resp.text.strip():
                        ip4_candidate = resp.text.strip()
                        if self._is_valid_ipv4(ip4_candidate):
                            ip4 = ip4_candidate
                            logging.info(f"通过内网服务获取 IPv4 成功: {ip4}")
                        else:
                            logging.warning(f"内网服务返回的 IPv4 格式不正确: '{ip4_candidate}'")
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
                        logging.warning(f"公网接口返回的 IPv4 格式不正确: '{ip4_candidate}'")
                except Exception as e2:
                    logging.error(f"IPv4 公网接口获取失败: {e2}")

        if conf["cloudflare"].get("enable_ipv6", False):
            try:
                local_ip = get_local_ip()
                if local_ip:
                    url = f"http://{local_ip}:{port}/ipv6"
                    logging.info(f"尝试从内网服务获取 IPv6: {url}")
                    resp = requests.get(url, timeout=3)
                    if resp.status_code == 200 and resp.text.strip():
                        ip6_candidate = resp.text.strip()
                        if self._is_valid_ipv6(ip6_candidate):
                            ip6 = ip6_candidate
                            logging.info(f"通过内网服务获取 IPv6 成功: {ip6}")
                        else:
                            logging.warning(f"内网服务返回的 IPv6 格式不正确: '{ip6_candidate}'")
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
                        logging.warning(f"公网接口返回的 IPv6 格式不正确: '{ip6_candidate}'")
                except Exception as e2:
                    logging.error(f"IPv6 公网接口获取失败: {e2}")

        if ip4:
            return ip4
        if ip6:
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

    # 移除 setup_tray 函数
    # def setup_tray(self):
    #     if not HAS_TRAY_ICON:
    #         return
    #     icon_path = os.path.join(BASE_DIR, "icon.ico")
    #     image = None
    #     if os.path.exists(icon_path):
    #         try:
    #             image = Image.open(icon_path)
    #         except Exception as e:
    #             logging.error(f"加载图标文件失败: {e}，使用默认蓝色图标。")
    #             image = Image.new("RGB", (64, 64), "blue")
    #     else:
    #         logging.warning(f"未找到图标文件: {icon_path}，使用默认蓝色图标。")
    #         image = Image.new("RGB", (64, 64), "blue")
    #     menu = Menu(MenuItem("显示", self.show_window), MenuItem("退出", self.quit_app))
    #     self.icon = Icon("CFTray", image, "CF DDNS", menu)
    #     threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon=None, item=None): # 保持参数兼容性
        """显示主窗口。"""
        self.root.after(0, lambda: self.root.deiconify())

    def quit_app(self, icon=None, item=None): # 保持参数兼容性
        """退出应用程序。"""
        logging.info("收到退出应用指令。")
        # if self.icon: # 移除托盘图标停止逻辑
        #     self.icon.stop()
        self.root.after(0, self.root.destroy)
        os._exit(0) 

def load_config():
    """加载配置文件，包含错误处理。"""
    logging.info(f"尝试加载配置文件: {CONFIG_FILE}")
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"未找到配置文件！请确保 '{CONFIG_FILE}' 存在于 EXE 相同目录下。")
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"配置文件 '{CONFIG_FILE}' 格式不正确，请检查 JSON 语法。", e.doc, e.pos)
    except Exception as e:
        raise Exception(f"加载配置文件时发生未知错误: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()

    app_config = None
    try:
        app_config = load_config()
        logging.info("配置文件加载成功！")
    except FileNotFoundError as e:
        logging.critical(f"严重错误：{e} 程序将退出。")
        messagebox.showerror("错误", str(e))
        sys.exit(1)
    except json.JSONDecodeError as e:
        logging.critical(f"严重错误：{e} 程序将退出。", exc_info=True)
        messagebox.showerror("错误", str(e))
        sys.exit(1)
    except Exception as e:
        logging.critical(f"严重错误：{e} 程序将退出。", exc_info=True)
        messagebox.showerror("错误", str(e))
        sys.exit(1)

    try:
        app = App(root, app_config)
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.mainloop()
    except Exception as e:
        logging.critical(f"程序 GUI 或 主循环启动时发生严重错误: {e}", exc_info=True)
        messagebox.showerror("严重错误", f"程序启动时发生严重错误！请查看日志文件 '{LOG_FILE}'。\n错误信息：{e}")
        sys.exit(1)
