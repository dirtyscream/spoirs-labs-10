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


def encode_packet(seq: int, flags: int, payload: bytes = b"") -> bytes:
    return struct.pack(HEADER_FORMAT, seq, flags) + payload


def decode_packet(raw: bytes) -> Tuple[int, int, bytes]:
    seq, flags = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])
    return seq, flags, raw[HEADER_SIZE:]


def split_chunks(data: bytes, max_size: int) -> List[bytes]:
    return [data[i:i + max_size] for i in range(0, len(data), max_size)]


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
        while True:
            seq, flags, payload, addr = self._recv_packet()
            if flags & FLAG_DATA:
                self._send_ack(seq, addr)
                return payload.decode(), addr

    def send_data(self, data: bytes, address: Tuple[str, int]) -> None:
        chunks = split_chunks(data, self._max_payload)
        if not chunks:
            chunks = [b""]
        self._send_windowed(chunks, address)
        fin = encode_packet(len(chunks), FLAG_FIN)
        self._send_with_ack(fin, address)

    def receive_data(self) -> Tuple[bytes, Tuple[str, int]]:
        received: Dict[int, bytes] = {}
        source = None
        while True:
            seq, flags, payload, addr = self._recv_packet()
            source = source or addr
            if flags & FLAG_FIN:
                self._send_ack(seq, addr)
                break
            if flags & FLAG_DATA:
                received[seq] = payload
                self._send_ack(seq, addr)
        ordered = sorted(received.items())
        return b"".join(v for _, v in ordered), source

    def close(self) -> None:
        if self._owns_socket:
            self._sock.close()

    def _send_windowed(self, chunks: List[bytes], address: Tuple[str, int]) -> None:
        total = len(chunks)
        base = 0
        next_seq = 0
        acked: Set[int] = set()
        stall_count = 0
        while base < total:
            next_seq = self._emit_window(
                chunks, base, next_seq, total, address)
            prev_base = base
            base = self._collect_acks(base, acked)
            stall_count = self._check_stall(base, prev_base, stall_count)
            self._resend_missing(base, next_seq, acked, chunks, address)

    def _check_stall(self, base: int, prev_base: int, stall_count: int) -> int:
        if base == prev_base:
            stall_count += 1
            if stall_count > self._max_retries:
                raise TimeoutError("Windowed transfer: max retries exceeded")
            return stall_count
        return 0

    def _emit_window(
        self, chunks: List[bytes], base: int, next_seq: int,
        total: int, address: Tuple[str, int],
    ) -> int:
        end = min(base + self._window, total)
        for i in range(next_seq, end):
            pkt = encode_packet(i, FLAG_DATA, chunks[i])
            self._sock.sendto(pkt, address)
        return max(next_seq, end)

    def _collect_acks(self, base: int, acked: Set[int]) -> int:
        deadline = time.time() + self._timeout * 2
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 0.01)
            self._sock.settimeout(remaining)
            try:
                raw, _ = self._sock.recvfrom(65535)
                seq, flags, _ = decode_packet(raw)
                if flags & FLAG_ACK:
                    acked.add(seq)
                    while base in acked:
                        acked.discard(base)
                        base += 1
            except socket.timeout:
                break
        return base

    def _resend_missing(
        self, base: int, next_seq: int, acked: Set[int],
        chunks: List[bytes], address: Tuple[str, int],
    ) -> None:
        for seq in range(base, next_seq):
            if seq not in acked:
                pkt = encode_packet(seq, FLAG_DATA, chunks[seq])
                self._sock.sendto(pkt, address)

    def _send_with_ack(self, packet: bytes, address: Tuple[str, int]) -> None:
        for _ in range(self._max_retries):
            self._sock.sendto(packet, address)
            if self._wait_for_ack():
                return
        raise TimeoutError("No ACK received after max retries")

    def _wait_for_ack(self) -> bool:
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 0.01)
            self._sock.settimeout(remaining)
            try:
                raw, _ = self._sock.recvfrom(65535)
                _, flags, _ = decode_packet(raw)
                if flags & FLAG_ACK:
                    return True
            except socket.timeout:
                return False
        return False

    def _send_ack(self, seq: int, address: Tuple[str, int]) -> None:
        self._sock.sendto(encode_packet(seq, FLAG_ACK), address)

    def _recv_packet(self) -> Tuple[int, int, bytes, Tuple[str, int]]:
        self._sock.settimeout(None)
        raw, addr = self._sock.recvfrom(65535)
        seq, flags, payload = decode_packet(raw)
        return seq, flags, payload, addr


def create_udp_socket(host: str = "", port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock
