import logging
from abc import abstractmethod
from pathlib import Path

import zstd
from PySide6.QtCore import QSettings
from astropy.io.fits import Header
from peewee import Database, SqliteDatabase


def get_default_astap_path():
    if Path("C:/Program Files/astap/astap.exe").exists():
        return "C:/Program Files/astap/astap.exe"
    else:  # else, assume it's on the PATH
        return "astap"


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
        return Header.fromstring(decompress(value)).get(header_key, None)


class ApplicationContext:

    @staticmethod
    def create_in_app_data(app_data_path: str, settings) -> 'ApplicationContext':
        if settings.get_last_database_path():
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
            'user_version': 1
        })

        register_udfs(self.database)

        logging.info("Database opened")
        self.settings.set_last_database_path(str(self.database_path))
        if self.database:
            from .models import CORE_MODELS
            self.database.bind(CORE_MODELS, bind_refs=False, bind_backrefs=False)
            for model in CORE_MODELS:
                model.create_table()

    def set_status_reporter(self, status_reporter: StatusReporter) -> None:
        self.status_reporter = status_reporter

    def close_database(self) -> None:
        if self.database:
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

    def get_astap_fallback_fov(self):
        return self.settings.value("astap_fov", 2.0, float)

    def set_astap_fallback_fov(self, value):
        self.settings.setValue("astap_fov", value)


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

    def set_last_export_custom_headers(self, value: str):
        self.settings.setValue("last_export_custom_headers", value)

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
