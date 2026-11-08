"""Plate solving from a grouped grid: the dialog offers one frame per group, as the grid shows it,
and solves each group's earliest frame."""
from photonfinder import solvers
from photonfinder.ui.PlateSolveDialog import PlateSolveDialog
from tests.e2e.test_diagnose_quality import add_frames, search
from tests.test_solve_service import FakeSolver


def _open_dialog(panel) -> PlateSolveDialog:
    panel.plate_solve_files()
    *_, dialog = panel.findChildren(PlateSolveDialog)
    return dialog


def test_a_grouped_grid_solves_one_frame_per_group(sandbox, app, monkeypatch):
    add_frames(sandbox, "M81", [3.0] * 4)
    add_frames(sandbox, "M82", [3.0] * 3)
    search(sandbox, app)
    solver = FakeSolver()
    monkeypatch.setattr(solvers, "build_solver", lambda settings, index: solver)
    panel = app.get_current_search_panel()

    assert not _open_dialog(panel).one_per_group_check.isChecked()  # one row per image: every frame

    app.actionGroupByNight.trigger()
    sandbox.wait_idle()
    dialog = _open_dialog(panel)
    assert dialog.one_per_group_check.isChecked()

    dialog.start_button.click()
    sandbox.qtbot.waitUntil(lambda: dialog._task is None, timeout=10000)

    assert sorted(solver.solved) == ["M81_0.fits", "M82_0.fits"]  # the earliest of each
    assert dialog.windowTitle() == "Plate Solve — solving 2 of 2 groups"

    # Again: every group has a solved frame now, so nothing is solved and the bar shows done,
    # not Qt's busy animation (a 0..0 range).
    dialog = _open_dialog(panel)
    dialog.start_button.click()
    sandbox.qtbot.waitUntil(lambda: dialog._task is None, timeout=10000)

    assert sorted(solver.solved) == ["M81_0.fits", "M82_0.fits"]
    assert dialog.progressBar.maximum() > 0
    assert dialog.progressBar.value() == dialog.progressBar.maximum()
