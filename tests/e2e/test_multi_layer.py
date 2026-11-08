"""A library holding a two-layer FITS file (a DWARFLAB dual-band stack: HO in the primary, SO
in an IMAGE extension "SOO") next to a single-image file: the grid lists images, file actions
take each file once."""
from pathlib import Path

import pytest
from astropy.io import fits

from photonfinder.models import CORE_MODELS, Project, SearchCriteria
from photonfinder.ui.ExportDialog import ExportDialog
from photonfinder.ui.ProjectEditDialog import ProjectEditDialog
from tests.e2e.helpers import YES, add_libraries
from tests.multi_image_files import write_multi_hdu_fits



@pytest.fixture
def library(sandbox, app, modals, boundary, tmp_path):
    folder = tmp_path / "dwarf"
    folder.mkdir()
    write_multi_hdu_fits(folder / "draco.fits")
    write_multi_hdu_fits(folder / "plain.fits", table=False)
    with fits.open(folder / "plain.fits") as hdul:
        primary = fits.PrimaryHDU(hdul[0].data, hdul[0].header)
    primary.header["FILTER"] = "L"
    primary.writeto(folder / "plain.fits", overwrite=True)
    add_libraries(sandbox, modals, boundary, {"dwarf": folder}, scan=True)
    return folder


def _search(sandbox, app, **criteria):
    app.get_current_search_panel().apply_search_criteria(SearchCriteria(**criteria))
    sandbox.wait_idle()


def _select_rows(panel, names: set[str]):
    from PySide6.QtCore import QItemSelectionModel
    panel.dataView.clearSelection()
    for row in range(panel.proxy_model.rowCount()):
        if panel.get_file_at_row(row).name in names:
            panel.dataView.selectionModel().select(
                panel.proxy_model.index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def test_the_grid_lists_each_layer(sandbox, app, library):
    grid = sandbox.grid()
    panel = grid.panel
    layer = grid.headers().index("Layer")

    assert panel.dataView.isColumnHidden(layer), "hidden in the Standard preset"
    rows = sorted((r["File name"], r["Filter"], r["Layer"] or "") for r in grid.rows())
    assert rows == [("draco.fits", "HO", ""), ("draco.fits", "SO", "SOO"), ("plain.fits", "L", "")]
    assert "3 images in 2 files" in panel.get_title()

    panel.visibility_controller.load_visibility("+Layer")
    assert not panel.dataView.isColumnHidden(layer)


def test_a_filter_search_finds_the_layer(sandbox, app, library):
    _search(sandbox, app, filter="SO")
    assert [(r["File name"], r["Layer"]) for r in sandbox.grid().rows()] == [("draco.fits", "SOO")]


def test_exporting_both_layers_writes_the_file_once(sandbox, app, library, qtbot, tmp_path):
    panel = app.get_current_search_panel()
    _select_rows(panel, {"draco.fits"})
    assert len(panel.get_selected_files()) == 2
    out = tmp_path / "out"

    panel.export_data()
    [dialog] = panel.findChildren(ExportDialog)
    qtbot.waitUntil(lambda: dialog._grid_loaded, timeout=10_000)
    assert dialog.calibrationTable.rowCount() == 2, "a session per layer: HO and SO get their own flats"
    dialog.outputPathEdit.setText(str(out))
    dialog.patternComboBox.setCurrentText("${filter}/${filename}")
    finished = []
    dialog.export_worker.finished.connect(lambda: finished.append(True))
    dialog.export_files()
    qtbot.waitUntil(lambda: bool(finished), timeout=30_000)
    dialog.close()

    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == ["HO/draco.fits"]
    with fits.open(out / "HO" / "draco.fits") as hdul:
        assert [h.name for h in hdul] == ["PRIMARY", "SOO", "STARS"]


def test_adding_both_layers_to_a_project_adds_the_file_once(sandbox, app, library):
    panel = app.get_current_search_panel()
    _select_rows(panel, {"draco.fits"})

    with sandbox.context.database.bind_ctx(CORE_MODELS):
        app.add_selection_to_project(Project(name="Draco"))
    [dialog] = app.findChildren(ProjectEditDialog)

    assert [link.file.name for link in dialog.links_to_add] == ["draco.fits"]
    dialog.close()


def test_marking_a_layer_bad_removes_the_whole_file(sandbox, app, library, boundary):
    panel = app.get_current_search_panel()
    row = next(r for r in range(panel.proxy_model.rowCount())
               if panel.get_file_at_row(r).name == "draco.fits" and panel.get_file_at_row(r).image.hdu == 1)
    boundary.answer("question", YES)

    assert panel.mark_file_as_bad(panel.get_file_at_row(row), row)

    assert [r["File name"] for r in sandbox.grid().rows()] == ["plain.fits"]
    assert (Path(library) / "BAD_draco.fits").exists()
    assert "1 files" in panel.get_title()


def test_a_solve_shows_on_every_layer_of_its_size(sandbox, app, library):
    from types import SimpleNamespace
    from photonfinder import plate_solution
    from tests.conftest import fixed_solution_header
    panel = app.get_current_search_panel()
    ho_row = next(panel.get_file_at_row(r) for r in range(panel.proxy_model.rowCount())
                  if panel.get_file_at_row(r).name == "draco.fits" and panel.get_file_at_row(r).image.hdu == 0)
    solution = fixed_solution_header()
    solution["NAXIS1"], solution["NAXIS2"] = 10, 8
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        plate_solution.store(ho_row, solution, "ASTAP")

    panel.on_files_solved(SimpleNamespace(solved_files=[ho_row]))

    draco = [(r["Layer"] or "", r["RA"], r["Solved"]) for r in sandbox.grid().rows() if r["File name"] == "draco.fits"]
    assert len({ra for _, ra, _ in draco}) == 1 and draco[0][1], draco
    assert [solved for _, _, solved in draco] == ["True", "True"]


def test_a_solve_leaves_a_layer_of_another_size_unsolved(sandbox, app, modals, boundary, tmp_path):
    from types import SimpleNamespace
    from photonfinder import plate_solution
    from tests.conftest import fixed_solution_header
    folder = tmp_path / "mixed"
    folder.mkdir()
    write_multi_hdu_fits(folder / "mixed.fits", extension_shape=(3, 4, 5))
    add_libraries(sandbox, modals, boundary, {"mixed": folder}, scan=True)
    panel = app.get_current_search_panel()
    first = next(panel.get_file_at_row(r) for r in range(panel.proxy_model.rowCount())
                 if panel.get_file_at_row(r).image.hdu == 0)
    solution = fixed_solution_header()
    solution["NAXIS1"], solution["NAXIS2"] = 10, 8
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        plate_solution.store(first, solution, "ASTAP")

    panel.on_files_solved(SimpleNamespace(solved_files=[first]))

    ra = {r["Layer"] or "": r["RA"] for r in sandbox.grid().rows()}
    assert ra[""], "the solved first image"
    assert ra["SOO"] == "", "a layer of another size has no solved centre"
