"""The Catalog Report window on the synthetic libraries after solving only part of a group: the
group's unsolved frames are listed too, greyed and in italics, with a tooltip naming the solved
frame that covers them."""
import pytest

from photonfinder import custom_catalog
from photonfinder.custom_catalog import parse_csv
from photonfinder.models import CORE_MODELS, File, FileWCS, Image
from photonfinder.ui.CatalogReportWindow import CatalogReportWindow
from tests.e2e.helpers import add_libraries


@pytest.fixture
def ngc7000_partly_solved(sandbox, app, modals, boundary, libraries):
    """The NGC 7000 Ha subs are all solved; keep the solution of the earliest two only. The
    synthetic frames span 45": a custom catalog has an entry at their centre."""
    add_libraries(sandbox, modals, boundary, libraries.roots, scan=True)
    assert custom_catalog.import_catalog(sandbox.context, "Targets", parse_csv("id,ra,dec\nNA,314.75,44.53\n"))
    with sandbox.context.database.bind_ctx(CORE_MODELS):
        subs = list(File.select(File, Image).join(Image)
                    .where((Image.object_name == "NGC 7000") & (Image.filter == "Ha")
                           & File.path.contains("2026-10-14/LIGHT"))
                    .order_by(Image.date_obs))
        assert len(subs) == 6
        FileWCS.delete().where(FileWCS.file.in_([f.rowid for f in subs[2:]])).execute()
    centre = subs[0].image
    assert custom_catalog.import_catalog(sandbox.context, "Targets",
                                         parse_csv(f"id,ra,dec\nNA,{centre.coord_ra},{centre.coord_dec}\n"))
    return [f.name for f in subs]


def _file_items(window, catalog_id):
    tree = window.treeWidget
    [entry] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
               if tree.topLevelItem(i).text(0) == catalog_id]
    return [entry.child(i).child(j) for i in range(entry.childCount()) for j in range(entry.child(i).childCount())]


def test_unsolved_frames_of_a_solved_group_are_listed_and_marked(sandbox, app, ngc7000_partly_solved):
    subs = ngc7000_partly_solved
    sandbox.qtbot.waitUntil(lambda: not sandbox.window.get_current_search_panel().update_in_progress)
    app.get_current_search_panel().report_catalog()
    [window] = app.findChildren(CatalogReportWindow)
    window.catalogCombo.setCurrentText("Targets")
    sandbox.qtbot.waitUntil(lambda: "objects matching" in window.statusbar.currentMessage(), timeout=20000)

    items = {item.text(0): item for item in _file_items(window, "NA")}

    assert set(subs) <= set(items)
    for name in subs[2:]:
        assert items[name].font(0).italic(), name
        assert items[name].toolTip(0) == f"Not plate-solved itself: covered by the solution of {subs[0]}"
    for name in subs[:2]:
        assert not items[name].font(0).italic() and not items[name].toolTip(0)
