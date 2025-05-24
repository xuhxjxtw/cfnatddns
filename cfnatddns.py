import subprocess
import re
import time
import os
import datetime
import glob
import psutil # pip install psutil

# Windows API 相关的库
import win32process
import win32console
import win32api
import win32con
import win32gui
import ctypes # 用于更低层的WinAPI调用，例如获取ForegroundWindow

# --- 配置部分 ---
LOG_FILE_NAME = "cfnat_ips.log" # 脚本自身的输出日志
PROXY_LAUNCHER_PATTERN = "cmd_tray-*.exe" # 代理启动器的通配符模式 (e.g., cmd_tray-HKG.exe)
AGENT_PROCESS_NAME = "cfnat-windows-amd64.exe" # 代理核心进程的名称
# --- 配置结束 ---

def log_message(message, file_handle=None, print_to_console=True):
    """同时打印到控制台和指定的日志文件"""
    timestamp_msg = f"[{datetime.datetime.now()}] {message}"
    if print_to_console:
        print(timestamp_msg)
    if file_handle:
        file_handle.write(timestamp_msg + "\n")
        file_handle.flush()

def get_console_window_for_pid(pid, max_attempts=10, delay=1):
    """
    尝试找到与指定PID关联的控制台窗口句柄。
    这是最复杂的部分，因为一个进程可能没有直接的控制台窗口，
    或者控制台属于其父进程，或者是一个全新的、独立的控制台。
    """
    for _ in range(max_attempts):
        log_message(f"尝试查找 PID {pid} 的控制台窗口...", file_handle=None, print_to_console=False) # 调试信息，不写入日志
        
        # 1. 尝试使用 GetConsoleProcessList
        # 这种方法只能获取当前进程组的控制台
        
        # 2. 枚举所有窗口，并通过PID匹配
        hwnds = []
        def enum_windows_callback(hwnd, lParam):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) != "":
                tid, _pid = win32process.GetWindowThreadProcessId(hwnd)
                if _pid == lParam: # 检查窗口所属进程是否是目标PID
                    class_name = win32gui.GetClassName(hwnd)
                    # 常见的控制台窗口类名
                    if "ConsoleWindow" in class_name or "ConsoleWindowClass" in class_name:
                        hwnds.append(hwnd)
            return True
        
        win32gui.EnumWindows(enum_windows_callback, pid)

        if hwnds:
            # 返回找到的第一个控制台窗口句柄
            return hwnds[0]
        
        time.sleep(delay)
    return None


def read_console_buffer(console_output_handle, buffer_info, last_read_cursor):
    """
    从控制台缓冲区读取新增内容。
    这是一个简化的读取方法，更高级的可能需要维护一个屏幕缓冲区视图。
    """
    new_text = ""
    try:
        # 获取最新的缓冲区信息
        current_buffer_info = win32console.GetConsoleScreenBufferInfo(console_output_handle)
        buffer_size = current_buffer_info.dwSize.X * current_buffer_info.dwSize.Y
        
        # 读取整个缓冲区内容
        # PyCOORDType(0,0) 从缓冲区左上角开始
        read_region = win32console.PyCOORDType(0, 0) 
        
        # 尝试读取整个缓冲区内容
        full_buffer_text = win32console.ReadConsoleOutputCharacter(console_output_handle, buffer_size, read_region)
        
        # 计算新内容（这是简化的，实际需要更复杂的逻辑来处理滚动等）
        # 这里假设新的内容是追加在末尾
        if len(full_buffer_text) > last_read_cursor:
            new_text = full_buffer_text[last_read_cursor:]
        
        # 记录当前的缓冲区总长度作为下次读取的起点
        return new_text, len(full_buffer_text)

    except Exception as e:
        # print(f"读取控制台缓冲区失败: {e}")
        return "", last_read_cursor


