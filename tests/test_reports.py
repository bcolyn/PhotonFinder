"""Tests for the shared report query layer (photonfinder.reports).

These call the report functions directly, the way both the Qt report windows and the MCP
server do, so the aggregates are covered without a widget or an event loop in sight.
"""
from datetime import datetime

from astropy.io.fits import Header

from photonfinder import reports
from photonfinder.core import compress
from photonfinder.models import File, FileWCS, FitsHeader, Image, SearchCriteria


def _add_light(root, path, name, **kwargs):
    file = File.create(root=root, path=path, name=name, size=1000, mtime_millis=111)
    fields = dict(image_type="LIGHT", filter="Ha", camera="ASI2600", telescope="RC8",
                  object_name="M31", exposure=300.0, gain=100, binning=1,
                  date_obs=datetime(2024, 1, 1, 22, 0, 0))
    fields.update(kwargs)
    Image.create(file=file, **fields)
    return file


# --- Target report ------------------------------------------------------------------

def test_target_report_groups_and_excludes_unnamed(sample):
    _, data = sample
    rows = reports.target_report(SearchCriteria())

    # The DARK has no object_name, so it is excluded entirely.
    assert len(rows) == 1
    row = rows[0]
    assert row.object_name == "M31"
    assert (row.filter, row.telescope, row.camera) == ("Ha", "RC8", "ASI2600")
    assert row.total_exposure == 300.0
    assert row.file_count == 1
    assert row.paths == ["lights/"]


def test_target_report_sums_exposure_across_files(sample):
    _, data = sample
    _add_light(data["root"], "lights/", "m31_002.fits")

    rows = reports.target_report(SearchCriteria())
    assert len(rows) == 1
    assert rows[0].total_exposure == 600.0
    assert rows[0].file_count == 2


def test_target_report_respects_criteria(sample):
    reports_for = lambda **kw: reports.target_report(SearchCriteria(**kw))
    assert len(reports_for(type="LIGHT")) == 1
    assert reports_for(filter="Hb") == []


def test_target_report_paths_is_a_list(sample):
    _, data = sample
    _add_light(data["root"], "lights/2024-01-02/", "m31_003.fits")

    rows = reports.target_report(SearchCriteria())
    assert sorted(rows[0].paths) == ["lights/", "lights/2024-01-02/"]
    # No residue from the ":" / ":," delimiter trick the SQL uses to join them.
    assert not any(":" in p for p in rows[0].paths)


def test_target_report_orders_case_insensitively(sample):
    _, data = sample
    _add_light(data["root"], "lights/", "ngc.fits", object_name="ngc7000")

    rows = reports.target_report(SearchCriteria())
    assert [r.object_name for r in rows] == ["M31", "ngc7000"]


def test_target_report_last_date_obs_normalizes_to_iso(sample):
    rows = reports.target_report(SearchCriteria())
    # Read through .tuples() the aggregate is raw SQLite text, not a datetime.
    assert datetime.fromisoformat(reports.iso_datetime(rows[0].last_date_obs)) \
        == datetime(2024, 1, 1, 22, 0, 0)


# --- Catalog coverage report --------------------------------------------------------

def test_catalog_report_matches_a_solved_image(solved_sample):
    ctx, _ = solved_sample
    result = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=True)

    # The fixture solution is centred on M31, which is NGC 224.
    assert result.matches, "expected the footprint test to match at least one catalog entry"
    assert "224" in [e.catalog_id for e in result.entries]
    assert result.images_considered == 1
    matched = next(iter(result.matches.values()))
    assert matched[0].file_name == "m31.fits"
    assert matched[0].file_id is not None


def test_catalog_report_progress_callback_is_called(solved_sample):
    ctx, _ = solved_sample
    seen = []
    reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=True,
                           progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 1)]


def test_catalog_report_only_matching_skips_the_full_scan(solved_sample):
    ctx, _ = solved_sample
    matching = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=True)
    full = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=False)

    assert matching.total_entries == len(matching.matches)
    # The full report counts the whole catalog, not just what was imaged.
    assert full.total_entries > matching.total_entries


