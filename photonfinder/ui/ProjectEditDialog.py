import operator
from datetime import datetime
from functools import reduce, cmp_to_key
from itertools import chain
from pathlib import Path
from typing import List, Set
from astropy import units as u

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTableWidgetItem, QFileDialog, QMessageBox
from astropy.coordinates import SkyCoord

from photonfinder.core import ApplicationContext
from photonfinder.models import Project, File, LibraryRoot, ProjectFile, Image, hp, RootAndPath
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
        self.project_files = list(ProjectFile.select(ProjectFile, File, LibraryRoot, Image)
                                  .join(File)
                                  .join_from(File, Image)
                                  .join_from(File, LibraryRoot)
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
        updated_files = self.get_current_files()
        updated_files.sort(key=lambda pf: (pf.file.root.rowid, pf.file.path, pf.file.name))
        self.tableWidget.setRowCount(len(updated_files))

        for row, project_file in enumerate(updated_files):
            first_item = QTableWidgetItem(project_file.file.root.name)
            first_item.setData(Qt.UserRole, project_file)
            self.tableWidget.setItem(row, 0, first_item)
            self.tableWidget.setItem(row, 1, QTableWidgetItem(project_file.file.path))
            self.tableWidget.setItem(row, 2, QTableWidgetItem(project_file.file.name))
            self.tableWidget.setItem(row, 3, QTableWidgetItem(_format_timestamp(project_file.file.mtime_millis)))

        self.tableWidget.resizeColumnsToContents()

    def get_current_files(self):
        return [f for f in self.project_files if f not in self.links_to_delete] + list(self.links_to_add)

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
        self.scan_more_button.clicked.connect(self.do_scan_more)

    def do_scan_more(self):
        #TODO: if any of the current files have WCS, maybe use that - at the very least FoV for max_dist
        project_files = self.get_current_files()
        used_file_ids = set([pf.file.rowid for pf in project_files])
        if not project_files:
            return # can't add more if we have nothing to go on

        # Add files with same (more or less) center coordinates
        coord_groups = _coords_by_pixel(
            project_files)  # group by, but in memory since not all data may be persisted yet
        max_dist = 10 * u.arcmin
        all_pixels = set(chain.from_iterable(
            hp.cone_search_skycoord(SkyCoord(ra=x[0], dec=x[1], unit=u.deg, frame='icrs'), max_dist) for x in
            coord_groups))

        # build query
        q = (File.select(File, Image, LibraryRoot)
             .join_from(File, LibraryRoot).join_from(File, Image))
        q = q.where(Image.coord_pix256.in_(all_pixels))
        q = q.where(File.rowid.not_in(used_file_ids))
        any_of_conditions = []

        # add if they are in the same directory as current files
        dirs_in_project = list(set(
            map(lambda pf: RootAndPath(pf.file.root.rowid, pf.file.root.name, pf.file.path), project_files)))

        any_of_conditions += list(map(
            lambda root_and_path: ((File.path == root_and_path.path) & (File.root == root_and_path.root_id)),
            dirs_in_project))

        # add if they are LIGHT files older than our current MASTER LIGHT images
        masters = list(filter(lambda pf: pf.file.image.image_type == "MASTER LIGHT", project_files))
        if masters:
            min_timestamp = min(map(lambda pf: pf.file.mtime_millis, masters))
            min_dt = datetime.fromtimestamp(min_timestamp / 1000)
            any_of_conditions.append((Image.image_type == "LIGHT") & (Image.date_obs < min_dt))

        q = q.where(reduce(operator.or_, any_of_conditions))
        q = q.order_by(File.root, File.path, File.name)

        for f in q.execute():
            #TODO: should we filter again on actual distance?
            pf = ProjectFile(file=f, project=self.project)
            self.add_file(pf)

        self.refresh_table()


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


def _coords_by_pixel(project_files: List[ProjectFile]):
    import numpy as np
    data = np.array([
        [pf.file.image.coord_ra, pf.file.image.coord_dec, pf.file.image.coord_pix256]
        for pf in project_files
    ])
    unique_pix_ids = np.unique(data[:, 2])
    result = [
        (
            # Use boolean indexing to get all rows for the current pix_id,
            # select the first two columns (ra, dec), and calculate their mean.
            *np.mean(data[data[:, 2] == pix_id, :2], axis=0),
            int(pix_id)
        )
        for pix_id in unique_pix_ids
    ]
    return result
