from PySide6.QtWidgets import *

from ..core import ApplicationContext
from .LibraryRootDialog import LibraryRootDialog
from .SearchPanel import SearchPanel
from .SettingsDialog import SettingsDialog
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

    def manage_library_roots(self):
        """
        Open the dialog for managing library roots.
        """
        if self.context.database:
            dialog = LibraryRootDialog(self.context.database, parent=self)
            dialog.exec()

    def open_settings_dialog(self):
        """
        Open the settings dialog.
        """
        dialog = SettingsDialog(self.context, parent=self)
        dialog.exec()
