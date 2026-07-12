"""AST-level Python symbol index powered by tree-sitter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser, Query, QueryCursor

# Directories commonly irrelevant for code navigation.
_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".tox",
    ".eggs",
    ".autopatch",
}

_PY_LANGUAGE = Language(tspython.language())

# tree-sitter query for Python definitions + import statements.
# Kept intentionally simple for grammar compatibility across tree-sitter-python versions.
_SYMBOL_QUERY = Query(
    _PY_LANGUAGE,
    """
(function_definition
  name: (identifier) @function.name) @function.def

(class_definition
  name: (identifier) @class.name) @class.def

(import_statement) @import.stmt

(import_from_statement) @import.from
""",
)


class SymbolKind(StrEnum):
    FUNCTION = "function"
    CLASS = "class"
    IMPORT = "import"
    METHOD = "method"


@dataclass(frozen=True)
class Symbol:
    """A single indexed code symbol."""

    name: str
    kind: SymbolKind
    file_path: str
    start_line: int
    end_line: int
    parent: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "parent": self.parent,
        }


def _node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _is_under_class(node: Node) -> bool:
    current = node.parent
    while current is not None:
        if current.type == "class_definition":
            return True
        if current.type == "function_definition":
            return False
        current = current.parent
    return False


def _enclosing_class_name(source: bytes, node: Node) -> str | None:
    current = node.parent
    while current is not None:
        if current.type == "class_definition":
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                return _node_text(source, name_node)
            return None
        current = current.parent
    return None


class SymbolIndex:
    """In-memory symbol map for a Python repository."""

    def __init__(self) -> None:
        self._symbols: list[Symbol] = []
        self._by_file: dict[str, list[Symbol]] = {}
        self._root: Path | None = None
        self._parser = Parser(_PY_LANGUAGE)

    @property
    def symbols(self) -> list[Symbol]:
        return list(self._symbols)

    @property
    def root(self) -> Path | None:
        return self._root

    def build(self, root: Path) -> int:
        """Parse all ``*.py`` files under ``root`` and populate the index.

        Returns:
            Number of symbols indexed.
        """
        root = root.resolve()
        self._root = root
        self._symbols.clear()
        self._by_file.clear()

        for path in sorted(root.rglob("*.py")):
            # Only skip relative to the workspace root. Absolute paths often live
            # under AutoPatch's own `.autopatch/work/<run>/repo/...`; if we
            # checked path.parts on the absolute path, every file would be
            # skipped because `.autopatch` is a skip name.
            try:
                rel_path = path.relative_to(root)
            except ValueError:
                continue
            if any(part in _SKIP_DIR_NAMES for part in rel_path.parts):
                continue
            try:
                source = path.read_bytes()
            except OSError:
                continue
            rel = rel_path.as_posix()
            file_symbols = self._parse_file(rel, source)
            self._by_file[rel] = file_symbols
            self._symbols.extend(file_symbols)
        return len(self._symbols)

    def _parse_file(self, rel_path: str, source: bytes) -> list[Symbol]:
        tree = self._parser.parse(source)
        cursor = QueryCursor(_SYMBOL_QUERY)
        matches = cursor.matches(tree.root_node)
        symbols: list[Symbol] = []
        seen: set[tuple[str, int, str]] = set()

        for _match_id, captures in matches:
            if "function.name" in captures and "function.def" in captures:
                name_node = captures["function.name"][0]
                def_node = captures["function.def"][0]
                name = _node_text(source, name_node)
                kind = SymbolKind.METHOD if _is_under_class(def_node) else SymbolKind.FUNCTION
                parent = _enclosing_class_name(source, def_node)
                key = (name, def_node.start_point[0] + 1, kind.value)
                if key not in seen:
                    seen.add(key)
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            file_path=rel_path,
                            start_line=def_node.start_point[0] + 1,
                            end_line=def_node.end_point[0] + 1,
                            parent=parent,
                        )
                    )
            elif "class.name" in captures and "class.def" in captures:
                name_node = captures["class.name"][0]
                def_node = captures["class.def"][0]
                name = _node_text(source, name_node)
                key = (name, def_node.start_point[0] + 1, SymbolKind.CLASS.value)
                if key not in seen:
                    seen.add(key)
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=SymbolKind.CLASS,
                            file_path=rel_path,
                            start_line=def_node.start_point[0] + 1,
                            end_line=def_node.end_point[0] + 1,
                        )
                    )
            elif "import.stmt" in captures or "import.from" in captures:
                stmt_nodes = captures.get("import.stmt") or captures.get("import.from") or []
                if not stmt_nodes:
                    continue
                stmt_node = stmt_nodes[0]
                name = _node_text(source, stmt_node).strip().splitlines()[0][:120]
                key = (name, stmt_node.start_point[0] + 1, SymbolKind.IMPORT.value)
                if key not in seen:
                    seen.add(key)
                    symbols.append(
                        Symbol(
                            name=name,
                            kind=SymbolKind.IMPORT,
                            file_path=rel_path,
                            start_line=stmt_node.start_point[0] + 1,
                            end_line=stmt_node.end_point[0] + 1,
                        )
                    )
        return symbols

    def get_file_symbols(self, file_path: str) -> list[Symbol]:
        """Return symbols for a repo-relative path."""
        normalized = file_path.replace("\\", "/")
        return list(self._by_file.get(normalized, []))

    def search(self, query: str, *, limit: int = 20) -> list[Symbol]:
        """Rank symbols by keyword / symbol-name match against ``query``."""
        tokens = _tokenize(query)
        if not tokens:
            return self._symbols[:limit]

        scored: list[tuple[float, Symbol]] = []
        for symbol in self._symbols:
            score = _score_symbol(symbol, tokens, query.lower())
            if score > 0:
                scored.append((score, symbol))
        scored.sort(key=lambda item: (-item[0], item[1].file_path, item[1].start_line))
        return [symbol for _, symbol in scored[:limit]]

    def relevant_files(self, query: str, *, limit: int = 10) -> list[str]:
        """Return distinct file paths ranked by symbol hit strength."""
        hits = self.search(query, limit=limit * 5)
        ordered: list[str] = []
        seen: set[str] = set()
        for symbol in hits:
            if symbol.file_path not in seen:
                seen.add(symbol.file_path)
                ordered.append(symbol.file_path)
            if len(ordered) >= limit:
                break
        return ordered


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text.lower()) if len(t) > 1]


def _score_symbol(symbol: Symbol, tokens: list[str], query_lower: str) -> float:
    name_lower = symbol.name.lower()
    file_lower = symbol.file_path.lower()
    score = 0.0
    if name_lower == query_lower:
        score += 50.0
    if name_lower in query_lower:
        score += 20.0
    for token in tokens:
        if token == name_lower:
            score += 15.0
        elif token in name_lower:
            score += 8.0
        if token in file_lower:
            score += 2.0
        if symbol.parent and token in symbol.parent.lower():
            score += 3.0
    # Prefer functions/classes over bare imports for retrieval.
    if symbol.kind in {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS}:
        score += 1.0
    return score
