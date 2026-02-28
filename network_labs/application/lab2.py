import os
import socket

from network_labs.config import ServerConfig, ClientConfig, FileConfig
from network_labs.domain.commands import parse_command, CommandType
from network_labs.domain.interfaces import LabRunner
from network_labs.domain.metrics import TransferMetrics
from network_labs.infrastructure.tcp_transport import (
    TcpTransport, create_server_socket, create_client_socket, enable_keepalive,
)
from network_labs.infrastructure.file_storage import LocalFileStore
from network_labs.application.handlers import CommandHandler


class Lab2Server:
    def __init__(self, config: ServerConfig, file_config: FileConfig) -> None:
        self._config = config
        self._file_cfg = file_config
        self._handler = CommandHandler()
        self._store = LocalFileStore(file_config.storage_dir)

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
                self._serve_client(transport)
            except (ConnectionError, OSError) as exc:
                print(f"Client error: {exc}")
            finally:
                transport.close()
                print(f"Client disconnected: {addr}")

    def _serve_client(self, transport: TcpTransport) -> None:
        while True:
            message = transport.receive_message()
            if message is None:
                break
            if not self._dispatch(message, transport):
                break

    def _dispatch(self, message: str, transport: TcpTransport) -> bool:
        cmd_type, args = parse_command(message)
        if cmd_type == CommandType.UPLOAD:
            self._handle_upload(args, transport)
            return True
        if cmd_type == CommandType.DOWNLOAD:
            self._handle_download(args, transport)
            return True
        response = self._handler.process(cmd_type, args)
        if response is None:
            return False
        transport.send_message(response)
        return True

    def _handle_upload(self, args: str, transport: TcpTransport) -> None:
        parts = args.split()
        if len(parts) < 2:
            transport.send_message("ERROR Usage: UPLOAD filename filesize")
            return
        filename, file_size = parts[0], int(parts[1])
        offset = self._store.get_size(filename)
        transport.send_message(f"READY {offset}")
        metrics = self._receive_to_store(transport, filename, offset, file_size - offset)
        transport.send_message(f"OK {metrics.summary()}")
        print(f"Upload complete: {filename} - {metrics.summary()}")

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
        print(f"Download complete: {filename} - {metrics.summary()}")

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

    def run(self) -> None:
        sock = create_client_socket(self._config.host, self._config.port)
        enable_keepalive(sock)
        transport = TcpTransport(sock)
        print(f"Connected to {self._config.host}:{self._config.port}")
        print("Commands: ECHO <text>, TIME, UPLOAD <file>, DOWNLOAD <file>, CLOSE")
        try:
            self._command_loop(transport)
        except KeyboardInterrupt:
            print("\nDisconnected.")
        finally:
            transport.close()

    def _command_loop(self, transport: TcpTransport) -> None:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            cmd_type, args = parse_command(user_input)
            if not self._execute(cmd_type, args, user_input, transport):
                break

    def _execute(
        self, cmd_type: CommandType, args: str,
        raw: str, transport: TcpTransport,
    ) -> bool:
        if cmd_type == CommandType.UPLOAD:
            self._upload(args, transport)
            return True
        if cmd_type == CommandType.DOWNLOAD:
            self._download(args, transport)
            return True
        if cmd_type == CommandType.CLOSE:
            transport.send_message(raw)
            print("Connection closed.")
            return False
        return self._send_text_command(raw, transport)

    def _send_text_command(self, raw: str, transport: TcpTransport) -> bool:
        transport.send_message(raw)
        response = transport.receive_message()
        if response is None:
            print("Server closed connection.")
            return False
        print(response)
        return True

    def _upload(self, filename: str, transport: TcpTransport) -> None:
        if not os.path.exists(filename):
            print(f"File not found: {filename}")
            return
        file_size = os.path.getsize(filename)
        offset = self._negotiate_upload(transport, filename, file_size)
        if offset is None:
            return
        self._perform_upload(transport, filename, offset, file_size)

    def _negotiate_upload(
        self, transport: TcpTransport, filename: str, file_size: int,
    ) -> int:
        transport.send_message(f"UPLOAD {os.path.basename(filename)} {file_size}")
        response = transport.receive_message()
        if not response or not response.startswith("READY"):
            print(f"Server: {response}")
            return None
        return int(response.split()[1])

    def _perform_upload(
        self, transport: TcpTransport, filename: str,
        offset: int, file_size: int,
    ) -> None:
        metrics = TransferMetrics()
        metrics.start()
        self._send_file(transport, filename, offset, file_size - offset, metrics)
        metrics.stop()
        result = transport.receive_message()
        print(f"Upload: {metrics.summary()}")
        if result:
            print(f"Server: {result}")

    def _download(self, filename: str, transport: TcpTransport) -> None:
        local_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        remaining = self._negotiate_download(transport, filename, local_size)
        if remaining is None:
            return
        self._perform_download(transport, filename, local_size, remaining)

    def _negotiate_download(
        self, transport: TcpTransport, filename: str, local_size: int,
    ) -> int:
        transport.send_message(f"DOWNLOAD {os.path.basename(filename)} {local_size}")
        response = transport.receive_message()
        if not response:
            print("Server closed connection.")
            return None
        if response.startswith("ERROR"):
            print(response)
            return None
        return int(response.split()[1])

    def _perform_download(
        self, transport: TcpTransport, filename: str,
        offset: int, remaining: int,
    ) -> None:
        metrics = TransferMetrics()
        metrics.start()
        self._receive_file(transport, filename, offset, remaining, metrics)
        metrics.stop()
        print(f"Download: {metrics.summary()}")

    def _send_file(
        self, transport: TcpTransport, filename: str,
        offset: int, size: int, metrics: TransferMetrics,
    ) -> None:
        with open(filename, "rb") as f:
            f.seek(offset)
            sent = 0
            while sent < size:
                chunk = f.read(min(self._file_cfg.chunk_size, size - sent))
                if not chunk:
                    break
                transport.send_bytes(chunk)
                sent += len(chunk)
                metrics.add_bytes(len(chunk))

    def _receive_file(
        self, transport: TcpTransport, filename: str,
        offset: int, size: int, metrics: TransferMetrics,
    ) -> None:
        mode = "r+b" if offset > 0 else "wb"
        with open(filename, mode) as f:
            f.seek(offset)
            received = 0
            while received < size:
                chunk_size = min(self._file_cfg.chunk_size, size - received)
                chunk = transport.receive_bytes(chunk_size)
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
