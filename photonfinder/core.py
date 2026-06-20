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
from peewee import SqliteDatabase


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

    @db.func("hp_cone_search", 3)
    def db_hp_cone_search(ra, dec, radius_deg):
        import json
        from astropy_healpix import HEALPix
        _hp = HEALPix(nside=256, order='nested', frame='icrs')
        pixels = _hp.cone_search_lonlat(ra * u.deg, dec * u.deg, radius_deg * u.deg)
        return json.dumps(pixels.tolist())


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

    def get_last_export_path(self):
        """Get the last export path."""
        return self.settings.value("last_export_path", "", str)

    def set_last_export_path(self, value):
        """Set the last export path."""
        self.settings.setValue("last_export_path", value)

    def get_last_export_decompress(self):
        """Get the last export decompress option."""
        return self.settings.value("last_export_decompress", True, bool)

    def set_last_export_decompress(self, value):
        """Set the last export decompress option."""
        self.settings.setValue("last_export_decompress", value)

    def get_last_export_patterns(self):
        """Get the last export patterns."""
        return self.settings.value("last_export_patterns", [], list)

    def set_last_export_patterns(self, value):
        """Set the last export patterns."""
        self.settings.setValue("last_export_patterns", value)

    def get_last_light_path(self):
        return self.settings.value("last_light_path", "", str)

    def set_last_light_path(self, value):
        self.settings.setValue("last_light_path", value)

    def get_last_database_path(self):
        return self.settings.value("last_database_path", "", str)

    def set_last_database_path(self, value):
        self.settings.setValue("last_database_path", value)

    def get_astap_path(self):
        """Get the path to the ASTAP executable."""
        return self.settings.value("astap_path", "", str)

    def set_astap_path(self, value):
        """Set the path to the ASTAP executable."""
        self.settings.setValue("astap_path", value)

    def get_astrometry_net_api_key(self):
        """Get the API key for astrometry.net."""
        return self.settings.value("astrometry_net_api_key", "", str)

    def set_astrometry_net_api_key(self, value):
        """Set the API key for astrometry.net."""
        self.settings.setValue("astrometry_net_api_key", value)

    def get_astrometry_net_force_image_upload(self):
        return self.settings.value('astrometry_net_force_image_upload', False, bool)

    def set_astrometry_net_force_image_upload(self, value):
        return self.settings.setValue('astrometry_net_force_image_upload', value)

    def get_solve_field_path(self) -> str:
        return self.settings.value('solve_field_path', '', str)

    def set_solve_field_path(self, value: str):
        self.settings.setValue('solve_field_path', value)

    def get_solve_field_wsl_distro(self) -> str:
        return self.settings.value('solve_field_wsl_distro', '', str)

    def set_solve_field_wsl_distro(self, value: str):
        self.settings.setValue('solve_field_wsl_distro', value)

    def get_solve_field_timeout(self) -> int:
        return self.settings.value('wsl_solver_timeout', 300, int)

    def set_solve_field_timeout(self, value: int):
        self.settings.setValue('wsl_solver_timeout', value)

    def get_plate_solve_primary_solver(self) -> int:
        return self.settings.value('plate_solve_primary_solver', 0, int)

    def set_plate_solve_primary_solver(self, value: int):
        self.settings.setValue('plate_solve_primary_solver', value)

    def get_plate_solve_backup_solver(self) -> int:
        return self.settings.value('plate_solve_backup_solver', -1, int)

    def set_plate_solve_backup_solver(self, value: int):
        self.settings.setValue('plate_solve_backup_solver', value)

    def get_plate_solve_hint_ra(self) -> str:
        return self.settings.value('plate_solve_hint_ra', '', str)

    def set_plate_solve_hint_ra(self, value: str):
        self.settings.setValue('plate_solve_hint_ra', value)

    def get_plate_solve_hint_dec(self) -> str:
        return self.settings.value('plate_solve_hint_dec', '', str)

    def set_plate_solve_hint_dec(self, value: str):
        self.settings.setValue('plate_solve_hint_dec', value)

    def get_plate_solve_hint_scale(self) -> float:
        return self.settings.value('plate_solve_hint_scale', 0.0, float)

    def set_plate_solve_hint_scale(self, value: float):
        self.settings.setValue('plate_solve_hint_scale', value)

    def get_plate_solve_hint_mode(self) -> str:
        return self.settings.value('plate_solve_hint_mode', 'fallback', str)

    def set_plate_solve_hint_mode(self, value: str):
        self.settings.setValue('plate_solve_hint_mode', value)

    def get_mcp_enabled(self) -> bool:
        return self.settings.value('mcp_enabled', False, bool)

    def set_mcp_enabled(self, value: bool):
        self.settings.setValue('mcp_enabled', value)

    def get_mcp_port(self) -> int:
        return self.settings.value('mcp_port', 8765, int)

    def set_mcp_port(self, value: int):
        self.settings.setValue('mcp_port', value)

    def get_last_export_xisf_as_fits(self):
        return self.settings.value("last_export_xisf_as_fits", False, bool)

    def get_last_export_override_platesolve(self):
        return self.settings.value("last_export_override_platesolve", False, bool)

    def get_last_export_custom_headers(self):
        return self.settings.value("last_export_custom_headers", "", str)

    def set_last_export_xisf_as_fits(self, value: bool):
        self.settings.setValue("last_export_xisf_as_fits", value)

    def set_last_export_override_platesolve(self, value: bool):
        self.settings.setValue("last_export_override_platesolve", value)

    def get_last_export_use_master(self):
        return self.settings.value("last_export_use_master", False, bool)

    def set_last_export_use_master(self, value: bool):
        self.settings.setValue("last_export_use_master", value)

    def get_last_export_shared_session(self) -> bool:
        return self.settings.value("last_export_shared_session", False, bool)

    def set_last_export_shared_session(self, value: bool):
        self.settings.setValue("last_export_shared_session", value)

    def set_last_export_custom_headers(self, value: str):
        self.settings.setValue("last_export_custom_headers", value)

    def get_last_catalog(self):
        return self.settings.value("last_catalog", "", str)

    def set_last_catalog(self, value: str):
        self.settings.setValue("last_catalog", value)

    def get_bad_file_patterns(self):
        return self.settings.value("bad_file_patterns", "bad*", str)

    def get_bad_dir_patterns(self):
        return self.settings.value("bad_dir_patterns", "bad*", str)

    def set_bad_file_patterns(self, value) -> str:
        self.settings.setValue("bad_file_patterns", value)

    def set_bad_dir_patterns(self, value) -> str:
        self.settings.setValue("bad_dir_patterns", value)

    def get_known_fits_keywords(self):
        return sorted(self.known_fits_keywords)

    def add_known_fits_keywords(self, keywords: list[str]):
        self.known_fits_keywords.update(keywords)

    def get_project_hidden_cols(self):
        return self.settings.value("project_hidden_cols", "", str)

    def set_project_hidden_cols(self, value) -> str:
        self.settings.setValue("project_hidden_cols", value)

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

    def get_use_internal_viewer(self) -> bool:
        return self.settings.value("use_internal_viewer", True, bool)

    def set_use_internal_viewer(self, value: bool):
        self.settings.setValue("use_internal_viewer", value)

    def get_compress_parallelism(self) -> int:
        return self.settings.value('compress_parallelism', 2, int)

    def set_compress_parallelism(self, value: int):
        self.settings.setValue('compress_parallelism', value)

    def get_compress_level(self) -> int:
        return self.settings.value('compress_level', 9, int)

    def set_compress_level(self, value: int):
        self.settings.setValue('compress_level', value)

    def get_obs_timezone(self) -> str:
        """Get the configured observatory timezone name (e.g. 'Europe/Brussels'). Empty = system default."""
        return self.settings.value('obs_timezone', '', str)

    def set_obs_timezone(self, value: str):
        self.settings.setValue('obs_timezone', value)

    def get_annotation_mag_limit(self) -> float:
        return self.settings.value('annotation_mag_limit', 19.0, float)

    def set_annotation_mag_limit(self, value: float):
        self.settings.setValue('annotation_mag_limit', value)

    def get_annotation_collapsed_catalogs(self) -> set[str]:
        raw = self.settings.value('annotation_collapsed_catalogs', '', str)
        return set(raw.split(',')) - {''} if raw else set()

    def set_annotation_collapsed_catalogs(self, catalogs: set[str]):
        self.settings.setValue('annotation_collapsed_catalogs', ','.join(sorted(catalogs)))

    def sync(self):
        """Ensure settings are saved to disk."""
        self.settings.setValue("known_fits_keywords", "|".join(self.known_fits_keywords))
        self.settings.sync()


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