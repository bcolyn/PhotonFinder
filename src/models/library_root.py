from pathlib import Path

from peewee import *


class LibraryRoot(Model):
    """
    Model representing a library root directory.
    A library root is a directory that contains files to be managed by the application.
    """
    name = CharField(unique=True)
    path = CharField(unique=True)

    class Meta:
        # This will be set dynamically when the database connection is provided
        database = None

    @classmethod
    def initialize(cls, database: Database):
        """
        Initialize the model with a database connection.
        """
        cls._meta.database = database

        # Set the database for the model
        cls.bind(database, bind_refs=False, bind_backrefs=False)

        # Create the table if it doesn't exist
        if not cls.table_exists():
            cls.create_table()

    @staticmethod
    def is_valid_path(path_str: str) -> bool:
        """
        Check if the given path is a valid directory.
        
        Args:
            path_str: Path string to validate
            
        Returns:
            bool: True if the path is a valid directory, False otherwise
        """
        path = Path(path_str)
        return path.exists() and path.is_dir()
