import tkinter as tk
from PIL import Image
import pystray
import threading
import sys
from service import start_service

class TrayApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CFNAT-DDNS")
        self.root.geometry("300x100")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)

        tk.Label(root, text="CFNAT-DDNS 正在运行...").pack(pady=20)

        threading.Thread(target=self.setup_tray, daemon=True).start()

    def minimize_to_tray(self):
        self.root.withdraw()  # 隐藏窗口到托盘

    def show_window(self, icon, item):
        self.root.after(0, self.root.deiconify)  # 从托盘显示窗口

    def quit_app(self, icon, item):
        icon.stop()
        self.root.destroy()
        sys.exit()

    def setup_tray(self):
        try:
            image = Image.open("icon.ico")
        except Exception:
            image = Image.new("RGB", (64, 64), "gray")
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self.show_window),
            pystray.MenuItem("退出", self.quit_app)
        )
        icon = pystray.Icon("cfnatddns", image, "CFNAT-DDNS", menu)
        icon.run()

if __name__ == "__main__":
    start_service()  # 启动后台DDNS任务
    root = tk.Tk()
    app = TrayApp(root)
    root.mainloop()
