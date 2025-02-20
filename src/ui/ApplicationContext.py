import logging
import sqlite3
from pathlib import Path
from sqlite3 import Connection


class ApplicationContext:
    def __init__(self, app_data_path: str) -> None:
        self.connection: Connection | None = None
        self.app_data_path: str = app_data_path

    def __enter__(self):
        self.open_database()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close_database()

    def open_database(self) -> None:
        database_path = Path(self.app_data_path) / "astroFileManager.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(database_path)
        if self.connection:
            logging.info(f"Database opened: {database_path}")

    def close_database(self) -> None:
        if self.connection:
            self.connection.close()
            logging.info("Database closed")
            self.connection = None
