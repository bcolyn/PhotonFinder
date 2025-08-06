from datetime import datetime
from typing import List, Collection

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QTableWidgetItem, QDialog, QMessageBox, QWidget

from photonfinder.core import ApplicationContext, Change
from photonfinder.models import Project, ProjectFile
from photonfinder.ui.generated.ProjectsWindow_ui import Ui_ProjectsWindow
from .ProjectEditDialog import ProjectEditDialog
from .common import _format_ra, _format_dec, _format_date, create_colored_svg_icon, ColumnVisibilityController


class ProjectsWindow(QWidget, Ui_ProjectsWindow):
    main_window: "MainWindow"
    closing = Signal()

    def __init__(self, context: ApplicationContext, main_window, parent=None):
        super(ProjectsWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.connect_signals()
        self.populate_table()
        self.main_window = main_window

        text_color = self.palette().color(QPalette.WindowText)
        size = QSize(24, 24)
        self.actionUseAsFilter.setIcon(create_colored_svg_icon(":/res/funnel.svg", size, text_color))
        self.visibility_controller = ColumnVisibilityController(self.tableWidget)
        hidden_cols = context.settings.get_project_hidden_cols()
        self.visibility_controller.load_visibility(hidden_cols)

    def connect_signals(self):
        self.actionCreate.triggered.connect(self.create_action)
        self.actionEdit.triggered.connect(self.edit_action)
        self.actionDelete.triggered.connect(self.delete_action)
        self.actionMerge.triggered.connect(self.merge_action)
        self.actionUseAsFilter.triggered.connect(self.use_as_filter_action)
        self.tableWidget.itemSelectionChanged.connect(self.enable_disable_actions)
        self.tableWidget.doubleClicked.connect(self.edit_action)
        self.context.signal_bus.projects_changed.connect(self.on_projects_changed)
        self.context.signal_bus.project_links_changed.connect(self.on_project_links_changed)

    def on_projects_changed(self, projects: Collection[Project], change: Change):
        # TODO partial update
        self.populate_table()

    def on_project_links_changed(self, project_files: Collection[ProjectFile], change: Change):
        #TODO partial update
        self.populate_table()

    def populate_table(self):
        projects = Project.list_projects_with_image_data()

        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(len(projects))

        for row, project in enumerate(projects):
            name_item = QTableWidgetItem(project.name)
            name_item.setData(Qt.UserRole, project)
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

    def save_cols(self):
        hidden_cols = self.visibility_controller.save_visibility()
        self.context.settings.set_project_hidden_cols(hidden_cols)

    def closeEvent(self, event, /):
        self.closing.emit()
        super().closeEvent(event)

    def delete_action(self):
        projects = self.get_selected_projects()

        # Confirm deletion
        if len(projects) > 1:
            message = f"Are you sure you want to delete the {len(projects)} selected projects?'?"
        else:
            project = projects[0]
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
                for project in projects:
                    project.delete_instance()
            self.context.signal_bus.projects_changed.emit(projects, Change.DELETE)

        self.populate_table()

    def create_action(self):
        project = Project()
        dialog = ProjectEditDialog(context=self.context, parent=self, project=project)
        dialog.show()
        dialog.refresh_table()

    def edit_action(self):
        projects = self.get_selected_projects()
        assert len(projects) == 1
        project = projects[0]
        dialog = ProjectEditDialog(context=self.context, parent=self, project=project)
        dialog.show()
        dialog.refresh_table()

    def merge_action(self):
        projects = self.get_selected_projects()
        leader = projects[0]
        to_merge = projects[1:]
        to_merge_ids = list(map(lambda p: p.rowid, to_merge))
        with self.context.database.atomic():
            leader.name = ",".join(map(lambda p: p.name, projects))
            leader.last_change = datetime.now()
            leader.save()
            ProjectFile.update(project=leader).where(ProjectFile.project.in_(to_merge_ids)).execute()
            Project.delete().where(Project.rowid.in_(to_merge_ids)).execute()
            self.context.signal_bus.projects_changed.emit([leader], Change.CREATE_OR_UPDATE)
            self.context.signal_bus.projects_changed.emit(to_merge, Change.DELETE)
        self.populate_table()

    def use_as_filter_action(self):
        from photonfinder.ui.SearchPanel import FilterButton, AdvancedFilter
        search_panel = self.main_window.get_current_search_panel()
        projects = self.get_selected_projects()
        assert len(projects) == 1
        project = projects[0]

        text = f"Project: {project.name}"
        filter_button = FilterButton(search_panel, text, AdvancedFilter.PROJECT)
        filter_button.on_remove_filter.connect(search_panel.reset_project_criteria)
        search_panel.add_filter_button_control(filter_button)
        search_panel.search_criteria.project = project
        search_panel.update_search_criteria()

    def enable_disable_actions(self):
        projects = self.get_selected_projects()
        self.actionDelete.setEnabled(self.tableWidget.rowCount() >= 1)
        self.actionMerge.setEnabled(len(projects) >= 2)
        self.actionDelete.setEnabled(len(projects) >= 1)
        self.actionEdit.setEnabled(len(projects) == 1)
        self.actionUseAsFilter.setEnabled(len(projects) == 1)

    def get_selected_projects(self) -> List[Project]:
        return [x.data(Qt.UserRole) for x in self.tableWidget.selectedItems() if x.data(Qt.UserRole)]
