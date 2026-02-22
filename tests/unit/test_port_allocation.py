"""Unit tests for port allocation (T4.2)."""

import socket

import pytest

from backend.project_service.services.docker_manager import (
    find_free_port,
    PORT_RANGE_START,
    PORT_RANGE_END,
)


class TestFindFreePort:
    """T4.2: Find free port avoids conflicts."""

    def test_returns_port_in_expected_range(self):
        port = find_free_port()
        assert PORT_RANGE_START <= port <= PORT_RANGE_END

    def test_never_returns_already_bound_port(self):
        """Bind a socket to a known port, verify find_free_port skips it."""
        bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound.bind(("0.0.0.0", 30000))
        bound.listen(1)
        try:
            for _ in range(20):
                port = find_free_port(start=30000, end=30020)
                assert port != 30000
        finally:
            bound.close()

    def test_no_duplicates_when_ports_held(self):
        """Bind each returned port before calling again -- all must differ."""
        held_sockets = []
        ports = []
        try:
            for _ in range(5):
                port = find_free_port(start=40000, end=40100)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                s.listen(1)
                held_sockets.append(s)
                ports.append(port)
            assert len(set(ports)) == 5
        finally:
            for s in held_sockets:
                s.close()

    def test_raises_when_range_exhausted(self):
        """All ports in a tiny range bound -- should raise RuntimeError."""
        held = []
        try:
            for p in range(50000, 50003):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", p))
                s.listen(1)
                held.append(s)
            with pytest.raises(RuntimeError, match="No free port"):
                find_free_port(start=50000, end=50002)
        finally:
            for s in held:
                s.close()
