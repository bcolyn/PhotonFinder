"""Group by Night on a grid holding all three synthetic libraries: one row per group with its
frame count, a selected row standing for its frames, the drill-in, and the mode surviving a
restart."""
from collections import Counter
from datetime import timedelta

import pytest

from photonfinder import filter_catalogue
from photonfinder.library_query import COLUMN_INDEX
from tests.e2e.helpers import Grid, add_libraries


@pytest.fixture
def grid(sandbox, app, modals, boundary, libraries) -> Grid:
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    return sandbox.grid()


def _groups(libraries) -> Counter:
    """Frames per group, by the group key: type, folder, object, filter, exposure, night."""
    return Counter((f.image_type, path.parent, f.object_name, f.filter, round(f.exposure, 2),
                    (f.date_loc - timedelta(hours=12)).date())
                   for path, f in libraries.files.items())


def _group_by_night(sandbox, app) -> Grid:
    app.actionGroupByNight.trigger()
    sandbox.wait_idle()
    return sandbox.grid()


def test_one_row_per_group_with_its_frames(sandbox, app, libraries, grid):
    panel = grid.panel
    assert panel.dataView.isColumnHidden(COLUMN_INDEX["frames"])

    grid = _group_by_night(sandbox, app)

    expected = _groups(libraries)
    assert grid.total == len(expected)
    assert sorted(int(n) for n in grid.column("Frames")) == sorted(expected.values())
    assert not panel.dataView.isColumnHidden(COLUMN_INDEX["frames"])
    assert app.actionGroupByNight.isChecked()
    assert panel.title.startswith(f"{len(libraries.files)} files in {len(expected)} groups")

    app.actionGroupByNight.trigger()  # back to one row per image
    sandbox.wait_idle()
    assert grid.total == len(libraries.files)
    assert panel.dataView.isColumnHidden(COLUMN_INDEX["frames"])


def test_a_selected_row_stands_for_its_frames(sandbox, app, libraries, grid):
    grid = _group_by_night(sandbox, app)
    biggest = max(int(n) for n in grid.column("Frames"))

    grid.select("Frames", str(biggest))

    assert len(grid.panel.get_selected_rows()) == 1
    assert len(grid.panel.get_selected_files()) == biggest


def test_double_click_opens_the_groups_frames(sandbox, app, libraries, grid):
    grid = _group_by_night(sandbox, app)
    frames = grid.column("Frames")
    row = frames.index(str(max(int(n) for n in frames)))
    tabs = len(app.get_search_panels())

    grid.panel.on_item_double_clicked(grid.model.index(row, 0))
    sandbox.wait_idle()

    assert len(app.get_search_panels()) == tabs + 1
    members = sandbox.grid()
    assert members.total == int(frames[row])
    assert not members.panel.search_criteria.one_per_group
    assert filter_catalogue.GROUPS in members.panel.advanced_options


def test_grouping_comes_back_after_a_restart(sandbox, app, libraries, grid):
    _group_by_night(sandbox, app)

    app = sandbox.restart()

    grid = sandbox.grid(app.get_search_panels()[-1])
    assert grid.panel.search_criteria.one_per_group
    assert grid.total == len(_groups(libraries))
