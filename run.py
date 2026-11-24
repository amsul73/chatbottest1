#!/usr/bin/env python3
"""치지직 방송을 듣고 채팅을 쳐주는 봇.

  python run.py              # 실행
  python run.py --check      # 자체 점검 (처음에 이걸 먼저)
  python run.py --dry-run    # 채팅을 보내지 않고 로그만
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from chatbot.app import ChatBotApp  # noqa: E402
from chatbot.config import ConfigError, load_config  # noqa: E402
from chatbot.log import setup_logging  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="치지직 방송을 듣고 채팅을 쳐주는 봇",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    parser.add_argument("--env", default=".env", help="환경변수 파일 경로")
    parser.add_argument(
        "--check",
        action="store_true",
        help="실행 전에 환경과 치지직 접속을 점검한다",
    )
    parser.add_argument(
        "--no-llm-check",
        action="store_true",
        help="--check 에서 Claude 실제 호출을 건너뛴다",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="채팅을 실제로 보내지 않고 로그에만 남긴다 (설정값보다 우선)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="실제로 채팅을 보낸다 (설정값보다 우선)",
    )
    parser.add_argument("--debug", action="store_true", help="자세한 로그")
    return parser.parse_args()


async def amain(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config, args.env)
    except ConfigError as exc:
        print(f"\n설정 오류\n{exc}\n", file=sys.stderr)
        return 2

    if args.dry_run and args.send:
        print("--dry-run 과 --send 를 같이 쓸 수 없습니다", file=sys.stderr)
        return 2
    if args.dry_run:
        cfg.bot.dry_run = True
    if args.send:
        cfg.bot.dry_run = False

    setup_logging("DEBUG" if args.debug else cfg.log.level, cfg.log.file)

    if args.check:
        from chatbot.doctor import run_doctor

        ok = await run_doctor(cfg, check_llm=not args.no_llm_check)
        return 0 if ok else 1

    app = ChatBotApp(cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, app.stop)

    await app.run()
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n종료합니다")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
