"""Merging and editing projects when files overlap, on a real in-memory library."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from photonfinder.models import File, Project, ProjectFile
from photonfinder.ui.ProjectEditDialog import ProjectEditDialog
from photonfinder.ui.ProjectsWindow import ProjectsWindow


def _files_of(project) -> list[str]:
    return sorted(pf.file.name for pf in ProjectFile.select().where(ProjectFile.project == project.rowid))


def _project(name, *files):
    project = Project.create(name=name)
    for f in files:
        ProjectFile.create(project=project, file=f)
    return project


@pytest.fixture(autouse=True)
def message_boxes(monkeypatch):
    """Record message boxes instead of showing them: a real one would block the test run."""
    shown = []
    for kind in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind,
                            lambda parent, title, text, *a, _kind=kind, **k: shown.append((_kind, text)))
    return shown


@pytest.fixture
def lib(sample):
    context, data = sample
    extra = File.create(root=data["root"], path="lights/", name="no-image.fits", size=1, mtime_millis=1)
    return context, dict(data, extra=extra)


def test_merging_projects_that_share_files_links_each_file_once(lib):
    context, d = lib
    a = d["project"]  # "Andromeda", has the light
    b = _project("B", d["light"], d["dark"])
    c = _project("C", d["dark"], d["extra"])

    with context.database.atomic():
        merged = Project.merge([a, b, c])

    assert merged.name == "Andromeda,B,C"
    assert _files_of(merged) == ["dark.fits", "m31.fits", "no-image.fits"]
    assert ProjectFile.select().count() == 3
    assert [p.name for p in Project.select()] == ["Andromeda,B,C"]


def test_a_merged_name_already_taken_gets_a_number(lib):
    context, d = lib
    _project("Andromeda,B")
    b = _project("B", d["dark"])

    with context.database.atomic():
        merged = Project.merge([d["project"], b])

    assert merged.name == "Andromeda,B (2)"


def test_the_merge_action_announces_the_change_after_the_commit(lib, monkeypatch):
    context, d = lib
    b = _project("B", d["light"])
    seen = []
    context.signal_bus.projects_changed.connect(
        lambda projects, change: seen.append((change, context.database.in_transaction(),
                                              Project.select().count())))
    window = SimpleNamespace(context=context, get_selected_projects=lambda: [d["project"], b])

    ProjectsWindow.merge_action(window)

    assert seen and all(not in_tx and count == 1 for _, in_tx, count in seen)


@pytest.fixture
def dialog(qtbot, lib):
    context, d = lib
    ProjectFile.create(project=d["project"], file=d["extra"])  # a file without an Image row

    def open_dialog(project):
        dlg = ProjectEditDialog(context, project)
        qtbot.addWidget(dlg)
        dlg.refresh_table()
        return dlg

    return open_dialog


def _table_names(dlg):
    return sorted(dlg.tableWidget.item(r, 2).text() for r in range(dlg.tableWidget.rowCount()))


def test_the_edit_dialog_shows_links_to_files_without_an_image(lib, dialog):
    _, d = lib
    dlg = dialog(d["project"])

    assert _table_names(dlg) == ["m31.fits", "no-image.fits"]


def test_re_adding_a_file_without_an_image_saves(lib, dialog, message_boxes):
    context, d = lib
    dlg = dialog(d["project"])
    # what Add Files does: look the file up, then add it
    dlg.add_file(ProjectFile.find_by_filename("/data/lights/no-image.fits", d["project"]))
    dlg.add_file(ProjectFile(project=d["project"], file=d["extra"]))  # or a fresh link to it
    dlg.name_edit.setText("Renamed")

    dlg.save_data_and_close()

    assert message_boxes == []
    assert Project.get_by_id(d["project"].rowid).name == "Renamed"
    assert _files_of(d["project"]) == ["m31.fits", "no-image.fits"]


def test_saving_announces_the_change_after_the_commit(lib, dialog):
    context, d = lib
    dlg = dialog(d["project"])
    dlg.add_file(ProjectFile(project=d["project"], file=d["dark"]))
    seen = []
    context.signal_bus.projects_changed.connect(lambda *a: seen.append(context.database.in_transaction()))
    context.signal_bus.project_links_changed.connect(lambda *a: seen.append(context.database.in_transaction()))

    dlg.save_data_and_close()

    assert seen == [False, False]


def test_a_name_clash_is_reported_as_such_and_saves_nothing(lib, dialog, message_boxes):
    _, d = lib
    _project("Taken")
    dlg = dialog(d["project"])
    dlg.add_file(ProjectFile(project=d["project"], file=d["dark"]))
    dlg.name_edit.setText("Taken")

    dlg.save_data_and_close()

    warnings = [text for kind, text in message_boxes if kind == "warning"]
    assert len(warnings) == 1 and "already exists" in warnings[0]
    assert Project.get_by_id(d["project"].rowid).name == "Andromeda"
    assert _files_of(d["project"]) == ["m31.fits", "no-image.fits"]


def test_another_integrity_error_is_not_called_a_name_clash(lib, dialog, message_boxes):
    _, d = lib
    dlg = dialog(d["project"])
    # a link the database already has, added as new: the (project, file) index refuses it
    dlg.links_to_add.add(ProjectFile(project=d["project"], file=d["light"]))

    dlg.save_data_and_close()

    warnings = [text for kind, text in message_boxes if kind == "warning"]
    assert len(warnings) == 1 and "already exists" not in warnings[0]
    assert "UNIQUE" in warnings[0]


def test_creating_a_new_project_saves_it_with_its_files(lib, qtbot, monkeypatch):
    context, d = lib
    project = Project()
    dlg = ProjectEditDialog(context, project)
    qtbot.addWidget(dlg)
    dlg.add_file(ProjectFile(project=project, file=d["dark"]))
    dlg.name_edit.setText("New one")

    dlg.save_data_and_close()

    saved = Project.get(Project.name == "New one")
    assert _files_of(saved) == ["dark.fits"]
