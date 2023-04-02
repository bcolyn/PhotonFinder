import logging
import sys

from PySide6.QtWidgets import QApplication

from ui.MainWindow import MainWindow

if __name__ == '__main__':
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logging.getLogger().addHandler(ch)
    logging.getLogger().setLevel(logging.DEBUG)
    app = QApplication()
    mainWindow = MainWindow(app)
    mainWindow.show()
    sys.exit(app.exec())
