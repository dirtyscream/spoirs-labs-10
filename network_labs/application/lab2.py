import os
import socket
import time
from typing import Dict, Optional, Tuple

from network_labs.config import ServerConfig, ClientConfig, FileConfig
from network_labs.domain.commands import parse_command, CommandType
from network_labs.domain.interfaces import LabRunner
from network_labs.domain.metrics import TransferMetrics
from network_labs.infrastructure.tcp_transport import (
    TcpTransport, create_server_socket, create_client_socket, enable_keepalive,
)
from network_labs.infrastructure.file_storage import LocalFileStore
from network_labs.application.handlers import CommandHandler

AUTO_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY = 2.0


class UploadSessionTracker:
    def __init__(self) -> None:
        self._owners: Dict[str, str] = {}

    def check_owner(self, filename: str, client_key: str) -> bool:
        owner = self._owners.get(filename)
        return owner is None or owner == client_key

    def register(self, filename: str, client_key: str) -> None:
        self._owners[filename] = client_key

    def remove(self, filename: str) -> None:
        self._owners.pop(filename, None)


class Lab2Server:
    def __init__(self, config: ServerConfig, file_config: FileConfig) -> None:
        self._config = config
        self._file_cfg = file_config
        self._handler = CommandHandler()
        self._store = LocalFileStore(file_config.storage_dir)
        self._sessions = UploadSessionTracker()

    def run(self) -> None:
        sock = create_server_socket(
            self._config.host, self._config.port, self._config.backlog,
        )
        print(f"Lab 2 Server listening on {self._config.host}:{self._config.port}")
        try:
            self._accept_loop(sock)
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            sock.close()

    def _accept_loop(self, server_sock: socket.socket) -> None:
        while True:
            client_sock, addr = server_sock.accept()
            enable_keepalive(client_sock)
            print(f"Client connected: {addr}")
            transport = TcpTransport(client_sock)
            try:
                self._serve_client(transport, addr)
            except (ConnectionError, OSError) as exc:
                print(f"Client error: {exc}")
            finally:
                transport.close()
                print(f"Client disconnected: {addr}")

    def _serve_client(
        self, transport: TcpTransport, addr: Tuple[str, int],
    ) -> None:
        while True:
            message = transport.receive_message()
            if message is None:
                break
            if not self._dispatch(message, transport, addr):
                break

    def _dispatch(
        self, message: str, transport: TcpTransport,
        addr: Tuple[str, int],
    ) -> bool:
        cmd_type, args = parse_command(message)
        if cmd_type == CommandType.UPLOAD:
            self._handle_upload(args, transport, addr)
            return True
        if cmd_type == CommandType.DOWNLOAD:
            self._handle_download(args, transport)
            return True
        response = self._handler.process(cmd_type, args)
        if response is None:
            return False
        transport.send_message(response)
        return True

    def _handle_upload(
        self, args: str, transport: TcpTransport,
        addr: Tuple[str, int],
    ) -> None:
        parts = args.split()
        if len(parts) < 2:
            transport.send_message("ERROR Usage: UPLOAD filename filesize")
            return
        filename, file_size = parts[0], int(parts[1])
        client_key = f"{addr[0]}:{addr[1]}"
        offset = self._resolve_upload_offset(filename, client_key)
        transport.send_message(f"READY {offset}")
        remaining = file_size - offset
        metrics = self._receive_to_store(transport, filename, offset, remaining)
        self._sessions.register(filename, client_key)
        transport.send_message(f"OK {metrics.summary()}")
        print(f"Upload complete: {filename} — {metrics.summary()}")

    def _resolve_upload_offset(self, filename: str, client_key: str) -> int:
        if not self._sessions.check_owner(filename, client_key):
            self._store.delete_file(filename)
            self._sessions.remove(filename)
            return 0
        return self._store.get_size(filename)

    def _handle_download(self, args: str, transport: TcpTransport) -> None:
        parts = args.split()
        filename = parts[0]
        offset = int(parts[1]) if len(parts) > 1 else 0
        if not self._store.exists(filename):
            transport.send_message("ERROR File not found")
            return
        remaining = self._store.get_size(filename) - offset
        transport.send_message(f"SIZE {remaining}")
        metrics = self._send_from_store(transport, filename, offset, remaining)
        print(f"Download complete: {filename} — {metrics.summary()}")

    def _receive_to_store(
        self, transport: TcpTransport, filename: str,
        offset: int, size: int,
    ) -> TransferMetrics:
        metrics = TransferMetrics()
        metrics.start()
        received = 0
        while received < size:
            chunk_size = min(self._file_cfg.chunk_size, size - received)
            chunk = transport.receive_bytes(chunk_size)
            if not chunk:
                break
            self._store.write_bytes(filename, offset + received, chunk)
            received += len(chunk)
            metrics.add_bytes(len(chunk))
        metrics.stop()
        return metrics

    def _send_from_store(
        self, transport: TcpTransport, filename: str,
        offset: int, size: int,
    ) -> TransferMetrics:
        metrics = TransferMetrics()
        metrics.start()
        sent = 0
        while sent < size:
            chunk_size = min(self._file_cfg.chunk_size, size - sent)
            chunk = self._store.read_bytes(filename, offset + sent, chunk_size)
            if not chunk:
                break
            transport.send_bytes(chunk)
            sent += len(chunk)
            metrics.add_bytes(len(chunk))
        metrics.stop()
        return metrics