def monitor_agent_console_output(agent_pid, script_log_file_handle, max_ips=50, timeout_seconds=180):
    """
    监控指定PID的控制台输出，提取IP地址。
    """
    ips_found = set()
    start_time = time.time()
    
    ip_pattern = re.compile(
        r'\b(?:'
        r'(?:\d{1,3}\.){3}\d{1,3}'
        r'|'
        r'(?:[0-9a-fA-F]{1,4}:){1,7}[0-9a-fA-F]{1,4}'
        r'|'
        r'::(?:[0-9a-fA-F]{1,4}){1,7}'
        r'|'
        r'[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6}::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?'
        r')\b'
    )

    console_output_handle = None
    last_read_buffer_length = 0 # 记录上次读取的缓冲区总长度

    try:
        # 获取核心程序的控制台窗口句柄
        console_hwnd = get_console_window_for_pid(agent_pid, max_attempts=20, delay=1)
        if not console_hwnd:
            log_message(f"警告：未能找到 PID {agent_pid} 对应的控制台窗口句柄。可能无法获取输出。", script_log_file_handle)
            return []

        # 尝试将自己附加到该控制台
        # 这是非常关键且有风险的步骤，因为它会改变当前进程的控制台关联
        # 并且通常一个进程只能附加到一个控制台
        try:
            win32console.FreeConsole() # 首先分离当前的控制台
            win32console.AttachConsole(agent_pid) # 尝试附加到目标进程的控制台
            # 获取附加后的标准输出句柄
            console_output_handle = win32console.GetStdHandle(win32console.STD_OUTPUT_HANDLE)
            log_message(f"成功附加到 PID {agent_pid} 的控制台。", script_log_file_handle)

        except Exception as attach_e:
            log_message(f"附加到 PID {agent_pid} 的控制台失败：{attach_e}", script_log_file_handle)
            log_message("这通常意味着该控制台已由另一个进程拥有，或者无法附加。将尝试备用读取方法。", script_log_file_handle)
            # 如果附加失败，则无法通过此方法读取，直接返回
            return []
        
        # 获取缓冲区信息（附加后）
        buffer_info = win32console.GetConsoleScreenBufferInfo(console_output_handle)
        
        log_message(f"开始轮询 PID {agent_pid} 的控制台缓冲区...", script_log_file_handle)

        while True:
            if time.time() - start_time > timeout_seconds:
                log_message(f"监控超时 ({timeout_seconds}秒)，停止监控。", script_log_file_handle)
                break
            
            if len(ips_found) >= max_ips:
                log_message(f"已收集到 {max_ips} 个不同的IP地址，停止监控。", script_log_file_handle)
                break

            # 检查代理核心进程是否仍然存活
            if not psutil.pid_exists(agent_pid) or not psutil.Process(agent_pid).is_running():
                log_message(f"代理核心进程 (PID: {agent_pid}) 已退出。", script_log_file_handle)
                break

            # 读取控制台缓冲区
            new_output_segment, current_buffer_length = read_console_buffer(console_output_handle, buffer_info, last_read_buffer_length)
            
            if new_output_segment:
                lines = new_output_segment.splitlines()
                for line in lines:
                    line = line.strip()
                    # log_message(f"控制台原始行: {line}", script_log_file_handle, print_to_console=False) # 调试用

                    matches = ip_pattern.findall(line)
                    for ip_match in matches:
                        if ip_match and ip_match not in ips_found:
                            log_message(f"在控制台输出中发现 IP: {ip_match}", script_log_file_handle)
                            ips_found.add(ip_match)
                last_read_buffer_length = current_buffer_length # 更新已读取的缓冲区长度

            time.sleep(1) # 每秒读取一次控制台

    except Exception as e:
        log_message(f"监控控制台输出时发生错误: {e}", script_log_file_handle)
    finally:
        # 重要：尝试分离控制台，恢复 Python 脚本的原始控制台状态
        try:
            if console_output_handle:
                # 尝试释放附加的控制台
                win32console.FreeConsole()
                # 重新分配一个新控制台（通常为了恢复标准行为）
                # 或者尝试附加回脚本启动时的控制台（如果已知）
                # 这里简单地创建一个新控制台，以便print语句能继续工作
                win32api.AttachConsole(win32con.ATTACH_PARENT_PROCESS)
                # 或 AllocateConsole()
                log_message("已尝试分离并恢复控制台状态。", script_log_file_handle)
        except Exception as free_e:
            log_message(f"分离控制台失败: {free_e}", script_log_file_handle)

    return list(ips_found)


