import astropy.units as u
from astropy.coordinates import SkyCoord

from PySide6.QtWidgets import QDialog

from photonfinder.core import ApplicationContext
from photonfinder.ui.generated.CoordinatesDialog_ui import Ui_CoordinatesDialog


class CoordinatesDialog(QDialog, Ui_CoordinatesDialog):
    """Dialog for entering coordinates (RA, DEC, and radius)."""

    def __init__(self, context: ApplicationContext, parent=None):
        super(CoordinatesDialog, self).__init__(parent)
        self.setupUi(self)
        self.context = context
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
        from photonfinder.ui.ObjectLookupDialog import ObjectLookupDialog
        dialog = ObjectLookupDialog(self.context, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.result_ra_dec
        if result is None:
            return
        ra_deg, dec_deg = result
        coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)
        self.ra_edit.setText(coord.ra.to_string(unit=u.hour, sep=':', precision=2))
        self.dec_edit.setText(coord.dec.to_string(unit=u.deg, sep=':', precision=2))
