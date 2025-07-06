from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QComboBox, QDialogButtonBox

from photonfinder.models import UsageReport
from photonfinder.ui.generated.CreateDataReportDialog_ui import Ui_CreateReportDialog


class CreateDataReportDialog(QDialog, Ui_CreateReportDialog):
    main_window: 'MainWindow'
    tabs: List['SearchPanel']

    def __init__(self, main_window: 'MainWindow', parent=None):
        super(CreateDataReportDialog, self).__init__(parent)
        self.setupUi(self)
        self.main_window = main_window
        self.refresh_tab_lists(main_window.get_search_panels())
        self.connect_signals()
        self.disable_enable_controls()

    def refresh_tab_lists(self, tabs: List['SearchPanel']):
        self.tabs = tabs
        for combobox in (self.lights_combo, self.integrations_combo):
            while combobox.count() < len(tabs):
                combobox.addItem("")
            while combobox.count() > len(tabs):
                combobox.removeItem(combobox.count() - 1)
            for i, item in enumerate(tabs):
                temp: QComboBox = combobox
                temp.setItemText(i, item.get_title())
                temp.setItemData(i, item, role=Qt.ItemDataRole.UserRole)

    def connect_signals(self):
        self.lights_combo.currentIndexChanged.connect(self.disable_enable_controls)
        self.integrations_combo.currentIndexChanged.connect(self.disable_enable_controls)
        self.name_edit.textChanged.connect(self.disable_enable_controls)
        self.tolerance_spin.valueChanged.connect(self.disable_enable_controls)

    def validate(self) -> bool:
        return (self.lights_combo.currentIndex() >= 0 and
                self.integrations_combo.currentIndex() >= 0 and
                self.tolerance_spin.value() > 0 and
                self.name_edit.text() != "")

    def disable_enable_controls(self):
        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.validate())

    def accept(self):
        integrations_criteria = self.integrations_combo.currentData(role=Qt.ItemDataRole.UserRole).get_search_criteria()
        lights_criteria = self.lights_combo.currentData(role=Qt.ItemDataRole.UserRole).get_search_criteria()
        UsageReport(name=self.name_edit.text(),
                    integrations_criteria=integrations_criteria.to_json(),
                    lights_criteria=lights_criteria.to_json(),
                    ).save()
        super(CreateDataReportDialog, self).accept()
