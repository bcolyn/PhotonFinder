import asyncio
import logging

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication
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

    # Create the Qt application and set up the asyncio event loop with QEventLoop
    app = QApplication()
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
