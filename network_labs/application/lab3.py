import os
from typing import Optional, Tuple

from network_labs.config import ServerConfig, ClientConfig, FileConfig, ReliableUdpConfig
from network_labs.domain.commands import parse_command, CommandType
from network_labs.domain.interfaces import LabRunner
from network_labs.domain.metrics import TransferMetrics
from network_labs.infrastructure.reliable_udp import ReliableUdp, create_udp_socket
from network_labs.infrastructure.file_storage import LocalFileStore
from network_labs.application.handlers import CommandHandler


class Lab3Server:
    def __init__(
        self, config: ServerConfig,
        file_config: FileConfig, rudp_config: ReliableUdpConfig,
    ) -> None:
        self._config = config
        self._file_cfg = file_config
        self._rudp_cfg = rudp_config
        self._handler = CommandHandler()
        self._store = LocalFileStore(file_config.storage_dir)

    def run(self) -> None:
        sock = create_udp_socket(self._config.host, self._config.port)
        rudp = ReliableUdp(sock, self._rudp_cfg)
        print(f"Lab 2 Reliable UDP Server on {self._config.host}:{self._config.port}")
        try:
            self._serve_loop(rudp)
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            rudp.close()

    def _serve_loop(self, rudp: ReliableUdp) -> None:
        while True:
            try:
                msg, addr = rudp.receive_message()
                print(f"Request from {addr}: {msg[:50]}")
                self._dispatch(msg, addr, rudp)
            except Exception as exc:
                print(f"Error handling request: {exc}")

    def _dispatch(
        self, message: str, addr: Tuple[str, int], rudp: ReliableUdp,
    ) -> None:
        cmd_type, args = parse_command(message)
        if cmd_type == CommandType.UPLOAD:
            self._handle_upload(args, addr, rudp)
        elif cmd_type == CommandType.DOWNLOAD:
            self._handle_download(args, addr, rudp)
        elif cmd_type == CommandType.CLOSE:
            rudp.send_message("BYE", addr)
        else:
            response = self._handler.process(cmd_type, args)
            rudp.send_message(response or "ERROR", addr)

    def _handle_upload(
        self, args: str, addr: Tuple[str, int], rudp: ReliableUdp,
    ) -> None:
        parts = args.split()
        if len(parts) < 2:
            rudp.send_message("ERROR Usage: UPLOAD filename filesize", addr)
            return
        filename, file_size = parts[0], int(parts[1])
        offset = self._store.get_size(filename)
        rudp.send_message(f"READY {offset}", addr)
        self._receive_and_store(rudp, filename, offset, addr)

    def _receive_and_store(
        self, rudp: ReliableUdp, filename: str,
        offset: int, addr: Tuple[str, int],
    ) -> None:
        metrics = TransferMetrics()
        metrics.start()
        data, _ = rudp.receive_data()
        self._store.write_bytes(filename, offset, data)
        metrics.add_bytes(len(data))
        metrics.stop()
        rudp.send_message(f"OK {metrics.summary()}", addr)
        print(f"Upload complete: {filename} - {metrics.summary()}")

    def _handle_download(
        self, args: str, addr: Tuple[str, int], rudp: ReliableUdp,
    ) -> None:
        parts = args.split()
        filename = parts[0]
        offset = int(parts[1]) if len(parts) > 1 else 0
        if not self._store.exists(filename):
            rudp.send_message("ERROR File not found", addr)
            return
        remaining = self._store.get_size(filename) - offset
        rudp.send_message(f"SIZE {remaining}", addr)
        self._send_stored_file(rudp, filename, offset, remaining, addr)

    def _send_stored_file(
        self, rudp: ReliableUdp, filename: str,
        offset: int, remaining: int, addr: Tuple[str, int],
    ) -> None:
        file_data = self._store.read_bytes(filename, offset, remaining)
        metrics = TransferMetrics()
        metrics.start()
        rudp.send_data(file_data, addr)
        metrics.add_bytes(len(file_data))
        metrics.stop()
        print(f"Download complete: {filename} - {metrics.summary()}")


