import asyncio
import logging
import sys

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import QApplication, QStyleFactory
from qasync import QEventLoop

from src.ui.ApplicationContext import ApplicationContext
from ui.MainWindow import MainWindow

async def main():
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

    # Create the Qt application and set up the asyncio event loop with QEventLoop
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

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    app_data_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    context = ApplicationContext(app_data_path)

    with context:
        main_window = MainWindow(app, context)
        main_window.show()

        # Run the asyncio event loop merged with the PySide6 event loop
        with loop:
            loop.run_forever()


if __name__ == '__main__':
    asyncio.run(main())
