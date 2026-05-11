from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from photonfinder.core import ApplicationContext
from photonfinder.ui.BackgroundLoader import BackgroundLoaderBase
from photonfinder.ui.generated.ObjectLookupDialog_ui import Ui_ObjectLookupDialog


class _OnlineLookupLoader(BackgroundLoaderBase):
    lookup_complete = Signal(object, str)  # (SkyCoord|None, error_message|None)

    def lookup(self, name):
        self.run_in_thread(self._task, name)

    def _task(self, name):
        try:
            from astropy.coordinates import SkyCoord
            self.lookup_complete.emit(SkyCoord.from_name(name), None)
        except Exception as e:
            self.lookup_complete.emit(None, str(e))


class ObjectLookupDialog(QDialog, Ui_ObjectLookupDialog):
    """Look up an object by local catalog ID or online name and return its RA/Dec."""

    def __init__(self, context: ApplicationContext, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.context = context
        self._result = None  # (ra_deg, dec_deg)

        self._loader = _OnlineLookupLoader(context)
        self._loader.lookup_complete.connect(self._on_online_complete)

        self._populate_catalogs()
        self._update_ok_button()

        self.local_lookup_button.clicked.connect(self._on_local_lookup)
        self.online_lookup_button.clicked.connect(self._on_online_lookup)
        self.catalog_id_edit.returnPressed.connect(self._on_local_lookup)
        self.name_edit.returnPressed.connect(self._on_online_lookup)

    @property
    def result_ra_dec(self):
        """Returns (ra_deg, dec_deg) or None if no result yet."""
        return self._result

    def _populate_catalogs(self):
        try:
            from photonfinder.models import CatalogEntry
            catalogs = list(
                CatalogEntry.select(CatalogEntry.catalog)
                .distinct()
                .order_by(CatalogEntry.catalog)
                .tuples()
            )
            for (name,) in catalogs:
                self.catalog_combo.addItem(name)
            if not catalogs:
                self.local_group.setEnabled(False)
                self.local_group.setToolTip("No catalog database found.")
        except Exception:
            self.local_group.setEnabled(False)
            self.local_group.setToolTip("Catalog database not available.")

    def _update_ok_button(self):
        from PySide6.QtWidgets import QDialogButtonBox
        ok = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setEnabled(self._result is not None)

    def _set_result(self, ra_deg, dec_deg, label):
        self._result = (ra_deg, dec_deg)
        self.result_label.setText(f"{label}  —  RA {ra_deg:.4f}°  Dec {dec_deg:+.4f}°")
        self._update_ok_button()

    def _on_local_lookup(self):
        catalog = self.catalog_combo.currentText()
        catalog_id = self.catalog_id_edit.text().strip()
        if not catalog or not catalog_id:
            return
        try:
            from photonfinder.models import CatalogEntry
            entry = (CatalogEntry
                     .select()
                     .where(
                         (CatalogEntry.catalog == catalog) &
                         (
                             (CatalogEntry.catalog_id == catalog_id) |
                             (CatalogEntry.canonical_id == catalog_id)
                         )
                     )
                     .first())
            if entry is None:
                self.result_label.setText(f"'{catalog_id}' not found in {catalog}.")
                self._result = None
                self._update_ok_button()
            else:
                self._set_result(entry.ra, entry.dec, f"{catalog} {entry.catalog_id}")
        except Exception as e:
            QMessageBox.warning(self, "Lookup Failed", str(e))

    def _on_online_lookup(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.setEnabled(False)
        self.result_label.setText(f"Looking up '{name}'…")
        self._loader.lookup(name)

    def _on_online_complete(self, coordinates, error_message):
        self.setEnabled(True)
        if error_message:
            self.result_label.setText(f"Lookup failed: {error_message}")
            self._result = None
            self._update_ok_button()
        elif coordinates:
            self._set_result(coordinates.ra.deg, coordinates.dec.deg,
                             self.name_edit.text().strip())
        else:
            self.result_label.setText("No coordinates found.")
            self._result = None
            self._update_ok_button()
