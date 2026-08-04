from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import run_server
from .config import RAGConfig
from .service import NewStudentAssistant


def _configure_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="PAIMON Next new-student RAG assistant.")
    parser.add_argument("question", nargs="*", help="Question to ask. Omit for interactive mode.")
    parser.add_argument("--root", default=str(Path.cwd()), help="Project root.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON.")
    parser.add_argument("--top-k", type=int, default=None, help="Number of sources to retrieve.")
    parser.add_argument("--reindex", action="store_true", help="Force rebuild the local index.")
    parser.add_argument("--serve", action="store_true", help="Run the HTTP API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8002, type=int)
    args = parser.parse_args()

    if args.serve:
        run_server(host=args.host, port=args.port, root=args.root)
        return

    config = RAGConfig.from_env(args.root)
    assistant = NewStudentAssistant(config)
    if args.reindex:
        status = assistant.reindex()
        print(f"Rebuilt index with {status['chunks']} chunks.")
        if "graph_terms" in status:
            print(
                f"Built graph with {status['graph_terms']} terms "
                f"and {status['graph_communities']} communities."
            )

    question = " ".join(args.question).strip()
    if question:
        _ask_once(assistant, question, args.json, args.top_k)
        return

    print("PAIMON Next interactive mode. Type /exit to quit, /clear to clear history.")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question in {"/exit", "exit", "quit"}:
            break
        if question == "/clear":
            assistant.clear()
            print("History cleared.")
            continue
        if not question:
            continue
        _ask_once(assistant, question, args.json, args.top_k)


def _ask_once(
    assistant: NewStudentAssistant,
    question: str,
    raw_json: bool,
    top_k: int | None,
) -> None:
    result = assistant.ask(question, top_k=top_k)
    data = result.to_dict()
    if raw_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(data["answer"])
    if data["sources"]:
        print("\n来源：")
        for source in data["sources"][:3]:
            print(f"- [{source['id']}] {source['title']} ({source['source']}, score={source['score']})")
    print(f"\n置信度：{data['confidence']}")


if __name__ == "__main__":
    main()
