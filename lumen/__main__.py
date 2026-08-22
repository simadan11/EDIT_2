"""
LUMEN — командная строка.

    python -m lumen serve [--host H] [--port P]   # платформа + веб-интерфейс
    python -m lumen desktop                       # настольная программа (окно)
    python -m lumen chat                          # диалог в терминале
    python -m lumen tools                         # список инструментов
    python -m lumen tools run <имя> [--arg k=v]   # запуск инструмента
    python -m lumen status                        # состояние платформы
    python -m lumen memory show|add|forget|recall # работа с памятью
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import BRAND_NAME, MODEL_NAME, TAGLINE, VERSION
from .config import LumenConfig, BASE_DIR, DATA_DIR
from .kernel.engine import create_engine


def _print_banner() -> None:
    print()
    print("  ◈ " + BRAND_NAME.upper() + f"  {VERSION}  ·  {TAGLINE}")
    print("    ядро " + MODEL_NAME + " · конвейер «Луч»")
    print()


def cmd_serve(args: argparse.Namespace) -> int:
    from .api.server import run_server
    _print_banner()
    run_server(host=args.host, port=args.port, block=True)
    return 0


def cmd_desktop(args: argparse.Namespace) -> int:
    from .desktop import run as run_desktop
    _print_banner()
    print("  Настольная программа: окно с диалогом, «LUMEN думает…»,")
    print("  голос (pip install pyttsx3) и самообучение каждую минуту.")
    return run_desktop()


def _engine() -> "tuple":
    cfg = LumenConfig()
    engine = create_engine(cfg)
    return cfg, engine


def cmd_chat(args: argparse.Namespace) -> int:
    cfg, engine = _engine()
    _print_banner()
    print(f"  модуль генерации: {engine.backend.display}")
    print("  /выход — завершить, /память — показать факты, /инструменты — список\n")
    sid = engine.memory.new_session("CLI-сессия")
    while True:
        try:
            text = input("Вы  › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/выход", "/exit", "/quit"):
            print("LUMEN: до связи. Сессия сохранена.")
            break
        if text == "/память":
            for cat, items in engine.memory.facts().items():
                for k, v in items.items():
                    print(f"  [{cat}] {k}: {v['value']}")
            continue
        if text == "/инструменты":
            for t in engine.registry.list():
                mark = "●" if t["available"] else "○"
                print(f"  {mark} {t['name']} — {t['description'][:80]}")
            continue
        resp = engine.process(text, session_id=sid)
        print(f"LUMEN › {resp.reply}\n")
        if resp.tools_used:
            used = ", ".join(f"{t['name']} ({'ок' if t['ok'] else 'ошибка'})"
                             for t in resp.tools_used)
            print(f"  ⚙ {used} · {resp.latency_ms} мс · {resp.intent}\n")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    cfg, engine = _engine()
    if args.tools_command == "run":
        args_map = {}
        for a in (args.arg or []):
            if "=" in a:
                k, v = a.split("=", 1)
                args_map[k] = v
        res = engine.registry.run(args.tools_name, args_map)
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1
    for t in engine.registry.list():
        mark = "●" if t["available"] else "○"
        extra = "" if t["available"] else f"  ({t['availability_reason']})"
        print(f"{mark} {t['name']:<28} {t['description'][:70]}{extra}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg, engine = _engine()
    _print_banner()
    b = engine.backend
    ok, reason = b.available()
    tools = engine.registry.list()
    print(f"  ядро            : {MODEL_NAME} (конвейер «Луч», 6 стадий)")
    print(f"  версия          : {VERSION}")
    print(f"  модуль генерации : {b.display} — {'доступен' if ok else 'недоступен: ' + reason}")
    print(f"  инструменты     : {sum(1 for t in tools if t['available'])} из {len(tools)} доступно")
    print(f"  фактов в памяти : {engine.memory.count()}")
    print(f"  сессий на диске : {len(list((BASE_DIR / 'lumen_data' / 'sessions').glob('*.jsonl'))) if (BASE_DIR / 'lumen_data' / 'sessions').exists() else 0}")
    print(f"  конфиг          : {cfg.path}")
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    cfg, engine = _engine()
    if args.memory_command == "show":
        for cat, items in engine.memory.facts().items():
            if not items:
                continue
            print(f"\n[{cat}]")
            for k, v in items.items():
                print(f"  {k}: {v['value']}  ({v.get('updated_human', '')})")
    elif args.memory_command == "add":
        engine.memory.remember(args.category, args.key, args.value)
        print(f"Записано: [{args.category}] {args.key} = {args.value}")
    elif args.memory_command == "forget":
        ok = engine.memory.forget(args.category, args.key)
        print("Удалено" if ok else "Не найдено")
    elif args.memory_command == "recall":
        for f in engine.memory.recall(args.query, top_k=10):
            print(f"[{f['category']}] {f['key']}: {f['value']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="lumen", description=f"{BRAND_NAME} — {TAGLINE}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="запустить платформу (API + веб-интерфейс)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8090)

    sub.add_parser("desktop", help="настольная программа (окно: диалог, голос, мысли)")

    sub.add_parser("chat", help="диалог в терминале")
    p = sub.add_parser("tools", help="инструменты")
    psub = p.add_subparsers(dest="tools_command")
    psub.add_parser("list", help="список инструментов (по умолчанию)")
    prun = psub.add_parser("run", help="запустить инструмент")
    prun.add_argument("tools_name")
    prun.add_argument("--arg", action="append", help="параметр key=value")

    sub.add_parser("status", help="состояние платформы")
    p = sub.add_parser("memory", help="память")
    msub = p.add_subparsers(dest="memory_command")
    msub.add_parser("show", help="показать факты")
    madd = msub.add_parser("add", help="добавить факт")
    madd.add_argument("category", choices=["identity", "preferences", "projects",
                                           "relationships", "wishes", "notes"])
    madd.add_argument("key")
    madd.add_argument("value")
    mfg = msub.add_parser("forget", help="удалить факт")
    mfg.add_argument("category")
    mfg.add_argument("key")
    mrcl = msub.add_parser("recall", help="вспомнить по запросу")
    mrcl.add_argument("query")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "desktop":
        return cmd_desktop(args)
    if args.command == "chat":
        return cmd_chat(args)
    if args.command == "tools":
        args.tools_command = args.tools_command or "list"
        return cmd_tools(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "memory":
        args.memory_command = args.memory_command or "show"
        return cmd_memory(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
