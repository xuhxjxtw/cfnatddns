import sys
from PyQt5 import QtWidgets, QtGui
from PyQt5.QtWidgets import QSystemTrayIcon, QMenu, QAction, QApplication, QWidget
from service import start_services
import threading

class TrayApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IP CF Sync")
        self.setWindowIcon(QtGui.QIcon("icon.ico"))
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QtGui.QIcon("icon.ico"))

        show_action = QAction("显示窗口")
        quit_action = QAction("退出")
        quit_action.triggered.connect(QApplication.instance().quit)

        tray_menu = QMenu()
        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        self.hide()

def run_gui():
    app = QApplication(sys.argv)
    window = TrayApp()

    threading.Thread(target=start_services, daemon=True).start()
    sys.exit(app.exec_())
