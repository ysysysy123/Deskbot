"""Run the local browser voice tester without loading ASR/LLM/TTS models."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Deskbot 本地语音测试页面")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    args = parser.parse_args()

    from aiohttp import web
    from voice_server.config import load_config
    from voice_server.local_testing import create_local_test_app

    config = load_config(args.config)
    print(f"浏览器测试页：http://{config.local_test.host}:{config.local_test.port}/")
    print("测试页连接真实语音服务；请另开终端运行 run.py。Ctrl+C 停止测试页。")
    web.run_app(create_local_test_app(config), host=config.local_test.host, port=config.local_test.port, access_log=None)


if __name__ == "__main__":
    main()
