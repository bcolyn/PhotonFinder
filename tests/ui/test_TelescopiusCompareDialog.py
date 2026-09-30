import json
import os

import pytest

from photonfinder.ui.TelescopiusCompareDialog import (parse_telescopius_json, parse_telescopius_lists,
                                                      fetch_telescopius_lists, TelescopiusTarget, TelescopiusList,
                                                      TelescopiusApiError, TELESCOPIUS_API_URL)


class TestTelescopiusCompareDialog:

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test fixtures."""
        # Load sample JSON data
        sample_json_path = os.path.join(os.path.dirname(__file__), '..', 'sample_telescopius.json')
        with open(sample_json_path, 'r') as f:
            self.sample_json_data = json.load(f)

    def test_parse_telescopius_json(self):
        """Test parsing of Telescopius JSON data into tuples."""
        # Test the parse function
        result = parse_telescopius_json(self.sample_json_data)

        assert isinstance(result, list)
        assert len(result) > 0

        # Check the first few entries match expected values from sample JSON
        expected_first_entries = [
            TelescopiusTarget("NGC 2648", 8.71105576, 14.28499985),
            TelescopiusTarget("NGC 2672", 8.82272243, 19.07444382),
            TelescopiusTarget("NGC 2685", 8.92636108, 58.73472214),
            TelescopiusTarget("NGC 2655", 8.92713928, 78.22360992),
            TelescopiusTarget("NGC 2782", 9.23472214, 40.11360931)
        ]

        # Verify first 5 entries
        for i, expected in enumerate(expected_first_entries):
            assert result[i] == expected, f"Entry {i} does not match expected value"

        for entry in result:
            assert isinstance(entry, TelescopiusTarget)

    def test_parse_telescopius_json_empty_data(self):
        assert parse_telescopius_json({}) == []
        assert parse_telescopius_json({"id": "test", "targets": []}) == []

    def test_parse_telescopius_lists(self):
        json_data = {"lists": [
            {"id": "9c9a9edc", "name": "Visual", "location_id": None, "targets_count": 24,
             "url": "https://telescopius.com/observing-lists/9c9a9edc"},
            {"id": "3a57f00d", "name": "Photography-NB", "location_id": 10255, "targets_count": 83},
        ]}
        result = parse_telescopius_lists(json_data)
        assert result == [TelescopiusList("9c9a9edc", "Visual", 24),
                          TelescopiusList("3a57f00d", "Photography-NB", 83)]
        assert result[0].display_name() == "Visual (24 targets)"
        assert parse_telescopius_lists({}) == []

    def test_fetch_sends_api_key(self, mocker):
        response = mocker.Mock(status_code=200)
        response.json.return_value = {"lists": []}
        get = mocker.patch("photonfinder.ui.TelescopiusCompareDialog.requests.get", return_value=response)
        assert fetch_telescopius_lists("secret") == []
        args, kwargs = get.call_args
        assert args[0] == f"{TELESCOPIUS_API_URL}/target-lists"
        assert kwargs["headers"]["Authorization"] == "Key secret"

    def test_fetch_unauthorized(self, mocker):
        mocker.patch("photonfinder.ui.TelescopiusCompareDialog.requests.get",
                     return_value=mocker.Mock(status_code=401))
        with pytest.raises(TelescopiusApiError):
            fetch_telescopius_lists("bad")


def test_table_sorts_ra_dec_numerically(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget
    from photonfinder.ui.TelescopiusCompareDialog import TelescopiusMatch, SortableTableWidgetItem

    # lexicographic order of the Dec strings (-05:.., -30:.., +10:..) differs from numeric order
    matches = [TelescopiusMatch(TelescopiusTarget("A", 20.5, -5.0), ["lib:a.fits"]),
               TelescopiusMatch(TelescopiusTarget("B", 3.2, -30.0), []),
               TelescopiusMatch(TelescopiusTarget("C", 10.0, 10.0), ["lib:c1.fits", "lib:c2.fits"])]
    table = QTableWidget(len(matches), 4)
    qtbot.addWidget(table)
    for row, match in enumerate(matches):
        for col, (value, key) in enumerate(zip(match.display_values(), match.sort_keys())):
            item = SortableTableWidgetItem(value)
            item.setData(SortableTableWidgetItem.SORT_ROLE, key)
            table.setItem(row, col, item)

    def names():
        return [table.item(r, 0).text() for r in range(table.rowCount())]

    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert names() == ["B", "C", "A"]
    table.sortItems(2, Qt.SortOrder.AscendingOrder)
    assert names() == ["B", "A", "C"]
    table.sortItems(3, Qt.SortOrder.AscendingOrder)
    assert names() == ["B", "A", "C"]


def test_show_filter_applies_to_view_save_and_copy(qtbot, mocker, app_context, tmp_path):
    from PySide6.QtWidgets import QApplication
    from photonfinder.ui.TelescopiusCompareDialog import TelescopiusCompareDialog, TelescopiusMatch, TelescopiusListsTask

    mocker.patch.object(TelescopiusListsTask, "start")  # no network
    dialog = TelescopiusCompareDialog(app_context, None, None)
    qtbot.addWidget(dialog)
    dialog.task.results = [TelescopiusMatch(TelescopiusTarget("M 31", 0.7, 41.3), ["lib:m31.fits"]),
                           TelescopiusMatch(TelescopiusTarget("M 42", 5.6, -5.4), []),
                           TelescopiusMatch(TelescopiusTarget("M 51", 13.5, 47.2), ["lib:a.fits", "lib:b.fits"])]
    dialog.on_complete()

    assert dialog.visible_names() == ["M 31", "M 42", "M 51"]
    dialog.show_matching_radio.setChecked(True)
    assert dialog.visible_names() == ["M 31", "M 51"]
    dialog.show_missing_radio.setChecked(True)
    assert dialog.visible_names() == ["M 42"]

    dialog.copy_names()
    assert QApplication.clipboard().text() == "M 42"

    out = tmp_path / "out.csv"
    dialog._export_table_data(str(out), "csv")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Name,")
    assert [line.split(",")[0] for line in lines[1:]] == ["M 42"]

    # filter survives re-sorting and is re-applied to a fresh fetch
    dialog.tableWidget.sortItems(2)
    assert dialog.visible_names() == ["M 42"]
    dialog.on_complete()
    assert dialog.visible_names() == ["M 42"]
    dialog.show_both_radio.setChecked(True)
    dialog.copy_names()
    assert QApplication.clipboard().text().splitlines() == dialog.visible_names()
    assert len(dialog.visible_names()) == 3


def test_copy_selection_context_menu_and_shortcut(qtbot, mocker, app_context):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QKeySequence
    from photonfinder.ui.TelescopiusCompareDialog import TelescopiusCompareDialog, TelescopiusMatch, TelescopiusListsTask

    mocker.patch.object(TelescopiusListsTask, "start")
    dialog = TelescopiusCompareDialog(app_context, None, None)
    qtbot.addWidget(dialog)
    dialog.task.results = [TelescopiusMatch(TelescopiusTarget("M 31", 0.7, 41.3), ["lib:a.fits", "lib:b.fits"]),
                           TelescopiusMatch(TelescopiusTarget("M 42", 5.6, -5.4), []),
                           TelescopiusMatch(TelescopiusTarget("M 51", 13.5, 47.2), ["lib:c.fits"])]
    dialog.on_complete()
    table = dialog.tableWidget

    assert table.contextMenuPolicy() == Qt.ContextMenuPolicy.ActionsContextMenu
    copy_action = next(a for a in table.actions() if a.text() == "Copy")
    assert copy_action.shortcut() == QKeySequence(QKeySequence.StandardKey.Copy)

    # single cell: copied as-is, including embedded newlines
    table.setCurrentCell(0, 3)
    copy_action.trigger()
    assert QApplication.clipboard().text() == "lib:a.fits\nlib:b.fits"

    # several cells via Ctrl+C: tab-separated rows, hidden rows skipped
    dialog.show_matching_radio.setChecked(True)
    table.selectAll()
    QApplication.clipboard().clear()
    dialog.show(); qtbot.waitExposed(dialog)
    table.setFocus()
    QTest.keySequence(table, QKeySequence(QKeySequence.StandardKey.Copy))
    lines = QApplication.clipboard().text().split("\n")
    assert [line.split("\t")[0] for line in lines] == ["M 31", "M 51"]
    assert lines[0].split("\t")[3] == "lib:a.fits|lib:b.fits"
