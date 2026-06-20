import logging
import os
import sys
from abc import abstractmethod
from enum import Enum
from pathlib import Path

import astropy.units as u
import zstd
from PySide6.QtCore import QSettings, QObject, Signal
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy_healpix import HEALPix
from peewee import SqliteDatabase

# Sky tessellation used for spatial indexing (cone searches). This is the single
# source of truth for the HEALPix parameters used throughout the application.
HEALPIX_NSIDE = 256
hp = HEALPix(nside=HEALPIX_NSIDE, order='nested', frame='icrs')


def get_default_astap_path():
    if Path("C:/Program Files/astap/astap.exe").exists():
        return "C:/Program Files/astap/astap.exe"
    else:  # else, assume it's on the PATH
        return "astap"

def fatal_error(title: str, message: str, details: str = "") -> None:
    """
    Display a fatal error dialog and exit the application.

    Args:
        title: The window title for the error dialog
        message: The main error message
        details: Optional additional details or information
    """
    from PySide6.QtWidgets import QMessageBox
    import sys

    logging.error(f"{title}: {message} {details}")

    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Critical)
    msg_box.setWindowTitle(title)
    msg_box.setText(message)
    if details:
        msg_box.setInformativeText(details)
    msg_box.setStandardButtons(QMessageBox.Ok)
    msg_box.exec()

    sys.exit(1)

class StatusReporter:
    @abstractmethod
    def update_status(self, message: str, bulk=False) -> None:
        logging.info(message)


def register_udfs(db: SqliteDatabase):
    @db.func("decompress", 1)
    def db_decompress(value):
        return decompress(value)

    @db.func("decompress_header_value", 2)
    def db_decompress_header_value(value, header_key: str):
        import json
        raw = decompress(value)
        if raw.startswith(b'{'):
            entries = json.loads(raw).get(header_key)
            if not entries:
                return None
            val = entries[0].get('value')
            try:
                return float(val)
            except (TypeError, ValueError):
                return val
        return Header.fromstring(raw).get(header_key, None)

    @db.func("sky_distance", 4)
    def db_sky_distance(ra1, dec1, ra2, dec2):
        coord1 = SkyCoord(ra1, dec1, unit=u.deg, frame='icrs')
        coord2 = SkyCoord(ra2, dec2, unit=u.deg, frame='icrs')
        retval = coord1.separation(coord2).value
        return retval


