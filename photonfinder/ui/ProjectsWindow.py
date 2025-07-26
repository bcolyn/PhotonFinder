from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTableWidgetItem, QDialog, QMessageBox, QWidget

from photonfinder.core import ApplicationContext
from photonfinder.models import Project, ProjectFile
from photonfinder.ui.generated.ProjectsWindow_ui import Ui_ProjectsWindow
from .ProjectEditDialog import ProjectEditDialog
from .formatting import _format_ra, _format_dec, _format_date


class ProjectsWindow(QWidget, Ui_ProjectsWindow):
    closing = Signal()

    def __init__(self, context: ApplicationContext, parent=None):
        super(ProjectsWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.connect_signals()
        self.populate_table()

    def connect_signals(self):
        self.actionCreate.triggered.connect(self.create_action)
        self.actionEdit.triggered.connect(self.edit_action)
        self.actionDelete.triggered.connect(self.delete_action)
        self.actionMerge.triggered.connect(self.merge_action)
        self.tableWidget.itemSelectionChanged.connect(self.enable_disable_actions)

    def populate_table(self):
        projects = Project.list_projects_with_image_data()

        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(len(projects))

        for row, project in enumerate(projects):
            project_id = project.rowid
            name_item = QTableWidgetItem(project.name)
            name_item.setData(Qt.UserRole, project_id)
            self.tableWidget.setItem(row, 0, name_item)
            date_str = project.date_obs
            date_iso = datetime.fromisoformat(date_str) if date_str else None
            self.tableWidget.setItem(row, 1, QTableWidgetItem(_format_date(date_iso)))
            self.tableWidget.setItem(row, 2, QTableWidgetItem(str(project.file_counts)))
            if hasattr(project, 'image'):
                image = project.image
                self.tableWidget.setItem(row, 3, QTableWidgetItem(_format_ra(image.coord_ra)))
                self.tableWidget.setItem(row, 4, QTableWidgetItem(_format_dec(image.coord_dec)))
                coord = image.get_sky_coord()
                self.tableWidget.setItem(row, 5, QTableWidgetItem(coord.get_constellation()))

        self.tableWidget.resizeColumnsToContents()
        self.enable_disable_actions()

    def closeEvent(self, event, /):
        self.closing.emit()
        super().closeEvent(event)

    def delete_action(self):
        project_ids = self.get_selected_projects()

        # Confirm deletion
        if len(project_ids) > 1:
            message = f"Are you sure you want to delete the {len(project_ids)} selected projects?'?"
        else:
            project = Project.get_by_id(project_ids[0])
            message = f"Are you sure you want to delete the '{project.name}' project?'?"

        result = QMessageBox.question(
            self,
            "Confirm Deletion",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if result == QMessageBox.Yes:
            with self.context.database.atomic():
                for project_id in project_ids:
                    Project.delete_by_id(project_id)

        self.populate_table()

    def create_action(self):
        project = Project()
        dialog = ProjectEditDialog(context=self.context, parent=self, project=project)
        if dialog.exec() == QDialog.Accepted:
            self.populate_table()

    def edit_action(self):
        project_ids = self.get_selected_projects()
        assert len(project_ids) == 1
        project = Project.get_by_id(project_ids[0])
        dialog = ProjectEditDialog(context=self.context, parent=self, project=project)
        if dialog.exec() == QDialog.Accepted:
            self.populate_table()

    def merge_action(self):
        project_ids = self.get_selected_projects()
        projects = list(Project.select().where(Project.rowid.in_(project_ids)).order_by(Project.rowid))
        leader = projects[0]
        to_merge = projects[1:]
        to_merge_ids = list(map(lambda p: p.rowid, to_merge))
        with self.context.database.atomic():
            leader.name = ",".join(map(lambda p: p.name, projects))
            leader.last_change = datetime.now()
            leader.save()
            ProjectFile.update(project=leader).where(ProjectFile.project.in_(to_merge_ids)).execute()
            Project.delete().where(Project.rowid.in_(to_merge_ids)).execute()
        self.populate_table()

    def enable_disable_actions(self):
        project_ids = self.get_selected_projects()
        self.actionDelete.setEnabled(self.tableWidget.rowCount() >= 1)
        self.actionMerge.setEnabled(len(project_ids) >= 2)
        self.actionDelete.setEnabled(len(project_ids) >= 1)
        self.actionEdit.setEnabled(len(project_ids) == 1)

    def get_selected_projects(self):
        return [x.data(Qt.UserRole) for x in self.tableWidget.selectedItems() if x.data(Qt.UserRole)]
