"""Custom catalogs: CSV parsing, import into the library, and the report seeing them."""
import threading

import pytest

from photonfinder import custom_catalog, reports
from photonfinder.core import ApplicationContext, StatusReporter
from photonfinder.custom_catalog import CatalogImportError, parse_csv
from photonfinder.models import CatalogEntry, CustomCatalogEntry, SearchCriteria
from tests.conftest import McpSettings

# The solved_sample fixture's 200x200 frame is centred on 10.68, 41.27 at 0.001 deg/pixel.
INSIDE = "Target A,10.68,41.27"
OUTSIDE = "Target B,180.0,-30.0"


# --- Parsing -------------------------------------------------------------------------

def test_comma_file_with_decimal_degrees():
    parsed = parse_csv("id,ra,dec,magnitude,size\nA,10.5,41.25,8.1,12\nB,0,-90,,\n")
    assert [e["catalog_id"] for e in parsed.entries] == ["A", "B"]
    a, b = parsed.entries
    assert (a["ra"], a["dec"], a["magnitude"], a["size"]) == (10.5, 41.25, 8.1, 12.0)
    assert (b["magnitude"], b["size"]) == (None, 0.0)
    assert parsed.skipped == []


def test_semicolon_file_with_aliases_and_any_case():
    parsed = parse_csv("Name;RA;DEC;Canonical_ID\nM42;05:35:17.3;-05:23:28;NGC1976\n")
    [entry] = parsed.entries
    assert entry["catalog_id"] == "M42"
    assert entry["canonical_id"] == "NGC1976"
    assert entry["ra"] == pytest.approx(83.822, abs=1e-3)   # sexagesimal RA is hours
    assert entry["dec"] == pytest.approx(-5.391, abs=1e-3)


@pytest.mark.parametrize("ra,dec", [
    ("05h35m17.3s", "-05d23m28s"),
    ("05 35 17.3", "-05 23 28"),
    ("05:35:17.3", "-05:23:28"),
])
def test_sexagesimal_forms(ra, dec):
    [entry] = parse_csv(f"id,ra,dec\nM42,{ra},{dec}\n").entries
    assert entry["ra"] == pytest.approx(83.822, abs=1e-3)
    assert entry["dec"] == pytest.approx(-5.391, abs=1e-3)


def test_bad_rows_are_skipped_with_line_and_reason():
    parsed = parse_csv("id,ra,dec,magnitude\n"
                       "A,10,20,\n"
                       ",10,20,\n"           # line 3: empty id
                       "B,abc,20,\n"         # line 4: bad RA
                       "C,10,95,\n"          # line 5: Dec out of range
                       "A,11,21,\n"          # line 6: duplicate, first kept
                       "D,10,20,bright\n"    # line 7: non-numeric magnitude
                       "\n"                  # blank lines are ignored
                       "E,400,20,\n")        # line 9: RA out of range
    assert [e["catalog_id"] for e in parsed.entries] == ["A"]
    assert parsed.entries[0]["ra"] == 10.0
    assert [(s.line, s.reason.split()[0]) for s in parsed.skipped] == [
        (3, "empty"), (4, "bad"), (5, "bad"), (6, "duplicate"), (7, "a"), (9, "bad")]
    assert parsed.summary().startswith("1 imported, 6 skipped:\nline 3: empty id")


def test_missing_required_column_rejects_the_file():
    with pytest.raises(CatalogImportError, match="dec"):
        parse_csv("id,ra\nA,10\n")


def test_no_usable_row_rejects_the_file():
    with pytest.raises(CatalogImportError, match="No usable rows"):
        parse_csv("id,ra,dec\nA,x,y\n")


def test_empty_file_is_rejected():
    with pytest.raises(CatalogImportError):
        parse_csv("")


def test_healpix_matches_the_library_index():
    from astropy.coordinates import SkyCoord
    from photonfinder.core import hp
    [entry] = parse_csv("id,ra,dec\nA,10.68,41.27\n").entries
    assert entry["healpix"] == int(hp.skycoord_to_healpix(SkyCoord(ra=10.68, dec=41.27, unit="deg")))


def test_utf8_bom_from_excel_is_ignored(tmp_path):
    path = tmp_path / "targets.csv"
    path.write_bytes("﻿id,ra,dec\nA,1,2\n".encode("utf-8"))
    assert [e["catalog_id"] for e in custom_catalog.parse_file(path).entries] == ["A"]


# --- Library -------------------------------------------------------------------------

def _import(ctx, name, text):
    assert custom_catalog.import_catalog(ctx, name, parse_csv(text))


def test_imported_catalog_is_matched_by_the_report(solved_sample):
    ctx, _ = solved_sample
    _import(ctx, "My targets", f"id,ra,dec\n{INSIDE}\n{OUTSIDE}\n")

    result = reports.catalog_report(ctx, "My targets", SearchCriteria(), only_matching=False)

    assert [e.catalog_id for e in result.entries] == ["Target A", "Target B"]
    assert result.total_entries == 2
    inside = next(e for e in result.entries if e.catalog_id == "Target A")
    assert inside.rowid < 0, "custom entries have negative rowids in the view"
    assert list(result.matches) == [inside.rowid]

    only = reports.catalog_report(ctx, "My targets", SearchCriteria(), only_matching=True)
    assert [e.catalog_id for e in only.entries] == ["Target A"]


