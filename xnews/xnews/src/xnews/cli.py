"""Command line entry point.

  xnews watch            poll all accounts in config.toml forever
  xnews once             poll every account one time and exit
  xnews recent [handle]  show stored posts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import load_config
from .monitor import Monitor, console_printer, jsonl_writer
from .sources import SourceError, build_source
from .store import Store


async def _run(cfg, forever: bool) -> None:
    source = build_source(cfg.source, cfg.source_settings)
    store = Store(cfg.db_path)
    callbacks = [console_printer(cfg.keywords)]
    if cfg.jsonl_path:
        callbacks.append(jsonl_writer(cfg.jsonl_path))
    mon = Monitor(cfg, source, store, callbacks)
    try:
        if forever:
            await mon.run_forever()
        else:
            n = await mon.poll_once()
            print(f"\n{n} new post(s).")
    finally:
        await source.aclose()
        store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xnews", description="Monitor X accounts for news.")
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("watch", help="poll forever")
    sub.add_parser("once", help="poll once and exit")
    rec = sub.add_parser("recent", help="show stored posts")
    rec.add_argument("account", nargs="?")
    rec.add_argument("-n", type=int, default=20)
    rec.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))

    if args.cmd == "recent":
        store = Store(cfg.db_path)
        rows = store.recent(args.account and args.account.lstrip("@"), args.n)
        for r in reversed(rows):
            if args.json:
                print(json.dumps(r, ensure_ascii=False))
            else:
                print(f"\n@{r['account']} {r['created_at']}\n{r['text']}\n{r['url']}")
        return

    try:
        asyncio.run(_run(cfg, forever=args.cmd == "watch"))
    except KeyboardInterrupt:
        print("\nStopped.")
    except SourceError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
