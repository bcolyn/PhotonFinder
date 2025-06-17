from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox
from PySide6.QtCore import Slot, Signal

from astrofilemanager.core import ApplicationContext
from astrofilemanager.ui.BackgroundLoader import BackgroundLoaderBase
from astrofilemanager.ui.generated.CoordinatesDialog_ui import Ui_CoordinatesDialog


class CoordinateLookupLoader(BackgroundLoaderBase):
    """Background loader for looking up coordinates by name."""
    lookup_complete = Signal(object, str)  # coordinates, error_message

    def lookup_coordinates(self, name):
        """Look up coordinates for the given name."""
        self.run_in_thread(self._lookup_coordinates_task, name)

    def _lookup_coordinates_task(self, name):
        """Background task to look up coordinates."""
        try:
            from astropy.coordinates import SkyCoord
            coordinates = SkyCoord.from_name(name)
            self.lookup_complete.emit(coordinates, None)
        except Exception as e:
            self.lookup_complete.emit(None, str(e))


class CoordinatesDialog(QDialog, Ui_CoordinatesDialog):
    """Dialog for entering coordinates (RA, DEC, and radius)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(CoordinatesDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context

        # Create the background loader
        self.lookup_loader = CoordinateLookupLoader(context)
        self.lookup_loader.lookup_complete.connect(self._on_lookup_complete)

        # Connect the lookup button
        self.lookup_button.clicked.connect(self._on_lookup_clicked)

    def get_coordinates(self) -> tuple:
        """Get the coordinates entered by the user."""
        return (self.ra_edit.text(), self.dec_edit.text(), self.radius_spin.value())

    def set_coordinates(self, ra: str, dec: str, radius: float):
        """Set the initial coordinate values."""
        if ra:
            self.ra_edit.setText(ra)
        if dec:
            self.dec_edit.setText(dec)
        if radius is not None:
            try:
                self.radius_spin.setValue(float(radius))
            except (ValueError, TypeError):
                pass

    def _on_lookup_clicked(self):
        """Handle the lookup button click."""
        # Ask the user for a name to look up
        name, ok = QInputDialog.getText(self, "Object Lookup", "Enter object name:")
        if ok and name:
            # Show a message that we're looking up the coordinates
            self.setEnabled(False)
            self.context.status_reporter.update_status(f"Looking up coordinates for {name}...")

            # Start the lookup in the background
            self.lookup_loader.lookup_coordinates(name)

    def _on_lookup_complete(self, coordinates, error_message):
        """Handle the completion of the coordinate lookup."""
        self.setEnabled(True)

        if error_message:
            # Show an error message
            QMessageBox.warning(self, "Lookup Failed", f"Failed to look up coordinates: {error_message}")
            self.context.status_reporter.update_status("Coordinate lookup failed.")
        elif coordinates:
            # Update the RA and DEC fields with the coordinates
            ra = coordinates.ra.to_string(unit='hour', sep=':', precision=2)
            dec = coordinates.dec.to_string(unit='deg', sep=':', precision=2)

            self.ra_edit.setText(ra)
            self.dec_edit.setText(dec)

            self.context.status_reporter.update_status(f"Coordinates found: RA={ra}, DEC={dec}")
        else:
            # This should not happen, but just in case
            QMessageBox.warning(self, "Lookup Failed", "No coordinates found and no error reported.")
            self.context.status_reporter.update_status("Coordinate lookup failed with unknown error.")
