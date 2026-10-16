"""Offline tests for upstream error reporting (local sockets only, no network)."""

import asyncio
import socket
import threading

import pytest

from app.upstream import Upstream, UpstreamError


def _resetting_server() -> int:
    """Accepts, reads the TLS ClientHello, then resets the connection - what a firewall in the path does."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def loop():
        while True:
            conn, _ = srv.accept()
            conn.recv(1024)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return srv.getsockname()[1]


def test_reset_during_tls_handshake_is_named():
    async def go():
        up = Upstream()
        try:
            await up._get_json("census", f"https://127.0.0.1:{_resetting_server()}/", {})
        finally:
            await up.aclose()

    with pytest.raises(UpstreamError) as info:
        asyncio.run(go())
    # httpx's own message is empty here; the root cause has to make it into what the user sees
    assert "ConnectionResetError" in info.value.message
    assert "reset" in info.value.message.lower()
