"""Pictures with the features around them: export copies them as they are, projects hold them,
image analysis skips them in one log line, and MCP says why a picture has no header."""
import string
from pathlib import Path

import cv2
import numpy as np
import pytest
from astropy.io import fits

from photonfinder import pictures
from photonfinder.mcp_server import query_file_details
from photonfinder.models import File, FileWCS, Image, LibraryRoot, Project, ProjectFile, SearchCriteria
from photonfinder.ui.BackgroundLoader import ImageAnalysisTask
from photonfinder.ui.ExportDialog import ExportWorker, template_filename


@pytest.fixture
def files(context, tmp_path):
    """A root with a JPEG picture and a small FITS light, both indexed."""
    root = LibraryRoot.create(name="r", path=str(tmp_path).replace("\\", "/") + "/", include_pictures=True)
    ok, jpeg = cv2.imencode(".jpg", np.full((30, 40, 3), 90, np.uint8))
    assert ok
    jpeg.tofile(str(tmp_path / "M27 final.jpg"))
    rng = np.random.default_rng(1)
    fits.PrimaryHDU(rng.normal(1000, 20, (64, 64)).astype(np.uint16)).writeto(tmp_path / "light.fits")
    picture = File.create(root=root, path="", name="M27 final.jpg", size=1, mtime_millis=1)
    Image.create(file=picture, hdu=0, image_type=pictures.PICTURE_TYPE, width=40, height=30)
    light = File.create(root=root, path="", name="light.fits", size=1, mtime_millis=1)
    Image.create(file=light, hdu=0, image_type="LIGHT", width=64, height=64)
    return context, tmp_path, picture, light


class TestExport:
    def test_a_picture_is_copied_byte_for_byte(self, files, tmp_path):
        context, folder, picture, _ = files
        FileWCS.create(file=picture, wcs=b"not used")
        worker = ExportWorker(context)
        worker.override_platesolve = True  # neither applies to a picture
        out = tmp_path / "out" / "M27 final.jpg"
        out.parent.mkdir()

        worker.copy_file(str(folder / "M27 final.jpg"), str(out), picture, {"OBJECT": "M 27"})

        assert out.read_bytes() == (folder / "M27 final.jpg").read_bytes()

    def test_a_picture_keeps_its_name_and_extension(self, files):
        from tests.conftest import DynamicSettings
        _, _, picture, _ = files
        row = File.get_by_id(picture.rowid)
        row.image = Image.get(Image.file == picture)  # as a search row carries it
        name = template_filename(row, string.Template("$image_type/$filename_no_ext.$ext"), DynamicSettings(),
                                 decompress=True, export_xisf_as_fits=True)
        assert Path(name).as_posix() == "PICTURE/M27 final.jpg"


class TestProjects:
    def test_pictures_are_linked_and_listed_in_the_project_editor(self, files, qtbot, tmp_path):
        """One picture already linked, one added as "Add to Project" adds a selection."""
        from photonfinder.ui.ProjectEditDialog import ProjectEditDialog
        context, folder, picture, light = files
        cv2.imencode(".png", np.zeros((10, 12), np.uint8))[1].tofile(str(folder / "annotated.png"))
        second = File.create(root=picture.root, path="", name="annotated.png", size=1, mtime_millis=1)
        Image.create(file=second, hdu=0, image_type=pictures.PICTURE_TYPE, width=12, height=10)
        project = Project.create(name="M 27")
        ProjectFile.create(project=project, file=picture)
        ProjectFile.create(project=project, file=light)

        dialog = ProjectEditDialog(context, project)
        qtbot.addWidget(dialog)
        dialog.add_file(ProjectFile(project=project, file=second))
        dialog.refresh_table()

        shown = {dialog.tableWidget.item(r, c).text() for r in range(dialog.tableWidget.rowCount())
                 for c in range(dialog.tableWidget.columnCount()) if dialog.tableWidget.item(r, c)}
        assert {"M27 final.jpg", "annotated.png", "light.fits"} <= shown


def _analysis(context, files):
    task = ImageAnalysisTask(context, SearchCriteria(), files)
    messages, finished = [], []
    task.message.connect(messages.append)
    task.finished.connect(lambda: finished.append(True))
    return task, messages, finished


class TestAnalysis:
    def test_pictures_in_a_selection_are_skipped_in_one_line(self, files):
        context, _, picture, light = files
        task, messages, finished = _analysis(context, [File.get_by_id(picture.rowid), File.get_by_id(light.rowid)])

        task._analyse()  # pylint: disable=protected-access  # the work, without the thread and lock

        assert [file.name for file, _ in task.analyzed_files] == ["light.fits"]
        assert [m for m in messages if "picture" in m] == [
            "Skipped 1 picture (JPEG/PNG): image analysis needs FITS or XISF data"]
        assert finished

    def test_a_selection_of_only_pictures_analyses_nothing(self, files):
        """Not the whole search instead, which an empty selection would mean."""
        context, _, picture, _ = files
        task, messages, finished = _analysis(context, [File.get_by_id(picture.rowid)])

        task._analyse()  # pylint: disable=protected-access

        assert task.analyzed_files == [] and finished
        assert any("Skipped 1 picture" in m for m in messages)

    def test_pictures_matching_the_search_are_skipped(self, files):
        context, _, _, _ = files
        task, messages, _ = _analysis(context, None)

        task._analyse()  # pylint: disable=protected-access

        assert [file.name for file, _ in task.analyzed_files] == ["light.fits"]
        assert any("Skipped 1 picture" in m for m in messages)


def test_mcp_file_details_say_why_a_picture_has_no_header(files):
    context, _, picture, light = files
    details = query_file_details(context, picture.rowid)
    assert details["header"] is None and "picture" in details["header_note"]
    assert "header_note" not in query_file_details(context, light.rowid)