class Lab2Client:
    def __init__(self, config: ClientConfig, file_config: FileConfig) -> None:
        self._config = config
        self._file_cfg = file_config
        self._transport: Optional[TcpTransport] = None

    def run(self) -> None:
        self._connect()
        print("Commands: ECHO <text>, TIME, UPLOAD <file>, DOWNLOAD <file>, CLOSE")
        try:
            self._command_loop()
        except KeyboardInterrupt:
            print("\nDisconnected.")
        finally:
            self._disconnect()

    def _connect(self) -> None:
        sock = create_client_socket(self._config.host, self._config.port)
        enable_keepalive(sock)
        self._transport = TcpTransport(sock)
        print(f"Connected to {self._config.host}:{self._config.port}")

    def _disconnect(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def _reconnect(self) -> None:
        self._disconnect()
        self._connect()

    def _command_loop(self) -> None:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            cmd_type, args = parse_command(user_input)
            if not self._execute(cmd_type, args, user_input):
                break

    def _execute(self, cmd_type: CommandType, args: str, raw: str) -> bool:
        if cmd_type == CommandType.UPLOAD:
            self._upload_with_retry(args)
            return True
        if cmd_type == CommandType.DOWNLOAD:
            self._download_with_retry(args)
            return True
        if cmd_type == CommandType.CLOSE:
            self._transport.send_message(raw)
            print("Connection closed.")
            return False
        return self._send_text_command(raw)

    def _send_text_command(self, raw: str) -> bool:
        self._transport.send_message(raw)
        response = self._transport.receive_message()
        if response is None:
            print("Server closed connection.")
            return False
        print(response)
        return True

    def _upload_with_retry(self, filename: str) -> None:
        while True:
            try:
                self._upload(filename)
                return
            except (ConnectionError, OSError):
                if not self._try_reconnect():
                    return

    def _download_with_retry(self, filename: str) -> None:
        while True:
            try:
                self._download(filename)
                return
            except (ConnectionError, OSError):
                if not self._try_reconnect():
                    return

    def _try_reconnect(self) -> bool:
        if self._auto_reconnect():
            return True
        return self._ask_user_retry()

    def _auto_reconnect(self) -> bool:
        for attempt in range(1, AUTO_RECONNECT_ATTEMPTS + 1):
            print(f"Connection lost. Reconnecting ({attempt}/{AUTO_RECONNECT_ATTEMPTS})...")
            time.sleep(RECONNECT_DELAY)
            try:
                self._reconnect()
                return True
            except (ConnectionError, OSError):
                continue
        return False

    def _ask_user_retry(self) -> bool:
        print("Could not reconnect automatically.")
        try:
            answer = input("Retry? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer != "y":
            return False
        try:
            self._reconnect()
            return True
        except (ConnectionError, OSError):
            print("Reconnect failed.")
            return False

    def _upload(self, filename: str) -> None:
        if not os.path.exists(filename):
            print(f"File not found: {filename}")
            return
        file_size = os.path.getsize(filename)
        offset = self._negotiate_upload(filename, file_size)
        if offset is None:
            return
        self._perform_upload(filename, offset, file_size)

    def _negotiate_upload(self, filename: str, file_size: int) -> Optional[int]:
        base = os.path.basename(filename)
        self._transport.send_message(f"UPLOAD {base} {file_size}")
        response = self._transport.receive_message()
        if not response or not response.startswith("READY"):
            print(f"Server: {response}")
            return None
        return int(response.split()[1])

    def _perform_upload(self, filename: str, offset: int, file_size: int) -> None:
        metrics = TransferMetrics()
        metrics.start()
        self._send_file(filename, offset, file_size - offset, metrics)
        metrics.stop()
        result = self._transport.receive_message()
        print(f"Upload: {metrics.summary()}")
        if result:
            print(f"Server: {result}")

    def _download(self, filename: str) -> None:
        local_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        remaining = self._negotiate_download(filename, local_size)
        if remaining is None:
            return
        self._perform_download(filename, local_size, remaining)

    def _negotiate_download(self, filename: str, local_size: int) -> Optional[int]:
        base = os.path.basename(filename)
        self._transport.send_message(f"DOWNLOAD {base} {local_size}")
        response = self._transport.receive_message()
        if not response:
            print("Server closed connection.")
            return None
        if response.startswith("ERROR"):
            print(response)
            return None
        return int(response.split()[1])

    def _perform_download(self, filename: str, offset: int, remaining: int) -> None:
        metrics = TransferMetrics()
        metrics.start()
        self._receive_file(filename, offset, remaining, metrics)
        metrics.stop()
        print(f"Download: {metrics.summary()}")

    def _send_file(
        self, filename: str, offset: int,
        size: int, metrics: TransferMetrics,
    ) -> None:
        with open(filename, "rb") as f:
            f.seek(offset)
            sent = 0
            while sent < size:
                chunk = f.read(min(self._file_cfg.chunk_size, size - sent))
                if not chunk:
                    break
                self._transport.send_bytes(chunk)
                sent += len(chunk)
                metrics.add_bytes(len(chunk))

    def _receive_file(
        self, filename: str, offset: int,
        size: int, metrics: TransferMetrics,
    ) -> None:
        mode = "r+b" if offset > 0 else "wb"
        with open(filename, mode) as f:
            f.seek(offset)
            received = 0
            while received < size:
                chunk_size = min(self._file_cfg.chunk_size, size - received)
                chunk = self._transport.receive_bytes(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                metrics.add_bytes(len(chunk))


class Lab2Runner(LabRunner):
    def __init__(
        self, server_config: ServerConfig,
        client_config: ClientConfig, file_config: FileConfig,
    ) -> None:
        self._server_cfg = server_config
        self._client_cfg = client_config
        self._file_cfg = file_config

    def run_server(self) -> None:
        Lab2Server(self._server_cfg, self._file_cfg).run()

    def run_client(self) -> None:
        Lab2Client(self._client_cfg, self._file_cfg).run()
