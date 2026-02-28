from enum import Enum, auto
from typing import Tuple


class CommandType(Enum):
    ECHO = auto()
    TIME = auto()
    CLOSE = auto()
    UPLOAD = auto()
    DOWNLOAD = auto()
    UNKNOWN = auto()


_LOOKUP = {cmd.name: cmd for cmd in CommandType if cmd != CommandType.UNKNOWN}


def parse_command(raw: str) -> Tuple[CommandType, str]:
    parts = raw.strip().split(maxsplit=1)
    if not parts:
        return CommandType.UNKNOWN, ""
    name = parts[0].upper()
    args = parts[1] if len(parts) > 1 else ""
    return _LOOKUP.get(name, CommandType.UNKNOWN), args
