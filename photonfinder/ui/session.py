import dataclasses
import json
import logging
from pathlib import Path
from typing import List

from photonfinder.models import SearchCriteria


@dataclasses.dataclass
class Session:
    criteria: SearchCriteria
    title: str
    hidden_columns: str

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
            raise f"Can't inflate value {value}"

    @staticmethod
    def _inflate_dict(data_dict):
        if data_dict.get('criteria', None):
            data_dict['criteria'] = SearchCriteria._inflate_dict(data_dict['criteria'])
        return Session(**data_dict)


def json_to_sessions(json_str: str) -> List[Session]:
    data = json.loads(json_str)
    assert isinstance(data, list)
    return Session._inflate(data)


def sessions_to_json(sessions: List[Session]) -> str:
    return json.dumps(sessions, default=Session._serialize, sort_keys=True, indent=4)


class SessionManager:

    def __init__(self, file):
        self.file = file

    def load_sessions(self) -> List[Session]:
        if self.file and Path(self.file).exists():
            with open(self.file, mode="rb") as fd:
                json_str = fd.read().decode("UTF-8")
                return json_to_sessions(json_str)
        return []

    def save_sessions(self, sessions: List[Session]):
        with open(self.file, mode='wb') as fd:
            json_str = sessions_to_json(sessions)
            fd.write(json_str.encode("UTF-8"))
        logging.info(f"Session saved to {self.file}")
