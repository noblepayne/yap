"""Pytest fixtures for integration tests.

Imports ChatServer and helpers from helpers.py (which is importable as a
regular module). conftest.py itself is auto-loaded by pytest and provides
the chat_server fixture.
"""

from helpers import ChatServer  # re-exported for fixture use

import pytest


@pytest.fixture
def chat_server():
    """Empty server; configure via chat_server.chunks before making requests."""
    server = ChatServer().start()
    yield server
    server.stop()
