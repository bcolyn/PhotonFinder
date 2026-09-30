from unittest.mock import MagicMock

from PySide6.QtCore import QModelIndex
from PySide6.QtTest import QAbstractItemModelTester

from photonfinder.ui.LibraryTreeModel import LibraryTreeModel, PathNode


def _library_root(rowid=1, name="lib"):
    root = MagicMock()
    root.rowid = rowid
    root.name = name
    return root


def _child_names(node):
    return [c.path_segment for c in node.children]


def _make_model(qtbot, root):
    model = LibraryTreeModel(MagicMock())
    model.file_paths_loader = MagicMock()  # paths are fed in directly, never from the DB
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model._on_library_roots_reloaded([root])
    return model, tester


def test_initial_load_builds_sorted_tree(qtbot):
    root = _library_root()
    model, _tester = _make_model(qtbot, root)

    model._on_paths_loaded(root, ["b/y", "a", "B/x", "b/x"])

    root_node = model.root_node.child(0).child(0)
    assert _child_names(root_node) == ["a", "B", "b"]
    assert _child_names(root_node.children[2]) == ["x", "y"]
    assert root_node.children[2].children[1].full_path == "b/y"
    assert root.rowid in model.loaded_library_roots


def test_reload_inserts_new_dirs_and_keeps_existing_nodes(qtbot):
    root = _library_root()
    model, _tester = _make_model(qtbot, root)
    model._on_paths_loaded(root, ["2024/m31", "2024/m42"])
    root_node = model.root_node.child(0).child(0)
    year_node = root_node.children[0]
    m42_node = year_node.children[1]

    inserted = []
    model.rowsInserted.connect(lambda parent, first, last: inserted.append((parent.internalPointer(), first, last)))

    model._on_paths_loaded(root, ["2024/m31", "2024/m42", "2024/m33/lights", "2025/m101"])

    # Existing nodes are kept (so the view keeps their expansion/selection state)
    assert root_node.children[0] is year_node
    assert year_node.children[2] is m42_node
    assert _child_names(year_node) == ["m31", "m33", "m42"]
    assert _child_names(year_node.children[1]) == ["lights"]
    assert year_node.children[1].children[0].full_path == "2024/m33/lights"
    assert _child_names(root_node) == ["2024", "2025"]
    # Only the new directories were announced to the view
    assert inserted == [(year_node, 1, 1), (root_node, 1, 1)]


def test_reload_removes_vanished_dirs(qtbot):
    root = _library_root()
    model, _tester = _make_model(qtbot, root)
    model._on_paths_loaded(root, ["a/x", "a/y", "b"])

    model._on_paths_loaded(root, ["a/y"])

    root_node = model.root_node.child(0).child(0)
    assert _child_names(root_node) == ["a"]
    assert _child_names(root_node.children[0]) == ["y"]


def test_refresh_loaded_paths_only_reloads_loaded_roots(qtbot):
    loaded, unloaded = _library_root(1, "loaded"), _library_root(2, "unloaded")
    model = LibraryTreeModel(MagicMock())
    model.file_paths_loader = MagicMock()
    model._on_library_roots_reloaded([loaded, unloaded])
    model._on_paths_loaded(loaded, ["a"])
    model.file_paths_loader.reset_mock()

    model.refresh_loaded_paths()

    model.file_paths_loader.load_paths_for_library.assert_called_once_with(loaded)
