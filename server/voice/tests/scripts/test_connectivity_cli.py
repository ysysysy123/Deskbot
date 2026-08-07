import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", ["check_asr.py", "check_llm.py", "check_tts.py"])
def test_connectivity_script_help_does_not_load_provider(script):
    result = subprocess.run(
        [sys.executable, f"scripts/{script}", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
