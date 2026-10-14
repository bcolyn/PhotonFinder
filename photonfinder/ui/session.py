import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import List

from photonfinder.models import SearchCriteria, File, Project

_CRITERIA_FIELDS = {f.name for f in dataclasses.fields(SearchCriteria)}


@dataclasses.dataclass
class Session:
    criteria: SearchCriteria
    title: str
    hidden_columns: str
    title_is_custom: bool = False

    @staticmethod
    def _serialize(value) -> dict:
        return SearchCriteria._serialize(value)

    @classmethod
    def _inflate(cls, value):
        if isinstance(value, list):
            return [cls._inflate(x) for x in value]
        elif isinstance(value, dict):
            return cls._inflate_dict(value)
        else:
            raise ValueError(f"Can't inflate value {value}")

    @staticmethod
    def _inflate_dict(data_dict):
        session_fields = {f.name for f in dataclasses.fields(Session)}
        data_dict = _known_keys(data_dict, session_fields, "session")
        if data_dict.get('criteria', None):
            criteria = _drop_stale_references(_known_keys(data_dict['criteria'], _CRITERIA_FIELDS, "criteria"))
            data_dict['criteria'] = SearchCriteria._inflate_dict(criteria)
        return Session(**data_dict)


def _known_keys(data: dict, fields: set, what: str) -> dict:
    """Drop keys written by another version of the application."""
    unknown = data.keys() - fields
    if unknown:
        logging.warning(f"Ignoring unknown {what} fields in saved session: {sorted(unknown)}")
    return {k: v for k, v in data.items() if k in fields}


def _drop_stale_references(criteria: dict) -> dict:
    """Clear a reference file or project that has since been deleted from the database,
    rather than failing to restore the tab."""
    ref = criteria.get('reference_file')
    if ref and File.get_or_none(File.rowid == ref) is None:
        logging.warning(f"Saved tab refers to file {ref}, which no longer exists; dropping that filter")
        criteria['reference_file'] = None
    project = criteria.get('project')
    if project and int(project) > 0 and Project.get_or_none(Project.rowid == int(project)) is None:
        logging.warning(f"Saved tab refers to project {project}, which no longer exists; dropping that filter")
        criteria['project'] = None
    return criteria


def json_to_sessions(json_str: str) -> List[Session]:
    data = json.loads(json_str)
    assert isinstance(data, list)
    sessions = []
    for entry in data:
        # One tab that can't be restored must not take all the others with it.
        try:
            sessions.append(Session._inflate(entry))
        except Exception as e:
            logging.error(f"Could not restore a saved tab, skipping it: {e}", exc_info=True)
    return sessions


def sessions_to_json(sessions: List[Session]) -> str:
    return json.dumps(sessions, default=Session._serialize, sort_keys=True, indent=4)


class SessionManager:

    def __init__(self, file):
        self.file = file

    def load_sessions(self) -> List[Session]:
        if self.file and Path(self.file).exists():
            with open(self.file, mode="rb") as fd:
                json_str = fd.read().decode("UTF-8")
            try:
                return json_to_sessions(json_str)
            except Exception:
                # Keep the unreadable file: the session saved on exit would otherwise overwrite it.
                corrupt = self.file + ".corrupt"
                os.replace(self.file, corrupt)
                logging.error(f"Could not read saved session, moved it to {corrupt}", exc_info=True)
        return []

    def save_sessions(self, sessions: List[Session]):
        # Serialize first and swap the file in atomically, so a failure can't leave it empty.
        json_str = sessions_to_json(sessions)
        tmp = self.file + ".tmp"
        with open(tmp, mode='wb') as fd:
            fd.write(json_str.encode("UTF-8"))
        os.replace(tmp, self.file)
        logging.info(f"Session saved to {self.file}")
