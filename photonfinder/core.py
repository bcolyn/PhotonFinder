import logging
import os
import sys
import threading
from abc import abstractmethod
from collections import Counter
from contextlib import contextmanager, nullcontext
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


MCP_PORT_FILENAME = "mcp_port.txt"


def user_app_data_dir() -> Path:
    """PhotonFinder's app-data folder, resolved without consulting environment variables.

    A process spawned by a sandboxed client -- an MSIX-packaged MCP client, say -- sees a
    redirected ``%LOCALAPPDATA%`` and a virtualized ``HKCU``, so neither the env var nor
    QSettings reliably points at what the application wrote. The user profile is not
    redirected, so deriving the path from it gives every process the same answer.
    """
    if sys.platform == 'win32':
        return Path.home() / "AppData" / "Local" / "photonfinder"
    return Path.home() / ".local" / "share" / "photonfinder"


def mcp_port_file() -> Path:
    """Where the running application advertises the port its MCP server listens on.

    The one piece of state that has to cross the sandbox boundary, which is why it is a
    file under the user profile rather than a setting.
    """
    return user_app_data_dir() / MCP_PORT_FILENAME


def get_default_astap_path():
    if Path("C:/Program Files/astap/astap.exe").exists():
        return "C:/Program Files/astap/astap.exe"
    else:  # else, assume it's on the PATH
        return "astap"


def get_default_saspro_path():
    """Where the Seti Astro Suite Pro installer puts it, or "" when it isn't there."""
    path = Path("C:/Program Files/SetiAstroSuitePro/SetiAstroSuitePro.exe")
    return str(path.as_posix()) if path.exists() else ""


def fatal_error(title: str, message: str, details: str = "") -> None:
    """
    Display a fatal error dialog and exit the application.

    Without a running QApplication -- the MCP server process, tests -- there is nothing to
    parent a dialog to and no event loop to run it, so the error only goes to the log and
    stderr. Showing it anyway would hang a headless process on an invisible modal.

    Args:
        title: The window title for the error dialog
        message: The main error message
        details: Optional additional details or information
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    logging.error(f"{title}: {message} {details}")

    if QApplication.instance() is None:
        print(f"{title}: {message}\n{details}", file=sys.stderr)
        sys.exit(1)

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
    # These run once per row inside queries: an exception would abort the whole query, so
    # a missing header (LEFT OUTER JOIN) or an unparsable one must yield NULL instead.

    @db.func("decompress", 1)
    def db_decompress(value):
        if value is None:
            return None
        return decompress(value)

    @db.func("decompress_header_value", 2)
    def db_decompress_header_value(value, header_key: str):
        import json
        if value is None:
            return None
        try:
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
            try:
                return Header.fromstring(raw).get(header_key, None)
            except Exception:
                # Slow path for tabs/bad cards; imported here because filesystem imports core.
                from photonfinder.filesystem import parse_FITS_header
                return parse_FITS_header(raw).get(header_key, None)
        except Exception as e:
            logging.debug(f"decompress_header_value({header_key!r}) failed: {e}")
            return None

    @db.func("sky_distance", 4)
    def db_sky_distance(ra1, dec1, ra2, dec2):
        coord1 = SkyCoord(ra1, dec1, unit=u.deg, frame='icrs')
        coord2 = SkyCoord(ra2, dec2, unit=u.deg, frame='icrs')
        retval = coord1.separation(coord2).value
        return retval


OTHER_WINDOW = "Another PhotonFinder window"


def library_busy_message(holder: str) -> str:
    """Why a library writer can't start: *holder* is what DatabaseLock.busy_with returned."""
    return f"{holder} is writing to this library. Try again when it has finished."


