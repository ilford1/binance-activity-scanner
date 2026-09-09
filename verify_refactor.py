"""Run behavioral verification; no source-string assertions."""
import pytest
if __name__ == '__main__':
    raise SystemExit(pytest.main(['-q','tests']))
