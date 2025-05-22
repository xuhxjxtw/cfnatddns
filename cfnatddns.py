import subprocess
import re
import sys
import threading
import queue
import os
import time
import json
import requests

# --- 配置部分 ---
# 获取当前脚本（或打包后的exe）所在的目录
current_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

# 定义 cfnat 主程序的匹配模式 (通配符)
# This regex will match any .exe file starting with "cmd_tray-"
cfnat_program_pattern = re.compile(r"^cmd_tray-.*\.exe$", re.IGNORECASE)

# 配置文件名称和路径
config_file_name = "config.json"
config_file_path = os.path.join(current_dir, config_file_name)

# 在 config.json 中，你希望使用哪个节点的 Cloudflare 配置来更新 DNS。
# 此键必须与 config.json 中 "nodes" 下的某个键匹配 (例如 "lax", "sjc")。
TARGET_CLOUDFLARE_NODE_KEY = "lax" 

# --- 全局变量 ---
# 用于存储 cfnat 输出行的队列
ip_queue = queue.Queue()
# 用于存储所有发现的唯一IP地址的集合
unique_ips = set()
# 用于存储 cfnat 最终选择的最佳连接IP
best_ip_found = None 
# 存储从 config.json 加载的配置
app_config = {}
# 存储实际找到并运行的 cfnat 主程序的完整路径
cfnat_actual_program_path = None 

# --- 函数：加载配置文件 ---
def load_config():
    """
    从 config.json 文件加载配置信息。
    """
    global app_config
    if not os.path.exists(config_file_path):
        print(f"错误: 配置文件 '{config_file_name}' 未找到于 '{current_dir}'。")
        print("请确保 config.json 与 cfnatddns.exe 在同一目录下。")
        input("按任意键退出...")
        sys.exit(1)

    try:
        with open(config_file_path, 'r', encoding='utf-8') as f:
            app_config = json.load(f)
        print(f"成功加载配置文件: {config_file_path}")
        # 验证目标节点配置是否存在
        if TARGET_CLOUDFLARE_NODE_KEY not in app_config.get("nodes", {}):
            print(f"错误: 配置文件中未找到目标节点 '{TARGET_CLOUDFLARE_NODE_KEY}' 的配置。")
            input("按任意键退出...")
            sys.exit(1)
        if "cloudflare" not in app_config["nodes"][TARGET_CLOUDFLARE_NODE_KEY]:
            print(f"错误: 目标节点 '{TARGET_CLOUDFLARE_NODE_KEY}' 中未找到 'cloudflare' 配置。")
            input("按任意键退出...")
            sys.exit(1)
            
    except json.JSONDecodeError as e:
        print(f"错误: 解析配置文件 '{config_file_name}' 失败。请检查JSON格式: {e}")
        input("按任意键退出...")
        sys.exit(1)
    except Exception as e:
        print(f"加载配置文件时发生未知错误: {e}")
        input("按任意键退出...")
        sys.exit(1)

