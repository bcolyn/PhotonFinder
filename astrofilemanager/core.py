import logging
from abc import abstractmethod
from pathlib import Path

from PySide6.QtCore import QSettings
from peewee import Database, SqliteDatabase


class StatusReporter:
    @abstractmethod
    def update_status(self, message: str, bulk=False) -> None:
        pass


class ApplicationContext:

    @classmethod
    def create_in_app_data(self, app_data_path: str) -> 'ApplicationContext':
        database_path = Path(app_data_path) / "astroFileManager.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        return ApplicationContext(database_path)

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path
        self.database: Database | None = None
        self.settings = Settings()
        self.status_reporter: StatusReporter | None = None

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
        logging.info("Database opened")

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
            logging.info("Database closed")
            self.database = None


class Settings:

    def __init__(self, organization_name="AstroFileManager", application_name="AstroFileManager"):
        self.settings = QSettings(organization_name, application_name)
        self._initialize_defaults()

    def _initialize_defaults(self):
        """Initialize default settings if they don't exist."""
        if not self.contains("cache_compressed_headers"):
            self.set_cache_compressed_headers(True)

    def contains(self, key):
        """Check if a setting exists."""
        return self.settings.contains(key)

    def get_cache_compressed_headers(self):
        """Get the 'cache compressed headers' setting."""
        return self.settings.value("cache_compressed_headers", True, bool)

    def set_cache_compressed_headers(self, value):
        """Set the 'cache compressed headers' setting."""
        self.settings.setValue("cache_compressed_headers", value)

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

    def sync(self):
        """Ensure settings are saved to disk."""
        self.settings.sync()
