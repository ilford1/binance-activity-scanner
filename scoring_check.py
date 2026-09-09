"""Compatibility entry point for behavioral scoring and history checks."""
import pytest
if __name__ == '__main__':
    raise SystemExit(pytest.main(['-q','tests/test_scoring.py','tests/test_history.py']))
