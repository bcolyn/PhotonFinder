import logging
import sys

from PySide6.QtCore import QStandardPaths, Qt, QThreadPool
from PySide6.QtWidgets import QApplication, QStyleFactory

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.MainWindow import MainWindow


def main():
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logging.getLogger().addHandler(ch)
    logging.getLogger().setLevel(logging.DEBUG)

    # Enable high DPI scaling
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):  # For compatibility with older Qt versions
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Create the Qt application
    app = QApplication(sys.argv)

    # Log available styles for reference
    available_styles = QStyleFactory.keys()
    logging.info(f"Available styles: {available_styles}")

    # Try to use windows11 style if available, otherwise fall back to windowsvista
    if "windows11" in available_styles:
        app.setStyle(QStyleFactory.create("windows11"))
        logging.info("Using windows11 style")
    else:
        app.setStyle(QStyleFactory.create("windowsvista"))
        logging.info("Using windowsvista style")

    logging.info(f"Current style: {app.style().objectName()}")

    # Log the maximum thread count
    thread_pool = QThreadPool.globalInstance()
    logging.info(f"Maximum thread count: {thread_pool.maxThreadCount()}")

    app_data_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    context = ApplicationContext(app_data_path)

    with context:
        main_window = MainWindow(app, context)
        main_window.show()

        # Run the Qt event loop
        app.exec()


if __name__ == '__main__':
    main()