class DatabaseLock:
    """Non-reentrant mutex guarding one kind of batch job on a library, held across processes.

    Within one process callers (e.g. the GUI and the MCP server) share a thread lock. The file
    lock covers what that cannot: a second copy of PhotonFinder opened on the same
    library, whether started by the user or by the MCP stub when it could not tell the
    first one was alive. The *suffix* names the lock file, so each kind of job can have its
    own. This combines:

    * ``threading.Lock`` -- serialises threads *within* one process. Needed on its own
      because repeated OS lock calls on the same file descriptor from a single process do
      not behave as a mutex.
    * OS advisory file lock on ``<database_path><suffix>`` -- serialises *between*
      processes. The kernel drops it when the holding process exits, so a crash cannot
      leave a stale lock behind.

    Scoping the file to the database means contention is per-library, which is what
    matters: two processes on different libraries never touch the same files.

    Exposes the same ``acquire(blocking=False)``/``release()`` surface as
    ``threading.Lock`` so callers do not care which mechanism is in play. For an
    in-memory database there is no file to lock and the thread lock alone is used.

    The holder named on acquire is kept while this process holds the lock, so a refused
    start can say what is running. Listeners are called on every acquire and release, from
    whichever thread did it, so the UI can enable or disable what starts a writer.
    """

    def __init__(self, database_path: str | Path, suffix: str) -> None:
        self._suffix = suffix
        self._thread_lock = threading.Lock()
        self._path: str | None = None
        self._fd: int | None = None
        self._holder: str | None = None
        self._listeners: list = []
        self.set_database_path(database_path)

    def set_database_path(self, database_path: str | Path) -> None:
        """Re-point the lock file after the context switches databases."""
        path = str(database_path)
        self._path = None if path == ':memory:' else path + self._suffix

    def add_listener(self, listener) -> None:
        """Call *listener()* after each acquire and release."""
        self._listeners.append(listener)

    @property
    def holder(self) -> str | None:
        """What holds the lock in this process, or None if this process doesn't hold it."""
        return self._holder

    def acquire(self, blocking: bool = False, holder: str = "A library job") -> bool:
        if blocking:
            raise ValueError("DatabaseLock only supports non-blocking acquisition")
        if not self._try_lock():
            return False
        self._holder = holder
        self._notify()
        return True

    def release(self) -> None:
        self._holder = None
        self._unlock()
        self._notify()

    def busy_with(self) -> str | None:
        """What holds the lock: its holder if it's in this process, OTHER_WINDOW if another
        process holds it, or None if it's free.

        Telling whether another process holds it means taking the lock for a moment, so a
        start elsewhere at that instant is refused. Only call this when the answer is about
        to be shown, never on a timer.
        """
        holder = self._holder
        if holder is not None:
            return holder
        if self._thread_lock.locked():  # another thread here, between taking it and naming itself
            return self._holder or "A library job"
        if not self._try_lock():
            if self._thread_lock.locked():  # taken here meanwhile
                return self._holder or "A library job"
            return OTHER_WINDOW
        self._unlock()
        return None

    def _try_lock(self) -> bool:
        if not self._thread_lock.acquire(blocking=False):
            return False
        if self._path is None:
            return True
        try:
            if not self._acquire_file_lock():
                self._thread_lock.release()
                return False
        except Exception:
            # A lock file we cannot create or lock (read-only media, exotic filesystem)
            # must not make the job unavailable; fall back to in-process guarding.
            logging.warning("Could not take the cross-process lock at %s; "
                            "falling back to in-process locking only", self._path,
                            exc_info=True)
        return True

    def _unlock(self) -> None:
        self._release_file_lock()
        self._thread_lock.release()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                logging.error("A library lock listener failed", exc_info=True)

    def _acquire_file_lock(self) -> bool:
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == 'win32':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # pylint: disable=import-error  # POSIX only; this branch never runs on Windows
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False  # held by another process
        self._fd = fd
        return True

    def _release_file_lock(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if sys.platform == 'win32':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # pylint: disable=import-error  # POSIX only; this branch never runs on Windows
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            logging.warning("Failed to release the lock file %s", self._path, exc_info=True)
        finally:
            os.close(fd)


class LibraryBusyError(Exception):
    """The library can't be switched while it's in use, or a writer can't start during a switch."""


class ActiveWriters:
    """The jobs in this process that are writing to the open library.

    Separate from library_write_lock, which is also held across processes: a writer in
    another PhotonFinder window has no effect on this window switching library. Readers
    don't register. A job registers when it's started, not when its thread gets to run.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = Counter()
        self._switching = False

    def begin(self, name: str) -> None:
        with self._lock:
            if self._switching:
                raise LibraryBusyError("The library is being switched.")
            self._running[name] += 1

    def end(self, name: str) -> None:
        with self._lock:
            self._running[name] -= 1
            if self._running[name] <= 0:
                del self._running[name]

    def running(self) -> list[str]:
        with self._lock:
            return sorted(self._running)

    @contextmanager
    def switching(self):
        """Keeps new writers out during a library switch, which is refused if any is running."""
        with self._lock:
            if self._running:
                raise LibraryBusyError(f"Still running: {', '.join(sorted(self._running))}.")
            self._switching = True
        try:
            yield
        finally:
            with self._lock:
                self._switching = False


class LibraryDatabase(SqliteDatabase):  # pylint: disable=abstract-method  # SQLite has no sequences
    """A library database. ApplicationContext.open_database binds the models to it, and only
    that binding counts: models are bound for the whole process, not per thread.

    So bind_ctx changes nothing. Peewee's would rebind the models to whatever they were bound
    to on entry when it exits, which for a job spanning a library switch is the old library.
    Later writes would silently go there while searches, which bind_ctx again, show the new
    one. Writers can't span a switch (see ActiveWriters); a reader that does simply carries on
    in the new library, and nothing is left to receive its results.
    """

    def bind_ctx(self, models, bind_refs=True, bind_backrefs=True):
        return nullcontext()

    # Built-in and custom catalogs as one table. TEMP because a view stored in main may not
    # refer to an attached schema, which makes it per connection: it is created wherever the
    # catalog is attached. Custom rowids are negated so both id spaces fit one key. SQLite
    # pushes the catalog/healpix filters into each arm, so both keep their indexes.
    _CATALOG_COLUMNS = "ra, dec, catalog, catalog_id, canonical_id, size, axis_ratio, angle, magnitude, healpix"
    _BUILTIN_CATALOG_SQL = f"SELECT rowid AS rowid, {_CATALOG_COLUMNS} FROM catalog.catalog_entry"
    _CUSTOM_CATALOG_SQL = f"SELECT -rowid AS rowid, {_CATALOG_COLUMNS} FROM main.custom_catalog_entry"

    @classmethod
    def _create_catalog_view(cls, conn) -> None:
        # A library always has the custom table (open_database creates it), but a connection
        # can see a main without it: :memory: gives every thread its own empty database. The
        # view then shows the built-in catalogs alone rather than failing every catalog read.
        has_custom = conn.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'custom_catalog_entry'"
        ).fetchone()
        body = cls._BUILTIN_CATALOG_SQL
        if has_custom:
            body += " UNION ALL " + cls._CUSTOM_CATALOG_SQL
        conn.execute("DROP VIEW IF EXISTS temp.catalog_entry_all")
        conn.execute("CREATE TEMP VIEW catalog_entry_all AS " + body)

    def _attach_databases(self, conn):
        super()._attach_databases(conn)
        if 'catalog' in self._attached:
            self._create_catalog_view(conn)

    def attach(self, filename, name):
        attached = super().attach(filename, name)
        if attached and name == 'catalog' and not self.is_closed():
            self._create_catalog_view(self.connection())
        return attached

    def detach(self, name):
        if name == 'catalog' and not self.is_closed():
            self.execute_sql("DROP VIEW IF EXISTS temp.catalog_entry_all")
        return super().detach(name)


class ApplicationContext:

    # 2: an image is (file, hdu) - Image, FitsHeader and ImageStats have an hdu column
    # 3: Image has local_time and local_time_source, filled in the background (local_time)
    CURRENT_DB_VERSION = 3

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
        # Single writer for batch jobs that change library files and their rows: library
        # scans, Compress Files, plate solving, image analysis, saving a quality diagnosis,
        # rebuilding the index, and changing or deleting a library root. Held across
        # processes, so it also keeps two copies of the application apart. Readers never
        # take it.
        self.library_write_lock = DatabaseLock(database_path, '.write.lock')
        # Emitted from whichever thread took or released it; Qt queues it to the GUI thread.
        self.library_write_lock.add_listener(self.signal_bus.library_write_lock_changed.emit)
        self.writers = ActiveWriters()

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
        self.database = LibraryDatabase(self.database_path, pragmas={
            'journal_mode': 'wal',
            'cache_size': -1 * 64000,  # 64MB
            'foreign_keys': 1,
            'application_id': 0x46495453,  # FITS
        })

        register_udfs(self.database)

        logging.info("Database opened")
        if self.database:
            from .models import CORE_MODELS
            # Bound before the version check: a migration rebuilds tables from the models.
            self.database.bind(CORE_MODELS, bind_refs=False, bind_backrefs=False)
        self._check_database_version()

        self.settings.set_last_database_path(str(self.database_path))
        if self.database:
            from .models import CATALOG_MODELS, CORE_MODELS
            for model in CORE_MODELS:
                model.create_table()
            self._migrate_image_dimensions()
            self._migrate_library_root_description()
            self._migrate_image_stats_diagnosis()
            from .models import SESSION_NIGHT_INDEX_SQL
            self.database.execute_sql(SESSION_NIGHT_INDEX_SQL)
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

    def _migrate_library_root_description(self) -> None:
        try:
            self.database.execute_sql('ALTER TABLE libraryroot ADD COLUMN description TEXT')
        except Exception:
            pass  # column already exists

    def _migrate_image_stats_diagnosis(self) -> None:
        try:
            self.database.execute_sql('ALTER TABLE imagestats ADD COLUMN diagnosis TEXT')
        except Exception:
            pass  # column already exists

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
        self.library_write_lock.set_database_path(database_path)
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
        if from_version < 2:
            self._migrate_to_per_image_rows()
        if from_version < 3:
            self._migrate_local_time()

    def _migrate_local_time(self) -> None:
        """Version 3: Image gets local_time and local_time_source. Only the columns are added,
        which is instant; a background task fills them (local_time.update_library), and the
        index on the night is created with the tables. A missing table (a new library) or
        columns already there are left alone."""
        columns = [row[1] for row in self.database.execute_sql('PRAGMA table_info("image")')]
        if not columns:
            return
        for name, kind in (('local_time', 'DATETIME'), ('local_time_source', 'VARCHAR(255)')):
            if name not in columns:
                self.database.execute_sql(f'ALTER TABLE image ADD COLUMN {name} {kind}')

    def _migrate_to_per_image_rows(self) -> None:
        """Version 2: a file holds one or more images, so Image, FitsHeader and ImageStats get
        an `hdu` column, and their unique key moves from `file` to (file, hdu). Existing rows
        are the file's first image, hdu 0.

        SQLite can't drop a UNIQUE in place, so each table is rebuilt, all in one transaction.
        A table that is missing (a new library) or already has `hdu` is left alone."""
        from .models import FitsHeader, Image, ImageStats
        with self.database.atomic():
            for model in (Image, FitsHeader, ImageStats):
                self._rebuild_with_hdu(model)

    def _rebuild_with_hdu(self, model) -> None:
        table = model._meta.table_name
        columns = [row[1] for row in self.database.execute_sql(f'PRAGMA table_info("{table}")')]
        if not columns or 'hdu' in columns:
            return
        old = f'{table}_before_hdu'
        logging.info(f"Rebuilding table {table} with an hdu column")
        self.database.execute_sql(f'ALTER TABLE "{table}" RENAME TO "{old}"')
        # The old indexes keep their names after the rename; drop them so the new table's
        # indexes (same names, from the model) can be created.
        indexes = [row[0] for row in self.database.execute_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (old,))]
        for index in indexes:
            self.database.execute_sql(f'DROP INDEX "{index}"')
        model.create_table(safe=False)
        new_columns = {row[1] for row in self.database.execute_sql(f'PRAGMA table_info("{table}")')}
        # PRAGMA table_info leaves out generated columns (image.coord_scale), which is right:
        # they can't be copied, and _migrate_image_dimensions adds coord_scale back.
        copied = ', '.join(f'"{c}"' for c in columns if c in new_columns)
        self.database.execute_sql(f'INSERT INTO "{table}" ({copied}) SELECT {copied} FROM "{old}"')
        self.database.execute_sql(f'DROP TABLE "{old}"')


class _SynchronizedQSettings:
    """A QSettings whose calls are serialised. QSettings is reentrant, not thread-safe, and the
    MCP server and background tasks read settings from worker threads."""

    def __init__(self, qsettings: QSettings):
        self._qsettings = qsettings
        self._lock = threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._qsettings, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)
        return call


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
        ("telescopius_api_key", "telescopius_api_key", "", str),
        ("solve_field_path", "solve_field_path", "", str),
        ("solve_field_wsl_distro", "solve_field_wsl_distro", "", str),
        ("solve_field_timeout", "wsl_solver_timeout", 300, int),
        ("saspro_path", "saspro_path", "", str),
        ("plate_solve_primary_solver", "plate_solve_primary_solver", 0, int),
        ("plate_solve_backup_solver", "plate_solve_backup_solver", -1, int),
        ("plate_solve_hint_ra", "plate_solve_hint_ra", "", str),
        ("plate_solve_hint_dec", "plate_solve_hint_dec", "", str),
        ("plate_solve_hint_scale", "plate_solve_hint_scale", 0.0, float),
        ("plate_solve_hint_mode", "plate_solve_hint_mode", "fallback", str),
        ("mcp_port", "mcp_port", 8765, int),
        ("mcp_allow_plate_solve", "mcp_allow_plate_solve", False, bool),
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
        ("quality_diagnosis_settings", "quality_diagnosis_settings", "", str),
    ]

    def __init__(self, organization_name="AstroFileManager", application_name="AstroFileManager",
                 path: str | Path | None = None):
        """Settings live in the registry (HKCU) unless *path* names an INI file to use instead,
        which keeps a sandboxed instance -- a test, a ``--data-dir`` run -- off the user's own."""
        if path is not None:
            qsettings = QSettings(str(path), QSettings.Format.IniFormat)
        else:
            qsettings = QSettings(organization_name, application_name)
        self.settings = _SynchronizedQSettings(qsettings)
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
        # Always set: stored local times depend on it, so it changes only when the user does.
        if not self.get_obs_timezone():  # pylint: disable=no-member  # generated accessor
            from .local_time import default_obs_timezone
            self.set_obs_timezone(default_obs_timezone())  # pylint: disable=no-member

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
            try:
                return self.settings.value(key, default, typ)
            except RuntimeError as e:
                # A pickled object, as older versions saved for a Path. PySide refuses to read those,
                # and removing it means this only happens once.
                logging.warning(f"Removing the unreadable setting {key}: {e}")
                self.settings.remove(key)
                return default
        return getter

    def _make_setter(key):
        def setter(self, value):
            # QSettings would pickle a Path, and PySide then can't read it back from the registry.
            self.settings.setValue(key, value.as_posix() if isinstance(value, Path) else value)
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

    library_write_lock_changed = Signal()
    """ApplicationContext.library_write_lock was taken or released in this process."""

    projects_changed = Signal(object, object)
    """Collection[Project], Change"""

    project_links_changed = Signal(object, object)
    """Collection[ProjectFile], Change"""