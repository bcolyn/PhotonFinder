from PySide6.QtWidgets import *

from .ApplicationContext import ApplicationContext
from .SearchPanel import SearchPanel
from .generated.MainWindow_ui import Ui_MainWindow


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self, app: QApplication, context: ApplicationContext, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.app = app
        self.new_search_tab()

    def new_search_tab(self):
        panel = SearchPanel(self.tabWidget)
        self.tabWidget.addTab(panel, "All data")

    def close_current_search_tab(self):
        self.close_search_tab(self.tabWidget.currentIndex())

    def close_search_tab(self, index):
        widget = self.tabWidget.widget(index)
        assert isinstance(widget, SearchPanel)
        self.tabWidget.removeTab(index)
        widget.destroy()