def test_builtin_catalog_results_are_unchanged_by_a_custom_catalog(solved_sample):
    ctx, _ = solved_sample
    before = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=True)
    _import(ctx, "My targets", f"id,ra,dec\n{INSIDE}\n")
    after = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=True)
    assert before.entries and before.entries == after.entries
    assert set(before.matches) == set(after.matches)
    assert all(rowid > 0 for rowid in after.matches)


def test_custom_catalog_is_listed_with_the_builtin_ones(context):
    _import(context, "My targets", f"id,ra,dec\n{INSIDE}\n")
    catalogs = {name for (name,) in CatalogEntry.select(CatalogEntry.catalog).distinct().tuples()}
    assert {"My targets", "NGC", "Messier"} <= catalogs
    assert custom_catalog.custom_catalog_names() == ["My targets"]
    assert custom_catalog.is_custom_catalog("My targets")
    assert not custom_catalog.is_custom_catalog("NGC")


def test_reimport_replaces_the_catalog(context):
    _import(context, "My targets", f"id,ra,dec\n{INSIDE}\n{OUTSIDE}\n")
    _import(context, "My targets", "id,ra,dec\nTarget C,1,2\n")
    ids = [e.catalog_id for e in CatalogEntry.select().where(CatalogEntry.catalog == "My targets")]
    assert ids == ["Target C"]


def test_failed_replace_keeps_the_old_catalog(context):
    _import(context, "My targets", f"id,ra,dec\n{INSIDE}\n")
    parsed = parse_csv("id,ra,dec\nTarget C,1,2\n")
    parsed.entries[0]["healpix"] = None   # violates NOT NULL inside the transaction
    with pytest.raises(Exception):
        custom_catalog.import_catalog(context, "My targets", parsed)
    ids = [e.catalog_id for e in CustomCatalogEntry.select().where(CustomCatalogEntry.catalog == "My targets")]
    assert ids == ["Target A"]
    assert context.library_write_lock.holder is None, "the lock is released after a failure"


def test_remove_deletes_only_that_catalog(context):
    _import(context, "Keep", f"id,ra,dec\n{INSIDE}\n")
    _import(context, "Drop", f"id,ra,dec\n{INSIDE}\n")
    assert custom_catalog.remove_catalog(context, "Drop")
    assert custom_catalog.custom_catalog_names() == ["Keep"]


@pytest.mark.parametrize("name", ["NGC", "messier", ""])
def test_builtin_or_empty_names_are_refused(context, name):
    with pytest.raises(CatalogImportError):
        custom_catalog.import_catalog(context, name, parse_csv(f"id,ra,dec\n{INSIDE}\n"))
    assert custom_catalog.custom_catalog_names() == []


def test_busy_library_refuses_without_writing(context):
    assert context.library_write_lock.acquire(blocking=False, holder="Another job")
    try:
        assert not custom_catalog.import_catalog(context, "My targets", parse_csv(f"id,ra,dec\n{INSIDE}\n"))
        assert not custom_catalog.remove_catalog(context, "My targets")
    finally:
        context.library_write_lock.release()
    assert custom_catalog.custom_catalog_names() == []


def test_view_works_on_a_background_thread_connection(tmp_path):
    """TEMP views belong to one connection; peewee opens one per thread."""
    ctx = ApplicationContext(str(tmp_path / "library.db"), McpSettings())
    ctx.set_status_reporter(StatusReporter())
    with ctx:
        _import(ctx, "My targets", f"id,ra,dec\n{INSIDE}\n")
        seen = []

        def read():
            try:
                seen.extend(e.catalog_id for e in
                            CatalogEntry.select().where(CatalogEntry.catalog == "My targets"))
                seen.append(CatalogEntry.select().where(CatalogEntry.catalog == "NGC").count() > 0)
            finally:
                ctx.database.close()

        worker = threading.Thread(target=read)
        worker.start()
        worker.join()
        assert seen == ["Target A", True]


def test_mcp_lists_and_looks_up_a_custom_catalog(context):
    from photonfinder import mcp_server
    _import(context, "My targets", "id,ra,dec,canonical_id\nTarget A,10.68,41.27,TA\n")
    assert "My targets" in mcp_server.query_list_catalogs(context)
    found = mcp_server.query_lookup_object(context, "My targets", "TA")
    assert (found["catalog_id"], found["ra"], found["dec"]) == ("Target A", 10.68, 41.27)


def test_the_documented_example_parses():
    parsed = parse_csv("name;ra;dec;magnitude;size\n"
                       "M42;05:35:17.3;-05:23:28;4.0;85\n"
                       "Heart Nebula;02:33:22;+61:26:36;6.5;150\n"
                       "Spaghetti Nebula;85.25;27.97;;180\n")
    assert [e["catalog_id"] for e in parsed.entries] == ["M42", "Heart Nebula", "Spaghetti Nebula"]
    assert parsed.skipped == []


def test_mcp_tells_the_model_to_suggest_an_import_for_an_unknown_catalog(context):
    from photonfinder import mcp_server
    for answer in (mcp_server.query_lookup_object(context, "Abell", "426"),
                   mcp_server.query_catalog_report(context, "Abell")):
        assert set(answer) == {"error"}
        assert "Unknown catalog 'Abell'" in answer["error"]
        assert "Import CSV" in answer["error"]
    # A known catalog with an unknown id is a different answer: no import hint.
    missing = mcp_server.query_lookup_object(context, "NGC", "999999")
    assert "Import CSV" not in missing["error"]
