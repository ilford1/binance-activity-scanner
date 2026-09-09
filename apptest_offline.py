"""Run the real app against isolated synthetic exchange/history fixtures."""
import pytest
if __name__ == '__main__':
    raise SystemExit(pytest.main(['-q','tests/test_app.py']))
