from __future__ import annotations

import subprocess
import sys


def test_synapse_module_prints_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "synapse"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Synapse - AI-powered coding agent CLI" in result.stdout
