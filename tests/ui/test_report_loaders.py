"""Guards the contract between photonfinder.reports and the Qt report windows.

The loaders now delegate their queries to `photonfinder.reports`, so what is left to
protect is the shape each window expects back. These test that mapping directly rather
than spinning up an event loop.
"""
from photonfinder.reports import CatalogReportEntry, MatchedFile, TargetReportRow
from photonfinder.ui.TargetObjectReportWindow import rows_to_table_data


def _row(**kwargs) -> TargetReportRow:
    fields = dict(object_name="M31", filter="Ha", telescope="RC8", camera="ASI2600",
                  total_exposure=600.0, last_date_obs="2024-01-01 22:00:00",
                  paths=["lights/", "lights/2024-01-02/"], file_count=2)
    fields.update(kwargs)
    return TargetReportRow(**fields)


def test_table_data_matches_the_window_headers():
    data = rows_to_table_data([_row()])
    assert len(data) == 1
    # on_complete indexes columns 0-6 and treats column 4 as numeric.
    assert data[0] == ("M31", "Ha", "RC8", "ASI2600", 600.0, "2024-01-01 22:00:00",
                       "lights/\nlights/2024-01-02/")
    assert isinstance(data[0][4], float)


def test_table_data_has_one_cell_per_header():
    # The window builds its headers list in __init__; keep the arity in step with it.
    headers = ["Object Name", "Filter", "Telescope", "Camera", "Total Exposure",
               "Latest data", "Paths"]
    assert len(rows_to_table_data([_row()])[0]) == len(headers)


def test_table_data_handles_a_row_without_paths():
    assert rows_to_table_data([_row(paths=[])])[0][6] == ""


def test_catalog_tree_reads_matched_files_by_attribute():
    """CatalogReportWindow.apply_filter groups by attribute, not tuple position."""
    m = MatchedFile("M31", "D:/data/lights/m31.fits", 1, "Main", "lights/", "m31.fits", 42)
    assert (m.root_id, m.root_name, m.file_dir) == (1, "Main", "lights/")
    assert m.file_name == "m31.fits"
    assert m.object_name == "M31"
    # _save_report unpacks the first two positionally and ignores the rest.
    obj_name, filepath, *_ = m
    assert (obj_name, filepath) == ("M31", "D:/data/lights/m31.fits")


def test_catalog_entries_expose_the_columns_the_tree_reads():
    entry = CatalogReportEntry(rowid=7, catalog_id="224", magnitude=3.4, size=190.0)
    assert (entry.rowid, entry.catalog_id, entry.magnitude, entry.size) \
        == (7, "224", 3.4, 190.0)
