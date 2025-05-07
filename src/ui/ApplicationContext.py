import logging
from pathlib import Path

from peewee import Database, SqliteDatabase

from src.models.library_root import LibraryRoot


class ApplicationContext:
    def __init__(self, app_data_path: str) -> None:
        self.database: Database | None = None
        self.app_data_path: str = app_data_path

    def __enter__(self):
        self.open_database()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close_database()

    def open_database(self) -> None:
        database_path = Path(self.app_data_path) / "astroFileManager.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)

        self.database = SqliteDatabase(database_path, pragmas={
            'journal_mode': 'wal',
            'cache_size': -1 * 64000,  # 64MB
            'foreign_keys': 1,
            'application_id': 0x46495453,  # FITS
            'user_version': 1
        })

        if self.database:
            # Initialize the LibraryRoot model with the connection
            LibraryRoot.initialize(self.database)
            logging.info("LibraryRoot model initialized")

    def close_database(self) -> None:
        if self.database:
            self.database.close()
            logging.info("Database closed")
            self.database = None
