"""T7.1: URL parsing extracts project_id and token."""

from backend.terminal_proxy.proxy import parse_ws_url


class TestParseWsUrl:
    """T7.1: URL parsing extracts project_id and token."""

    def test_extracts_project_id_and_token(self):
        """T7.1 case 1: /terminal/abc-123?token=eyJhbG..."""
        project_id, token = parse_ws_url("/terminal/abc-123?token=eyJhbG...")
        assert project_id == "abc-123"
        assert token == "eyJhbG..."

    def test_extracts_project_id_without_token(self):
        """T7.1 case 2: /terminal/abc-123 (no token)."""
        project_id, token = parse_ws_url("/terminal/abc-123")
        assert project_id == "abc-123"
        assert token is None

    def test_invalid_path_returns_none(self):
        """T7.1 case 3: /invalid/path."""
        project_id, token = parse_ws_url("/invalid/path")
        assert project_id is None
        assert token is None

    def test_root_path_returns_none(self):
        project_id, token = parse_ws_url("/")
        assert project_id is None
        assert token is None

    def test_empty_path_returns_none(self):
        project_id, token = parse_ws_url("")
        assert project_id is None
        assert token is None
