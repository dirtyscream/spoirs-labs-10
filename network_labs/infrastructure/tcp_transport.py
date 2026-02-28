import socket
from typing import Optional

from network_labs.domain.interfaces import MessageTransport

DELIMITER = b"\n"
LINE_ENDING = "\r\n"
RECV_SIZE = 4096


class TcpTransport(MessageTransport):
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    def send_message(self, message: str) -> None:
        self._sock.sendall((message + LINE_ENDING).encode())

    def receive_message(self) -> Optional[str]:
        while True:
            idx = self._buffer.find(DELIMITER)
            if idx >= 0:
                return self._extract_line(idx)
            chunk = self._safe_recv()
            if not chunk:
                return self._flush_remaining()
            self._buffer += chunk

    def send_bytes(self, data: bytes) -> None:
        self._sock.sendall(data)

    def receive_bytes(self, count: int) -> bytes:
        result = bytearray()
        buffered = min(count, len(self._buffer))
        if buffered:
            result.extend(self._buffer[:buffered])
            self._buffer = self._buffer[buffered:]
            count -= buffered
        while count > 0:
            chunk = self._safe_recv(min(count, RECV_SIZE))
            if not chunk:
                break
            result.extend(chunk)
            count -= len(chunk)
        return bytes(result)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def _safe_recv(self, size: int = RECV_SIZE) -> bytes:
        try:
            return self._sock.recv(size)
        except (ConnectionError, OSError):
            return b""

    def _extract_line(self, idx: int) -> str:
        line = self._buffer[:idx].rstrip(b"\r")
        self._buffer = self._buffer[idx + 1:]
        return line.decode()

    def _flush_remaining(self) -> Optional[str]:
        if self._buffer:
            remaining = self._buffer.decode()
            self._buffer = b""
            return remaining
        return None


def create_server_socket(host: str, port: int, backlog: int = 5) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(backlog)
    return sock


def create_client_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    return sock


def enable_keepalive(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
