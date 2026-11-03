import astropy.units as u
import numpy as np
from PySide6.QtWidgets import QWidget, QTableWidgetItem
from astropy.coordinates import SkyCoord
from astropy.io.fits import Header
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from .common import _format_ra, _format_dec
from photonfinder.ui.generated.HeaderDialog_ui import Ui_HeaderDialog


class HeaderDialog(QWidget, Ui_HeaderDialog):
    """
    Dialog for displaying cached FITS header content.
    """

    def __init__(self, header: Header, wcs_header: Header = None, parent=None):
        super(HeaderDialog, self).__init__(parent)
        self.setupUi(self)

        header_content = header.tostring(sep="\n", endcard=False, padding=False)
        self.headerTextEdit.setPlainText(header_content)

        if wcs_header is None:
            self.wcsWidget.setVisible(False)
            return

        wcs_content = wcs_header.tostring(sep="\n", endcard=False, padding=False) if wcs_header is not None else ""
        self.wcsHeaderTextEdit.setPlainText(wcs_content)

        wcs_model = WCS(wcs_header)
        try:
            shape = get_shape_from_header(header, wcs_header)
        except ValueError:
            return  # image size unknown: show the WCS header without the summary
        results = analyze_wcs(wcs_model, shape)
        self.wcsSummary.setRowCount(1)
        self.wcsSummary.setItem(0, 0, QTableWidgetItem(f"{results['arcsec_per_pixel'][0]:.2f}\"/px"))
        self.wcsSummary.setItem(0, 1, QTableWidgetItem(f"{_format_ra(results['center'].ra.value)} "
                                                       f"{_format_dec(results['center'].dec.value)}"))
        self.wcsSummary.setItem(0, 2, QTableWidgetItem(f"{int(results['fov_arcmin'][0])}' x"
                                                       f" {int(results['fov_arcmin'][1])}'"))
        self.wcsSummary.setItem(0, 3, QTableWidgetItem(f"{results['rotation_deg']:.2f}°"))
        self.wcsSummary.resizeColumnsToContents()


def analyze_wcs(wcs, shape):
    # Arcsec per pixel
    pixel_scales = proj_plane_pixel_scales(wcs)  # in degrees/pixel
    arcsec_per_pixel = pixel_scales * 3600  # convert to arcsec/pixel

    # Image center in pixel coordinates
    ny, nx = shape
    center_pixel = np.array([nx / 2, ny / 2])

    # Center in world coordinates
    center_world = wcs.wcs_pix2world([center_pixel], 0)[0]  # [RA, Dec]
    center_coord = SkyCoord(ra=center_world[0] * u.deg, dec=center_world[1] * u.deg)

    # Field of view (in arcminutes)
    fov_deg = pixel_scales * np.array([nx, ny])
    fov_arcmin = fov_deg * 60

    # Rotation angle (from CD or PC matrix)
    cd = wcs.wcs.cd if wcs.wcs.has_cd() else wcs.pixel_scale_matrix
    rotation_rad = np.arctan2(cd[0, 1], cd[0, 0])
    rotation_deg = np.degrees(rotation_rad)

    return {
        'arcsec_per_pixel': arcsec_per_pixel,
        'center': center_coord,
        'fov_arcmin': fov_arcmin,
        'rotation_deg': float(rotation_deg)
    }


def get_shape_from_header(header, wcs):
    """(rows, columns) of the image: NAXISn from the header, else from the WCS, else twice the
    reference pixel. Raises ValueError if none of them is there."""
    if header.get('NAXIS', wcs.get('NAXIS', 2)) >= 2:
        nx = _axis_length(header, wcs, 1)  # number of columns (x-axis)
        ny = _axis_length(header, wcs, 2)  # number of rows (y-axis)
        return ny, nx
    else:
        raise ValueError("Header does not contain 2D image information.")


def _axis_length(header, wcs, axis: int) -> int:
    for source in (header, wcs):
        value = source.get(f'NAXIS{axis}')
        if value is not None:
            return int(value)
    crpix = wcs.get(f'CRPIX{axis}')
    if crpix is None:
        raise ValueError(f"Neither NAXIS{axis} nor CRPIX{axis} is known.")
    return int(crpix * 2)
