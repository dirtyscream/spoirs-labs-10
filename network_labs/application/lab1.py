import socket
from typing import Optional

from network_labs.config import ServerConfig, ClientConfig
from network_labs.domain.commands import parse_command, CommandType
from network_labs.domain.interfaces import LabRunner
from network_labs.infrastructure.tcp_transport import (
    TcpTransport, create_server_socket, create_client_socket,
)
from network_labs.application.handlers import CommandHandler


class Lab1Server:
    def __init__(self, config: ServerConfig) -> None:
        self._config = config
        self._handler = CommandHandler()

    def run(self) -> None:
        sock = create_server_socket(
            self._config.host, self._config.port, self._config.backlog,
        )
        print(f"Lab 1 Server listening on {self._config.host}:{self._config.port}")
        try:
            self._accept_loop(sock)
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            sock.close()

    def _accept_loop(self, server_sock: socket.socket) -> None:
        while True:
            client_sock, addr = server_sock.accept()
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
            response = self._process(message)
            if response is None:
                break
            transport.send_message(response)

    def _process(self, raw: str) -> Optional[str]:
        cmd_type, args = parse_command(raw)
        return self._handler.process(cmd_type, args)


class Lab1Client:
    def __init__(self, config: ClientConfig) -> None:
        self._config = config

    def run(self) -> None:
        sock = create_client_socket(self._config.host, self._config.port)
        transport = TcpTransport(sock)
        print(f"Connected to {self._config.host}:{self._config.port}")
        print("Commands: ECHO <text>, TIME, CLOSE")
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
            if not self._handle_input(user_input, transport):
                break

    def _handle_input(self, user_input: str, transport: TcpTransport) -> bool:
        transport.send_message(user_input)
        cmd_type, _ = parse_command(user_input)
        if cmd_type == CommandType.CLOSE:
            print("Connection closed.")
            return False
        response = transport.receive_message()
        if response is None:
            print("Server closed connection.")
            return False
        print(response)
        return True


class Lab1Runner(LabRunner):
    def __init__(self, server_config: ServerConfig, client_config: ClientConfig) -> None:
        self._server_cfg = server_config
        self._client_cfg = client_config

    def run_server(self) -> None:
        Lab1Server(self._server_cfg).run()

    def run_client(self) -> None:
        Lab1Client(self._client_cfg).run()