# --- 函数：更新 Cloudflare DNS 记录 ---
def update_cloudflare_dns(ip_address, cloudflare_settings):
    """
    使用 Cloudflare API 更新 DNS 记录。
    """
    print(f"\n--- 正在尝试更新 Cloudflare DNS 记录 ---")
    email = cloudflare_settings.get("email")
    api_key = cloudflare_settings.get("api_key")
    zone_id = cloudflare_settings.get("zone_id")
    record_name = cloudflare_settings.get("record_name")
    enable_ipv4 = cloudflare_settings.get("enable_ipv4", False)
    enable_ipv6 = cloudflare_settings.get("enable_ipv6", False)

    if not all([email, api_key, zone_id, record_name]):
        print("错误: Cloudflare 配置信息不完整。无法更新 DNS。")
        return

    # 判断IP类型 (IPv4 或 IPv6)
    is_ipv4 = re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip_address)
    is_ipv6 = re.match(r'^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){1,7}:[0-9a-fA-F]{0,4}$', ip_address)

    record_type = None
    if is_ipv4 and enable_ipv4:
        record_type = "A"
    elif is_ipv6 and enable_ipv6:
        record_type = "AAAA"
    else:
        print(f"警告: IP地址 '{ip_address}' 类型与配置中启用的IPv4/IPv6不匹配，或未启用对应类型。跳过更新。")
        return

    print(f"准备更新 DNS 记录: 域名={record_name}, 类型={record_type}, 新IP={ip_address}")

    headers = {
        "X-Auth-Email": email,
        "X-Auth-Key": api_key,
        "Content-Type": "application/json"
    }

    # 1. 获取现有 DNS 记录 ID
    list_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type={record_type}&name={record_name}"
    try:
        response = requests.get(list_url, headers=headers, timeout=10)
        response.raise_for_status() # 检查 HTTP 状态码
        data = response.json()

        if data["success"] and data["result"]:
            record_id = data["result"][0]["id"]
            current_ip = data["result"][0]["content"]
            print(f"找到现有记录: ID={record_id}, 当前IP={current_ip}")

            if current_ip == ip_address:
                print("当前IP与新IP相同，无需更新。")
                return
        else:
            print(f"未找到现有记录 '{record_name}' ({record_type})。尝试创建新记录。")
            record_id = None # 表示需要创建

    except requests.exceptions.RequestException as e:
        print(f"获取 DNS 记录失败: {e}")
        return
    except Exception as e:
        print(f"处理获取 DNS 记录响应时发生错误: {e}")
        return

    # 2. 更新或创建 DNS 记录
    payload = {
        "type": record_type,
        "name": record_name,
        "content": ip_address,
        "ttl": 1, # TTL 1 表示自动，或者你可以设置一个具体值
        "proxied": True # 通常 DDNS 希望开启代理，如果不需要可以改为 False
    }

    if record_id:
        # 更新现有记录
        update_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
        try:
            response = requests.put(update_url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data["success"]:
                print(f"DNS 记录 '{record_name}' 更新成功！新IP: {ip_address}")
            else:
                print(f"DNS 记录更新失败: {data.get('errors', '未知错误')}")
        except requests.exceptions.RequestException as e:
            print(f"更新 DNS 记录失败: {e}")
        except Exception as e:
            print(f"处理更新 DNS 记录响应时发生错误: {e}")
    else:
        # 创建新记录
        create_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
        try:
            response = requests.post(create_url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data["success"]:
                print(f"DNS 记录 '{record_name}' 创建成功！IP: {ip_address}")
            else:
                print(f"DNS 记录创建失败: {data.get('errors', '未知错误')}")
        except requests.exceptions.RequestException as e:
            print(f"创建 DNS 记录失败: {e}")
        except Exception as e:
            print(f"处理创建 DNS 记录响应时发生错误: {e}")

# --- 线程函数：从子进程输出流中读取 ---
def enqueue_output(out, q):
    """
    从子进程的输出流中读取行并放入队列。
    """
    for line in iter(out.readline, b''): 
        try:
            q.put(line.decode('utf-8', errors='ignore').strip()) 
        except Exception as e:
            print(f"解码 cfnat 输出时发生错误: {e}")
    out.close()

# --- 主逻辑：处理 cfnat 输出并提取IP ---
def process_cfnat_output():
    global best_ip_found 
    global cfnat_actual_program_path # 允许修改全局变量

    print("--- cfnatddns 启动中 ---")
    print(f"当前目录: {current_dir}")

    # --- 步骤 1: 动态查找 cfnat 主程序 ---
    found_programs = []
    for filename in os.listdir(current_dir):
        # 检查是否是文件且文件名匹配模式
        if os.path.isfile(os.path.join(current_dir, filename)) and cfnat_program_pattern.match(filename):
            found_programs.append(filename)

    if not found_programs:
        print(f"错误: 在 '{current_dir}' 目录下未找到符合 'cmd_tray-*.exe' 模式的程序。")
        print("请确保 cmd_tray-xxx.exe (例如 cmd_tray-HKG.exe) 与 cfnatddns.exe 在同一目录下。")
        input("按任意键退出...")
        return # 退出函数

    if len(found_programs) > 1:
        print("警告: 找到多个符合 'cmd_tray-*.exe' 模式的程序:")
        for p in found_programs:
            print(f"- {p}")
        print(f"将默认使用第一个找到的程序: {found_programs[0]}")
        cfnat_actual_program_name = found_programs[0]
    else:
        cfnat_actual_program_name = found_programs[0]
        print(f"找到主程序: {cfnat_actual_program_name}")

    cfnat_actual_program_path = os.path.join(current_dir, cfnat_actual_program_name)
    print(f"主程序完整路径: {cfnat_actual_program_path}")

    # --- 步骤 2: 启动主程序并捕获其输出 ---
    try:
        proc = subprocess.Popen([cfnat_actual_program_path], 
                                stdout=subprocess.PIPE,  
                                stderr=subprocess.PIPE,  
                                bufsize=1,               
                                universal_newlines=False) 
    except FileNotFoundError:
        print(f"错误: 无法启动程序 '{cfnat_actual_program_path}'。请检查路径或权限。")
        input("按任意键退出...")
        return
    except Exception as e:
        print(f"启动主程序时发生未知错误: {e}")
        input("按任意键退出...")
        return

    # --- 步骤 3: 使用线程实时读取输出 ---
    stdout_thread = threading.Thread(target=enqueue_output, args=(proc.stdout, ip_queue))
    stderr_thread = threading.Thread(target=enqueue_output, args=(proc.stderr, ip_queue))

    stdout_thread.daemon = True 
    stderr_thread.daemon = True

    stdout_thread.start()
    stderr_thread.start()

    print("\n正在捕获主程序输出并提取IPs...")
    print("-----------------------------------")

    # --- 步骤 4: 主循环：从队列中获取输出并处理 ---
    # 正则表达式来查找IPv4地址
    ipv4_pattern = r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'
    # 正则表达式来查找IPv6地址（根据你截图中的格式）
    ipv6_pattern = r'\[([0-9a-fA-F:]+)\]:\d+' 
    # 正则表达式来匹配“选择最佳连接”行，并捕获IPv6地址和延迟
    best_connection_pattern = r'选择 最佳 连接 : 地址 : \[(?P<ipv6>[0-9a-fA-F:]+)\]:\d+ 延迟 : (?P<latency>\d+) ms'

    while True:
        try:
            line = ip_queue.get(timeout=0.5) 
            print(f"[主程序] {line}") 

            # 查找IPv4地址
            ipv4_matches = re.findall(ipv4_pattern, line)
            for ip in ipv4_matches:
                if ip not in unique_ips:
                    unique_ips.add(ip)
                    print(f"新发现IPv4: {ip}")

            # 查找IPv6地址
            ipv6_matches = re.findall(ipv6_pattern, line)
            for ip in ipv6_matches:
                if ip not in unique_ips:
                    unique_ips.add(ip)
                    print(f"新发现IPv6: {ip}")
            
            # 尝试匹配“选择最佳连接”行来获取最佳IP
            best_match = re.search(best_connection_pattern, line)
            if best_match:
                best_ip_found = best_match.group('ipv6')
                latency = best_match.group('latency')
                print(f"主程序已选择最佳连接: {best_ip_found} (延迟: {latency} ms)")
                
                # --- DDNS 更新逻辑 ---
                # 获取目标节点的 Cloudflare 配置
                target_node_config = app_config["nodes"][TARGET_CLOUDFLARE_NODE_KEY]["cloudflare"]
                update_cloudflare_dns(best_ip_found, target_node_config)
                # 最佳IP已经找到并更新，如果cfnat程序会持续运行并更新，则可以继续循环
                # 如果cfnat只是运行一次就退出，那么通常DDNS更新后就可以让脚本退出了。
                # 如果你想在更新后立即退出，可以取消注释下面这行：
                # return 
                # --- DDNS 更新逻辑结束 ---

        except queue.Empty:
            # 队列为空，检查子进程是否还在运行
            if proc.poll() is not None: 
                print("\n主程序已结束，或无更多输出。")
                break 

        except Exception as e:
            print(f"处理主程序输出时发生错误: {e}")

    # --- 步骤 5: 等待子进程完全结束 ---
    proc.wait() 

    print("\n--- 任务完成 ---")
    print("所有提取到的唯一IP地址：")
    if unique_ips:
        for ip in sorted(list(unique_ips)):
            print(ip)
    else:
        print("未从主程序输出中提取到任何IP地址。")
    
    if best_ip_found:
        print(f"\n主程序最终选择的最佳连接IP: {best_ip_found}")
    else:
        print("\n未从主程序输出中找到明确的最佳连接IP。")

# --- 主程序入口点 ---
if __name__ == "__main__":
    load_config() # 首先加载配置文件
    process_cfnat_output()
    # 保持命令行窗口打开，直到用户按下任意键
    input("\n按 Enter 键退出...") 
