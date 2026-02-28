import datetime
from typing import Optional

from network_labs.domain.commands import CommandType


class CommandHandler:
    def process(self, cmd_type: CommandType, args: str) -> Optional[str]:
        if cmd_type == CommandType.ECHO:
            return args
        if cmd_type == CommandType.TIME:
            return datetime.datetime.now().isoformat()
        if cmd_type == CommandType.CLOSE:
            return None
        return f"ERROR Unknown command"
