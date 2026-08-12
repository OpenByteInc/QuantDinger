"""Restrict FutuOpenD to loopback while exposing it to local Docker bridges."""

from __future__ import annotations

import os
import select
import socket
import socketserver


LISTEN = (
    os.getenv("OPEND_RELAY_BIND_HOST", "172.17.0.1"),
    int(os.getenv("OPEND_RELAY_PORT", "11112")),
)
UPSTREAM = (
    os.getenv("OPEND_HOST", "127.0.0.1"),
    int(os.getenv("OPEND_PORT", "11111")),
)


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection(UPSTREAM, timeout=5)
        except OSError:
            return
        with upstream:
            upstream.settimeout(None)
            sockets = (self.request, upstream)
            try:
                while True:
                    readable, _, _ = select.select(sockets, [], [])
                    for source in readable:
                        target = upstream if source is self.request else self.request
                        data = source.recv(65536)
                        if not data:
                            return
                        target.sendall(data)
            except (ConnectionError, OSError):
                return


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(
        f"OpenD relay listening on {LISTEN[0]}:{LISTEN[1]} "
        f"and forwarding to {UPSTREAM[0]}:{UPSTREAM[1]}",
        flush=True,
    )
    with RelayServer(LISTEN, RelayHandler) as server:
        server.serve_forever()
