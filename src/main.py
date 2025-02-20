import logging
import asyncio
import sys

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

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

    mainWindow = MainWindow(app)
    mainWindow.show()

    # Run the asyncio event loop merged with the PySide6 event loop
    with loop:
        loop.run_forever()


if __name__ == '__main__':
    asyncio.run(main())
