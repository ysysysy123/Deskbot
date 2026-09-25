from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "firmware" / "lichuang-s3" / "source" / "xiaozhi-esp32" / "scripts" / "build_default_assets.py"

runpy.run_path(str(TARGET), run_name="__main__")
