"""UI code reading a joined row that a LEFT OUTER JOIN did not find.

Peewee sets the related attribute to None in that case, so `hasattr(file, 'image')` is no
presence check: it is true for every row. These run the app's real queries over a real
database, on files with no Image or FileWCS row and projects with no coordinates.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from astropy.io.fits import Header
from astropy.wcs import WCS
from peewee import JOIN
from PySide6.QtGui import QStandardItem, QStandardItemModel

from photonfinder.core import compress
from photonfinder.models import File, FileWCS, FitsHeader, Image, Project, ProjectFile, SearchCriteria, \
    search_files
from photonfinder.ui.BackgroundLoader import ImageReindexWorker, ProjectsLoader
from photonfinder.ui.ExportDialog import ExportWorker
from photonfinder.ui.ProjectEditDialog import _coords_by_pixel
from photonfinder.ui.ProjectsWindow import ProjectsWindow
from photonfinder.ui.SearchPanel import ROWID_ROLE, SearchPanel
from tests.conftest import DynamicSettings


@pytest.fixture
def library(sample):
    """The sample library plus a file with no Image row and a project with no coordinates."""
    context, objs = sample
    context.settings = DynamicSettings()
    objs["bare"] = File.create(root=objs["root"], path="other/", name="bare.fits", size=1, mtime_millis=1)
    objs["empty_project"] = Project.create(name="Empty")
    return context, objs


def _header_blob(**cards):
    header = Header()
    for key, value in cards.items():
        header[key] = value
    return compress(header.tostring().encode("ascii"))


def _wcs_blob(ra, dec):
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [50, 50]
    wcs.wcs.cdelt = [-0.001, 0.001]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    header = wcs.to_header()
    header["NAXIS"] = 2
    header["NAXIS1"] = 100
    header["NAXIS2"] = 100
    return compress(header.tostring().encode("ascii"))


def _reindex(context):
    ImageReindexWorker(context)._reindex_images_task()
    return {image.file.name: image for image in Image.select()}


def test_reindex_keeps_files_without_a_plate_solution(library):
    context, objs = library
    FitsHeader.create(file=objs["dark"], header=_header_blob(IMAGETYP="DARK", EXPTIME=300.0))

    images = _reindex(context)

    assert images["dark.fits"].image_type == "DARK"
    assert images["dark.fits"].coord_ra is None


def test_reindex_restores_coordinates_from_the_plate_solution(library):
    context, objs = library
    FileWCS.update(wcs=_wcs_blob(10.68, 41.27)).where(FileWCS.file == objs["light"]).execute()

    images = _reindex(context)

    assert images["m31.fits"].coord_ra == pytest.approx(10.68, abs=0.01)
    assert images["m31.fits"].coord_dec == pytest.approx(41.27, abs=0.01)


def test_project_list_loads_with_a_project_without_coordinates(library):
    context, _ = library
    loader = ProjectsLoader(context)
    loaded = []
    loader.projects_loaded.connect(loaded.append)

    loader._reload_projects_task()

    assert {"Andromeda", "Empty"} <= {project.name for project in loaded[0]}


def test_projects_window_shows_a_project_without_coordinates(library, qtbot):
    context, _ = library
    context.settings.set_project_hidden_cols("")
    with patch.object(ProjectsLoader, "reload_projects"):  # a loader thread can't share :memory:
        window = ProjectsWindow(context, None)
    qtbot.addWidget(window)

    window._on_projects_loaded(Project.list_projects_with_image_data())

    table = window.tableWidget
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert table.item(names.index("Empty"), 3) is None
    assert table.item(names.index("Andromeda"), 3).text()


def test_export_wcs_override_skips_files_without_a_plate_solution(library):
    context, objs = library
    worker = ExportWorker(context)
    worker.override_platesolve = True
    file = (File.select(File, FileWCS).join_from(File, FileWCS, JOIN.LEFT_OUTER)
            .where(File.rowid == objs["dark"].rowid)).first()
    header = Header()

    worker._copy_wcs(file, header)

    assert len(header) == 0


def test_nearby_scan_skips_files_without_an_image(library):
    _, objs = library
    Image.update(coord_ra=10.68, coord_dec=41.27, coord_pix256=1234).where(Image.file == objs["light"]).execute()
    files = (File.select(File, Image).join_from(File, Image, JOIN.LEFT_OUTER)
             .where(File.rowid.in_([objs["light"].rowid, objs["bare"].rowid])))

    groups = _coords_by_pixel([ProjectFile(project=objs["project"], file=file) for file in files])

    assert groups == [pytest.approx((10.68, 41.27, 1234))]


def test_solving_a_file_without_an_image_marks_its_row_solved(library, qtbot):
    _, objs = library
    rows, _, _ = search_files(SearchCriteria())
    file = next(row for row in rows if row.rowid == objs["bare"].rowid)
    model = QStandardItemModel(1, 18)
    name_item = QStandardItem(file.name)
    name_item.setData(file, ROWID_ROLE)
    model.setItem(0, 0, name_item)
    panel = SimpleNamespace(data_model=model, search_criteria=SimpleNamespace(plate_solved=None))

    SearchPanel.on_files_solved(panel, SimpleNamespace(solved_files=[file]))

    assert model.index(0, 17).data() == "True"
    assert model.index(0, 15).data() is None
