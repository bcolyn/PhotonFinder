# PhotonFinder — Tests

Tests are written with pytest and organised by component.

## Test structure

| File / folder | What it covers |
|---|---|
| `conftest.py` | Shared fixtures and configuration |
| `test_core.py` | ApplicationContext, settings, SQLite UDFs |
| `test_models.py` | Peewee ORM models and search criteria |
| `test_filesystem.py` | File scanning, FITS/XISF header reading, Importer |
| `test_fits_handlers.py` | FITS header normalisation per capture software |
| `test_export_dialog.py` | Export worker logic |
| `test_platesolver.py` | Plate-solver helpers |
| `test_session.py` | Session state persistence |
| `ui/` | Qt GUI tests (pytest-qt) |

## Prerequisites

```bash
uv sync --extra dev
```

## Running tests

```bash
# Default run — slow and internet-dependent tests excluded
uv run pytest tests/

# Include slow tests
uv run pytest tests/ -m ""

# Single file
uv run pytest tests/test_models.py

# With coverage
uv run pytest --cov=photonfinder tests/
```

## Test markers

| Marker | Meaning | Included by default |
|---|---|---|
| *(none)* | Fast unit tests | yes |
| `slow` | Plate-solving and other long-running tests | no |
| `internet` | Tests that call external services | no |

## Sample data files

Several tests require large astronomical image files (FITS, XISF) that are not
checked into the repository. They are stored separately on Hugging Face:
`bcolyn/PhotonFinder-test-data`.

When `tests/data/` is empty those tests are **automatically skipped** and a
warning is printed at the end of the run pointing to the download location.
No flags or markers are needed.

## Fixtures

| Fixture | Scope | Description |
|---|---|---|
| `app_context` | class | In-memory SQLite database with full schema |
| `database` | function | Bare in-memory database (no app context) |
| `filesystem` | function | In-memory PyFilesystem2 tree with dummy FITS files |
| `global_test_data_dir` | session | Path to `tests/data/`; skips if data absent |
| `settings` | class | `DynamicSettings` mock (no persistent storage) |

## Writing new tests

- Use `app_context` for tests that need the full ORM.
- Use `filesystem` for importer/scanner tests — avoids real disk I/O.
- Use `global_test_data_dir` only when a real image file is required; the test
  will be skipped automatically on machines without the data.
- Use `qtbot` (from pytest-qt) for UI interaction tests.