def test_catalog_report_limit_and_offset_slice_entries(solved_sample):
    ctx, _ = solved_sample
    full = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=False)
    page = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=False,
                                  limit=5, offset=5)

    assert len(page.entries) == 5
    assert page.total_entries == full.total_entries
    assert page.entries == full.entries[5:10]


def test_catalog_report_unmatched_criteria_yields_no_matches(solved_sample):
    ctx, _ = solved_sample
    result = reports.catalog_report(ctx, "NGC", SearchCriteria(filter="Hb"),
                                    only_matching=True)
    assert result.matches == {}
    assert result.images_considered == 0


def test_catalog_report_unknown_catalog_is_empty(solved_sample):
    ctx, _ = solved_sample
    result = reports.catalog_report(ctx, "NOT_A_CATALOG", SearchCriteria(),
                                    only_matching=False)
    assert result.entries == []
    assert result.matches == {}
    assert result.total_entries == 0


def test_catalog_report_ignores_unsolved_images(sample):
    """`sample`'s WCS blob is not decodable, so nothing can be projected."""
    ctx, _ = sample
    result = reports.catalog_report(ctx, "NGC", SearchCriteria(), only_matching=True)
    assert result.images_considered == 1
    assert result.matches == {}


# --- Metadata field extraction ------------------------------------------------------

def _load_file(rowid, sources=("fits", "platesolving")):
    return reports.header_values_query(SearchCriteria(), set(sources)) \
        .where(File.rowid == rowid).get()


def test_extract_photonfinder_field(sample):
    _, data = sample
    file = _load_file(data["light"].rowid)
    assert reports.extract_field_value(file, "Image.exposure", "photonfinder") == 300.0
    assert reports.extract_field_value(file, "File.name", "photonfinder") == "m31.fits"


def test_extract_fits_field(sample):
    _, data = sample
    file = _load_file(data["light"].rowid)
    assert reports.extract_field_value(file, "GAIN", "fits") == 100
    assert reports.extract_field_value(file, "OBJECT", "fits") == "M31"


def test_extract_fits_field_unknown_keyword_is_none(sample):
    _, data = sample
    file = _load_file(data["light"].rowid)
    assert reports.extract_field_value(file, "NOSUCHKW", "fits") is None


def test_extract_fits_field_without_a_header_is_none(sample):
    _, data = sample
    file = _load_file(data["dark"].rowid)
    assert reports.extract_field_value(file, "GAIN", "fits") is None


def test_extract_platesolving_field(solved_sample):
    _, data = solved_sample
    file = _load_file(data["light"].rowid)
    assert reports.extract_field_value(file, "CRVAL1", "platesolving") == 10.68


def test_extract_field_value_unknown_source_is_none(sample):
    _, data = sample
    file = _load_file(data["light"].rowid)
    assert reports.extract_field_value(file, "GAIN", "nonsense") is None


def test_parse_field_spec():
    assert reports.parse_field_spec("WCS:CRVAL1") == ("CRVAL1", "platesolving")
    assert reports.parse_field_spec("Image.exposure") == ("Image.exposure", "photonfinder")
    assert reports.parse_field_spec("File.size") == ("File.size", "photonfinder")
    assert reports.parse_field_spec("FOCTEMP") == ("FOCTEMP", "fits")


def test_header_values_query_joins_only_what_is_asked_for(sample):
    _, data = sample
    # Without the fits source the FitsHeader join is absent, so the memoised header is
    # never populated -- the point being that an unused join is not paid for.
    sql, _ = reports.header_values_query(SearchCriteria(), {"photonfinder"}).sql()
    assert "fitsheader" not in sql.lower()
    assert "filewcs" not in sql.lower()
    sql, _ = reports.header_values_query(SearchCriteria(), {"fits"}).sql()
    assert "fitsheader" in sql.lower()