# --- 脚本运行入口 ---
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_log_path = os.path.join(script_dir, LOG_FILE_NAME)
    
    main_log_file_handle = None
    try:
        main_log_file_handle = open(script_log_path, 'a', encoding='utf-8')
        log_message(f"\n[{datetime.datetime.now()}] --- cfnatddnd.py 脚本开始运行 ---", main_log_file_handle)
        log_message(f"脚本日志将写入到: {script_log_path}", main_log_file_handle)
    except Exception as e:
        print(f"错误：无法打开脚本日志文件 {script_log_path}，将只在控制台输出。错误信息: {e}")
        main_log_file_handle = None

    # 第一步：启动代理启动器
    matching_launchers = glob.glob(os.path.join(script_dir, PROXY_LAUNCHER_PATTERN))
    
    if not matching_launchers:
        log_message(f"错误：在当前目录下未找到匹配 '{PROXY_LAUNCHER_PATTERN}' 的代理启动器文件。", main_log_file_handle)
        log_message("请确保你的代理启动器（如 cmd_tray-HKG.exe）存在于此目录下。", main_log_file_handle)
        if main_log_file_handle: main_log_file_handle.close()
        exit(1)

    proxy_launcher_path = matching_launchers[0]
    proxy_launcher_command = [os.path.basename(proxy_launcher_path)]
    
    log_message(f"已找到代理启动器：{proxy_launcher_path}", main_log_file_handle)
    log_message(f"将使用命令：{proxy_launcher_command} 启动代理启动器。", main_log_file_handle)

    try:
        # 启动而不捕获输出，让它在自己的新窗口中运行
        # CREATE_NEW_CONSOLE 确保它有自己的独立控制台
        launcher_process = subprocess.Popen(
            proxy_launcher_command,
            cwd=script_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE # 关键：创建一个新的控制台窗口
        )
        log_message(f"代理启动器 '{proxy_launcher_command[0]}' 已启动 (PID: {launcher_process.pid})。", main_log_file_handle)
        
    except Exception as e:
        log_message(f"启动代理启动器时发生错误: {e}", main_log_file_handle)
        if main_log_file_handle: main_log_file_handle.close()
        exit(1)

    # 等待 cfnat-windows-amd64.exe 启动
    time.sleep(5) # 给予启动器一些时间来启动其子进程

    # 查找 cfnat-windows-amd64.exe 的 PID
    agent_pid = None
    for _ in range(30): # 尝试30次，每次等待1秒，共30秒
        for proc in psutil.process_iter(['pid', 'name']):
            if proc.info['name'] == AGENT_PROCESS_NAME:
                agent_pid = proc.info['pid']
                break
        if agent_pid:
            log_message(f"找到代理核心进程 '{AGENT_PROCESS_NAME}' PID: {agent_pid}", main_log_file_handle)
            break
        time.sleep(1)

    if not agent_pid:
        log_message(f"错误：未能找到代理核心进程 '{AGENT_PROCESS_NAME}' 的 PID，无法监控其控制台。", main_log_file_handle)
        log_message("请确保 cfnat-windows-amd64.exe 确实被启动了。", main_log_file_handle)
        if main_log_file_handle: main_log_file_handle.close()
        exit(1)

    # 第二步：监控 cfnat-windows-amd64.exe 的独立控制台输出
    found_ips = monitor_agent_console_output(agent_pid, main_log_file_handle, max_ips=50, timeout_seconds=120)

    # 脚本运行结束，记录最终结果
    log_message(f"\n--- 最终收集到的 Cloudflare IP 地址列表 (共 {len(found_ips)} 个) ---", main_log_file_handle)
    if found_ips:
        for ip in found_ips:
            log_message(ip, main_log_file_handle)
    else:
        log_message("未获取到任何 Cloudflare IP 地址。", main_log_file_handle)
    log_message(f"[{datetime.datetime.now()}] --- cfnatddnd.py 脚本运行结束 --- \n", main_log_file_handle)

    if main_log_file_handle:
        main_log_file_handle.close()
