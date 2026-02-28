from abc import ABC, abstractmethod
from typing import Optional, Tuple


class MessageTransport(ABC):
    @abstractmethod
    def send_message(self, message: str) -> None: ...

    @abstractmethod
    def receive_message(self) -> Optional[str]: ...

    @abstractmethod
    def send_bytes(self, data: bytes) -> None: ...

    @abstractmethod
    def receive_bytes(self, count: int) -> bytes: ...

    @abstractmethod
    def close(self) -> None: ...


class ReliableTransport(ABC):
    @abstractmethod
    def send_message(self, message: str, address: Tuple[str, int]) -> None: ...

    @abstractmethod
    def receive_message(self) -> Tuple[str, Tuple[str, int]]: ...

    @abstractmethod
    def send_data(self, data: bytes, address: Tuple[str, int]) -> None: ...

    @abstractmethod
    def receive_data(self) -> Tuple[bytes, Tuple[str, int]]: ...

    @abstractmethod
    def close(self) -> None: ...


class FileStore(ABC):
    @abstractmethod
    def read_bytes(self, filename: str, offset: int, size: int) -> bytes: ...

    @abstractmethod
    def write_bytes(self, filename: str, offset: int, data: bytes) -> None: ...

    @abstractmethod
    def get_size(self, filename: str) -> int: ...

    @abstractmethod
    def exists(self, filename: str) -> bool: ...

    @abstractmethod
    def delete_file(self, filename: str) -> None: ...


class LabRunner(ABC):
    @abstractmethod
    def run_server(self) -> None: ...

    @abstractmethod
    def run_client(self) -> None: ...
