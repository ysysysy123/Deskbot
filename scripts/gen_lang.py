from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "firmware" / "atk-dnesp32s3-eye-uart" / "source" / "xiaozhi-esp32" / "scripts" / "gen_lang.py"

runpy.run_path(str(TARGET), run_name="__main__")
