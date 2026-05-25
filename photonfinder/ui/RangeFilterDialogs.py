from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
                               QLabel, QVBoxLayout, QWidget)


def _make_range_row(parent, label: str, is_int: bool, suffix: str = "", decimals: int = 2,
                    min_val: float = 0, max_val: float = 999999, step: float = 1.0):
    """Build a labelled row with two optional bound spinboxes (min/max).

    Returns (group_widget, min_checkbox, min_spin, max_checkbox, max_spin).
    Spinboxes are disabled until their checkbox is ticked.
    """
    group = QGroupBox(label, parent)
    layout = QHBoxLayout(group)
    layout.setContentsMargins(4, 4, 4, 4)

    min_cb = QCheckBox("Min:", group)
    if is_int:
        min_spin = QSpinBox(group)
        min_spin.setRange(int(min_val), int(max_val))
        min_spin.setSingleStep(int(step))
    else:
        min_spin = QDoubleSpinBox(group)
        min_spin.setRange(min_val, max_val)
        min_spin.setDecimals(decimals)
        min_spin.setSingleStep(step)
    if suffix:
        min_spin.setSuffix(f" {suffix}")
    min_spin.setEnabled(False)
    min_cb.toggled.connect(min_spin.setEnabled)

    max_cb = QCheckBox("Max:", group)
    if is_int:
        max_spin = QSpinBox(group)
        max_spin.setRange(int(min_val), int(max_val))
        max_spin.setSingleStep(int(step))
    else:
        max_spin = QDoubleSpinBox(group)
        max_spin.setRange(min_val, max_val)
        max_spin.setDecimals(decimals)
        max_spin.setSingleStep(step)
    if suffix:
        max_spin.setSuffix(f" {suffix}")
    max_spin.setEnabled(False)
    max_cb.toggled.connect(max_spin.setEnabled)

    layout.addWidget(min_cb)
    layout.addWidget(min_spin)
    layout.addSpacing(8)
    layout.addWidget(max_cb)
    layout.addWidget(max_spin)
    layout.addStretch()

    return group, min_cb, min_spin, max_cb, max_spin


def _get_optional(cb, spin):
    """Return spinbox value if checkbox is checked, else None."""
    return spin.value() if cb.isChecked() else None


def _set_optional(cb, spin, value):
    """Pre-fill a checkbox+spinbox pair from an optional value."""
    if value is not None:
        cb.setChecked(True)
        spin.setValue(value)


class ImageSizeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Size Filter")

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._w_group, self._w_min_cb, self._w_min, self._w_max_cb, self._w_max = \
            _make_range_row(self, "Width", is_int=True, suffix="px", min_val=0, max_val=99999)
        self._h_group, self._h_min_cb, self._h_min, self._h_max_cb, self._h_max = \
            _make_range_row(self, "Height", is_int=True, suffix="px", min_val=0, max_val=99999)

        layout.addWidget(self._w_group)
        layout.addWidget(self._h_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_size_criteria(self):
        """Return (width_min, width_max, height_min, height_max) — each int | None."""
        return (
            _get_optional(self._w_min_cb, self._w_min),
            _get_optional(self._w_max_cb, self._w_max),
            _get_optional(self._h_min_cb, self._h_min),
            _get_optional(self._h_max_cb, self._h_max),
        )

    def set_from_criteria(self, width_min, width_max, height_min, height_max):
        _set_optional(self._w_min_cb, self._w_min, width_min)
        _set_optional(self._w_max_cb, self._w_max, width_max)
        _set_optional(self._h_min_cb, self._h_min, height_min)
        _set_optional(self._h_max_cb, self._h_max, height_max)

    def set_from_image(self, image):
        if image.width is not None:
            self._w_min_cb.setChecked(True)
            self._w_min.setValue(image.width)
            self._w_max_cb.setChecked(True)
            self._w_max.setValue(image.width)
        if image.height is not None:
            self._h_min_cb.setChecked(True)
            self._h_min.setValue(image.height)
            self._h_max_cb.setChecked(True)
            self._h_max.setValue(image.height)


class PlateScaleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Scale Filter")

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._group, self._min_cb, self._min_spin, self._max_cb, self._max_spin = \
            _make_range_row(self, "Image Scale", is_int=False, suffix="arcsec/px",
                            min_val=0.0, max_val=999.99, step=0.1, decimals=2)

        layout.addWidget(self._group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_scale_criteria(self):
        """Return (scale_min, scale_max) — each float | None."""
        return (
            _get_optional(self._min_cb, self._min_spin),
            _get_optional(self._max_cb, self._max_spin),
        )

    def set_from_criteria(self, scale_min, scale_max):
        _set_optional(self._min_cb, self._min_spin, scale_min)
        _set_optional(self._max_cb, self._max_spin, scale_max)

    def set_from_image(self, image):
        if image.coord_scale is not None:
            self._min_cb.setChecked(True)
            self._min_spin.setValue(image.coord_scale)
            self._max_cb.setChecked(True)
            self._max_spin.setValue(image.coord_scale)


class ImageQualityDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Statistics Filter")

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._stars_g, self._stars_min_cb, self._stars_min, self._stars_max_cb, self._stars_max = \
            _make_range_row(self, "Star Count", is_int=True, min_val=0, max_val=999999)
        self._fwhm_g, self._fwhm_min_cb, self._fwhm_min, self._fwhm_max_cb, self._fwhm_max = \
            _make_range_row(self, "FWHM median", is_int=False, suffix="px",
                            min_val=0.0, max_val=999.0, step=0.1, decimals=2)
        self._bg_g, self._bg_min_cb, self._bg_min, self._bg_max_cb, self._bg_max = \
            _make_range_row(self, "Background median", is_int=False,
                            min_val=0.0, max_val=999999.0, step=1.0, decimals=1)
        self._bgrms_g, self._bgrms_min_cb, self._bgrms_min, self._bgrms_max_cb, self._bgrms_max = \
            _make_range_row(self, "Background RMS", is_int=False,
                            min_val=0.0, max_val=999999.0, step=1.0, decimals=1)
        self._elong_g, self._elong_min_cb, self._elong_min, self._elong_max_cb, self._elong_max = \
            _make_range_row(self, "Elongation median", is_int=False,
                            min_val=1.0, max_val=10.0, step=0.05, decimals=2)

        for g in [self._stars_g, self._fwhm_g, self._bg_g, self._bgrms_g, self._elong_g]:
            layout.addWidget(g)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_quality_criteria(self):
        """Return dict with all 10 optional quality values."""
        return {
            "star_count_min": _get_optional(self._stars_min_cb, self._stars_min),
            "star_count_max": _get_optional(self._stars_max_cb, self._stars_max),
            "fwhm_min": _get_optional(self._fwhm_min_cb, self._fwhm_min),
            "fwhm_max": _get_optional(self._fwhm_max_cb, self._fwhm_max),
            "background_min": _get_optional(self._bg_min_cb, self._bg_min),
            "background_max": _get_optional(self._bg_max_cb, self._bg_max),
            "background_rms_min": _get_optional(self._bgrms_min_cb, self._bgrms_min),
            "background_rms_max": _get_optional(self._bgrms_max_cb, self._bgrms_max),
            "elongation_min": _get_optional(self._elong_min_cb, self._elong_min),
            "elongation_max": _get_optional(self._elong_max_cb, self._elong_max),
        }

    def set_from_criteria(self, criteria):
        _set_optional(self._stars_min_cb, self._stars_min, criteria.star_count_min)
        _set_optional(self._stars_max_cb, self._stars_max, criteria.star_count_max)
        _set_optional(self._fwhm_min_cb, self._fwhm_min, criteria.fwhm_min)
        _set_optional(self._fwhm_max_cb, self._fwhm_max, criteria.fwhm_max)
        _set_optional(self._bg_min_cb, self._bg_min, criteria.background_min)
        _set_optional(self._bg_max_cb, self._bg_max, criteria.background_max)
        _set_optional(self._bgrms_min_cb, self._bgrms_min, criteria.background_rms_min)
        _set_optional(self._bgrms_max_cb, self._bgrms_max, criteria.background_rms_max)
        _set_optional(self._elong_min_cb, self._elong_min, criteria.elongation_min)
        _set_optional(self._elong_max_cb, self._elong_max, criteria.elongation_max)

    def set_from_stats(self, stats):
        if stats is None:
            return
        if stats.star_count is not None:
            _set_optional(self._stars_min_cb, self._stars_min, stats.star_count)
            _set_optional(self._stars_max_cb, self._stars_max, stats.star_count)
        if stats.fwhm_median is not None:
            _set_optional(self._fwhm_min_cb, self._fwhm_min, stats.fwhm_median)
            _set_optional(self._fwhm_max_cb, self._fwhm_max, stats.fwhm_median)
        if stats.background_median is not None:
            _set_optional(self._bg_min_cb, self._bg_min, stats.background_median)
            _set_optional(self._bg_max_cb, self._bg_max, stats.background_median)
        if stats.background_rms is not None:
            _set_optional(self._bgrms_min_cb, self._bgrms_min, stats.background_rms)
            _set_optional(self._bgrms_max_cb, self._bgrms_max, stats.background_rms)
        if stats.elongation_median is not None:
            _set_optional(self._elong_min_cb, self._elong_min, stats.elongation_median)
            _set_optional(self._elong_max_cb, self._elong_max, stats.elongation_median)
