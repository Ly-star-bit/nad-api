"""Offline tests for the upstream client (local sockets only, no network)."""

import asyncio
import http.server
import socket
import threading
import time

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


def test_failed_call_is_recorded_for_health():
    async def go():
        up = Upstream()
        try:
            with pytest.raises(UpstreamError):
                await up._get_json("census_geocoder", f"https://127.0.0.1:{_resetting_server()}/", {})
            return up.status
        finally:
            await up.aclose()

    status = asyncio.run(go())
    assert status["census_geocoder"]["ok"] is False
    assert "ConnectionResetError" in status["census_geocoder"]["error"]


def test_identical_requests_in_flight_share_one_call():
    hits = []

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            time.sleep(0.2)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/q"

    async def go():
        up = Upstream()
        try:
            same = await asyncio.gather(*(up._get_json("nad", url, {"a": 1}) for _ in range(5)))
            other = await up._get_json("nad", url, {"a": 2})
            cached = await up._get_json("nad", url, {"a": 1})
            return same, other, cached
        finally:
            await up.aclose()

    try:
        same, other, cached = asyncio.run(go())
    finally:
        srv.shutdown()
    assert all(r == {"ok": True} for r in same) and other == cached == {"ok": True}
    assert len(hits) == 2  # five identical concurrent calls -> one request; the different one -> one; then cache


def test_null_text_from_partner_exports_is_empty():
    # Alabama 911 Board records: AddNo_Full "28869 <Null>", AddNum_Suf "<Null>"
    from app.upstream import nad_record
    rec = nad_record({"Add_Number": 28869, "AddNo_Full": "28869 <Null>", "AddNum_Suf": "<Null>",
                      "St_Name": "Fanning", "StNam_Full": "Fanning Drive Northwest", "State": "AL"})
    assert rec["address_number"] == "28869"
    assert rec["address_number_suffix"] is None
