from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8888
    backlog: int = 5


@dataclass(frozen=True)
class ClientConfig:
    host: str = "127.0.0.1"
    port: int = 8888


@dataclass(frozen=True)
class ReliableUdpConfig:
    packet_size: int = 1400
    window_size: int = 10
    ack_timeout: float = 0.1
    max_retries: int = 10


@dataclass(frozen=True)
class DynamicPoolConfig:
    min_threads: int = 5
    max_threads: int = 20
    idle_timeout: float = 60.0


@dataclass(frozen=True)
class Lab4Config:
    pool: DynamicPoolConfig = field(default_factory=DynamicPoolConfig)
    host: str = "0.0.0.0"
    port: int = 8888
    client_host: str = "127.0.0.1"


@dataclass(frozen=True)
class FileConfig:
    chunk_size: int = 4096
    storage_dir: str = "lab_files"
