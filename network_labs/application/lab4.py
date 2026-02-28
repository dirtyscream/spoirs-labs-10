import os
import socket
from typing import Optional, Tuple

from network_labs.config import Lab4Config, FileConfig, ReliableUdpConfig
from network_labs.domain.commands import parse_command, CommandType
from network_labs.domain.interfaces import LabRunner
from network_labs.domain.metrics import TransferMetrics
from network_labs.infrastructure.reliable_udp import ReliableUdp, create_udp_socket
from network_labs.infrastructure.file_storage import LocalFileStore
from network_labs.infrastructure.dynamic_pool import DynamicThreadPool
from network_labs.application.handlers import CommandHandler


class Lab4Server:
    def __init__(
        self, config: Lab4Config,
        file_config: FileConfig, rudp_config: ReliableUdpConfig,
    ) -> None:
        self._config = config
        self._file_cfg = file_config
        self._rudp_cfg = rudp_config
        self._store = LocalFileStore(file_config.storage_dir)
        self._pool = DynamicThreadPool(config.pool)
        self._handler = CommandHandler()

    def run(self) -> None:
        sock = create_udp_socket(self._config.host, self._config.port)
        self._pool.start()
        print(f"Lab 4 Server on {self._config.host}:{self._config.port}")
        print(f"Pool: {self._pool.stats}")
        try:
            self._listen_loop(sock)
        except KeyboardInterrupt:
            print("\nStopping server...")
        finally:
            self._pool.shutdown()
            sock.close()

    def _listen_loop(self, sock: socket.socket) -> None:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except OSError:
                continue
            self._pool.submit(
                lambda d=data, a=addr, s=sock: self._handle_request(d, a, s)
            )

    def _handle_request(
        self, data: bytes, addr: Tuple[str, int], sock: socket.socket,
    ) -> None:
        message = data.decode()
        cmd_type, args = parse_command(message)
        print(f"[{cmd_type.name}] from {addr}")
        if cmd_type == CommandType.UPLOAD:
            self._handle_upload(args, addr, sock)
        elif cmd_type == CommandType.DOWNLOAD:
            self._handle_download(args, addr, sock)
        else:
            response = self._handler.process(cmd_type, args)
            self._reply(sock, addr, response or "BYE")

    def _reply(
        self, sock: socket.socket, addr: Tuple[str, int], message: str,
    ) -> None:
        sock.sendto(message.encode(), addr)

    def _handle_upload(
        self, args: str, addr: Tuple[str, int], sock: socket.socket,
    ) -> None:
        parts = args.split()
        if len(parts) < 2:
            self._reply(sock, addr, "ERROR Usage: UPLOAD filename filesize")
            return
        filename, file_size = parts[0], int(parts[1])
        offset = self._store.get_size(filename)
        transfer_sock = create_udp_socket("0.0.0.0", 0)
        transfer_port = transfer_sock.getsockname()[1]
        self._reply(sock, addr, f"READY {offset} {transfer_port}")
        self._receive_file(transfer_sock, filename, offset)

    def _receive_file(
        self, transfer_sock: socket.socket, filename: str, offset: int,
    ) -> None:
        rudp = ReliableUdp(transfer_sock, self._rudp_cfg)
        try:
            metrics = TransferMetrics()
            metrics.start()
            data, sender = rudp.receive_data()
            self._store.write_bytes(filename, offset, data)
            metrics.add_bytes(len(data))
            metrics.stop()
            rudp.send_message(f"OK {metrics.summary()}", sender)
            print(f"[upload] {filename} - {metrics.summary()}")
        finally:
            rudp.close()

    def _handle_download(
        self, args: str, addr: Tuple[str, int], sock: socket.socket,
    ) -> None:
        parts = args.split()
        filename = parts[0] if parts else ""
        offset = int(parts[1]) if len(parts) > 1 else 0
        if not self._store.exists(filename):
            self._reply(sock, addr, "ERROR File not found")
            return
        remaining = self._store.get_size(filename) - offset
        transfer_sock = create_udp_socket("0.0.0.0", 0)
        transfer_port = transfer_sock.getsockname()[1]
        self._reply(sock, addr, f"SIZE {remaining} {transfer_port}")
        self._send_file(transfer_sock, filename, offset, remaining, addr)

    def _send_file(
        self, transfer_sock: socket.socket, filename: str,
        offset: int, remaining: int, addr: Tuple[str, int],
    ) -> None:
        file_data = self._store.read_bytes(filename, offset, remaining)
        rudp = ReliableUdp(transfer_sock, self._rudp_cfg)
        try:
            metrics = TransferMetrics()
            metrics.start()
            rudp.send_data(file_data, addr)
            metrics.add_bytes(len(file_data))
            metrics.stop()
            print(f"[download] {filename} - {metrics.summary()}")
        finally:
            rudp.close()