class ApplicationContext:

    CURRENT_DB_VERSION = 1

    @staticmethod
    def create_in_app_data(app_data_path: str, settings) -> 'ApplicationContext':
        if settings.get_last_database_path() and os.path.exists(settings.get_last_database_path()):
            database_path = settings.get_last_database_path()
        else:
            database_path = Path(app_data_path) / "astroFileManager.db"
            database_path.parent.mkdir(parents=True, exist_ok=True)
        session_file = Path(app_data_path) / "session_v1.json"
        return ApplicationContext(database_path, settings, str(session_file))

    def __init__(self, database_path: str | Path, settings, session_file: str | None = None) -> None:
        self.database_path = database_path
        self.database: SqliteDatabase | None = None
        self.settings = settings
        self.status_reporter: StatusReporter | None = None
        self.session_file = session_file
        self.signal_bus = SignalBus()

    def __enter__(self):
        self.open_database()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close_database()
        # Ensure settings are saved
        self.settings.sync()
        logging.info("Settings synced")

    def open_database(self) -> None:
        logging.info(f"Database path: {self.database_path}")
        self.database = SqliteDatabase(self.database_path, pragmas={
            'journal_mode': 'wal',
            'cache_size': -1 * 64000,  # 64MB
            'foreign_keys': 1,
            'application_id': 0x46495453,  # FITS
        })

        register_udfs(self.database)

        logging.info("Database opened")
        self._check_database_version()

        self.settings.set_last_database_path(str(self.database_path))
        if self.database:
            from .models import CATALOG_MODELS, CORE_MODELS
            self.database.bind(CORE_MODELS, bind_refs=False, bind_backrefs=False)
            for model in CORE_MODELS:
                model.create_table()
            self._migrate_image_dimensions()
            self.database.attach(self._catalog_path(), 'catalog')
            self.database.bind(CATALOG_MODELS, bind_refs=False, bind_backrefs=False)

    def _migrate_image_dimensions(self) -> None:
        for col in ('width', 'height'):
            try:
                self.database.execute_sql(f'ALTER TABLE image ADD COLUMN {col} INTEGER')
            except Exception:
                pass  # column already exists
        try:
            self.database.execute_sql(
                'ALTER TABLE image ADD COLUMN coord_scale REAL'
                ' GENERATED ALWAYS AS ('
                'ROUND((coord_radius * 2.0 * 3600.0) /'
                ' SQRT(CAST(width AS REAL) * width + CAST(height AS REAL) * height), 2)'
                ') VIRTUAL'
            )
        except Exception:
            pass  # column already exists
        try:
            self.database.execute_sql(
                'CREATE INDEX IF NOT EXISTS idx_image_camera_scale'
                ' ON image(camera, coord_scale)'
                ' WHERE camera IS NOT NULL AND coord_scale IS NOT NULL'
            )
        except Exception:
            pass

    def set_status_reporter(self, status_reporter: StatusReporter) -> None:
        self.status_reporter = status_reporter

    def _catalog_path(self) -> str:
        if getattr(sys, 'frozen', False):
            base = Path(sys._MEIPASS)
        else:
            base = Path(__file__).parent.parent / 'data'
        return str(base / 'catalog.db')

    def close_database(self) -> None:
        if self.database:
            try:
                self.database.detach('catalog')
            except Exception:
                pass
            self.database.close()
            logging.info(f"Database closed: {self.database_path}")
            self.database = None

    def switch_database(self, database_path: str | Path) -> None:
        self.close_database()
        self.database_path = database_path
        self.open_database()

    def get_known_fits_keywords(self) -> list[str]:
        return self.settings.get_known_fits_keywords()

    def get_session_file(self) -> str | None:
        return self.session_file

    def _check_database_version(self):
        if not self.database:
            return

        cursor = self.database.execute_sql('PRAGMA user_version')
        current_version = cursor.fetchone()[0]

        if current_version < self.CURRENT_DB_VERSION:
            logging.info(
                f"Database version {current_version} is older than {self.CURRENT_DB_VERSION}. Running migration...")
            self._run_database_migration(current_version, self.CURRENT_DB_VERSION)
            # Update the version after successful migration
            self.database.execute_sql(f'PRAGMA user_version = {self.CURRENT_DB_VERSION}')
            logging.info(f"Database migrated to version {self.CURRENT_DB_VERSION}")
        elif current_version > self.CURRENT_DB_VERSION:
            fatal_error(
                "Database Version Error",
                "Database version mismatch",
                f"The database file was created with a newer version of this application.\n\n"
                f"Database version: {current_version}\n"
                f"Application supports: {self.CURRENT_DB_VERSION}\n\n"
                f"Please update the application to the latest version."
            )
        else:
            logging.info(f"Database version is up to date: {current_version}")

    def _run_database_migration(self, from_version: int, to_version: int):
        """
        Run database migration from one version to another.
        This method should be implemented to handle specific migration steps.
        """
        logging.info(f"Running migration from version {from_version} to {to_version}")
        # if from_version < 1:
        #     self._migrate_to_version_1()
        # if from_version < 2:
        #     self._migrate_to_version_2()
        # etc.
        pass


