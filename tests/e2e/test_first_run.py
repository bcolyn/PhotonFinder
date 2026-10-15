"""First start on an empty sandbox, and what a restart brings back."""
from photonfinder.ui.SearchPanel import SearchPanel


def test_first_run_opens_sandbox_database_with_one_empty_tab(sandbox, app):
    assert app.windowTitle() == f"PhotonFinder - {sandbox.database_path}"
    assert sandbox.database_path.exists()

    panels = app.get_search_panels()
    assert len(panels) == 1
    assert isinstance(panels[0], SearchPanel)
    assert app.mcp_controller is None


def test_settings_and_tabs_survive_restart(sandbox, app):
    app.new_search_tab()
    app.get_current_search_panel().set_custom_title("Lights")
    sandbox.wait_idle()

    sandbox.stop()

    assert (sandbox.data_dir / "session_v1.json").exists()
    assert "last_database_path" in sandbox.settings_path.read_text(encoding="utf-8")

    app = sandbox.start()
    titles = [p.get_title() for p in app.get_search_panels()]
    assert len(titles) == 2
    assert titles[1] == "Lights"
    assert app.windowTitle() == f"PhotonFinder - {sandbox.database_path}"
