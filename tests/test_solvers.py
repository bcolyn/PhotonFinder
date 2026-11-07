"""One table of solvers and one builder, shared by the plate-solve dialog and the MCP tool."""
import pytest

from photonfinder import solvers
from photonfinder.platesolver import ASTAPSolver, AstrometryNetSolver, SolveFieldSolver


class Settings:
    """The settings the builder reads, with the values a user would have set."""

    def get_astap_path(self):
        return "C:/astap/astap.exe"

    def get_astrometry_net_api_key(self):
        return "key"

    def get_astrometry_net_force_image_upload(self):
        return True

    def get_solve_field_path(self):
        return "/usr/bin/solve-field"

    def get_solve_field_timeout(self):
        return 120

    def get_solve_field_wsl_distro(self):
        return "Ubuntu"


def test_primary_only():
    primary, backup = solvers.build_solvers(Settings(), 0)

    assert isinstance(primary, ASTAPSolver) and backup is None


def test_primary_and_backup_are_configured_from_the_settings():
    primary, backup = solvers.build_solvers(Settings(), 2, 1)

    assert isinstance(primary, SolveFieldSolver) and isinstance(backup, AstrometryNetSolver)
    assert solvers.label_of(primary) == "solve-field" and solvers.label_of(backup) == "Astrometry.net"


@pytest.mark.parametrize("index", [solvers.NO_BACKUP, 3, 99])
def test_no_primary_configured_is_an_error(index):
    with pytest.raises(solvers.SolverConfigError, match="No primary solver"):
        solvers.build_solvers(Settings(), index)


def test_an_unknown_backup_index_means_no_backup():
    assert solvers.build_solvers(Settings(), 0, 7)[1] is None


def test_the_table_keeps_the_stored_indexes():
    """Settings saved by earlier versions name solvers by these indexes."""
    assert [(e.index, e.key, e.label) for e in solvers.SOLVERS] == [
        (0, "astap", "ASTAP"), (1, "astrometry_net", "Astrometry.net"), (2, "solve_field", "solve-field")]


def test_keys_name_solvers_and_none_only_where_allowed():
    assert solvers.index_for(" Astrometry_Net ") == 1
    assert solvers.index_for("none", allow_none=True) == solvers.NO_BACKUP
    with pytest.raises(solvers.SolverConfigError, match="Valid solvers: astap, astrometry_net, solve_field.$"):
        solvers.index_for("none")
    with pytest.raises(solvers.SolverConfigError, match="'Bogus'"):
        solvers.index_for("Bogus", allow_none=True)
