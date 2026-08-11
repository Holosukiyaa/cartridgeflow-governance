"""Build a deterministic governance index from card scopes and target repositories."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import importlib.metadata
import json
import os
import posixpath
import sqlite3
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from .governance_db import DEFAULT_DATABASE as DEFAULT_SOURCE_DATABASE
    from .governance_db import canonical_json, verify_database as verify_source_database
except ImportError:  # Direct execution: python scripts/build_governance_index.py
    from governance_db import DEFAULT_DATABASE as DEFAULT_SOURCE_DATABASE
    from governance_db import canonical_json, verify_database as verify_source_database


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ROOT / "targets.json"
DEFAULT_INDEX = ROOT / ".data" / "governance-index.sqlite"
INDEX_SCHEMA = ROOT / "schema" / "governance_index.sql"
INDEX_DATABASE_SCHEMA = "cartridgeflow.governance.index.v3"
SCANNER_VERSION = "0.5.0"
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "blocker": 3}
TYPESCRIPT_EXTRACTOR = ROOT / "scripts" / "extract_typescript_imports.mjs"
TYPESCRIPT_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


class GovernanceIndexError(RuntimeError):
    """Raised when a governance index cannot be built or verified."""


def _git_bytes(path: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _git_text(path: Path, *args: str) -> str:
    return _git_bytes(path, *args).decode("utf-8").strip()


def _nul_paths(value: bytes) -> set[str]:
    return {
        item.decode("utf-8").replace("\\", "/")
        for item in value.split(b"\0")
        if item
    }


def _under_roots(path: str, roots: list[str]) -> bool:
    normalized = path.strip("/")
    return any(normalized == root.strip("/") or normalized.startswith(root.strip("/") + "/") for root in roots)


def path_matches(path: str, selector: str) -> bool:
    normalized_path = PurePosixPath(path).as_posix().lstrip("./")
    normalized_selector = PurePosixPath(selector).as_posix().lstrip("./")
    return fnmatch.fnmatchcase(normalized_path, normalized_selector)


def _target_files(target_path: Path, governed_roots: list[str]) -> tuple[list[str], set[str], set[str]]:
    tracked = _nul_paths(_git_bytes(target_path, "ls-files", "-z", "--", *governed_roots))
    untracked = _nul_paths(
        _git_bytes(target_path, "ls-files", "--others", "--exclude-standard", "-z", "--", *governed_roots)
    )
    modified = _nul_paths(_git_bytes(target_path, "diff", "--name-only", "-z", "--", *governed_roots))
    modified.update(_nul_paths(_git_bytes(target_path, "diff", "--cached", "--name-only", "-z", "--", *governed_roots)))
    files = sorted(path for path in tracked | untracked if _under_roots(path, governed_roots) and (target_path / path).is_file())
    return files, modified, untracked


def _target_snapshot(
    target_path: Path,
    governed_roots: list[str],
) -> tuple[list[dict[str, Any]], str, str, int, str]:
    files, modified, untracked = _target_files(target_path, governed_roots)
    facts: list[dict[str, Any]] = []
    for relative in files:
        path = target_path / relative
        content = path.read_bytes()
        facts.append(
            {
                "artifact_id": relative,
                "path": relative,
                "kind": path.suffix.lower().lstrip(".") or "file",
                "size": len(content),
                "digest": hashlib.sha256(content).hexdigest(),
                "state": "untracked" if relative in untracked else "modified" if relative in modified else "tracked",
            }
        )
    head = _git_text(target_path, "rev-parse", "HEAD")
    remote = _git_text(target_path, "remote", "get-url", "origin")
    dirty_count = len(_git_text(target_path, "status", "--porcelain=v1").splitlines())
    content_digest = hashlib.sha256(canonical_json(facts).encode("utf-8")).hexdigest()
    return facts, head, remote, dirty_count, content_digest


def _load_scopes(source: Path) -> dict[str, list[dict[str, Any]]]:
    connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        scopes: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            "SELECT scope.*, card.status FROM card_scope AS scope "
            "JOIN card ON card.card_id = scope.card_id "
            "WHERE card.status = 'active' ORDER BY scope.target_id, scope.card_id, scope.scope_id"
        ):
            scopes.setdefault(str(row["target_id"]), []).append(dict(row))
        return scopes
    finally:
        connection.close()


def _load_dependency_permissions(source: Path) -> set[tuple[str, str]]:
    connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {
            (str(source_card), str(target_card))
            for source_card, target_card in connection.execute(
                "SELECT relation.source_card_id, relation.target_card_id "
                "FROM card_relation AS relation "
                "JOIN card AS source ON source.card_id = relation.source_card_id "
                "JOIN card AS target ON target.card_id = relation.target_card_id "
                "WHERE relation.relation_type = 'depends_on' "
                "AND source.status = 'active' AND target.status = 'active'"
            )
        }
    finally:
        connection.close()


def _source_identity(source: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return dict(connection.execute("SELECT key, value FROM registry_metadata"))
    finally:
        connection.close()


def _governance_facts_digest(connection: sqlite3.Connection) -> str:
    payload = {
        "targets": connection.execute(
            "SELECT target_id, git_head, artifact_count, content_digest "
            "FROM target_revision ORDER BY target_id"
        ).fetchall(),
        "coverage": connection.execute(
            "SELECT artifact_id, primary_card_ids_json, coverage_status "
            "FROM scope_coverage ORDER BY artifact_id"
        ).fetchall(),
        "dependencies": connection.execute(
            "SELECT dependency_id, dependency_kind, target_reference, resolution_status "
            "FROM observed_dependency ORDER BY dependency_id"
        ).fetchall(),
        "symbols": connection.execute(
            "SELECT symbol_id, artifact_id, symbol_kind, qualified_name, line_start, line_end "
            "FROM observed_symbol ORDER BY symbol_id"
        ).fetchall(),
        "contracts": connection.execute(
            "SELECT contract_key, lifecycle, generation, content_digest FROM observed_contract ORDER BY contract_key"
        ).fetchall(),
        "contract_matches": connection.execute(
            "SELECT binding_id, card_id, contract_key, match_status FROM card_contract_match ORDER BY binding_id"
        ).fetchall(),
        "context_chunks": connection.execute(
            "SELECT chunk_id, card_id, source_kind, source_id, content_digest FROM context_chunk ORDER BY chunk_id"
        ).fetchall(),
        "findings": connection.execute(
            "SELECT finding_id, severity, finding_type, artifact_id, status "
            "FROM finding ORDER BY finding_id"
        ).fetchall(),
    }
    serializable = {key: [list(row) for row in rows] for key, rows in payload.items()}
    return hashlib.sha256(canonical_json(serializable).encode("utf-8")).hexdigest()


def _matching_scopes(path: str, scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards_with_exclusion = {
        item["card_id"]
        for item in scopes
        if item["selector_kind"] == "path_glob"
        and item["polarity"] == "exclude"
        and path_matches(path, item["selector"])
    }
    return [
        item
        for item in scopes
        if item["card_id"] not in cards_with_exclusion
        and item["selector_kind"] == "path_glob"
        and item["polarity"] == "include"
        and path_matches(path, item["selector"])
    ]


def _python_module_name(path: str, roots: list[str]) -> tuple[str, bool] | None:
    candidate = PurePosixPath(path)
    for root in roots:
        try:
            relative = candidate.relative_to(PurePosixPath(root.strip("/")))
        except ValueError:
            continue
        if relative.suffix != ".py":
            return None
        parts = list(relative.with_suffix("").parts)
        is_package = bool(parts and parts[-1] == "__init__")
        if is_package:
            parts.pop()
        if parts:
            return ".".join(parts), is_package
    return None


def _absolute_from_module(module_name: str, is_package: bool, level: int, imported: str | None) -> str:
    if level == 0:
        return str(imported or "")
    package = module_name.split(".") if is_package else module_name.split(".")[:-1]
    ascend = level - 1
    if ascend > len(package):
        return ""
    base = package[: len(package) - ascend]
    if imported:
        base.extend(str(imported).split("."))
    return ".".join(base)


def _python_import_references(
    content: bytes,
    module_name: str,
    is_package: bool,
) -> list[tuple[str, list[str]]]:
    tree = ast.parse(content, filename=module_name)
    references: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend((alias.name, [alias.name]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_from_module(module_name, is_package, node.level, node.module)
            for alias in node.names:
                reference = f"{base}.{alias.name}".strip(".") if alias.name != "*" else base
                candidates = [reference]
                if base and base not in candidates:
                    candidates.append(base)
                references.append((reference, candidates))
    return references


def _insert_symbol(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    artifact_id: str,
    language: str,
    symbol_kind: str,
    qualified_name: str,
    line_start: int,
    line_end: int,
    visibility: str,
) -> None:
    identity = f"{artifact_id}:{language}:{symbol_kind}:{qualified_name}:{line_start}"
    connection.execute(
        "INSERT OR IGNORE INTO observed_symbol VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            target_id,
            artifact_id,
            language,
            symbol_kind,
            qualified_name,
            line_start,
            line_end,
            visibility,
        ),
    )


def _insert_python_symbols(
    connection: sqlite3.Connection,
    target_id: str,
    artifact_id: str,
    module_name: str,
    tree: ast.AST,
) -> None:
    def walk_body(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                qualified = f"{prefix}.{node.name}" if prefix else node.name
                _insert_symbol(
                    connection,
                    target_id=target_id,
                    artifact_id=artifact_id,
                    language="python",
                    symbol_kind=kind,
                    qualified_name=qualified,
                    line_start=int(node.lineno),
                    line_end=int(getattr(node, "end_lineno", node.lineno)),
                    visibility="internal" if node.name.startswith("_") else "public",
                )
                walk_body(node.body, qualified)
            elif not prefix and isinstance(node, (ast.Assign, ast.AnnAssign)):
                candidates = node.targets if isinstance(node, ast.Assign) else [node.target]
                for assignment_target in candidates:
                    if not isinstance(assignment_target, ast.Name):
                        continue
                    qualified = f"{module_name}.{assignment_target.id}" if module_name else assignment_target.id
                    _insert_symbol(
                        connection,
                        target_id=target_id,
                        artifact_id=artifact_id,
                        language="python",
                        symbol_kind="variable",
                        qualified_name=qualified,
                        line_start=int(node.lineno),
                        line_end=int(getattr(node, "end_lineno", node.lineno)),
                        visibility="internal" if assignment_target.id.startswith("_") else "public",
                    )

    walk_body(getattr(tree, "body", []), module_name)


def _insert_python_dependencies(
    connection: sqlite3.Connection,
    target_id: str,
    target_path: Path,
    target_facts: list[dict[str, Any]],
    python_roots: list[str],
    allowed_dependencies: set[tuple[str, str]],
) -> None:
    if not python_roots:
        return
    modules: dict[str, str] = {}
    source_modules: dict[str, tuple[str, bool]] = {}
    facts_by_id = {str(fact["artifact_id"]): fact for fact in target_facts}
    for fact in target_facts:
        identity = _python_module_name(str(fact["path"]), python_roots)
        if identity is None:
            continue
        module_name, is_package = identity
        artifact_id = str(fact["artifact_id"])
        if module_name in modules and modules[module_name] != artifact_id:
            raise GovernanceIndexError(f"duplicate Python module identity: {target_id}:{module_name}")
        modules[module_name] = artifact_id
        source_modules[artifact_id] = identity

    local_roots = {name.split(".", 1)[0] for name in modules}
    coverage = {
        str(artifact_id): json.loads(str(owner_ids))
        for artifact_id, owner_ids in connection.execute(
            "SELECT coverage.artifact_id, coverage.primary_card_ids_json "
            "FROM scope_coverage AS coverage "
            "JOIN observed_artifact AS artifact ON artifact.artifact_id = coverage.artifact_id "
            "WHERE artifact.target_id = ?",
            (target_id,),
        )
    }
    dependencies: dict[str, tuple[str, str | None, str]] = {}
    for artifact_id, (module_name, is_package) in sorted(source_modules.items()):
        fact = facts_by_id[artifact_id]
        try:
            content = (target_path / str(fact["path"])).read_bytes()
            references = _python_import_references(content, module_name, is_package)
            tree = ast.parse(content, filename=module_name)
            _insert_python_symbols(connection, target_id, artifact_id, module_name, tree)
        except (SyntaxError, ValueError) as exc:
            finding_id = hashlib.sha256(f"python-parse:{artifact_id}".encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO finding "
                "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
                "VALUES (?, 'error', 'dependency-parse-error', 'constitution.dependency-observable', "
                "'constitution.project', ?, ?, ?, 'open')",
                (
                    finding_id,
                    artifact_id,
                    f"Cannot parse Python imports for {target_id}:{fact['path']}: {exc}",
                    canonical_json({"language": "python", "scanner_version": SCANNER_VERSION}),
                ),
            )
            continue
        for reference, candidates in references:
            resolved = next((modules[item] for item in candidates if item in modules), None)
            if resolved == artifact_id:
                continue
            status = (
                "resolved"
                if resolved
                else "unresolved"
                if reference.split(".", 1)[0] in local_roots
                else "external"
            )
            key = f"{artifact_id}:python-import:{reference}:{resolved or ''}"
            dependencies[key] = (reference, resolved, status)

    for key, (reference, resolved, status) in sorted(dependencies.items()):
        source_artifact_id = key.split(":python-import:", 1)[0]
        dependency_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO observed_dependency VALUES (?, ?, ?, 'python-import', ?, ?, ?)",
            (dependency_id, target_id, source_artifact_id, reference, resolved, status),
        )
        if not resolved:
            continue
        source_owners = coverage.get(source_artifact_id, [])
        target_owners = coverage.get(resolved, [])
        if len(source_owners) != 1 or len(target_owners) != 1 or source_owners == target_owners:
            continue
        relation = (str(source_owners[0]), str(target_owners[0]))
        if relation in allowed_dependencies:
            continue
        finding_id = hashlib.sha256(f"dependency-relation:{dependency_id}:{relation}".encode("utf-8")).hexdigest()
        target_artifact = facts_by_id[resolved]
        connection.execute(
            "INSERT INTO finding "
            "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
            "VALUES (?, 'error', 'card-dependency-undocumented', "
            "'constitution.dependency-declared', ?, ?, ?, ?, 'open')",
            (
                finding_id,
                relation[0],
                source_artifact_id,
                f"{relation[0]} imports {relation[1]} without an active depends_on relation: {reference}",
                canonical_json(
                    {
                        "source_card_id": relation[0],
                        "target_card_id": relation[1],
                        "target_artifact_id": resolved,
                        "target_artifact_path": target_artifact["path"],
                        "required_relation": "depends_on",
                    }
                ),
            ),
        )


def _resolve_typescript_reference(
    source_path: str,
    reference: str,
    artifact_ids_by_path: dict[str, str],
) -> str | None:
    if not reference.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), reference))
    candidates = [base]
    suffix = PurePosixPath(base).suffix.lower()
    if suffix in (".js", ".jsx", ".mjs", ".cjs"):
        stem = str(PurePosixPath(base).with_suffix(""))
        candidates.extend(stem + item for item in TYPESCRIPT_SUFFIXES)
    elif not suffix:
        candidates.extend(base + item for item in TYPESCRIPT_SUFFIXES)
        candidates.extend(posixpath.join(base, "index" + item) for item in TYPESCRIPT_SUFFIXES)
    return next((artifact_ids_by_path[item] for item in candidates if item in artifact_ids_by_path), None)


def _insert_typescript_dependencies(
    connection: sqlite3.Connection,
    target_id: str,
    target_path: Path,
    target_facts: list[dict[str, Any]],
    package_roots: list[str],
    allowed_dependencies: set[tuple[str, str]],
) -> dict[str, str]:
    if not package_roots:
        return {}
    facts_by_id = {str(fact["artifact_id"]): fact for fact in target_facts}
    artifact_ids_by_path = {str(fact["path"]): str(fact["artifact_id"]) for fact in target_facts}
    coverage = {
        str(artifact_id): json.loads(str(owner_ids))
        for artifact_id, owner_ids in connection.execute(
            "SELECT coverage.artifact_id, coverage.primary_card_ids_json "
            "FROM scope_coverage AS coverage "
            "JOIN observed_artifact AS artifact ON artifact.artifact_id = coverage.artifact_id "
            "WHERE artifact.target_id = ?",
            (target_id,),
        )
    }
    parser_versions: dict[str, str] = {}
    dependencies: dict[str, tuple[str, str | None, str, int]] = {}
    for package_root in package_roots:
        files = sorted(
            str(fact["path"])
            for fact in target_facts
            if _under_roots(str(fact["path"]), [package_root])
            and PurePosixPath(str(fact["path"])).suffix.lower() in TYPESCRIPT_SUFFIXES
        )
        if not files:
            continue
        compiler = target_path / package_root / "node_modules" / "typescript" / "lib" / "typescript.js"
        if not compiler.is_file():
            raise GovernanceIndexError(
                f"TypeScript compiler is missing for configured package: {target_id}:{package_root}"
            )
        completed = subprocess.run(
            ["node", str(TYPESCRIPT_EXTRACTOR), str(compiler), str(target_path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=json.dumps({"files": files}),
        )
        if completed.returncode != 0:
            raise GovernanceIndexError(
                f"TypeScript import extraction failed for {target_id}:{package_root}: {completed.stderr.strip()}"
            )
        result = json.loads(completed.stdout)
        parser_versions[f"{target_id}:{package_root}:typescript"] = str(result["compilerVersion"])
        for error in result.get("errors", []):
            artifact_id = artifact_ids_by_path.get(str(error["file"]))
            if not artifact_id:
                continue
            finding_id = hashlib.sha256(f"typescript-parse:{artifact_id}".encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO finding "
                "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
                "VALUES (?, 'error', 'dependency-parse-error', 'constitution.dependency-observable', "
                "'constitution.project', ?, ?, ?, 'open')",
                (
                    finding_id,
                    artifact_id,
                    f"Cannot parse TypeScript imports for {target_id}:{error['file']}: {error['message']}",
                    canonical_json(
                        {
                            "language": "typescript",
                            "compiler_version": result["compilerVersion"],
                            "scanner_version": SCANNER_VERSION,
                        }
                    ),
                ),
            )
        for item in result.get("symbols", []):
            source_path = str(item["file"]).replace("\\", "/")
            source_artifact_id = artifact_ids_by_path.get(source_path)
            if not source_artifact_id:
                continue
            _insert_symbol(
                connection,
                target_id=target_id,
                artifact_id=source_artifact_id,
                language="typescript",
                symbol_kind=str(item["kind"]),
                qualified_name=str(item["name"]),
                line_start=int(item["lineStart"]),
                line_end=int(item["lineEnd"]),
                visibility=str(item["visibility"]),
            )
        for item in result.get("dependencies", []):
            source_path = str(item["file"]).replace("\\", "/")
            source_artifact_id = artifact_ids_by_path.get(source_path)
            if not source_artifact_id:
                continue
            reference = str(item["reference"])
            resolved = _resolve_typescript_reference(source_path, reference, artifact_ids_by_path)
            if resolved == source_artifact_id:
                continue
            status = "resolved" if resolved else "unresolved" if reference.startswith(".") else "external"
            key = f"{source_artifact_id}:typescript-import:{reference}:{resolved or ''}"
            dependencies[key] = (reference, resolved, status, int(item["line"]))

    for key, (reference, resolved, status, line) in sorted(dependencies.items()):
        source_artifact_id = key.split(":typescript-import:", 1)[0]
        dependency_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO observed_dependency VALUES (?, ?, ?, 'typescript-import', ?, ?, ?)",
            (dependency_id, target_id, source_artifact_id, reference, resolved, status),
        )
        if not resolved:
            continue
        source_owners = coverage.get(source_artifact_id, [])
        target_owners = coverage.get(resolved, [])
        if len(source_owners) != 1 or len(target_owners) != 1 or source_owners == target_owners:
            continue
        relation = (str(source_owners[0]), str(target_owners[0]))
        if relation in allowed_dependencies:
            continue
        finding_id = hashlib.sha256(f"dependency-relation:{dependency_id}:{relation}".encode("utf-8")).hexdigest()
        target_artifact = facts_by_id[resolved]
        connection.execute(
            "INSERT INTO finding "
            "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
            "VALUES (?, 'error', 'card-dependency-undocumented', "
            "'constitution.dependency-declared', ?, ?, ?, ?, 'open')",
            (
                finding_id,
                relation[0],
                source_artifact_id,
                f"{relation[0]} imports {relation[1]} without an active depends_on relation: {reference}",
                canonical_json(
                    {
                        "source_card_id": relation[0],
                        "target_card_id": relation[1],
                        "target_artifact_id": resolved,
                        "target_artifact_path": target_artifact["path"],
                        "line": line,
                        "required_relation": "depends_on",
                    }
                ),
            ),
        )
    return parser_versions


def _tree_sitter_go_parser() -> tuple[Any, str]:
    try:
        import tree_sitter_go
        from tree_sitter import Language, Parser
    except ImportError as exc:
        raise GovernanceIndexError(
            "Go dependency scanning requires requirements-scanner.txt"
        ) from exc
    core_version = importlib.metadata.version("tree-sitter")
    grammar_version = importlib.metadata.version("tree-sitter-go")
    return Parser(Language(tree_sitter_go.language())), (
        f"tree-sitter-go/{grammar_version} (tree-sitter/{core_version})"
    )


def _walk_syntax_tree(root: Any) -> Any:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _go_syntax_facts(content: bytes) -> tuple[list[tuple[str, int]], list[dict[str, Any]], str]:
    parser, parser_version = _tree_sitter_go_parser()
    tree = parser.parse(content)
    root = tree.root_node
    if root.has_error:
        problem = next(
            (node for node in _walk_syntax_tree(root) if node.is_error or node.is_missing),
            root,
        )
        line = problem.start_point.row + 1
        column = problem.start_point.column + 1
        raise ValueError(f"Go syntax tree contains an error at {line}:{column}")

    references: list[tuple[str, int]] = []
    symbols: list[dict[str, Any]] = []
    for node in _walk_syntax_tree(root):
        if node.type == "import_spec":
            path_node = node.child_by_field_name("path")
            if path_node is None:
                continue
            literal = content[path_node.start_byte:path_node.end_byte].decode("utf-8")
            if literal.startswith("`") and literal.endswith("`"):
                reference = literal[1:-1]
            else:
                reference = json.loads(literal)
            references.append((str(reference), path_node.start_point.row + 1))
            continue
        kind = {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "type",
            "const_spec": "constant",
            "var_spec": "variable",
        }.get(node.type)
        if not kind:
            continue
        if node.type in {"type_spec", "const_spec", "var_spec"}:
            parent = node.parent
            grandparent = parent.parent if parent is not None else None
            if grandparent is None or grandparent.type != "source_file":
                continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = content[name_node.start_byte:name_node.end_byte].decode("utf-8")
        symbols.append(
            {
                "kind": kind,
                "name": name,
                "line_start": node.start_point.row + 1,
                "line_end": node.end_point.row + 1,
                "visibility": "public" if name[:1].isupper() else "internal",
            }
        )
    return references, symbols, parser_version


def _go_import_references(content: bytes) -> tuple[list[tuple[str, int]], str]:
    """Compatibility wrapper for callers that only need imports."""
    references, _symbols, parser_version = _go_syntax_facts(content)
    return references, parser_version


def _go_module_path(content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("module "):
            continue
        value = line.split("//", 1)[0].split(None, 1)[1].strip()
        if value.startswith('"'):
            value = str(json.loads(value))
        if value:
            return value
    raise ValueError("go.mod has no module directive")


def _resolve_go_reference(
    reference: str,
    module_path: str,
    module_root: str,
    package_artifacts: dict[str, str],
) -> str | None:
    if reference == module_path:
        relative = ""
    elif reference.startswith(module_path + "/"):
        relative = reference[len(module_path) + 1:]
    else:
        return None
    package_path = posixpath.normpath(posixpath.join(module_root, relative)).strip(".")
    return package_artifacts.get(package_path.rstrip("/"))


def _insert_go_dependencies(
    connection: sqlite3.Connection,
    target_id: str,
    target_path: Path,
    target_facts: list[dict[str, Any]],
    module_roots: list[str],
    allowed_dependencies: set[tuple[str, str]],
) -> dict[str, str]:
    if not module_roots:
        return {}
    facts_by_id = {str(fact["artifact_id"]): fact for fact in target_facts}
    coverage = {
        str(artifact_id): json.loads(str(owner_ids))
        for artifact_id, owner_ids in connection.execute(
            "SELECT coverage.artifact_id, coverage.primary_card_ids_json "
            "FROM scope_coverage AS coverage "
            "JOIN observed_artifact AS artifact ON artifact.artifact_id = coverage.artifact_id "
            "WHERE artifact.target_id = ?",
            (target_id,),
        )
    }
    parser_versions: dict[str, str] = {}
    dependencies: dict[str, tuple[str, str | None, str, int]] = {}
    for module_root in module_roots:
        go_mod = target_path / module_root / "go.mod"
        if not go_mod.is_file():
            raise GovernanceIndexError(f"go.mod is missing for configured module: {target_id}:{module_root}")
        try:
            module_path = _go_module_path(go_mod.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise GovernanceIndexError(f"cannot read Go module identity for {target_id}:{module_root}: {exc}") from exc

        go_facts = [
            fact for fact in target_facts
            if _under_roots(str(fact["path"]), [module_root])
            and PurePosixPath(str(fact["path"])).suffix.lower() == ".go"
        ]
        packages: dict[str, list[dict[str, Any]]] = {}
        for fact in go_facts:
            packages.setdefault(posixpath.dirname(str(fact["path"])), []).append(fact)
        package_artifacts = {
            package: str(sorted(
                facts,
                key=lambda fact: (str(fact["path"]).endswith("_test.go"), str(fact["path"])),
            )[0]["artifact_id"])
            for package, facts in packages.items()
        }

        for fact in sorted(go_facts, key=lambda item: str(item["path"])):
            artifact_id = str(fact["artifact_id"])
            try:
                references, symbols, parser_version = _go_syntax_facts(
                    (target_path / str(fact["path"])).read_bytes()
                )
                parser_versions[f"{target_id}:{module_root}:go"] = parser_version
                for symbol in symbols:
                    _insert_symbol(
                        connection,
                        target_id=target_id,
                        artifact_id=artifact_id,
                        language="go",
                        symbol_kind=str(symbol["kind"]),
                        qualified_name=str(symbol["name"]),
                        line_start=int(symbol["line_start"]),
                        line_end=int(symbol["line_end"]),
                        visibility=str(symbol["visibility"]),
                    )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                finding_id = hashlib.sha256(f"go-parse:{artifact_id}".encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO finding "
                    "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
                    "VALUES (?, 'error', 'dependency-parse-error', 'constitution.dependency-observable', "
                    "'constitution.project', ?, ?, ?, 'open')",
                    (
                        finding_id,
                        artifact_id,
                        f"Cannot parse Go imports for {target_id}:{fact['path']}: {exc}",
                        canonical_json({"language": "go", "scanner_version": SCANNER_VERSION}),
                    ),
                )
                continue
            for reference, line in references:
                resolved = _resolve_go_reference(reference, module_path, module_root, package_artifacts)
                if resolved == artifact_id:
                    continue
                is_local = reference == module_path or reference.startswith(module_path + "/")
                status = "resolved" if resolved else "unresolved" if is_local else "external"
                key = f"{artifact_id}:go-import:{reference}:{resolved or ''}"
                dependencies[key] = (reference, resolved, status, line)

    for key, (reference, resolved, status, line) in sorted(dependencies.items()):
        source_artifact_id = key.split(":go-import:", 1)[0]
        dependency_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO observed_dependency VALUES (?, ?, ?, 'go-import', ?, ?, ?)",
            (dependency_id, target_id, source_artifact_id, reference, resolved, status),
        )
        if not resolved:
            continue
        source_owners = coverage.get(source_artifact_id, [])
        target_owners = coverage.get(resolved, [])
        if len(source_owners) != 1 or len(target_owners) != 1 or source_owners == target_owners:
            continue
        relation = (str(source_owners[0]), str(target_owners[0]))
        if relation in allowed_dependencies:
            continue
        finding_id = hashlib.sha256(f"dependency-relation:{dependency_id}:{relation}".encode("utf-8")).hexdigest()
        target_artifact = facts_by_id[resolved]
        connection.execute(
            "INSERT INTO finding "
            "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
            "VALUES (?, 'error', 'card-dependency-undocumented', "
            "'constitution.dependency-declared', ?, ?, ?, ?, 'open')",
            (
                finding_id,
                relation[0],
                source_artifact_id,
                f"{relation[0]} imports {relation[1]} without an active depends_on relation: {reference}",
                canonical_json(
                    {
                        "source_card_id": relation[0],
                        "target_card_id": relation[1],
                        "target_artifact_id": resolved,
                        "target_artifact_path": target_artifact["path"],
                        "line": line,
                        "required_relation": "depends_on",
                    }
                ),
            ),
        )
    return parser_versions


def _insert_target(
    connection: sqlite3.Connection,
    target: dict[str, Any],
    scopes: list[dict[str, Any]],
    allowed_dependencies: set[tuple[str, str]],
) -> dict[str, str]:
    target_id = str(target["id"])
    target_path = (ROOT / str(target["path"])).resolve()
    roots = [str(item).replace("\\", "/").strip("/") for item in target.get("governed_roots", [])]
    if not roots:
        raise GovernanceIndexError(f"target has no governed_roots: {target_id}")
    snapshot, head, remote, dirty_count, target_digest = _target_snapshot(target_path, roots)
    target_facts = [
        {**fact, "artifact_id": f"{target_id}:{fact['path']}"}
        for fact in snapshot
    ]
    connection.execute(
        "INSERT INTO target_revision VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (target_id, target["role"], str(target_path), remote, head, dirty_count, len(target_facts), target_digest),
    )
    for fact in target_facts:
        connection.execute(
            "INSERT INTO observed_artifact VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fact["artifact_id"], target_id, fact["path"], fact["kind"],
                fact["size"], fact["digest"], fact["state"],
            ),
        )
        matches = _matching_scopes(fact["path"], scopes)
        for scope in matches:
            connection.execute(
                "INSERT INTO scope_match VALUES (?, ?, ?, ?)",
                (fact["artifact_id"], scope["card_id"], scope["scope_id"], scope["ownership"]),
            )
        owners = sorted({scope["card_id"] for scope in matches if scope["ownership"] == "primary"})
        status = "covered" if len(owners) == 1 else "uncovered" if not owners else "ambiguous"
        connection.execute(
            "INSERT INTO scope_coverage VALUES (?, ?, ?, ?)",
            (fact["artifact_id"], len(owners), canonical_json(owners), status),
        )
        if status != "covered":
            finding_id = hashlib.sha256(f"coverage:{fact['artifact_id']}:{status}".encode("utf-8")).hexdigest()
            severity = "warning" if status == "uncovered" else "error"
            message = (
                f"No primary floor card owns {target_id}:{fact['path']}"
                if status == "uncovered"
                else f"Multiple primary floor cards own {target_id}:{fact['path']}: {', '.join(owners)}"
            )
            connection.execute(
                "INSERT INTO finding "
                "(finding_id, severity, finding_type, rule_id, card_id, artifact_id, message, details_json, status) "
                "VALUES (?, ?, ?, 'constitution.scope-primary-owner', 'constitution.project', ?, ?, ?, 'open')",
                (finding_id, severity, f"scope-{status}", fact["artifact_id"], message, canonical_json({"owners": owners})),
            )
    python_roots = [str(item).replace("\\", "/").strip("/") for item in target.get("python_roots", [])]
    _insert_python_dependencies(
        connection,
        target_id,
        target_path,
        target_facts,
        python_roots,
        allowed_dependencies,
    )
    typescript_packages = [
        str(item).replace("\\", "/").strip("/") for item in target.get("typescript_packages", [])
    ]
    parser_versions = _insert_typescript_dependencies(
        connection,
        target_id,
        target_path,
        target_facts,
        typescript_packages,
        allowed_dependencies,
    )
    go_modules = [
        str(item).replace("\\", "/").strip("/") for item in target.get("go_modules", [])
    ]
    parser_versions.update(_insert_go_dependencies(
        connection,
        target_id,
        target_path,
        target_facts,
        go_modules,
        allowed_dependencies,
    ))
    return parser_versions


def _insert_context_chunks(connection: sqlite3.Connection, source: Path) -> None:
    source_connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    source_connection.row_factory = sqlite3.Row
    try:
        for row in source_connection.execute(
            "SELECT card_id, authority, title, summary, body_markdown FROM card "
            "WHERE status = 'active' ORDER BY card_id"
        ):
            content = f"# {row['title']}\n\n{row['summary']}\n\n{row['body_markdown']}".strip()
            paragraphs = [item.strip() for item in content.split("\n\n") if item.strip()]
            chunks: list[str] = []
            current = ""
            for paragraph in paragraphs:
                candidate = paragraph if not current else current + "\n\n" + paragraph
                if current and len(candidate) > 1600:
                    chunks.append(current)
                    current = paragraph
                else:
                    current = candidate
            if current:
                chunks.append(current)
            for index, chunk in enumerate(chunks):
                identity = f"card:{row['card_id']}:{index}:{chunk}"
                connection.execute(
                    "INSERT INTO context_chunk VALUES (?, ?, 'card', ?, ?, ?, ?, ?)",
                    (
                        hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                        row["card_id"],
                        row["card_id"],
                        index,
                        1 if row["authority"] == "normative" else 0,
                        chunk,
                        hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    ),
                )
        for row in source_connection.execute(
            "SELECT section_id, card_id, content FROM card_section ORDER BY card_id, section_order"
        ):
            content = str(row["content"])
            identity = f"section:{row['section_id']}:{content}"
            authority = source_connection.execute(
                "SELECT authority FROM card WHERE card_id = ?", (row["card_id"],)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO context_chunk VALUES (?, ?, 'section', ?, 0, ?, ?, ?)",
                (
                    hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    row["card_id"],
                    row["section_id"],
                    1 if authority == "normative" else 0,
                    content,
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                ),
            )
    finally:
        source_connection.close()


def _insert_observed_contracts(
    connection: sqlite3.Connection,
    source: Path,
    config: dict[str, Any],
) -> None:
    registry_targets = {
        str(target["id"])
        for target in config.get("targets", [])
        if target.get("contract_registry")
    }
    for target in config.get("targets", []):
        registry_relative = target.get("contract_registry")
        if not registry_relative:
            continue
        target_id = str(target["id"])
        target_path = (ROOT / str(target["path"])).resolve()
        registry_path = (target_path / str(registry_relative)).resolve()
        if not registry_path.is_file():
            raise GovernanceIndexError(f"configured contract registry is missing: {target_id}:{registry_relative}")
        registry = sqlite3.connect(f"{registry_path.as_uri()}?mode=ro", uri=True)
        registry.row_factory = sqlite3.Row
        try:
            rows = registry.execute(
                "SELECT family.contract_id, family.display_name, family.layer, family.domain, family.visibility, "
                "release.contract_release_key AS release_key, release.version, release.generation, release.lifecycle, "
                "release.content_digest, artifact.artifact_path AS definition_artifact_path "
                "FROM data_contract_family AS family "
                "JOIN data_contract_release AS release ON release.contract_id = family.contract_id "
                "JOIN artifact ON artifact.artifact_id = release.definition_artifact_id "
                "ORDER BY family.contract_id, release.version"
            ).fetchall()
            release_to_contract: dict[str, str] = {}
            for row in rows:
                contract_key = f"{target_id}:{row['contract_id']}@{row['version']}"
                release_to_contract[str(row["release_key"])] = contract_key
                connection.execute(
                    "INSERT INTO observed_contract VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        contract_key, target_id, row["contract_id"], row["version"], row["generation"],
                        row["lifecycle"], row["layer"], row["domain"], row["visibility"], row["display_name"],
                        row["release_key"], row["definition_artifact_path"], row["content_digest"],
                    ),
                )
            for row in registry.execute(
                "SELECT usage_key, contract_release_key, stage, actor, direction, notes "
                "FROM data_contract_usage ORDER BY usage_key"
            ):
                contract_key = release_to_contract.get(str(row["contract_release_key"]))
                if not contract_key:
                    continue
                connection.execute(
                    "INSERT INTO observed_contract_usage VALUES (?, ?, ?, ?, ?, ?)",
                    (row["usage_key"], contract_key, row["stage"], row["actor"], row["direction"], row["notes"]),
                )
        finally:
            registry.close()

    source_connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    source_connection.row_factory = sqlite3.Row
    try:
        bindings = source_connection.execute(
            "SELECT binding.*, card.card_type FROM card_contract_binding AS binding "
            "JOIN card ON card.card_id = binding.card_id WHERE card.status = 'active' ORDER BY binding.binding_id"
        ).fetchall()
    finally:
        source_connection.close()
    for binding in bindings:
        if str(binding["target_id"]) not in registry_targets:
            continue
        if str(binding["version_constraint"]) == "*":
            matches = connection.execute(
                "SELECT contract_key FROM observed_contract WHERE target_id = ? AND contract_id = ? ORDER BY version",
                (binding["target_id"], binding["contract_id"]),
            ).fetchall()
        else:
            matches = connection.execute(
                "SELECT contract_key FROM observed_contract WHERE target_id = ? AND contract_id = ? AND version = ?",
                (binding["target_id"], binding["contract_id"], binding["version_constraint"]),
            ).fetchall()
        status = "matched" if len(matches) == 1 else "missing" if not matches else "ambiguous"
        contract_key = str(matches[0][0]) if len(matches) == 1 else None
        details = {
            "target_id": binding["target_id"],
            "contract_id": binding["contract_id"],
            "version_constraint": binding["version_constraint"],
            "binding_role": binding["binding_role"],
            "disposition": binding["disposition"],
            "match_count": len(matches),
        }
        connection.execute(
            "INSERT INTO card_contract_match VALUES (?, ?, ?, ?, ?)",
            (binding["binding_id"], binding["card_id"], contract_key, status, canonical_json(details)),
        )
        if status == "matched" or not int(binding["required"]):
            continue
        finding_id = hashlib.sha256(f"contract-binding:{binding['binding_id']}:{status}".encode("utf-8")).hexdigest()
        details.update(
            {
                "reason": "declared product contract reference did not resolve exactly once",
                "expected": "one matching observed contract release",
                "actual": f"{len(matches)} matching releases",
                "boundary_card_id": binding["card_id"] if binding["card_type"] == "boundary" else None,
            }
        )
        connection.execute(
            "INSERT INTO finding VALUES (?, 'error', 'contract-reference-invalid', "
            "'constitution.references-exist', ?, NULL, ?, ?, 'open')",
            (
                finding_id,
                binding["card_id"],
                f"Contract binding does not resolve exactly once: {binding['contract_id']}@{binding['version_constraint']}",
                canonical_json(details),
            ),
        )

    unclassified = connection.execute(
        "SELECT contract.contract_key FROM observed_contract AS contract "
        "WHERE NOT EXISTS (SELECT 1 FROM card_contract_match AS match "
        "WHERE match.contract_key = contract.contract_key AND match.match_status = 'matched') "
        "ORDER BY contract.contract_key"
    ).fetchall()
    for (contract_key,) in unclassified:
        finding_id = hashlib.sha256(f"contract-unclassified:{contract_key}".encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO finding VALUES (?, 'error', 'contract-unclassified', "
            "'constitution.references-exist', 'constitution.project', NULL, ?, ?, 'open')",
            (
                finding_id,
                f"Observed product contract has no explicit governance disposition: {contract_key}",
                canonical_json(
                    {
                        "reason": "observed contract is absent from every explicit card binding",
                        "expected": "boundary, knowledge, or legacy-review disposition",
                        "actual": "unclassified",
                        "boundary_card_id": None,
                    }
                ),
            ),
        )


def _check_source_references(
    connection: sqlite3.Connection,
    source: Path,
    config: dict[str, Any],
) -> None:
    target_paths = {
        str(item["id"]): (ROOT / str(item["path"])).resolve()
        for item in config.get("targets", [])
        if item.get("validate_authority_references", False)
    }
    source_connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    source_connection.row_factory = sqlite3.Row
    try:
        references = source_connection.execute(
            "SELECT reference.*, card.card_type FROM card_source_reference AS reference "
            "JOIN card ON card.card_id = reference.card_id WHERE card.status = 'active' "
            "ORDER BY reference.source_ref_id"
        ).fetchall()
    finally:
        source_connection.close()
    for item in references:
        target_path = target_paths.get(str(item["target_id"]))
        if target_path is None:
            continue
        exists = False
        if target_path is not None and item["reference_kind"] == "path":
            candidate = (target_path / str(item["reference"])).resolve()
            try:
                candidate.relative_to(target_path)
                exists = candidate.exists()
            except ValueError:
                exists = False
        elif item["reference_kind"] in {"artifact", "api"}:
            exists = connection.execute(
                "SELECT COUNT(*) FROM observed_artifact WHERE target_id = ? AND artifact_path = ?",
                (item["target_id"], item["reference"]),
            ).fetchone()[0] == 1
        elif item["reference_kind"] == "symbol":
            exists = connection.execute(
                "SELECT COUNT(*) FROM observed_symbol WHERE target_id = ? AND qualified_name = ?",
                (item["target_id"], item["reference"]),
            ).fetchone()[0] > 0
        if exists:
            continue
        finding_id = hashlib.sha256(f"source-reference:{item['source_ref_id']}".encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT INTO finding VALUES (?, 'error', 'source-reference-missing', "
            "'constitution.references-exist', ?, NULL, ?, ?, 'open')",
            (
                finding_id,
                item["card_id"],
                f"Card source reference does not exist: {item['target_id']}:{item['reference']}",
                canonical_json(
                    {
                        "reason": "declared source reference was not found in the target",
                        "expected": "an existing declared path, artifact, API, or symbol",
                        "actual": "missing",
                        "boundary_card_id": item["card_id"] if item["card_type"] == "boundary" else None,
                    }
                ),
            ),
        )


def build_index(source: Path, targets_path: Path, target: Path) -> None:
    source_errors = verify_source_database(source)
    if source_errors:
        raise GovernanceIndexError("card source verification failed:\n- " + "\n- ".join(source_errors))
    config = json.loads(targets_path.read_text(encoding="utf-8"))
    if config.get("schema") != "cartridgeflow.governance.targets.v1":
        raise GovernanceIndexError("target registry schema is invalid")
    scopes = _load_scopes(source)
    allowed_dependencies = _load_dependency_permissions(source)
    source_identity = _source_identity(source)
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="governance-index-", suffix=".sqlite", dir=target.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(INDEX_SCHEMA.read_text(encoding="utf-8"))
        metadata = {
            "schema": INDEX_DATABASE_SCHEMA,
            "schema_version": "3",
            "scanner_version": SCANNER_VERSION,
            "card_source_publication_id": source_identity["publication_id"],
            "card_source_publication_digest": source_identity["publication_digest"],
            "symbol_scanning_required": "1" if any(
                item.get("python_roots") or item.get("typescript_packages") or item.get("go_modules")
                for item in config.get("targets", [])
            ) else "0",
            "contract_scanning_required": "1" if any(
                item.get("contract_registry") for item in config.get("targets", [])
            ) else "0",
        }
        for key, value in sorted(metadata.items()):
            connection.execute("INSERT INTO registry_metadata VALUES (?, ?)", (key, value))
        parser_versions: dict[str, str] = {}
        for item in config.get("targets", []):
            parser_versions.update(_insert_target(
                connection,
                item,
                scopes.get(str(item["id"]), []),
                allowed_dependencies,
            ))
        _insert_observed_contracts(connection, source, config)
        _check_source_references(connection, source, config)
        _insert_context_chunks(connection, source)
        connection.execute(
            "INSERT INTO registry_metadata VALUES ('parser_versions', ?)",
            (canonical_json(parser_versions),),
        )
        connection.execute(
            "INSERT INTO registry_metadata VALUES ('governance_facts_digest', ?)",
            (_governance_facts_digest(connection),),
        )
        connection.commit()
        errors = verify_index_connection(connection)
        if errors:
            raise GovernanceIndexError("index verification failed:\n- " + "\n- ".join(errors))
        connection.execute("PRAGMA optimize")
        connection.commit()
        connection.close()
        connection = None
        os.replace(temporary, target)
    except Exception:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        raise


def verify_index_connection(connection: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    if connection.execute("PRAGMA user_version").fetchone()[0] != 3:
        errors.append("index SQLite user_version must be 3")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        errors.append("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("SQLite foreign key check failed")
    metadata = dict(connection.execute("SELECT key, value FROM registry_metadata"))
    if metadata.get("schema") != INDEX_DATABASE_SCHEMA:
        errors.append(f"index schema must be {INDEX_DATABASE_SCHEMA}")
    if metadata.get("schema_version") != "3":
        errors.append("index schema_version must be 3")
    if metadata.get("scanner_version") != SCANNER_VERSION:
        errors.append(f"index scanner_version must be {SCANNER_VERSION}")
    try:
        parser_versions = json.loads(metadata.get("parser_versions", ""))
        if not isinstance(parser_versions, dict):
            errors.append("index parser_versions must be a JSON object")
    except json.JSONDecodeError:
        errors.append("index parser_versions is invalid JSON")
    expected_facts_digest = _governance_facts_digest(connection)
    if metadata.get("governance_facts_digest") != expected_facts_digest:
        errors.append("governance facts digest mismatch")
    artifacts = connection.execute("SELECT COUNT(*) FROM observed_artifact").fetchone()[0]
    coverage = connection.execute("SELECT COUNT(*) FROM scope_coverage").fetchone()[0]
    if artifacts == 0:
        errors.append("index must contain observed artifacts")
    if coverage != artifacts:
        errors.append("scope coverage does not contain every observed artifact")
    if metadata.get("symbol_scanning_required") == "1" and connection.execute(
        "SELECT COUNT(*) FROM observed_symbol"
    ).fetchone()[0] == 0:
        errors.append("index must contain observed symbols")
    contracts = connection.execute("SELECT COUNT(*) FROM observed_contract").fetchone()[0]
    if metadata.get("contract_scanning_required") == "1" and contracts == 0:
        errors.append("index must contain observed product contracts")
    unmatched_contracts = connection.execute(
        "SELECT COUNT(*) FROM observed_contract AS contract WHERE NOT EXISTS ("
        "SELECT 1 FROM card_contract_match AS match WHERE match.contract_key = contract.contract_key "
        "AND match.match_status = 'matched')"
    ).fetchone()[0]
    if unmatched_contracts:
        errors.append(f"observed product contracts lack a card disposition: {unmatched_contracts}")
    if connection.execute("SELECT COUNT(*) FROM context_chunk").fetchone()[0] == 0:
        errors.append("index must contain deterministic context chunks")
    embedding_objects = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE lower(name) LIKE '%embedding%'"
    ).fetchone()[0]
    if embedding_objects:
        errors.append("embedding indexes are deferred and must not participate in normative index v3")
    mismatched_targets = connection.execute(
        "SELECT target.target_id FROM target_revision AS target "
        "LEFT JOIN observed_artifact AS artifact ON artifact.target_id = target.target_id "
        "GROUP BY target.target_id HAVING target.artifact_count <> COUNT(artifact.artifact_id)"
    ).fetchall()
    errors.extend(f"target artifact count mismatch: {row[0]}" for row in mismatched_targets)
    return errors


def verify_index(path: Path) -> list[str]:
    if not path.is_file():
        return [f"governance index does not exist: {path}"]
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return verify_index_connection(connection)
    except sqlite3.Error as exc:
        return [f"cannot verify governance index: {exc}"]
    finally:
        connection.close()


def verify_index_freshness(source: Path, targets_path: Path, index: Path) -> list[str]:
    errors = verify_index(index)
    if errors:
        return errors
    source_errors = verify_source_database(source)
    if source_errors:
        return ["card source is invalid: " + error for error in source_errors]
    try:
        config = json.loads(targets_path.read_text(encoding="utf-8"))
        if config.get("schema") != "cartridgeflow.governance.targets.v1":
            return ["target registry schema is invalid"]
        source_identity = _source_identity(source)
        connection = sqlite3.connect(f"{index.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            metadata = dict(connection.execute("SELECT key, value FROM registry_metadata"))
            indexed_targets = {
                str(row["target_id"]): dict(row)
                for row in connection.execute("SELECT * FROM target_revision ORDER BY target_id")
            }
        finally:
            connection.close()
        if metadata.get("card_source_publication_digest") != source_identity.get("publication_digest"):
            errors.append("governance index is stale: card source publication changed")
        configured_ids = {str(target["id"]) for target in config.get("targets", [])}
        if configured_ids != set(indexed_targets):
            errors.append("governance index is stale: configured target set changed")
        for target in config.get("targets", []):
            target_id = str(target["id"])
            indexed = indexed_targets.get(target_id)
            if indexed is None:
                continue
            target_path = (ROOT / str(target["path"])).resolve()
            roots = [str(item).replace("\\", "/").strip("/") for item in target.get("governed_roots", [])]
            snapshot, head, remote, dirty_count, content_digest = _target_snapshot(target_path, roots)
            comparisons = {
                "repository path": (str(indexed["repository_path"]), str(target_path)),
                "repository remote": (str(indexed["repository_remote"]), remote),
                "Git HEAD": (str(indexed["git_head"]), head),
                "dirty path count": (int(indexed["dirty_path_count"]), dirty_count),
                "artifact count": (int(indexed["artifact_count"]), len(snapshot)),
                "governed content": (str(indexed["content_digest"]), content_digest),
            }
            for label, (recorded, current) in comparisons.items():
                if recorded != current:
                    errors.append(f"governance index is stale: {target_id} {label} changed")
        return errors
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        return [f"cannot verify governance index freshness: {exc}"]


def index_summary(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            "database": str(path.resolve()),
            "metadata": dict(connection.execute("SELECT key, value FROM registry_metadata ORDER BY key")),
            "targets": [dict(row) for row in connection.execute("SELECT * FROM target_revision ORDER BY target_id")],
            "coverage": [
                dict(row)
                for row in connection.execute(
                    "SELECT coverage_status, COUNT(*) AS count FROM scope_coverage GROUP BY coverage_status ORDER BY coverage_status"
                )
            ],
            "dependencies": [
                dict(row)
                for row in connection.execute(
                    "SELECT dependency_kind, resolution_status, COUNT(*) AS count "
                    "FROM observed_dependency GROUP BY dependency_kind, resolution_status "
                    "ORDER BY dependency_kind, resolution_status"
                )
            ],
            "symbols": [
                dict(row)
                for row in connection.execute(
                    "SELECT language, symbol_kind, COUNT(*) AS count FROM observed_symbol "
                    "GROUP BY language, symbol_kind ORDER BY language, symbol_kind"
                )
            ],
            "contracts": [
                dict(row)
                for row in connection.execute(
                    "SELECT generation, lifecycle, COUNT(*) AS count FROM observed_contract "
                    "GROUP BY generation, lifecycle ORDER BY generation, lifecycle"
                )
            ],
            "findings": [
                dict(row)
                for row in connection.execute(
                    "SELECT severity, finding_type, COUNT(*) AS count FROM finding WHERE status = 'open' "
                    "GROUP BY severity, finding_type ORDER BY severity, finding_type"
                )
            ],
        }
    finally:
        connection.close()


def failing_findings(path: Path, fail_on: str = "warning") -> list[dict[str, Any]]:
    if fail_on not in SEVERITY_RANK:
        raise GovernanceIndexError(f"unknown failure threshold: {fail_on}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM finding_catalog WHERE status = 'open' "
            "ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'error' THEN 1 "
            "WHEN 'warning' THEN 2 ELSE 3 END, finding_type, target_id, artifact_path"
        )
        threshold = SEVERITY_RANK[fail_on]
        return [dict(row) for row in rows if SEVERITY_RANK[str(row["severity"])] >= threshold]
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DATABASE)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("build")
    subparsers.add_parser("verify")
    subparsers.add_parser("summary")
    subparsers.add_parser("findings")
    check = subparsers.add_parser("check")
    check.add_argument("--fail-on", choices=tuple(SEVERITY_RANK), default="warning")
    check.add_argument("--limit", type=int, default=20)
    parser.set_defaults(command="check", fail_on="warning", limit=20)
    args = parser.parse_args()
    try:
        if args.command == "build":
            build_index(args.source.resolve(), args.targets.resolve(), args.index.resolve())
            print(f"Published governance index: {args.index.resolve()}")
            return 0
        if args.command == "verify":
            errors = verify_index_freshness(
                args.source.resolve(), args.targets.resolve(), args.index.resolve()
            )
            if errors:
                print("Governance index verification failed:\n- " + "\n- ".join(errors))
                return 1
            print("Governance index verified.")
            return 0
        if args.command == "summary":
            print(json.dumps(index_summary(args.index.resolve()), ensure_ascii=False, indent=2))
            return 0
        if args.command == "check":
            errors = verify_index_freshness(
                args.source.resolve(), args.targets.resolve(), args.index.resolve()
            )
            if errors:
                print("Governance index verification failed:\n- " + "\n- ".join(errors))
                return 1
            findings = failing_findings(args.index.resolve(), args.fail_on)
            if not findings:
                print(f"Static governance checks passed at severity >= {args.fail_on}.")
                return 0
            print(
                f"Static governance checks failed: {len(findings)} open finding(s) "
                f"at severity >= {args.fail_on}."
            )
            for item in findings[:max(args.limit, 0)]:
                location = ":".join(
                    part for part in (item.get("target_id"), item.get("artifact_path")) if part
                )
                print(
                    f"- [{item['severity']}] {item['rule_id']} -> {item['card_id']} "
                    f"({item['finding_type']}) {location}: {item['message']}"
                )
            hidden = len(findings) - max(args.limit, 0)
            if hidden > 0:
                print(f"- ... {hidden} additional finding(s); run `findings` for the full report.")
            return 1
        connection = sqlite3.connect(f"{args.index.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in connection.execute("SELECT * FROM finding_catalog WHERE status = 'open' ORDER BY severity, finding_type, target_id, artifact_path")]
        finally:
            connection.close()
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, ValueError, json.JSONDecodeError, sqlite3.Error, subprocess.CalledProcessError, GovernanceIndexError) as exc:
        print(f"Governance index command failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