class Lab3Client:
    def __init__(
        self, config: ClientConfig, rudp_config: ReliableUdpConfig,
    ) -> None:
        self._config = config
        self._rudp_cfg = rudp_config

    def run(self) -> None:
        sock = create_udp_socket()
        rudp = ReliableUdp(sock, self._rudp_cfg)
        server_addr = (self._config.host, self._config.port)
        print(f"Lab 2 Reliable UDP Client -> {self._config.host}:{self._config.port}")
        print("Commands: ECHO <text>, TIME, UPLOAD <file>, DOWNLOAD <file>, CLOSE")
        try:
            self._command_loop(rudp, server_addr)
        except KeyboardInterrupt:
            print("\nDisconnected.")
        finally:
            rudp.close()

    def _command_loop(
        self, rudp: ReliableUdp, server: Tuple[str, int],
    ) -> None:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            cmd_type, args = parse_command(user_input)
            if not self._execute(cmd_type, args, user_input, rudp, server):
                break

    def _execute(
        self, cmd_type: CommandType, args: str, raw: str,
        rudp: ReliableUdp, server: Tuple[str, int],
    ) -> bool:
        if cmd_type == CommandType.UPLOAD:
            self._upload(args, rudp, server)
            return True
        if cmd_type == CommandType.DOWNLOAD:
            self._download(args, rudp, server)
            return True
        rudp.send_message(raw, server)
        response, _ = rudp.receive_message()
        print(response)
        return cmd_type != CommandType.CLOSE

    def _upload(
        self, filename: str, rudp: ReliableUdp, server: Tuple[str, int],
    ) -> None:
        if not os.path.exists(filename):
            print(f"File not found: {filename}")
            return
        file_size = os.path.getsize(filename)
        offset = self._negotiate_upload(rudp, server, filename, file_size)
        if offset is None:
            return
        self._transmit_file(rudp, server, filename, offset)

    def _negotiate_upload(
        self, rudp: ReliableUdp, server: Tuple[str, int],
        filename: str, file_size: int,
    ) -> Optional[int]:
        rudp.send_message(
            f"UPLOAD {os.path.basename(filename)} {file_size}", server,
        )
        response, _ = rudp.receive_message()
        if not response.startswith("READY"):
            print(f"Server: {response}")
            return None
        return int(response.split()[1])

    def _transmit_file(
        self, rudp: ReliableUdp, server: Tuple[str, int],
        filename: str, offset: int,
    ) -> None:
        with open(filename, "rb") as f:
            f.seek(offset)
            file_data = f.read()
        metrics = TransferMetrics()
        metrics.start()
        rudp.send_data(file_data, server)
        metrics.add_bytes(len(file_data))
        metrics.stop()
        result, _ = rudp.receive_message()
        print(f"Upload: {metrics.summary()}")
        print(f"Server: {result}")

    def _download(
        self, filename: str, rudp: ReliableUdp, server: Tuple[str, int],
    ) -> None:
        offset = os.path.getsize(filename) if os.path.exists(filename) else 0
        remaining = self._negotiate_download(rudp, server, filename, offset)
        if remaining is None:
            return
        self._receive_file(rudp, filename, offset)

    def _negotiate_download(
        self, rudp: ReliableUdp, server: Tuple[str, int],
        filename: str, offset: int,
    ) -> Optional[int]:
        rudp.send_message(
            f"DOWNLOAD {os.path.basename(filename)} {offset}", server,
        )
        response, _ = rudp.receive_message()
        if response.startswith("ERROR"):
            print(response)
            return None
        return int(response.split()[1])

    def _receive_file(
        self, rudp: ReliableUdp, filename: str, offset: int,
    ) -> None:
        metrics = TransferMetrics()
        metrics.start()
        file_data, _ = rudp.receive_data()
        metrics.add_bytes(len(file_data))
        metrics.stop()
        self._save_file(filename, offset, file_data)
        print(f"Download: {metrics.summary()}")

    def _save_file(self, filename: str, offset: int, data: bytes) -> None:
        mode = "r+b" if offset > 0 else "wb"
        with open(filename, mode) as f:
            f.seek(offset)
            f.write(data)


class Lab3Runner(LabRunner):
    def __init__(
        self, server_config: ServerConfig, client_config: ClientConfig,
        file_config: FileConfig, rudp_config: ReliableUdpConfig,
    ) -> None:
        self._server_cfg = server_config
        self._client_cfg = client_config
        self._file_cfg = file_config
        self._rudp_cfg = rudp_config

    def run_server(self) -> None:
        Lab3Server(self._server_cfg, self._file_cfg, self._rudp_cfg).run()

    def run_client(self) -> None:
        Lab3Client(self._client_cfg, self._rudp_cfg).run()
