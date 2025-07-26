from datetime import datetime
from pathlib import Path
from typing import List, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTableWidgetItem, QFileDialog, QMessageBox

from photonfinder.core import ApplicationContext
from photonfinder.models import Project, File, LibraryRoot, ProjectFile, Image
from photonfinder.ui.common import _format_timestamp
from photonfinder.ui.generated.ProjectEditDialog_ui import Ui_ProjectEditDialog


class ProjectEditDialog(QDialog, Ui_ProjectEditDialog):
    project_files: List[ProjectFile]
    links_to_delete: Set[ProjectFile]
    links_to_add: Set[ProjectFile]

    def __init__(self, context: ApplicationContext, project: Project, parent=None):
        super(ProjectEditDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.project = project
        self.project_files = (ProjectFile.select(ProjectFile, File, LibraryRoot)
                              .join(File)
                              .join(LibraryRoot)
                              .where(ProjectFile.project == self.project.rowid)
                              .order_by(LibraryRoot.name, File.path, File.name))
        if not project.rowid:
            self.setWindowTitle("Create Project")
        else:
            self.setWindowTitle(f"Edit Project {project.name}")
        self.connect_signals()
        self.reload_data()
        self.enable_disable_actions()

    def refresh_table(self):
        self.tableWidget.clearContents()

        updated_files = [f for f in self.project_files if f not in self.links_to_delete] + list(self.links_to_add)
        self.tableWidget.setRowCount(len(updated_files))

        for row, project_file in enumerate(updated_files):
            first_item = QTableWidgetItem(project_file.file.root.name)
            first_item.setData(Qt.UserRole, project_file)
            self.tableWidget.setItem(row, 0, first_item)
            self.tableWidget.setItem(row, 1, QTableWidgetItem(project_file.file.path))
            self.tableWidget.setItem(row, 2, QTableWidgetItem(project_file.file.name))
            self.tableWidget.setItem(row, 3, QTableWidgetItem(_format_timestamp(project_file.file.mtime_millis)))

        self.tableWidget.resizeColumnsToContents()

    def save_data_and_close(self):
        if self.dirty():
            self.project.name = self.name_edit.text()
            self.project.last_change = datetime.now()
            with self.context.database.atomic():
                self.project.save()
                for link in self.links_to_delete:
                    link.delete_instance()
                for link in self.links_to_add:
                    link.save(force_insert=True)

        self.accept()

    def reload_data(self):
        self.name_edit.setText(self.project.name)
        self.links_to_delete = set()
        self.links_to_add = set()
        self.refresh_table()

    def connect_signals(self):
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save_data_and_close)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self.reload_data)
        self.tableWidget.itemSelectionChanged.connect(self.enable_disable_actions)
        self.name_edit.textEdited.connect(self.enable_disable_actions)
        self.remove_button.clicked.connect(self.delete_selected)
        self.add_button.clicked.connect(self.add_files)

    def enable_disable_actions(self):
        selected = self.get_selected_files()
        self.remove_button.setEnabled(len(selected) >= 1)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Reset).setEnabled(self.dirty())

    def dirty(self) -> bool:
        return not (self.name_edit.text() == self.project.name and
                    len(self.links_to_delete) == 0 and
                    len(self.links_to_add) == 0)

    def get_selected_files(self) -> List[ProjectFile]:
        return [x.data(Qt.UserRole) for x in self.tableWidget.selectedItems() if x.data(Qt.UserRole)]

    def delete_selected(self):
        selected = self.get_selected_files()
        self.links_to_add -= set(selected)
        self.links_to_delete.update(selected)
        self.refresh_table()

    def add_files(self):
        files = self.prompt_add_files()
        if not files:
            return

        mismatches: List[str] = list()
        added_files: List[File] = list()
        for f in files:
            db_file = ProjectFile.find_by_filename(f, self.project)
            if not db_file:
                mismatches.append(f)
            else:
                self.add_file(db_file)
                added_files.append(db_file.file)

        if mismatches:
            QMessageBox.warning(self, "Some Files could not be added",
                                "Some files could not be added to the project as they are not found in the database.\n "
                                "You may want to run File->Scan Libraries first. \n"
                                "The files that failed:\n" +
                                "\n".join(mismatches))

        if not self.name_edit.text():
            name = (Image.select(Image.object_name)
                    .where(Image.file.in_(added_files))
                    .limit(1)).scalar()
            self.name_edit.setText(name)

        self.refresh_table()

    def add_file(self, db_file: ProjectFile):
        if db_file not in self.project_files:
            self.links_to_add.add(db_file)
        if db_file in self.links_to_delete:  # remove deletion marker
            self.links_to_delete.remove(db_file)

    def prompt_add_files(self):
        custom_filter = (
            "Astronomy Image Files (*.xisf *.fit *.fits "
            "*.fit.gz *.fit.xz *.fit.bz2 "
            "*.fits.gz *.fits.xz *.fits.bz2)"
        )
        all_files_filter = "All Files (*.*)"
        filters = f"{custom_filter};;{all_files_filter}"
        selected_files = self.get_selected_files()
        if len(selected_files) > 0:
            file = selected_files[0].file.full_filename()
            fdir = str(Path(file).parent)
        else:
            fdir = ""
        files, _ = QFileDialog.getOpenFileNames(self, f"Select Files for project {self.project.name}", fdir, filters)
        return files
