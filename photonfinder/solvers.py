"""Which plate solvers there are, and building them from the settings.

Settings store a solver by its index in SOLVERS: the primary as 0..n-1, the backup the same way
or NO_BACKUP. MCP tools name solvers by key, the GUI shows their label; all three come from the
one table here. The plate-solve dialog and the MCP plate-solve tool both build their solvers
through build_solvers.
"""
import logging
from typing import NamedTuple, Optional

from photonfinder.platesolver import (
    ASTAPSolver, AstrometryNetSolver, SolveFieldSolver, SolverBase, SolverType,
)

logger = logging.getLogger(__name__)

NO_BACKUP = -1
NONE_KEY = "none"


class SolverEntry(NamedTuple):
    index: int        # as stored in the settings
    key: str          # as MCP tools name it
    label: str        # as the GUI and the solve log show it
    solver_type: SolverType
    solver_class: type


SOLVERS = (
    SolverEntry(0, "astap", "ASTAP", SolverType.ASTAP, ASTAPSolver),
    SolverEntry(1, "astrometry_net", "Astrometry.net", SolverType.ASTROMETRY_NET, AstrometryNetSolver),
    SolverEntry(2, "solve_field", "solve-field", SolverType.SOLVE_FIELD, SolveFieldSolver),
)
_BY_INDEX = {entry.index: entry for entry in SOLVERS}
_BY_KEY = {entry.key: entry for entry in SOLVERS}


class SolverConfigError(ValueError):
    """The solvers asked for can't be built: no primary, or an unknown name."""


def index_for(key: str, allow_none: bool = False) -> int:
    """The settings index for a solver key ("astap", ...); NO_BACKUP for "none" where allowed.
    Raises SolverConfigError for anything else."""
    normalized = key.strip().lower()
    if allow_none and normalized == NONE_KEY:
        return NO_BACKUP
    if normalized not in _BY_KEY:
        valid = [*sorted(_BY_KEY), *([NONE_KEY] if allow_none else [])]
        raise SolverConfigError(f"Unknown solver '{key}'. Valid solvers: {', '.join(valid)}.")
    return _BY_KEY[normalized].index


def label_of(solver: SolverBase) -> str:
    """The name the GUI and the solve log use for a built solver; a solver of another class
    (a stand-in in tests) goes by its class name."""
    entry = next((e for e in SOLVERS if isinstance(solver, e.solver_class)), None)
    return entry.label if entry else type(solver).__name__


def build_solver(settings, index: int) -> Optional[SolverBase]:
    """The solver at `index`, configured from the settings; None for an index not in SOLVERS."""
    entry = _BY_INDEX.get(index)
    if entry is None:
        return None
    if entry.solver_type is SolverType.ASTAP:
        return ASTAPSolver(exe=settings.get_astap_path())
    if entry.solver_type is SolverType.ASTROMETRY_NET:
        return AstrometryNetSolver(
            api_key=settings.get_astrometry_net_api_key(),
            force_image_upload=settings.get_astrometry_net_force_image_upload(),
        )
    return SolveFieldSolver(
        exe_path=settings.get_solve_field_path(),
        timeout=settings.get_solve_field_timeout(),
        wsl_distro=settings.get_solve_field_wsl_distro(),
    )


def build_solvers(settings, primary_index: int, backup_index: int = NO_BACKUP):
    """(primary, backup) for these settings indexes; backup is None for NO_BACKUP. Raises
    SolverConfigError when the primary index is not a solver. A backup index that is not a
    solver means no backup, as it always has."""
    primary = build_solver(settings, primary_index)
    if primary is None:
        raise SolverConfigError(f"No primary solver configured (index {primary_index}).")
    backup = None
    if backup_index != NO_BACKUP:
        backup = build_solver(settings, backup_index)
        if backup is None:
            logger.warning("Ignoring unknown backup solver index %s", backup_index)
    return primary, backup