class Lab4Client:
    def __init__(
        self, config: Lab4Config, rudp_config: ReliableUdpConfig,
    ) -> None:
        self._config = config
        self._rudp_cfg = rudp_config

    def run(self) -> None:
        sock = create_udp_socket()
        server = (self._config.client_host, self._config.port)
        print(f"Lab 4 Client -> {server[0]}:{server[1]}")
        print("Commands: ECHO <text>, TIME, UPLOAD <file>, DOWNLOAD <file>, CLOSE")
        try:
            self._command_loop(sock, server)
        except KeyboardInterrupt:
            print("\nDisconnected.")
        finally:
            sock.close()

    def _command_loop(
        self, sock: socket.socket, server: Tuple[str, int],
    ) -> None:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            cmd_type, args = parse_command(user_input)
            if cmd_type == CommandType.CLOSE:
                self._send_and_print(sock, user_input, server)
                break
            self._dispatch(cmd_type, args, user_input, sock, server)

    def _dispatch(
        self, cmd_type: CommandType, args: str, raw: str,
        sock: socket.socket, server: Tuple[str, int],
    ) -> None:
        if cmd_type == CommandType.UPLOAD:
            self._upload(sock, args, server)
        elif cmd_type == CommandType.DOWNLOAD:
            self._download(sock, args, server)
        else:
            self._send_and_print(sock, raw, server)

    def _send_and_print(
        self, sock: socket.socket, message: str, server: Tuple[str, int],
    ) -> None:
        sock.sendto(message.encode(), server)
        sock.settimeout(5.0)
        try:
            data, _ = sock.recvfrom(65535)
            print(data.decode())
        except socket.timeout:
            print("No response (timeout)")

    def _upload(
        self, sock: socket.socket, filename: str, server: Tuple[str, int],
    ) -> None:
        if not os.path.exists(filename):
            print(f"File not found: {filename}")
            return
        file_size = os.path.getsize(filename)
        transfer = self._request_upload(sock, filename, file_size, server)
        if transfer is None:
            return
        offset, transfer_addr = transfer
        self._send_file_data(sock, filename, offset, transfer_addr)

    def _request_upload(
        self, sock: socket.socket, filename: str,
        file_size: int, server: Tuple[str, int],
    ) -> Optional[Tuple[int, Tuple[str, int]]]:
        sock.sendto(
            f"UPLOAD {os.path.basename(filename)} {file_size}".encode(), server,
        )
        response = self._receive_text(sock)
        if not response or not response.startswith("READY"):
            print(f"Server: {response}")
            return None
        parts = response.split()
        offset = int(parts[1])
        transfer_port = int(parts[2])
        return offset, (server[0], transfer_port)

    def _send_file_data(
        self, sock: socket.socket, filename: str,
        offset: int, transfer_addr: Tuple[str, int],
    ) -> None:
        with open(filename, "rb") as f:
            f.seek(offset)
            file_data = f.read()
        rudp = ReliableUdp(sock, self._rudp_cfg, owns_socket=False)
        metrics = TransferMetrics()
        metrics.start()
        rudp.send_data(file_data, transfer_addr)
        metrics.add_bytes(len(file_data))
        metrics.stop()
        result, _ = rudp.receive_message()
        print(f"Upload: {metrics.summary()}")
        print(f"Server: {result}")

    def _download(
        self, sock: socket.socket, filename: str, server: Tuple[str, int],
    ) -> None:
        offset = os.path.getsize(filename) if os.path.exists(filename) else 0
        transfer = self._request_download(sock, filename, offset, server)
        if transfer is None:
            return
        remaining, transfer_addr = transfer
        self._receive_file_data(sock, filename, offset, transfer_addr)

    def _request_download(
        self, sock: socket.socket, filename: str,
        offset: int, server: Tuple[str, int],
    ) -> Optional[Tuple[int, Tuple[str, int]]]:
        sock.sendto(
            f"DOWNLOAD {os.path.basename(filename)} {offset}".encode(), server,
        )
        response = self._receive_text(sock)
        if not response or response.startswith("ERROR"):
            print(response or "No response")
            return None
        parts = response.split()
        remaining = int(parts[1])
        transfer_port = int(parts[2])
        return remaining, (server[0], transfer_port)

    def _receive_file_data(
        self, sock: socket.socket, filename: str,
        offset: int, transfer_addr: Tuple[str, int],
    ) -> None:
        rudp = ReliableUdp(sock, self._rudp_cfg, owns_socket=False)
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

    def _receive_text(self, sock: socket.socket) -> Optional[str]:
        sock.settimeout(5.0)
        try:
            data, _ = sock.recvfrom(65535)
            return data.decode()
        except socket.timeout:
            print("No response (timeout)")
            return None


class Lab4Runner(LabRunner):
    def __init__(
        self, config: Lab4Config,
        file_config: FileConfig, rudp_config: ReliableUdpConfig,
    ) -> None:
        self._config = config
        self._file_cfg = file_config
        self._rudp_cfg = rudp_config

    def run_server(self) -> None:
        Lab4Server(self._config, self._file_cfg, self._rudp_cfg).run()

    def run_client(self) -> None:
        Lab4Client(self._config, self._rudp_cfg).run()
