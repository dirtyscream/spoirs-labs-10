import socket
import struct
import time
from typing import Dict, List, Set, Tuple

from network_labs.config import ReliableUdpConfig
from network_labs.domain.interfaces import ReliableTransport

HEADER_FORMAT = "!IB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

FLAG_DATA = 0x01
FLAG_ACK = 0x02
FLAG_FIN = 0x04

SOCKET_BUF_SIZE = 1048576


def encode_packet(seq: int, flags: int, payload: bytes = b"") -> bytes:
    return struct.pack(HEADER_FORMAT, seq, flags) + payload


def decode_packet(raw: bytes) -> Tuple[int, int, bytes]:
    seq, flags = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])
    return seq, flags, raw[HEADER_SIZE:]


def split_chunks(data: bytes, max_size: int) -> List[bytes]:
    return [data[i:i + max_size] for i in range(0, len(data), max_size)]


def _prebuild_packets(chunks: List[bytes]) -> List[bytes]:
    return [encode_packet(i, FLAG_DATA, c) for i, c in enumerate(chunks)]


class ReliableUdp(ReliableTransport):
    def __init__(
        self,
        sock: socket.socket,
        config: ReliableUdpConfig,
        owns_socket: bool = True,
    ) -> None:
        self._sock = sock
        self._max_payload = config.packet_size - HEADER_SIZE
        self._window = config.window_size
        self._timeout = config.ack_timeout
        self._max_retries = config.max_retries
        self._owns_socket = owns_socket

    def send_message(self, message: str, address: Tuple[str, int]) -> None:
        packet = encode_packet(0, FLAG_DATA, message.encode())
        self._send_with_ack(packet, address)

    def receive_message(self) -> Tuple[str, Tuple[str, int]]:
        self._sock.settimeout(None)
        while True:
            raw, addr = self._sock.recvfrom(65535)
            seq, flags, payload = decode_packet(raw)
            if flags & FLAG_DATA:
                self._send_ack(seq, addr)
                return payload.decode(), addr

    def send_data(self, data: bytes, address: Tuple[str, int]) -> None:
        chunks = split_chunks(data, self._max_payload)
        if not chunks:
            chunks = [b""]
        packets = _prebuild_packets(chunks)
        self._send_windowed(packets, address)
        fin = encode_packet(len(chunks), FLAG_FIN)
        self._send_with_ack(fin, address)

    def receive_data(self) -> Tuple[bytes, Tuple[str, int]]:
        received: Dict[int, bytes] = {}
        source = None
        self._sock.settimeout(None)
        while True:
            raw, addr = self._sock.recvfrom(65535)
            seq, flags, payload = decode_packet(raw)
            source = source or addr
            if flags & FLAG_FIN:
                self._send_ack(seq, addr)
                break
            if flags & FLAG_DATA:
                received[seq] = payload
                self._send_ack(seq, addr)
        return b"".join(v for _, v in sorted(received.items())), source

    def close(self) -> None:
        if self._owns_socket:
            self._sock.close()

    def _send_windowed(
        self, packets: List[bytes], address: Tuple[str, int],
    ) -> None:
        total = len(packets)
        base = 0
        next_seq = 0
        acked: Set[int] = set()
        retries = 0
        while base < total:
            next_seq = self._fill_window(
                packets, base, next_seq, total, address)
            prev_base = base
            base = self._advance_base(base, acked)
            if base == prev_base:
                retries += 1
                if retries > self._max_retries:
                    raise TimeoutError("Windowed transfer: max retries")
                self._resend_unacked(
                    packets, base, next_seq, acked, address)
            else:
                retries = 0

    def _fill_window(
        self, packets: List[bytes], base: int, next_seq: int,
        total: int, address: Tuple[str, int],
    ) -> int:
        end = min(total, base + self._window)
        while next_seq < end:
            self._sock.sendto(packets[next_seq], address)
            next_seq += 1
        return next_seq

    def _advance_base(self, base: int, acked: Set[int]) -> int:
        self._sock.settimeout(self._timeout)
        try:
            base = self._recv_ack(base, acked)
        except socket.timeout:
            return base
        return self._drain_pending(base, acked)

    def _drain_pending(self, base: int, acked: Set[int]) -> int:
        self._sock.settimeout(0)
        try:
            while True:
                base = self._recv_ack(base, acked)
        except (BlockingIOError, socket.timeout, OSError):
            pass
        return base

    def _recv_ack(self, base: int, acked: Set[int]) -> int:
        raw, _ = self._sock.recvfrom(65535)
        seq, flags, _ = decode_packet(raw)
        if flags & FLAG_ACK:
            acked.add(seq)
            while base in acked:
                acked.discard(base)
                base += 1
        return base

    def _resend_unacked(
        self, packets: List[bytes], base: int, next_seq: int,
        acked: Set[int], address: Tuple[str, int],
    ) -> None:
        for seq in range(base, next_seq):
            if seq not in acked:
                self._sock.sendto(packets[seq], address)

    def _send_with_ack(self, packet: bytes, address: Tuple[str, int]) -> None:
        for _ in range(self._max_retries):
            self._sock.sendto(packet, address)
            if self._wait_for_ack():
                return
        raise TimeoutError("No ACK received after max retries")

    def _wait_for_ack(self) -> bool:
        self._sock.settimeout(self._timeout)
        while True:
            try:
                raw, _ = self._sock.recvfrom(65535)
                _, flags, _ = decode_packet(raw)
                if flags & FLAG_ACK:
                    return True
            except socket.timeout:
                return False

    def _send_ack(self, seq: int, address: Tuple[str, int]) -> None:
        self._sock.sendto(encode_packet(seq, FLAG_ACK), address)


def create_udp_socket(host: str = "", port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _enlarge_buffers(sock)
    sock.bind((host, port))
    return sock


def _enlarge_buffers(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUF_SIZE)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUF_SIZE)
    except OSError:
        pass
