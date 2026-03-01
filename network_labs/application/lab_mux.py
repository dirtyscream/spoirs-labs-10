import selectors
import socket
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, Tuple

from network_labs.config import ServerConfig, ClientConfig, FileConfig
from network_labs.domain.commands import parse_command, CommandType
from network_labs.domain.interfaces import LabRunner
from network_labs.domain.metrics import TransferMetrics
from network_labs.infrastructure.tcp_transport import (
    create_server_socket, enable_keepalive,
)
from network_labs.infrastructure.file_storage import LocalFileStore
from network_labs.application.handlers import CommandHandler
from network_labs.application.lab2 import Lab2Client

MUX_CHUNK = 8192


class Phase(Enum):
    COMMAND = auto()
    UPLOAD = auto()
    DOWNLOAD = auto()


@dataclass
class Session:
    addr: Tuple[str, int]
    sock: socket.socket
    fd: int = 0
    buf_in: bytearray = field(default_factory=bytearray)
    buf_out: bytearray = field(default_factory=bytearray)
    phase: Phase = Phase.COMMAND
    filename: str = ""
    offset: int = 0
    remaining: int = 0
    metrics: Optional[TransferMetrics] = None


class MuxServer:
    def __init__(self, config: ServerConfig, file_config: FileConfig) -> None:
        self._config = config
        self._file_cfg = file_config
        self._handler = CommandHandler()
        self._store = LocalFileStore(file_config.storage_dir)
        self._sel = selectors.DefaultSelector()
        self._sessions: Dict[int, Session] = {}

    def run(self) -> None:
        srv = create_server_socket(
            self._config.host, self._config.port, self._config.backlog,
        )
        srv.setblocking(False)
        self._sel.register(srv, selectors.EVENT_READ)
        print(
            f"Lab 3 Multiplexing TCP Server on {self._config.host}:{self._config.port}")
        try:
            self._loop(srv)
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            self._sel.close()
            srv.close()

    def _loop(self, srv: socket.socket) -> None:
        while True:
            for key, mask in self._sel.select(timeout=1.0):
                if key.fileobj is srv:
                    self._accept(srv)
                else:
                    self._handle(key.fileobj, mask)

    def _accept(self, srv: socket.socket) -> None:
        client, addr = srv.accept()
        client.setblocking(False)
        enable_keepalive(client)
        s = Session(addr=addr, sock=client, fd=client.fileno())
        self._sessions[s.fd] = s
        self._sel.register(client, selectors.EVENT_READ)
        print(f"Connected: {addr}")

    def _handle(self, sock: socket.socket, mask: int) -> None:
        fd = sock.fileno()
        s = self._sessions.get(fd)
        if not s:
            return
        try:
            if mask & selectors.EVENT_READ:
                self._on_read(s)
            if (mask & selectors.EVENT_WRITE) and s.fd in self._sessions:
                self._on_write(s)
        except (ConnectionError, OSError) as exc:
            print(f"Error {s.addr}: {exc}")
            if s.fd in self._sessions:
                self._drop(s)

    def _on_read(self, s: Session) -> None:
        data = s.sock.recv(MUX_CHUNK)
        if not data:
            self._drop(s)
            return
        if s.phase == Phase.UPLOAD:
            self._recv_upload(s, data)
        else:
            s.buf_in.extend(data)
            self._parse_commands(s)

    def _on_write(self, s: Session) -> None:
        if s.buf_out:
            self._flush(s)
        elif s.phase == Phase.DOWNLOAD:
            self._send_download(s)

    def _parse_commands(self, s: Session) -> None:
        while b"\n" in s.buf_in:
            idx = s.buf_in.index(b"\n")
            line = bytes(s.buf_in[:idx]).rstrip(b"\r").decode()
            s.buf_in = s.buf_in[idx + 1:]
            self._dispatch(s, line)

    def _dispatch(self, s: Session, msg: str) -> None:
        cmd, args = parse_command(msg)
        if cmd == CommandType.UPLOAD:
            self._begin_upload(s, args)
        elif cmd == CommandType.DOWNLOAD:
            self._begin_download(s, args)
        elif cmd == CommandType.CLOSE:
            self._drop(s)
        else:
            resp = self._handler.process(cmd, args)
            if resp is None:
                self._drop(s)
            else:
                self._send_msg(s, resp)

    def _begin_upload(self, s: Session, args: str) -> None:
        parts = args.split()
        if len(parts) < 2:
            self._send_msg(s, "ERROR Usage: UPLOAD filename filesize")
            return
        s.filename = parts[0]
        total = int(parts[1])
        s.offset = self._store.get_size(s.filename)
        s.remaining = total - s.offset
        s.phase = Phase.UPLOAD
        s.metrics = TransferMetrics()
        s.metrics.start()
        self._send_msg(s, f"READY {s.offset}")

    def _recv_upload(self, s: Session, data: bytes) -> None:
        take = min(len(data), s.remaining)
        if take > 0:
            self._store.write_bytes(s.filename, s.offset, data[:take])
            s.offset += take
            s.remaining -= take
            s.metrics.add_bytes(take)
        if s.remaining <= 0:
            self._complete_upload(s)
        if take < len(data):
            s.buf_in.extend(data[take:])
            if s.phase == Phase.COMMAND:
                self._parse_commands(s)

    def _complete_upload(self, s: Session) -> None:
        s.metrics.stop()
        summary = s.metrics.summary()
        s.phase = Phase.COMMAND
        self._send_msg(s, f"OK {summary}")
        print(f"Upload done: {s.filename} — {summary}")

    def _begin_download(self, s: Session, args: str) -> None:
        parts = args.split()
        s.filename = parts[0]
        s.offset = int(parts[1]) if len(parts) > 1 else 0
        if not self._store.exists(s.filename):
            self._send_msg(s, "ERROR File not found")
            return
        s.remaining = self._store.get_size(s.filename) - s.offset
        s.phase = Phase.DOWNLOAD
        s.metrics = TransferMetrics()
        s.metrics.start()
        self._send_msg(s, f"SIZE {s.remaining}")

    def _send_download(self, s: Session) -> None:
        if s.remaining <= 0:
            self._complete_download(s)
            return
        chunk = self._store.read_bytes(
            s.filename, s.offset, min(MUX_CHUNK, s.remaining),
        )
        if not chunk:
            self._complete_download(s)
            return
        try:
            sent = s.sock.send(chunk)
        except BlockingIOError:
            return
        s.offset += sent
        s.remaining -= sent
        s.metrics.add_bytes(sent)
        if s.remaining <= 0:
            self._complete_download(s)

    def _complete_download(self, s: Session) -> None:
        s.metrics.stop()
        print(f"Download done: {s.filename} — {s.metrics.summary()}")
        s.phase = Phase.COMMAND
        self._set_events(s, selectors.EVENT_READ)

    def _send_msg(self, s: Session, msg: str) -> None:
        s.buf_out.extend((msg + "\r\n").encode())
        self._set_events(s, selectors.EVENT_READ | selectors.EVENT_WRITE)

    def _flush(self, s: Session) -> None:
        try:
            sent = s.sock.send(bytes(s.buf_out))
        except BlockingIOError:
            return
        s.buf_out = s.buf_out[sent:]
        if not s.buf_out and s.phase != Phase.DOWNLOAD:
            self._set_events(s, selectors.EVENT_READ)

    def _set_events(self, s: Session, events: int) -> None:
        try:
            self._sel.modify(s.sock, events)
        except (ValueError, KeyError):
            pass

    def _drop(self, s: Session) -> None:
        try:
            self._sel.unregister(s.sock)
        except (ValueError, KeyError):
            pass
        s.sock.close()
        self._sessions.pop(s.fd, None)
        print(f"Disconnected: {s.addr}")


class LabMuxRunner(LabRunner):
    def __init__(
        self, server_config: ServerConfig,
        client_config: ClientConfig, file_config: FileConfig,
    ) -> None:
        self._srv_cfg = server_config
        self._cli_cfg = client_config
        self._file_cfg = file_config

    def run_server(self) -> None:
        MuxServer(self._srv_cfg, self._file_cfg).run()

    def run_client(self) -> None:
        Lab2Client(self._cli_cfg, self._file_cfg).run()
