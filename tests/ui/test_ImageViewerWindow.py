from photonfinder.models import File
from photonfinder.ui.ImageViewerWindow import ImageViewerWindow
from tests.conftest import DynamicSettings


def test_annotate_shown_for_plain_file_with_wcs(qtbot, sample):
    """A File fetched outside the search query (e.g. by the project editor) carries no
    has_wcs attribute; the viewer must look it up rather than hide Annotate."""
    ctx, objs = sample
    ctx.settings = DynamicSettings()
    ctx.settings.set_annotation_mag_limit(19.0)
    viewer = ImageViewerWindow(context=ctx)
    qtbot.addWidget(viewer)

    light = File.get_by_id(objs["light"].rowid)
    assert not hasattr(light, "has_wcs")
    viewer.load_file(light)
    assert viewer._annotate_action.isVisible()

    dark = File.get_by_id(objs["dark"].rowid)
    viewer.load_file(dark)
    assert not viewer._annotate_action.isVisible()
