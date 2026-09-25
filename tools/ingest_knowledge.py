"""Index local documents for JARVIS knowledge-grounded questions.

Usage:
    venv\\Scripts\\python.exe tools\\ingest_knowledge.py
    venv\\Scripts\\python.exe tools\\ingest_knowledge.py C:\\path\\to\\notes
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge import KnowledgeBase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest supported files into JARVIS knowledge.db")
    parser.add_argument("path", nargs="?", help="file or directory; defaults to data/knowledge/documents")
    args = parser.parse_args()
    knowledge = KnowledgeBase(ROOT)
    try:
        result = knowledge.ingest(args.path)
        print(f"Ingested {result['documents']} document(s) into {result['chunks']} chunk(s).")
        print(f"Backend: {result['status']['backend']}")
        print(f"Diagnostic: {result['status']['diagnostic']}")
        for error in result.get("errors", []):
            print(f"Skipped: {error['path']} — {error['error']}")
        return 0 if not result.get("errors") or result["documents"] > 0 else 1
    finally:
        knowledge.close()


if __name__ == "__main__":
    raise SystemExit(main())
