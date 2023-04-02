import time
from typing import Optional

import PySide6.QtWidgets
from PySide6.QtWidgets import QFrame, QPushButton

from ui.generated.SearchPanel_ui import Ui_SearchPanel


class SearchPanel(QFrame, Ui_SearchPanel):
    def __init__(self, parent=None) -> None:
        super(SearchPanel, self).__init__(parent)
        self.setupUi(self)

    def add_filter(self):
        self.add_filter_button()

    def set_title(self, text: str):
        my_index = self.parent().indexOf(self)
        self.parent().setTabText(my_index, text)

    def add_filter_button(self):
        button = FilterButton(self)
        self.filter_layout.insertWidget(1, button)
        button.clicked.connect(self.remove_filter_button)

    def remove_filter_button(self):
        sender = self.sender()
        self.filter_layout.removeWidget(sender)
        sender.hide()
        sender.destroy()


class FilterButton(QPushButton):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setText("FilterTest" + str(int(time.time())))
        self.setMinimumHeight(20)
        self.setStyleSheet("border-radius : 10px; border : 2px solid black; padding-left:10px; padding-right:10px")