class Settings:
    # Declarative spec for plain value-backed settings: (method_suffix, qsettings_key,
    # default, type). A get_<suffix>()/set_<suffix>() pair is generated for each entry by
    # _install_setting_accessors() after the class body. Settings that need custom logic
    # (sets, JSON, the in-memory keyword cache) are written out as methods below instead.
    _SPECS = [
        ("last_export_path", "last_export_path", "", str),
        ("last_export_decompress", "last_export_decompress", True, bool),
        ("last_export_patterns", "last_export_patterns", [], list),
        ("last_light_path", "last_light_path", "", str),
        ("last_database_path", "last_database_path", "", str),
        ("astap_path", "astap_path", "", str),
        ("astrometry_net_api_key", "astrometry_net_api_key", "", str),
        ("astrometry_net_force_image_upload", "astrometry_net_force_image_upload", False, bool),
        ("solve_field_path", "solve_field_path", "", str),
        ("solve_field_wsl_distro", "solve_field_wsl_distro", "", str),
        ("solve_field_timeout", "wsl_solver_timeout", 300, int),
        ("plate_solve_primary_solver", "plate_solve_primary_solver", 0, int),
        ("plate_solve_backup_solver", "plate_solve_backup_solver", -1, int),
        ("plate_solve_hint_ra", "plate_solve_hint_ra", "", str),
        ("plate_solve_hint_dec", "plate_solve_hint_dec", "", str),
        ("plate_solve_hint_scale", "plate_solve_hint_scale", 0.0, float),
        ("plate_solve_hint_mode", "plate_solve_hint_mode", "fallback", str),
        ("mcp_enabled", "mcp_enabled", False, bool),
        ("mcp_port", "mcp_port", 8765, int),
        ("last_export_xisf_as_fits", "last_export_xisf_as_fits", False, bool),
        ("last_export_override_platesolve", "last_export_override_platesolve", False, bool),
        ("last_export_custom_headers", "last_export_custom_headers", "", str),
        ("last_export_use_master", "last_export_use_master", False, bool),
        ("last_export_shared_session", "last_export_shared_session", False, bool),
        ("last_catalog", "last_catalog", "", str),
        ("bad_file_patterns", "bad_file_patterns", "bad*", str),
        ("bad_dir_patterns", "bad_dir_patterns", "bad*", str),
        ("project_hidden_cols", "project_hidden_cols", "", str),
        ("use_internal_viewer", "use_internal_viewer", True, bool),
        ("compress_parallelism", "compress_parallelism", 2, int),
        ("compress_level", "compress_level", 9, int),
        ("obs_timezone", "obs_timezone", "", str),
        ("annotation_mag_limit", "annotation_mag_limit", 19.0, float),
    ]

    def __init__(self, organization_name="AstroFileManager", application_name="AstroFileManager"):
        self.settings = QSettings(organization_name, application_name)
        self._initialize_defaults()
        self.known_fits_keywords = set()
        stored_keywords = str(self.settings.value("known_fits_keywords", "", str))
        self.add_known_fits_keywords(stored_keywords.split("|") if stored_keywords else [])

    def _initialize_defaults(self):
        """Initialize default settings if they don't exist."""
        if not self.contains("astap_path"):
            self.set_astap_path(get_default_astap_path())
        if not self.contains("astrometry_net_api_key"):
            self.set_astrometry_net_api_key("")

    def contains(self, key):
        """Check if a setting exists."""
        return self.settings.contains(key)

    # --- Settings needing custom (non value-backed) logic ---------------------

    def get_known_fits_keywords(self):
        return sorted(self.known_fits_keywords)

    def add_known_fits_keywords(self, keywords: list[str]):
        self.known_fits_keywords.update(keywords)

    def get_column_presets(self) -> dict:
        import json
        raw = self.settings.value("column_presets", "{}", str)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def set_column_presets(self, presets: dict):
        import json
        self.settings.setValue("column_presets", json.dumps(presets))

    def get_annotation_collapsed_catalogs(self) -> set[str]:
        raw = self.settings.value('annotation_collapsed_catalogs', '', str)
        return set(raw.split(',')) - {''} if raw else set()

    def set_annotation_collapsed_catalogs(self, catalogs: set[str]):
        self.settings.setValue('annotation_collapsed_catalogs', ','.join(sorted(catalogs)))

    def sync(self):
        """Ensure settings are saved to disk."""
        self.settings.setValue("known_fits_keywords", "|".join(self.known_fits_keywords))
        self.settings.sync()


def _install_setting_accessors(cls) -> None:
    """Generate get_<suffix>/set_<suffix> methods on *cls* from its ``_SPECS`` table.

    Custom methods already defined on the class take precedence and are never overwritten.
    """
    def _make_getter(key, default, typ):
        def getter(self):
            return self.settings.value(key, default, typ)
        return getter

    def _make_setter(key):
        def setter(self, value):
            self.settings.setValue(key, value)
        return setter

    for suffix, key, default, typ in cls._SPECS:
        if f"get_{suffix}" not in cls.__dict__:
            setattr(cls, f"get_{suffix}", _make_getter(key, default, typ))
        if f"set_{suffix}" not in cls.__dict__:
            setattr(cls, f"set_{suffix}", _make_setter(key))


_install_setting_accessors(Settings)


def backup_database(db: SqliteDatabase, backup_path: str | Path):
    import sqlite3
    try:
        with sqlite3.connect(backup_path) as target:
            db.connection().backup(target)
    finally:
        target.close()
    logging.info(f"Database backup created at {backup_path}")


def compress(value: bytes) -> bytes:
    return zstd.compress(value)


def decompress(value: bytes) -> bytes:
    return zstd.decompress(value)


class Change(Enum):
    CREATE_OR_UPDATE = 0
    DELETE = 1


class SignalBus(QObject):

    projects_changed = Signal(object, object)
    """Collection[Project], Change"""

    project_links_changed = Signal(object, object)
    """Collection[ProjectFile], Change"""