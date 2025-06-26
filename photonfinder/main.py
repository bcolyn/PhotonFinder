import logging
import sys

from PySide6.QtCore import QStandardPaths, Qt, QThreadPool
from PySide6.QtWidgets import QApplication, QStyleFactory

from photonfinder.core import ApplicationContext, Settings
from photonfinder.ui.MainWindow import MainWindow


LEVEL=logging.INFO

def init_logging(path: str = None):
    logger = logging.getLogger()
    logger.setLevel(LEVEL)

    # Create file handler
    file_handler = logging.FileHandler(f"{path}/photonfinder.log")
    file_handler.setLevel(LEVEL)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(LEVEL)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def main():
    app_data_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    init_logging(app_data_path)

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
    settings = Settings()
    if settings.get_last_database_path():
        context = ApplicationContext(settings.get_last_database_path(), settings)
    else:
        context = ApplicationContext.create_in_app_data(app_data_path, settings)

    with context:
        main_window = MainWindow(app, context)
        main_window.show()

        # Run the Qt event loop
        app.exec()


if __name__ == '__main__':
    main()
