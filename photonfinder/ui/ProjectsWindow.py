from datetime import datetime
from typing import List, Collection

from PySide6.QtCore import Qt, Signal, QSize, QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QTableWidgetItem, QMessageBox, QWidget, QToolTip
from peewee import JOIN

from photonfinder.core import ApplicationContext, Change
from photonfinder.models import Project, ProjectFile, File, Image
from photonfinder.ui.BackgroundLoader import ProjectsLoader
from photonfinder.ui.generated.ProjectsWindow_ui import Ui_ProjectsWindow
from .ProjectEditDialog import ProjectEditDialog
from .SearchPanel import FILE_DRAG_MIME
from .common import _format_ra, _format_dec, _format_date, create_colored_svg_icon, ColumnVisibilityController


class ProjectsWindow(QWidget, Ui_ProjectsWindow):
    main_window: "MainWindow"
    closing = Signal()

    def __init__(self, context: ApplicationContext, main_window, parent=None):
        super(ProjectsWindow, self).__init__(parent)
        self.setupUi(self)
        self.context = context
        self.main_window = main_window
        self._loader = ProjectsLoader(context)
        self._loader.projects_loaded.connect(self._on_projects_loaded)
        self._pending_select_project = None
        self.connect_signals()

        text_color = self.palette().color(QPalette.WindowText)
        size = QSize(24, 24)
        self.actionUseAsFilter.setIcon(create_colored_svg_icon(":/res/funnel.svg", size, text_color))
        self.visibility_controller = ColumnVisibilityController(self.tableWidget)
        hidden_cols = context.settings.get_project_hidden_cols()
        self.visibility_controller.load_visibility(hidden_cols)

        self._loader.reload_projects()

        self.tableWidget.viewport().setAcceptDrops(True)
        self.tableWidget.viewport().installEventFilter(self)

    def connect_signals(self):
        self.actionCreate.triggered.connect(self.create_action)
        self.actionEdit.triggered.connect(self.edit_action)
        self.actionDelete.triggered.connect(self.delete_action)
        self.actionMerge.triggered.connect(self.merge_action)
        self.actionUseAsFilter.triggered.connect(self.use_as_filter_action)
        self.tableWidget.itemSelectionChanged.connect(self.enable_disable_actions)
        self.tableWidget.doubleClicked.connect(self.edit_action)
        self.filterEdit.textChanged.connect(self._apply_filter)
        self.context.signal_bus.projects_changed.connect(self.on_projects_changed)
        self.context.signal_bus.project_links_changed.connect(self.on_project_links_changed)

    def on_projects_changed(self, projects: Collection[Project], change: Change):
        # TODO partial update
        self._loader.reload_projects()

    def on_project_links_changed(self, project_files: Collection[ProjectFile], change: Change):
        # TODO partial update
        self._loader.reload_projects()

    def _on_projects_loaded(self, projects):
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
                self.tableWidget.setItem(row, 5, QTableWidgetItem(getattr(project, '_constellation', "")))
            last_change = project.last_change
            last_change_dt = datetime.fromisoformat(last_change) if isinstance(last_change, str) else last_change
            self.tableWidget.setItem(row, 6, QTableWidgetItem(_format_date(last_change_dt)))

        self.tableWidget.resizeColumnsToContents()
        self._apply_filter(self.filterEdit.text())
        if self._pending_select_project is not None:
            self._select_project_row(self._pending_select_project)
            self._pending_select_project = None

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for row in range(self.tableWidget.rowCount()):
            item = self.tableWidget.item(row, 0)
            hidden = bool(needle) and (item is None or needle not in item.text().lower())
            self.tableWidget.setRowHidden(row, hidden)
        self.enable_disable_actions()

    def select_project_after_load(self, project: Project):
        """Select the given project after the next load completes."""
        self._pending_select_project = project.rowid

    def select_project(self, project: Project):
        """Select the given project immediately if the table is populated, otherwise defer."""
        if self.tableWidget.rowCount() > 0:
            self._select_project_row(project.rowid)
        else:
            self._pending_select_project = project.rowid

    def _select_project_row(self, project_rowid: int):
        for row in range(self.tableWidget.rowCount()):
            item = self.tableWidget.item(row, 0)
            if item and item.data(Qt.UserRole) and item.data(Qt.UserRole).rowid == project_rowid:
                self.tableWidget.selectRow(row)
                self.tableWidget.scrollToItem(item)
                return

    def eventFilter(self, obj, event):
        if obj is self.tableWidget.viewport():
            t = event.type()
            if t == QEvent.DragEnter:
                if event.mimeData().hasFormat(FILE_DRAG_MIME):
                    event.acceptProposedAction()
                    return True
            elif t == QEvent.DragMove:
                if event.mimeData().hasFormat(FILE_DRAG_MIME):
                    item = self.tableWidget.itemAt(event.position().toPoint())
                    if item:
                        self.tableWidget.selectRow(item.row())
                        project = self.tableWidget.item(item.row(), 0).data(Qt.UserRole)
                        if project:
                            QToolTip.showText(
                                self.tableWidget.viewport().mapToGlobal(event.position().toPoint()),
                                f"Add to: {project.name}",
                                self.tableWidget.viewport()
                            )
                    else:
                        self.tableWidget.clearSelection()
                        QToolTip.hideText()
                    event.acceptProposedAction()
                    return True
            elif t == QEvent.DragLeave:
                self.tableWidget.clearSelection()
                QToolTip.hideText()
            elif t == QEvent.Drop:
                return self._handle_file_drop(event)
        return super().eventFilter(obj, event)

    def _handle_file_drop(self, event):
        mime = event.mimeData()
        if not mime.hasFormat(FILE_DRAG_MIME):
            return False
        item = self.tableWidget.itemAt(event.position().toPoint())
        if item is None:
            return False
        project = self.tableWidget.item(item.row(), 0).data(Qt.UserRole)
        if not project or project.rowid <= 0:
            return False
        raw = bytes(mime.data(FILE_DRAG_MIME)).decode()
        rowids = [int(x) for x in raw.split(",") if x]
        files = list(File.select(File, Image).join_from(File, Image, JOIN.LEFT_OUTER).where(File.rowid.in_(rowids)))
        files_to_add = File.remove_already_mapped(project, files)
        edit_dialog = ProjectEditDialog(context=self.context, project=project,
                                        parent=self.main_window, main_window=self.main_window)
        for file in files_to_add:
            edit_dialog.add_file(ProjectFile(project=project, file=file))
        edit_dialog.show()
        edit_dialog.refresh_table()
        event.acceptProposedAction()
        return True

    def populate_table(self):
        self._loader.reload_projects()

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
        else:
            self.populate_table()

    def create_action(self):
        project = Project()
        dialog = ProjectEditDialog(context=self.context, parent=self, project=project, main_window=self.main_window)
        dialog.show()
        dialog.refresh_table()

    def edit_action(self):
        projects = self.get_selected_projects()
        assert len(projects) == 1
        project = projects[0]
        dialog = ProjectEditDialog(context=self.context, parent=self, project=project, main_window=self.main_window)
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
