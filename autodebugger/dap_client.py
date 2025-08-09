from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


HEADER_SEP = b"\r\n\r\n"


def _read_headers(sock: socket.socket) -> Tuple[Dict[str, str], bytes]:
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("DAP connection closed while reading headers")
        buf.extend(chunk)
        idx = buf.find(HEADER_SEP)
        if idx != -1:
            header_block = bytes(buf[:idx])
            rest = bytes(buf[idx + len(HEADER_SEP):])
            break
    headers: Dict[str, str] = {}
    for line in header_block.split(b"\r\n"):
        if not line:
            continue
        key, _, value = line.partition(b": ")
        headers[key.decode()] = value.decode()
    return headers, rest


def _read_content(sock: socket.socket, length: int, initial: bytes = b"") -> bytes:
    data = bytearray(initial)
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("DAP connection closed while reading content")
        data.extend(chunk)
    return bytes(data)


@dataclass
class DapMessage:
    type: str  # request | response | event
    seq: int
    body: Dict[str, Any]
    command: Optional[str] = None
    event: Optional[str] = None
    request_seq: Optional[int] = None
    success: Optional[bool] = None


class DapClient:
    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self._seq = 1
        self._lock = threading.Lock()
        self._responses: Dict[int, DapMessage] = {}
        self._listener: Optional[threading.Thread] = None
        self._events: list[DapMessage] = []
        self._running = False

    def connect(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(1.0)
        self.sock = s
        self._running = True
        self._listener = threading.Thread(target=self._listen, daemon=True)
        self._listener.start()

    def close(self) -> None:
        self._running = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _listen(self) -> None:
        assert self.sock is not None
        buffer = bytearray()
        while self._running:
            try:
                try:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        # connection closed
                        break
                    buffer.extend(chunk)
                except (socket.timeout, TimeoutError):
                    # no data this tick; try to parse what we have
                    pass

                # parse as many complete messages as available
                while True:
                    header_end = buffer.find(HEADER_SEP)
                    if header_end == -1:
                        break  # need more data for headers
                    header_block = bytes(buffer[:header_end])
                    # parse headers
                    headers: Dict[str, str] = {}
                    for line in header_block.split(b"\r\n"):
                        if not line:
                            continue
                        key, _, value = line.partition(b": ")
                        headers[key.decode()] = value.decode()
                    content_length = int(headers.get("Content-Length", "0"))
                    total_len = header_end + len(HEADER_SEP) + content_length
                    if len(buffer) < total_len:
                        # wait for full content
                        break
                    # extract content
                    payload = bytes(buffer[header_end + len(HEADER_SEP) : total_len])
                    # remove processed bytes
                    del buffer[:total_len]

                    # decode message
                    try:
                        msg = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    dm = DapMessage(
                        type=msg.get("type"),
                        seq=msg.get("seq"),
                        body=msg.get("body") or {},
                        command=msg.get("command"),
                        event=msg.get("event"),
                        request_seq=msg.get("request_seq"),
                        success=msg.get("success"),
                    )
                    if dm.type == "response" and dm.request_seq is not None:
                        with self._lock:
                            self._responses[dm.request_seq] = dm
                    elif dm.type == "event":
                        self._events.append(dm)
                    elif dm.type == "request":
                        # Minimal handling for reverse requests (e.g., runInTerminal)
                        cmd = dm.command or ""
                        if cmd == "runInTerminal":
                            # Reply not supported to let adapter fallback
                            self.send_response(request_seq=dm.seq, command=cmd, success=False, body={}, message="runInTerminal not supported by client")
                        else:
                            self.send_response(request_seq=dm.seq, command=cmd, success=False, body={}, message="Request not supported by client")
                    # else: ignore unexpected types
            except Exception:
                if self._running:
                    self._running = False
                break

    def _send(self, payload: Dict[str, Any]) -> int:
        assert self.sock is not None
        with self._lock:
            seq = self._seq
            self._seq += 1
        payload = {"seq": seq, "type": "request", **payload}
        raw = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
        self.sock.sendall(header + raw)
        return seq

    def _send_raw(self, payload: Dict[str, Any]) -> None:
        assert self.sock is not None
        raw = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
        self.sock.sendall(header + raw)

    def send_response(self, request_seq: int, command: str, success: bool, body: Optional[Dict[str, Any]] = None, message: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            "type": "response",
            "request_seq": request_seq,
            "success": success,
            "command": command,
        }
        if body is not None:
            payload["body"] = body
        if message is not None:
            payload["message"] = message
        self._send_raw(payload)

    def send_request(self, command: str, arguments: Optional[Dict[str, Any]] = None) -> int:
        if arguments is None:
            arguments = {}
        return self._send({"command": command, "arguments": arguments})

    def wait_response(self, seq: int, wait: float = 10.0) -> DapMessage:
        start = time.time()
        while time.time() - start < wait:
            with self._lock:
                dm = self._responses.pop(seq, None)
            if dm is not None:
                return dm
            time.sleep(0.01)
        raise TimeoutError(f"Timed out waiting for DAP response seq={seq}")

    def request(self, command: str, arguments: Optional[Dict[str, Any]] = None, wait: float = 10.0) -> DapMessage:
        if arguments is None:
            arguments = {}
        # Send once with arguments
        assert self.sock is not None
        seq = self.send_request(command, arguments)
        return self.wait_response(seq, wait=wait)

    def pop_events(self) -> list[DapMessage]:
        evs = list(self._events)
        self._events.clear()
        return evs
