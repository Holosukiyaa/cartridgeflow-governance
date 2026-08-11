"""FastAPI application for the read-only governance card browser."""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from scripts.compile_context import ContextCompilationError, compile_context
from scripts.build_governance_index import DEFAULT_TARGETS
from scripts.governance_ledger import DEFAULT_LEDGER, evidence_freshness


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "governance-source.sqlite"
DEFAULT_INDEX = ROOT / ".data" / "governance-index.sqlite"
STYLESHEET = ROOT / "viewer" / "static" / "browser.css"


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"governance database is unavailable: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection = _connect(path)
    try:
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def _metadata(path: Path) -> dict[str, str]:
    return {str(row["key"]): str(row["value"]) for row in _rows(path, "SELECT key, value FROM registry_metadata")}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _badge(value: str, kind: str = "neutral") -> str:
    return f'<span class="badge badge-{_e(kind)}">{_e(value)}</span>'


def _nav(active: str) -> str:
    items = (
        ("overview", "/", "总览"),
        ("catalog", "/catalog", "总管目录"),
        ("cards", "/cards", "卡片"),
        ("rules", "/rules", "规则"),
        ("relations", "/relations", "关系"),
        ("coverage", "/coverage", "覆盖"),
        ("dependencies", "/dependencies", "依赖"),
        ("symbols", "/symbols", "符号"),
        ("contracts", "/contracts", "合同"),
        ("findings", "/findings", "诊断"),
        ("checks", "/checks", "检测"),
        ("impact", "/impact", "影响查询"),
        ("context", "/context", "上下文"),
    )
    return "".join(
        f'<a class="nav-link{" active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in items
    )


def _layout(title: str, active: str, body: str) -> HTMLResponse:
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)} | CartridgeFlow 治理</title>
  <link rel="stylesheet" href="/static/browser.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span><span>CartridgeFlow 治理卡片</span></a>
    <span class="read-only">只读</span>
  </header>
  <div class="app-shell">
    <nav class="sidebar" aria-label="主导航">{_nav(active)}</nav>
    <main class="main-content">{body}</main>
  </div>
  <footer>本机只读 · 权威卡片源与生成索引分离</footer>
</body>
</html>"""
    return HTMLResponse(document)


def _table(headers: list[str], rows: list[list[str]], empty: str = "暂无数据") -> str:
    if not rows:
        return f'<div class="empty-state">{_e(empty)}</div>'
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _page_header(title: str, subtitle: str, actions: str = "") -> str:
    return (
        '<div class="page-header"><div>'
        f'<h1>{_e(title)}</h1><p>{_e(subtitle)}</p>'
        f'</div><div class="page-actions">{actions}</div></div>'
    )


def _summary(source_path: Path, index_path: Path, ledger_path: Path, targets_path: Path) -> dict[str, Any]:
    source_metadata = _metadata(source_path)
    index_metadata = _metadata(index_path)
    cards = _rows(source_path, "SELECT card_type, status, COUNT(*) AS count FROM card GROUP BY card_type, status")
    coverage = _rows(index_path, "SELECT coverage_status, COUNT(*) AS count FROM scope_coverage GROUP BY coverage_status")
    findings = _rows(
        index_path,
        "SELECT severity, finding_type, COUNT(*) AS count FROM finding WHERE status = 'open' "
        "GROUP BY severity, finding_type ORDER BY severity, finding_type",
    )
    dependencies = _rows(
        index_path,
        "SELECT dependency_kind, resolution_status, COUNT(*) AS count FROM observed_dependency "
        "GROUP BY dependency_kind, resolution_status ORDER BY dependency_kind, resolution_status",
    )
    targets = _rows(index_path, "SELECT * FROM target_revision ORDER BY target_id")
    checks = _check_runs(source_path, index_path, ledger_path, targets_path, limit=6)
    acceptance = _rows(
        ledger_path,
        "SELECT result.* FROM acceptance_result AS result "
        "JOIN route_run AS route ON route.route_run_id = result.route_run_id "
        "WHERE route.finished_at = (SELECT MAX(finished_at) FROM route_run) "
        "ORDER BY CASE acceptance_kind WHEN 'static' THEN 0 WHEN 'floor' THEN 1 "
        "WHEN 'boundary' THEN 2 WHEN 'scenario' THEN 3 ELSE 4 END",
    )
    return {
        "source_metadata": source_metadata,
        "index_metadata": index_metadata,
        "cards": cards,
        "coverage": coverage,
        "findings": findings,
        "dependencies": dependencies,
        "targets": targets,
        "checks": checks,
        "acceptance": acceptance,
    }


def _check_runs(
    source_path: Path,
    index_path: Path,
    ledger_path: Path,
    targets_path: Path,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    runs = _rows(
        ledger_path,
        "SELECT run.*, route.routing_state, route.footprint_complete, route.source_publication_digest, "
        "route.governance_facts_digest, route.target_config_digest "
        "FROM check_run AS run JOIN route_run AS route ON route.route_run_id = run.route_run_id "
        "ORDER BY run.finished_at DESC, run.checker_id LIMIT ?",
        (limit,),
    )
    for run in runs:
        freshness = evidence_freshness(
            ledger_path,
            source_path,
            index_path,
            targets_path,
            run_id=str(run["run_id"]),
        )
        state = freshness[0] if freshness else {"status": "stale", "dependency_count": 0, "mismatches": []}
        run["is_current"] = state["status"] == "current"
        run["dependency_count"] = state["dependency_count"]
        run["freshness_mismatches"] = state["mismatches"]
    return runs


def create_app(
    source_path: Path = DEFAULT_SOURCE,
    index_path: Path = DEFAULT_INDEX,
    ledger_path: Path = DEFAULT_LEDGER,
    targets_path: Path = DEFAULT_TARGETS,
) -> FastAPI:
    app = FastAPI(title="CartridgeFlow Governance Cards", docs_url=None, redoc_url=None)
    app.state.source_path = source_path.resolve()
    app.state.index_path = index_path.resolve()
    app.state.ledger_path = ledger_path.resolve()
    app.state.targets_path = targets_path.resolve()

    @app.middleware("http")
    async def read_only_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-CartridgeFlow-Governance-Browser"] = "1"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/static/browser.css", include_in_schema=False)
    def stylesheet() -> FileResponse:
        return FileResponse(STYLESHEET, media_type="text/css")

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": app.state.source_path.is_file() and app.state.index_path.is_file() and app.state.ledger_path.is_file(),
                "source": str(app.state.source_path),
                "index": str(app.state.index_path),
                "ledger": str(app.state.ledger_path),
                "read_only": True,
            }
        )

    @app.get("/api/summary", include_in_schema=False)
    def summary_api() -> JSONResponse:
        return JSONResponse(_summary(app.state.source_path, app.state.index_path, app.state.ledger_path, app.state.targets_path))

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        summary = _summary(app.state.source_path, app.state.index_path, app.state.ledger_path, app.state.targets_path)
        card_total = sum(int(item["count"]) for item in summary["cards"] if item["status"] == "active")
        coverage = {item["coverage_status"]: int(item["count"]) for item in summary["coverage"]}
        error_total = sum(
            int(item["count"]) for item in summary["findings"] if item["severity"] in ("blocker", "error")
        )
        warning_total = sum(int(item["count"]) for item in summary["findings"] if item["severity"] == "warning")
        facts_digest = summary["index_metadata"].get("governance_facts_digest", "")
        metrics = f"""<section class="metric-strip" aria-label="治理摘要">
          <div><strong>{card_total}</strong><span>活动卡片</span></div>
          <div><strong>{coverage.get('covered', 0)}</strong><span>已覆盖文件</span></div>
          <div class="metric-danger"><strong>{error_total}</strong><span>阻断诊断</span></div>
          <div class="metric-warning"><strong>{warning_total}</strong><span>覆盖警告</span></div>
        </section>"""
        acceptance_labels = {
            "static": "静态",
            "floor": "楼层",
            "boundary": "边界",
            "scenario": "场景",
            "complete": "完整",
        }
        acceptance_strip = '<section class="acceptance-strip" aria-label="验收状态">' + "".join(
            '<div><span>' + _e(acceptance_labels.get(str(item["acceptance_kind"]), str(item["acceptance_kind"])))
            + '</span>' + _badge(
                str(item["status"]),
                "ok" if item["status"] == "passed" else "danger" if item["status"] in {"failed", "error"} else "warning",
            ) + '</div>'
            for item in summary["acceptance"]
        ) + '</section>'
        targets = _table(
            ["目标", "角色", "提交", "状态", "文件"],
            [
                [
                    _e(item["target_id"]),
                    _e(item["role"]),
                    f'<code>{_e(str(item["git_head"])[:12])}</code>',
                    _badge("clean" if int(item["dirty_path_count"]) == 0 else f"dirty {item['dirty_path_count']}", "ok" if int(item["dirty_path_count"]) == 0 else "warning"),
                    _e(item["artifact_count"]),
                ]
                for item in summary["targets"]
            ],
        )
        recent_checks = _table(
            ["检测器", "结果", "证据", "完成时间", "耗时"],
            [
                [
                    f'<a href="/checks/{quote(str(item["run_id"]), safe="")}"><code>{_e(item["checker_id"])}</code></a>',
                    _badge(str(item["status"]), "ok" if item["status"] == "passed" else "danger"),
                    _badge("当前" if item["is_current"] else "过期", "ok" if item["is_current"] else "warning"),
                    _e(item["finished_at"]),
                    _e(f"{item['duration_ms']} ms"),
                ]
                for item in summary["checks"]
            ],
            "尚未运行统一检测",
        )
        cards = _rows(
            app.state.source_path,
            "SELECT * FROM card_catalog WHERE status = 'active' "
            "ORDER BY CASE card_type WHEN 'constitution' THEN 0 WHEN 'floor' THEN 1 ELSE 2 END, card_id",
        )
        card_table = _table(
            ["卡片", "类型", "摘要", "作用域", "规则"],
            [
                [
                    f'<a href="/cards/{quote(str(item["card_id"]), safe="")}"><code>{_e(item["card_id"])}</code><br><span>{_e(item["title"])}</span></a>',
                    _badge(str(item["card_type"]), "accent"),
                    _e(item["summary"]),
                    _e(item["scope_count"]),
                    _e(item["rule_count"]),
                ]
                for item in cards
            ],
        )
        finding_rows = _rows(
            app.state.index_path,
            "SELECT * FROM finding_catalog WHERE status = 'open' "
            "ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, "
            "target_id, artifact_path LIMIT 12",
        )
        finding_table = _table(
            ["级别", "规则 / 卡片", "位置", "诊断"],
            [
                [
                    _badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"),
                    f'<code>{_e(item["rule_id"])}</code><br><a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"])}</a>',
                    f'<code>{_e(item["target_id"])}:{_e(item["artifact_path"])}</code>',
                    _e(item["message"]),
                ]
                for item in finding_rows
            ],
        )
        body = (
            _page_header("治理总览", f"事实摘要 {facts_digest[:16]} · 扫描器 {summary['index_metadata'].get('scanner_version', '')}")
            + metrics
            + acceptance_strip
            + '<section class="content-section"><div class="section-heading"><h2>目标快照</h2></div>'
            + targets
            + '</section><section class="content-section"><div class="section-heading"><h2>最近检测</h2><a href="/checks">查看全部</a></div>'
            + recent_checks
            + '</section><section class="content-section"><div class="section-heading"><h2>活动卡片</h2><a href="/cards">查看全部</a></div>'
            + card_table
            + '</section><section class="content-section"><div class="section-heading"><h2>开放诊断</h2><a href="/findings">查看全部</a></div>'
            + finding_table
            + "</section>"
        )
        return _layout("治理总览", "overview", body)

    @app.get("/catalog", response_class=HTMLResponse, include_in_schema=False)
    def manager_catalog() -> HTMLResponse:
        cards = _rows(
            app.state.source_path,
            "SELECT * FROM card_catalog WHERE status = 'active' ORDER BY "
            "CASE card_type WHEN 'constitution' THEN 0 WHEN 'floor' THEN 1 WHEN 'boundary' THEN 2 "
            "WHEN 'knowledge' THEN 3 ELSE 4 END, card_id",
        )
        relations = _rows(app.state.source_path, "SELECT * FROM relation_catalog ORDER BY relation_type, source_card_id")
        checkers = _rows(
            app.state.source_path,
            "SELECT checker.*, COUNT(DISTINCT binding.rule_id) AS rule_count, "
            "COUNT(DISTINCT scenario.scenario_id) AS scenario_count FROM checker "
            "LEFT JOIN rule_check_binding AS binding ON binding.checker_id = checker.checker_id "
            "LEFT JOIN scenario_checker_binding AS scenario ON scenario.checker_id = checker.checker_id "
            "GROUP BY checker.checker_id ORDER BY checker.checker_stage, checker.checker_id",
        )
        card_table = _table(
            ["卡片", "类型", "作用域", "规则", "合同", "关系"],
            [
                [
                    f'<a href="/cards/{quote(str(item["card_id"]), safe="")}"><code>{_e(item["card_id"])}</code></a>',
                    _badge(str(item["card_type"]), "accent"), _e(item["scope_count"]),
                    _e(item["rule_count"]), _e(item["contract_count"]),
                    _e(int(item["outgoing_relation_count"]) + int(item["incoming_relation_count"])),
                ]
                for item in cards
            ],
        )
        relation_table = _table(
            ["来源", "关系", "目标"],
            [[f'<code>{_e(item["source_card_id"])}</code>', _badge(str(item["relation_type"])), f'<code>{_e(item["target_card_id"])}</code>'] for item in relations],
        )
        checker_table = _table(
            ["检测器", "阶段", "输出", "规则", "场景"],
            [[f'<code>{_e(item["checker_id"])}</code>', _badge(str(item["checker_stage"]), "accent"), _e(item["output_contract"]), _e(item["rule_count"]), _e(item["scenario_count"])] for item in checkers],
        )
        body = (
            _page_header("总管目录", "只展示治理骨架，不展开卡片正文")
            + f'<section class="content-section"><div class="section-heading"><h2>卡片索引</h2><span>{len(cards)} 张</span></div>{card_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>关系索引</h2><a href="/relations">分视图查看</a></div>{relation_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>检测与场景</h2><span>{len(checkers)} 个检测器</span></div>{checker_table}</section>'
        )
        return _layout("总管目录", "catalog", body)

    @app.get("/rules", response_class=HTMLResponse, include_in_schema=False)
    def rules_page(
        severity: str = Query(default="", pattern="^(|blocker|error|warning|info)$"),
        checker: str = Query(default="", max_length=200),
        q: str = Query(default="", max_length=300),
    ) -> HTMLResponse:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if severity:
            clauses.append("catalog.severity = ?")
            params.append(severity)
        if checker.strip():
            clauses.append("binding.checker_id = ?")
            params.append(checker.strip())
        if q.strip():
            clauses.append("(lower(catalog.rule_id) LIKE lower(?) OR lower(catalog.statement) LIKE lower(?) OR lower(catalog.card_id) LIKE lower(?))")
            params.extend((f"%{q.strip()}%", f"%{q.strip()}%", f"%{q.strip()}%"))
        rows = _rows(
            app.state.source_path,
            "SELECT catalog.*, group_concat(binding.checker_id, ', ') AS checkers FROM rule_catalog AS catalog "
            "LEFT JOIN rule_check_binding AS binding ON binding.rule_id = catalog.rule_id "
            f"WHERE {' AND '.join(clauses)} GROUP BY catalog.rule_id ORDER BY "
            "CASE catalog.severity WHEN 'blocker' THEN 0 WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, catalog.rule_id",
            tuple(params),
        )
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="规则 ID、卡片或约束" aria-label="搜索规则">
          <select name="severity" aria-label="严重级别"><option value="">全部级别</option>{''.join(f'<option value="{value}"{" selected" if severity == value else ""}>{value}</option>' for value in ("blocker", "error", "warning", "info"))}</select>
          <input type="text" name="checker" value="{_e(checker)}" placeholder="检测器 ID" aria-label="按检测器筛选">
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["级别", "规则", "所属卡片", "约束", "检测器"],
            [[_badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"), f'<code>{_e(item["rule_id"])}</code>', f'<a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"])}</a>', _e(item["statement"]), f'<code>{_e(item["checkers"] or "none")}</code>'] for item in rows],
        )
        return _layout("规则", "rules", _page_header("规则目录", f"{len(rows)} 条") + form + table)

    @app.get("/relations", response_class=HTMLResponse, include_in_schema=False)
    def relations_page(
        view: str = Query(default="ownership", pattern="^(ownership|dependency|impact)$"),
        q: str = Query(default="", max_length=300),
    ) -> HTMLResponse:
        relation_types = {
            "ownership": ("governs", "has_producer", "has_consumer", "explains"),
            "dependency": ("depends_on",),
            "impact": ("impacts", "related_to"),
        }[view]
        placeholders = ", ".join("?" for _ in relation_types)
        params: list[Any] = list(relation_types)
        clauses = [f"relation_type IN ({placeholders})"]
        if q.strip():
            clauses.append("(lower(source_card_id) LIKE lower(?) OR lower(target_card_id) LIKE lower(?) OR lower(rationale) LIKE lower(?))")
            params.extend((f"%{q.strip()}%", f"%{q.strip()}%", f"%{q.strip()}%"))
        rows = _rows(
            app.state.source_path,
            f"SELECT * FROM relation_catalog WHERE {' AND '.join(clauses)} ORDER BY relation_type, source_card_id, target_card_id",
            tuple(params),
        )
        tabs = '<div class="segmented" role="navigation">' + "".join(
            f'<a class="{"active" if view == key else ""}" href="/relations?view={key}">{label}</a>'
            for key, label in (("ownership", "归属"), ("dependency", "依赖"), ("impact", "影响"))
        ) + "</div>"
        form = f'<form class="filter-bar compact-filter" method="get"><input type="hidden" name="view" value="{_e(view)}"><input type="search" name="q" value="{_e(q)}" placeholder="卡片或关系理由"><button type="submit">查询</button></form>'
        table = _table(
            ["来源卡片", "关系", "目标卡片", "理由"],
            [[f'<a href="/cards/{quote(str(item["source_card_id"]), safe="")}">{_e(item["source_card_id"])}</a>', _badge(str(item["relation_type"]), "accent"), f'<a href="/cards/{quote(str(item["target_card_id"]), safe="")}">{_e(item["target_card_id"])}</a>', _e(item["rationale"])] for item in rows],
        )
        return _layout("关系", "relations", _page_header("关系视图", "归属、依赖与影响分开阅读", tabs) + form + table)

    @app.get("/cards", response_class=HTMLResponse, include_in_schema=False)
    def cards_page(
        q: str = Query(default="", max_length=200),
        card_type: str = Query(default=""),
        status: str = Query(default="active", pattern="^(|active|draft|retired)$"),
        floor: str = Query(default="", max_length=200),
        scope: str = Query(default="", max_length=300),
        mode: str = Query(default="fts", pattern="^(fts|exact)$"),
    ) -> HTMLResponse:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if status:
            clauses.append("card.status = ?")
            params.append(status)
        if card_type:
            clauses.append("card.card_type = ?")
            params.append(card_type)
        if floor:
            clauses.append(
                "(card.card_id = ? OR card.card_id IN (SELECT card_id FROM knowledge_profile WHERE floor_card_id = ?) "
                "OR card.card_id IN (SELECT source_card_id FROM card_relation WHERE target_card_id = ? AND relation_type IN ('governs', 'has_producer', 'has_consumer')) "
                "OR card.card_id IN (SELECT target_card_id FROM card_relation WHERE source_card_id = ? AND relation_type IN ('depends_on', 'governs')))"
            )
            params.extend((floor, floor, floor, floor))
        if scope.strip():
            clauses.append(
                "EXISTS (SELECT 1 FROM card_scope AS filtered_scope WHERE filtered_scope.card_id = card.card_id "
                "AND lower(filtered_scope.selector) LIKE lower(?))"
            )
            params.append(f"%{scope.strip()}%")
        if q.strip():
            if mode == "fts":
                clauses.append(
                    "(card.card_id IN (SELECT card_id FROM card_fts WHERE card_fts MATCH ?) "
                    "OR instr(lower(card.title), lower(?)) > 0 "
                    "OR instr(lower(card.summary), lower(?)) > 0 "
                    "OR instr(lower(card.body_markdown), lower(?)) > 0)"
                )
                params.extend(
                    (
                        '"' + q.strip().replace('"', '""') + '"',
                        q.strip(),
                        q.strip(),
                        q.strip(),
                    )
                )
            else:
                clauses.append("(card.card_id = ? OR lower(card.title) LIKE lower(?))")
                params.extend((q.strip(), f"%{q.strip()}%"))
        sql = (
            "SELECT card.*, catalog.scope_count, catalog.rule_count FROM card "
            "JOIN card_catalog AS catalog ON catalog.card_id = card.card_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY CASE card.card_type WHEN 'constitution' THEN 0 WHEN 'floor' THEN 1 "
            "WHEN 'boundary' THEN 2 ELSE 3 END, card.card_id"
        )
        try:
            cards = _rows(app.state.source_path, sql, tuple(params))
        except sqlite3.OperationalError as exc:
            cards = []
            search_error = str(exc)
        else:
            search_error = ""
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="卡片 ID、标题或正文" aria-label="搜索卡片">
          <select name="mode" aria-label="搜索方式"><option value="fts"{' selected' if mode == 'fts' else ''}>全文</option><option value="exact"{' selected' if mode == 'exact' else ''}>精确</option></select>
          <select name="card_type" aria-label="卡片类型"><option value="">全部类型</option>{''.join(f'<option value="{value}"{" selected" if card_type == value else ""}>{label}</option>' for value, label in (("constitution", "宪法"), ("floor", "楼层"), ("boundary", "边界"), ("knowledge", "知识"), ("task", "任务")))}</select>
          <select name="status" aria-label="卡片状态"><option value="">全部状态</option>{''.join(f'<option value="{value}"{" selected" if status == value else ""}>{label}</option>' for value, label in (("active", "活动"), ("draft", "草案"), ("retired", "退役")))}</select>
          <input type="text" name="floor" value="{_e(floor)}" placeholder="楼层卡 ID" aria-label="按楼层筛选">
          <input type="text" name="scope" value="{_e(scope)}" placeholder="作用域路径" aria-label="按作用域筛选">
          <button type="submit">查询</button>
        </form>"""
        error = f'<div class="inline-alert danger">{_e(search_error)}</div>' if search_error else ""
        table = _table(
            ["卡片", "类型", "摘要", "修订", "作用域", "规则"],
            [
                [
                    f'<a href="/cards/{quote(str(item["card_id"]), safe="")}"><code>{_e(item["card_id"])}</code><br>{_e(item["title"])}</a>',
                    _badge(str(item["card_type"]), "accent"),
                    _e(item["summary"]),
                    _e(item["revision"]) if item["revision"] is not None else _badge("当前内容 · 无历史", "ok"),
                    _e(item["scope_count"]),
                    _e(item["rule_count"]),
                ]
                for item in cards
            ],
            "没有匹配的卡片",
        )
        body = _page_header("卡片目录", f"{len(cards)} 张匹配卡片") + form + error + table
        return _layout("卡片目录", "cards", body)

    @app.get("/cards/{card_id}", response_class=HTMLResponse, include_in_schema=False)
    def card_detail(card_id: str) -> HTMLResponse:
        cards = _rows(app.state.source_path, "SELECT * FROM card WHERE card_id = ?", (card_id,))
        if not cards:
            raise HTTPException(status_code=404, detail="card not found")
        card = cards[0]
        scopes = _rows(app.state.source_path, "SELECT * FROM card_scope WHERE card_id = ? ORDER BY scope_id", (card_id,))
        rules = _rows(
            app.state.source_path,
            "SELECT rule.*, group_concat(binding.checker_id, ', ') AS checkers FROM rule "
            "LEFT JOIN rule_check_binding AS binding ON binding.rule_id = rule.rule_id "
            "WHERE rule.card_id = ? GROUP BY rule.rule_id ORDER BY rule.rule_id",
            (card_id,),
        )
        relations = _rows(
            app.state.source_path,
            "SELECT * FROM relation_catalog WHERE source_card_id = ? OR target_card_id = ? ORDER BY relation_id",
            (card_id, card_id),
        )
        coverage = _rows(
            app.state.index_path,
            "SELECT artifact.target_id, artifact.artifact_path, artifact.worktree_state, match.ownership "
            "FROM scope_match AS match JOIN observed_artifact AS artifact ON artifact.artifact_id = match.artifact_id "
            "WHERE match.card_id = ? ORDER BY artifact.target_id, artifact.artifact_path LIMIT 300",
            (card_id,),
        )
        findings = _rows(
            app.state.index_path,
            "SELECT * FROM finding_catalog WHERE status = 'open' AND card_id = ? "
            "ORDER BY severity, target_id, artifact_path",
            (card_id,),
        )
        revisions = _rows(
            app.state.source_path,
            "SELECT revision, published_at, change_summary, content_digest, snapshot_json "
            "FROM card_revision WHERE card_id = ? ORDER BY revision DESC",
            (card_id,),
        )
        responsibilities = _rows(
            app.state.source_path,
            "SELECT * FROM card_responsibility WHERE card_id = ? "
            "ORDER BY responsibility_kind, item_order",
            (card_id,),
        )
        interfaces = _rows(
            app.state.source_path,
            "SELECT * FROM card_interface WHERE card_id = ? ORDER BY direction, name",
            (card_id,),
        )
        examples = _rows(
            app.state.source_path,
            "SELECT * FROM card_example WHERE card_id = ? ORDER BY example_kind DESC, title",
            (card_id,),
        )
        evidence_requirements = _rows(
            app.state.source_path,
            "SELECT * FROM card_evidence_requirement WHERE card_id = ? ORDER BY evidence_kind, requirement_id",
            (card_id,),
        )
        source_references = _rows(
            app.state.source_path,
            "SELECT * FROM card_source_reference WHERE card_id = ? ORDER BY target_id, reference",
            (card_id,),
        )
        contract_bindings = _rows(
            app.state.source_path,
            "SELECT * FROM card_contract_binding WHERE card_id = ? "
            "ORDER BY disposition, contract_id, version_constraint",
            (card_id,),
        )
        contract_matches = {
            str(item["binding_id"]): item
            for item in _rows(
                app.state.index_path,
                "SELECT binding_id, match_status, contract_key FROM card_contract_match WHERE card_id = ?",
                (card_id,),
            )
        }
        for binding in contract_bindings:
            match = contract_matches.get(str(binding["binding_id"]), {})
            binding["match_status"] = match.get("match_status")
            binding["contract_key"] = match.get("contract_key")
        knowledge_profile = _rows(
            app.state.source_path,
            "SELECT * FROM knowledge_profile WHERE card_id = ?",
            (card_id,),
        )
        task_directives = _rows(
            app.state.source_path,
            "SELECT * FROM task_directive WHERE card_id = ? ORDER BY directive_kind, item_order",
            (card_id,),
        )
        other_cards = _rows(
            app.state.source_path,
            "SELECT card_id, card_type, title, summary FROM card WHERE status = 'active' AND card_id <> ?",
            (card_id,),
        )
        ignored_terms = {"card", "floor", "boundary", "knowledge", "task", "project", "v060", "v070"}
        terms = {
            term.casefold()
            for term in str(card["card_id"]).replace("-", ".").split(".")
            if len(term) >= 3 and term.casefold() not in ignored_terms
        }
        suggestions = []
        for candidate in other_cards:
            haystack = " ".join((str(candidate["card_id"]), str(candidate["title"]), str(candidate["summary"]))).casefold()
            score = sum(1 for term in terms if term in haystack)
            if score:
                suggestions.append((score, candidate))
        suggestions.sort(key=lambda item: (-item[0], str(item[1]["card_id"])))
        scope_table = _table(
            ["目标", "选择器", "极性", "所有权", "理由"],
            [[_e(item["target_id"]), f'<code>{_e(item["selector"])}</code>', _badge(str(item["polarity"])), _badge(str(item["ownership"]), "accent"), _e(item["rationale"])] for item in scopes],
        )
        rule_table = _table(
            ["规则", "级别", "约束", "检测器"],
            [[f'<code>{_e(item["rule_id"])}</code>', _badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"), _e(item["statement"]), f'<code>{_e(item["checkers"] or "none")}</code>'] for item in rules],
        )
        relation_table = _table(
            ["来源", "关系", "目标", "必需"],
            [[f'<a href="/cards/{quote(str(item["source_card_id"]), safe="")}">{_e(item["source_card_id"])}</a>', _badge(str(item["relation_type"])), f'<a href="/cards/{quote(str(item["target_card_id"]), safe="")}">{_e(item["target_card_id"])}</a>', _e(item["required"])] for item in relations],
        )
        coverage_table = _table(
            ["目标", "文件", "状态", "归属"],
            [[_e(item["target_id"]), f'<code>{_e(item["artifact_path"])}</code>', _badge(str(item["worktree_state"]), "warning" if item["worktree_state"] != "tracked" else "neutral"), _e(item["ownership"])] for item in coverage],
        )
        finding_table = _table(
            ["级别", "规则", "位置", "诊断"],
            [[_badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"), f'<code>{_e(item["rule_id"])}</code>', f'<code>{_e(item["target_id"])}:{_e(item["artifact_path"])}</code>', _e(item["message"])] for item in findings],
            "该卡片没有开放诊断",
        )
        revision_table = _table(
            ["修订", "发布时间", "标题快照", "变更", "摘要"],
            [
                [
                    _badge(f"r{item['revision']}", "accent" if item["revision"] == card["revision"] else "neutral"),
                    _e(item["published_at"]),
                    _e(json.loads(str(item["snapshot_json"]))["title"]),
                    _e(item["change_summary"]),
                    f'<code>{_e(str(item["content_digest"])[:12])}</code>',
                ]
                for item in revisions
            ],
        )
        responsibility_table = _table(
            ["边界", "内容"],
            [
                [
                    _badge("负责" if item["responsibility_kind"] == "owns" else "不负责", "ok" if item["responsibility_kind"] == "owns" else "warning"),
                    _e(item["statement"]),
                ]
                for item in responsibilities
            ],
        )
        interface_table = _table(
            ["方向", "接口", "合同", "对端", "说明"],
            [
                [
                    _badge(str(item["direction"]), "accent"), _e(item["name"]),
                    f'<code>{_e(item["contract_ref"] or "-")}</code>',
                    f'<a href="/cards/{quote(str(item["counterparty_card_id"]), safe="")}">{_e(item["counterparty_card_id"] or "-")}</a>' if item["counterparty_card_id"] else "-",
                    _e(item["description"]),
                ]
                for item in interfaces
            ],
        )
        example_table = _table(
            ["结果", "示例", "预期", "关联规则"],
            [
                [
                    _badge(str(item["example_kind"]), "ok" if item["example_kind"] == "valid" else "danger"),
                    f'<strong>{_e(item["title"])}</strong><br>{_e(item["description"])}',
                    _e(item["expected_outcome"]),
                    f'<code>{_e(item["expected_rule_id"] or "-")}</code>',
                ]
                for item in examples
            ],
        )
        evidence_table = _table(
            ["证据类型", "要求"],
            [[_badge(str(item["evidence_kind"])), _e(item["statement"])] for item in evidence_requirements],
        )
        source_table = _table(
            ["目标", "类型", "来源", "用途"],
            [[_e(item["target_id"]), _badge(str(item["reference_kind"])), f'<code>{_e(item["reference"])}</code>', _e(item["purpose"])] for item in source_references],
        )
        contract_table = _table(
            ["合同", "版本", "角色", "分类", "匹配"],
            [
                [
                    f'<a href="/contracts?q={quote(str(item["contract_id"]))}"><code>{_e(item["contract_id"])}</code></a>',
                    _e(item["version_constraint"]), _badge(str(item["binding_role"])),
                    _badge(str(item["disposition"]), "accent" if item["disposition"] == "boundary" else "neutral"),
                    _badge(str(item["match_status"] or "未索引"), "ok" if item["match_status"] == "matched" else "warning"),
                ]
                for item in contract_bindings
            ],
        )
        directive_table = _table(
            ["指令", "内容"],
            [[_badge(str(item["directive_kind"]), "accent"), _e(item["value"])] for item in task_directives],
        )
        suggestion_table = _table(
            ["建议卡片", "类型", "理由"],
            [
                [
                    f'<a href="/cards/{quote(str(item["card_id"]), safe="")}"><code>{_e(item["card_id"])}</code><br>{_e(item["title"])}</a>',
                    _badge(str(item["card_type"])), _e(f"标识词重合度 {score}"),
                ]
                for score, item in suggestions[:5]
            ],
            "没有词面相关建议",
        )
        profile_section = ""
        if knowledge_profile:
            profile = knowledge_profile[0]
            profile_section = (
                '<section class="content-section knowledge-profile"><div class="section-heading"><h2>知识卡定位</h2>'
                '<span>当前可复用知识 · 无修订历史</span></div>'
                f'<dl class="fact-grid"><div><dt>解释楼层</dt><dd><a href="/cards/{quote(str(profile["floor_card_id"]), safe="")}">{_e(profile["floor_card_id"])}</a></dd></div>'
                f'<div><dt>读者</dt><dd>{_e(profile["audience"])}</dd></div>'
                f'<div><dt>适用范围</dt><dd>{_e(profile["applicability"])}</dd></div>'
                f'<div><dt>非目标</dt><dd>{_e(profile["non_goals"])}</dd></div></dl></section>'
            )
        revision_section = (
            f'<section class="content-section"><div class="section-heading"><h2>修订历史</h2><span>{len(revisions)} 个快照</span></div>{revision_table}</section>'
            if revisions
            else '<section class="content-section"><div class="section-heading"><h2>内容状态</h2></div>'
                 f'<div class="inline-alert current-only">此卡只表达当前可复用知识，不保存历史。内容摘要 <code>{_e(str(card["content_digest"])[:16])}</code></div></section>'
        )
        revision_badge = _badge(f"r{card['revision']}", "neutral") if card["revision"] is not None else _badge("当前内容 · 无历史", "ok")
        body = (
            _page_header(str(card["title"]), str(card["summary"]), _badge(str(card["card_type"]), "accent") + revision_badge)
            + profile_section
            + f'<section class="reader"><pre>{_e(card["body_markdown"])}</pre></section>'
            + f'<section class="content-section"><div class="section-heading"><h2>负责与排除</h2></div>{responsibility_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>作用域</h2><span>{len(coverage)} 个命中文件</span></div>{scope_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>输入与输出</h2></div>{interface_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>规则</h2></div>{rule_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>有效与无效示例</h2></div>{example_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>证据要求</h2></div>{evidence_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>源码依据</h2></div>{source_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>产品合同分类</h2></div>{contract_table}</section>'
            + (f'<section class="content-section"><div class="section-heading"><h2>任务指令</h2></div>{directive_table}</section>' if task_directives else "")
            + f'<section class="content-section"><div class="section-heading"><h2>关系</h2></div>{relation_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>开放诊断</h2></div>{finding_table}</section>'
            + revision_section
            + f'<section class="content-section"><div class="section-heading"><h2>覆盖文件</h2></div>{coverage_table}</section>'
            + f'<section class="content-section advisory"><div class="section-heading"><h2>相关卡片建议</h2><span>非规范建议，不参与判定</span></div>{suggestion_table}</section>'
        )
        return _layout(str(card["title"]), "cards", body)

    @app.get("/coverage", response_class=HTMLResponse, include_in_schema=False)
    def coverage_page(
        status: str = Query(default="", pattern="^(|covered|uncovered|ambiguous)$"),
        target: str = Query(default=""),
        q: str = Query(default="", max_length=300),
    ) -> HTMLResponse:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if status:
            clauses.append("coverage_status = ?")
            params.append(status)
        if target:
            clauses.append("target_id = ?")
            params.append(target)
        if q.strip():
            clauses.append("lower(artifact_path) LIKE lower(?)")
            params.append(f"%{q.strip()}%")
        rows = _rows(
            app.state.index_path,
            f"SELECT * FROM coverage_catalog WHERE {' AND '.join(clauses)} "
            "ORDER BY CASE coverage_status WHEN 'ambiguous' THEN 0 WHEN 'uncovered' THEN 1 ELSE 2 END, "
            "target_id, artifact_path LIMIT 500",
            tuple(params),
        )
        targets = _rows(app.state.index_path, "SELECT target_id FROM target_revision ORDER BY target_id")
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="文件路径" aria-label="搜索文件路径">
          <select name="status" aria-label="覆盖状态"><option value="">全部状态</option>{''.join(f'<option value="{value}"{" selected" if status == value else ""}>{label}</option>' for value, label in (("covered", "已覆盖"), ("uncovered", "未覆盖"), ("ambiguous", "冲突")))}</select>
          <select name="target" aria-label="目标仓库"><option value="">全部目标</option>{''.join(f'<option value="{_e(item["target_id"])}"{" selected" if target == item["target_id"] else ""}>{_e(item["target_id"])}</option>' for item in targets)}</select>
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["状态", "目标", "文件", "主要所有者", "工作区"],
            [[_badge(str(item["coverage_status"]), "ok" if item["coverage_status"] == "covered" else "danger" if item["coverage_status"] == "ambiguous" else "warning"), _e(item["target_id"]), f'<code>{_e(item["artifact_path"])}</code>', f'<code>{_e(", ".join(json.loads(item["primary_card_ids_json"])))}</code>', _badge(str(item["worktree_state"]), "warning" if item["worktree_state"] != "tracked" else "neutral")] for item in rows],
        )
        return _layout("覆盖", "coverage", _page_header("作用域覆盖", f"显示 {len(rows)} 条") + form + table)

    @app.get("/dependencies", response_class=HTMLResponse, include_in_schema=False)
    def dependencies_page(
        kind: str = Query(default=""),
        status: str = Query(default="", pattern="^(|resolved|external|unresolved)$"),
        q: str = Query(default="", max_length=300),
    ) -> HTMLResponse:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if kind:
            clauses.append("dependency_kind = ?")
            params.append(kind)
        if status:
            clauses.append("resolution_status = ?")
            params.append(status)
        if q.strip():
            clauses.append("(lower(source_artifact_path) LIKE lower(?) OR lower(target_reference) LIKE lower(?))")
            params.extend((f"%{q.strip()}%", f"%{q.strip()}%"))
        rows = _rows(
            app.state.index_path,
            f"SELECT * FROM dependency_catalog WHERE {' AND '.join(clauses)} "
            "ORDER BY target_id, source_artifact_path, target_reference LIMIT 500",
            tuple(params),
        )
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="来源文件或目标引用" aria-label="搜索依赖">
          <select name="kind" aria-label="依赖类型"><option value="">全部语言</option><option value="python-import"{' selected' if kind == 'python-import' else ''}>Python</option><option value="typescript-import"{' selected' if kind == 'typescript-import' else ''}>TypeScript</option><option value="go-import"{' selected' if kind == 'go-import' else ''}>Go</option></select>
          <select name="status" aria-label="解析状态"><option value="">全部状态</option>{''.join(f'<option value="{value}"{" selected" if status == value else ""}>{label}</option>' for value, label in (("resolved", "已解析"), ("external", "外部"), ("unresolved", "未解析")))}</select>
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["类型", "来源文件", "目标引用", "解析文件", "状态"],
            [[_badge(str(item["dependency_kind"]), "accent"), f'<code>{_e(item["source_artifact_path"])}</code>', f'<code>{_e(item["target_reference"])}</code>', f'<code>{_e(item["resolved_artifact_path"] or "-")}</code>', _badge(str(item["resolution_status"]), "ok" if item["resolution_status"] == "resolved" else "warning" if item["resolution_status"] == "unresolved" else "neutral")] for item in rows],
        )
        return _layout("依赖", "dependencies", _page_header("源码依赖", f"显示 {len(rows)} 条") + form + table)

    @app.get("/symbols", response_class=HTMLResponse, include_in_schema=False)
    def symbols_page(
        q: str = Query(default="", max_length=300),
        language: str = Query(default=""),
        kind: str = Query(default=""),
        card: str = Query(default="", max_length=200),
    ) -> HTMLResponse:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if q.strip():
            clauses.append("(lower(qualified_name) LIKE lower(?) OR lower(artifact_path) LIKE lower(?))")
            params.extend((f"%{q.strip()}%", f"%{q.strip()}%"))
        if language:
            clauses.append("language = ?")
            params.append(language)
        if kind:
            clauses.append("symbol_kind = ?")
            params.append(kind)
        if card:
            clauses.append("EXISTS (SELECT 1 FROM json_each(primary_card_ids_json) WHERE value = ?)")
            params.append(card)
        rows = _rows(
            app.state.index_path,
            f"SELECT * FROM symbol_catalog WHERE {' AND '.join(clauses)} "
            "ORDER BY target_id, artifact_path, line_start LIMIT 1000",
            tuple(params),
        )
        languages = _rows(app.state.index_path, "SELECT DISTINCT language FROM observed_symbol ORDER BY language")
        kinds = _rows(app.state.index_path, "SELECT DISTINCT symbol_kind FROM observed_symbol ORDER BY symbol_kind")
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="符号或文件路径" aria-label="搜索符号">
          <select name="language" aria-label="语言"><option value="">全部语言</option>{''.join(f'<option value="{_e(item["language"])}"{" selected" if language == item["language"] else ""}>{_e(item["language"])}</option>' for item in languages)}</select>
          <select name="kind" aria-label="符号类型"><option value="">全部类型</option>{''.join(f'<option value="{_e(item["symbol_kind"])}"{" selected" if kind == item["symbol_kind"] else ""}>{_e(item["symbol_kind"])}</option>' for item in kinds)}</select>
          <input type="text" name="card" value="{_e(card)}" placeholder="所属楼层卡 ID" aria-label="按卡片筛选">
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["符号", "语言 / 类型", "位置", "可见性", "所有者"],
            [
                [
                    f'<code>{_e(item["qualified_name"])}</code>',
                    _badge(str(item["language"]), "accent") + _badge(str(item["symbol_kind"])),
                    f'<code>{_e(item["target_id"])}:{_e(item["artifact_path"])}:{_e(item["line_start"])}</code>',
                    _badge(str(item["visibility"])),
                    " ".join(f'<a href="/cards/{quote(str(owner), safe="")}">{_e(owner)}</a>' for owner in json.loads(str(item["primary_card_ids_json"]))),
                ]
                for item in rows
            ],
        )
        return _layout("符号", "symbols", _page_header("符号视图", f"显示 {len(rows)} 个源码符号") + form + table)

    @app.get("/contracts", response_class=HTMLResponse, include_in_schema=False)
    def contracts_page(
        q: str = Query(default="", max_length=300),
        generation: str = Query(default=""),
        lifecycle: str = Query(default=""),
        disposition: str = Query(default=""),
        card: str = Query(default="", max_length=200),
    ) -> HTMLResponse:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if q.strip():
            clauses.append("(lower(contract_id) LIKE lower(?) OR lower(display_name) LIKE lower(?) OR lower(domain) LIKE lower(?))")
            params.extend((f"%{q.strip()}%", f"%{q.strip()}%", f"%{q.strip()}%"))
        if generation:
            clauses.append("generation = ?")
            params.append(generation)
        if lifecycle:
            clauses.append("lifecycle = ?")
            params.append(lifecycle)
        if card:
            clauses.append("card_id = ?")
            params.append(card)
        rows = _rows(
            app.state.index_path,
            f"SELECT * FROM contract_catalog WHERE {' AND '.join(clauses)} ORDER BY generation, contract_id, version",
            tuple(params),
        )
        for item in rows:
            details = json.loads(str(item["binding_details_json"] or "{}"))
            item["disposition"] = details.get("disposition", "unclassified")
            item["binding_role"] = details.get("binding_role", "-")
            item["usage_count"] = _rows(
                app.state.index_path,
                "SELECT COUNT(*) AS count FROM observed_contract_usage WHERE contract_key = ?",
                (item["contract_key"],),
            )[0]["count"]
        if disposition:
            rows = [item for item in rows if item["disposition"] == disposition]
        generations = _rows(app.state.index_path, "SELECT DISTINCT generation FROM observed_contract ORDER BY generation")
        lifecycles = _rows(app.state.index_path, "SELECT DISTINCT lifecycle FROM observed_contract ORDER BY lifecycle")
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="合同 ID、名称或领域" aria-label="搜索合同">
          <select name="generation" aria-label="代际"><option value="">全部代际</option>{''.join(f'<option value="{_e(item["generation"])}"{" selected" if generation == item["generation"] else ""}>{_e(item["generation"])}</option>' for item in generations)}</select>
          <select name="lifecycle" aria-label="生命周期"><option value="">全部状态</option>{''.join(f'<option value="{_e(item["lifecycle"])}"{" selected" if lifecycle == item["lifecycle"] else ""}>{_e(item["lifecycle"])}</option>' for item in lifecycles)}</select>
          <select name="disposition" aria-label="治理分类"><option value="">全部分类</option>{''.join(f'<option value="{value}"{" selected" if disposition == value else ""}>{value}</option>' for value in ("boundary", "knowledge", "legacy-review", "unclassified"))}</select>
          <input type="text" name="card" value="{_e(card)}" placeholder="绑定卡片 ID" aria-label="按卡片筛选">
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["合同版本", "代际 / 状态", "领域", "治理分类", "绑定卡片", "使用"],
            [
                [
                    f'<code>{_e(item["contract_id"])}@{_e(item["version"])}</code><br>{_e(item["display_name"])}',
                    _badge(str(item["generation"]), "accent") + _badge(str(item["lifecycle"]), "ok" if item["lifecycle"] == "active" else "neutral"),
                    _e(item["domain"]),
                    _badge(str(item["disposition"]), "accent" if item["disposition"] == "boundary" else "neutral") + _badge(str(item["binding_role"])),
                    f'<a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"] or "-")}</a>' if item["card_id"] else "-",
                    _e(item["usage_count"]),
                ]
                for item in rows
            ],
        )
        return _layout("合同", "contracts", _page_header("产品合同全局视图", f"{len(rows)} 个已分类版本") + form + table)

    @app.get("/impact", response_class=HTMLResponse, include_in_schema=False)
    def impact_page(
        q: str = Query(default="", max_length=500),
        kind: str = Query(default="file", pattern="^(file|card|rule|checker)$"),
    ) -> HTMLResponse:
        form = f"""<form class="filter-bar compact-filter" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="文件、卡片、规则或检测器 ID" aria-label="影响查询">
          <select name="kind" aria-label="查询类型">{''.join(f'<option value="{value}"{" selected" if kind == value else ""}>{label}</option>' for value, label in (("file", "文件"), ("card", "卡片"), ("rule", "规则"), ("checker", "检测器")))}</select>
          <button type="submit">查询</button>
        </form>"""
        result = ""
        if q.strip() and kind == "file":
            needle = q.strip()
            target_id, separator, artifact_path = needle.partition(":")
            clauses = "artifact_path = ?"
            params: tuple[Any, ...] = (artifact_path if separator else needle,)
            if separator:
                clauses += " AND target_id = ?"
                params += (target_id,)
            artifacts = _rows(app.state.index_path, f"SELECT * FROM coverage_catalog WHERE {clauses}", params)
            symbols = _rows(app.state.index_path, f"SELECT * FROM symbol_catalog WHERE {clauses.replace('artifact_path', 'artifact_path')}", params)
            dependencies = _rows(app.state.index_path, "SELECT * FROM dependency_catalog WHERE source_artifact_path = ? OR resolved_artifact_path = ? ORDER BY source_artifact_path", (artifact_path if separator else needle, artifact_path if separator else needle))
            result = (
                f'<section class="content-section"><div class="section-heading"><h2>文件归属</h2></div>{_table(["目标", "文件", "状态", "所有者"], [[_e(item["target_id"]), f"<code>{_e(item["artifact_path"])}</code>", _badge(str(item["coverage_status"])), f"<code>{_e(item["primary_card_ids_json"])}</code>"] for item in artifacts])}</section>'
                f'<section class="content-section"><div class="section-heading"><h2>文件符号</h2><span>{len(symbols)} 个</span></div>{_table(["符号", "类型", "行"], [[f"<code>{_e(item["qualified_name"])}</code>", _badge(str(item["symbol_kind"])), _e(item["line_start"])] for item in symbols])}</section>'
                f'<section class="content-section"><div class="section-heading"><h2>依赖影响</h2><span>{len(dependencies)} 条</span></div>{_table(["来源", "引用", "目标"], [[f"<code>{_e(item["source_artifact_path"])}</code>", _e(item["target_reference"]), f"<code>{_e(item["resolved_artifact_path"] or "-")}</code>"] for item in dependencies])}</section>'
            )
        elif q.strip() and kind == "card":
            rows = _rows(app.state.source_path, "SELECT * FROM relation_catalog WHERE source_card_id = ? OR target_card_id = ? ORDER BY relation_type", (q.strip(), q.strip()))
            rules = _rows(app.state.source_path, "SELECT * FROM rule_catalog WHERE card_id = ? ORDER BY rule_id", (q.strip(),))
            result = _table(["来源", "关系", "目标"], [[_e(item["source_card_id"]), _badge(str(item["relation_type"])), _e(item["target_card_id"])] for item in rows]) + '<section class="content-section">' + _table(["规则", "级别", "约束"], [[f'<code>{_e(item["rule_id"])}</code>', _badge(str(item["severity"])), _e(item["statement"])] for item in rules]) + '</section>'
        elif q.strip() and kind == "rule":
            rows = _rows(app.state.source_path, "SELECT catalog.*, binding.checker_id FROM rule_catalog AS catalog LEFT JOIN rule_check_binding AS binding ON binding.rule_id = catalog.rule_id WHERE catalog.rule_id = ?", (q.strip(),))
            result = _table(["规则", "卡片", "级别", "检测器", "失败含义"], [[f'<code>{_e(item["rule_id"])}</code>', f'<a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"])}</a>', _badge(str(item["severity"])), f'<code>{_e(item["checker_id"] or "-")}</code>', _e(item["failure_message"])] for item in rows])
        elif q.strip() and kind == "checker":
            rows = _rows(app.state.source_path, "SELECT checker.*, binding.rule_id FROM checker LEFT JOIN rule_check_binding AS binding ON binding.checker_id = checker.checker_id WHERE checker.checker_id = ? ORDER BY binding.rule_id", (q.strip(),))
            result = _table(["检测器", "阶段", "输出合同", "绑定规则"], [[f'<code>{_e(item["checker_id"])}</code>', _badge(str(item["checker_stage"])), _e(item["output_contract"]), f'<code>{_e(item["rule_id"] or "-")}</code>'] for item in rows])
        return _layout("影响查询", "impact", _page_header("影响查询", "按一种精确标识查看上下游事实") + form + result)

    @app.get("/findings", response_class=HTMLResponse, include_in_schema=False)
    def findings_page(
        severity: str = Query(default="", pattern="^(|blocker|error|warning|info)$"),
        q: str = Query(default="", max_length=300),
    ) -> HTMLResponse:
        clauses = ["status = 'open'"]
        params: list[Any] = []
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if q.strip():
            clauses.append("(lower(artifact_path) LIKE lower(?) OR lower(rule_id) LIKE lower(?) OR lower(message) LIKE lower(?))")
            params.extend((f"%{q.strip()}%", f"%{q.strip()}%", f"%{q.strip()}%"))
        rows = _rows(
            app.state.index_path,
            f"SELECT * FROM finding_catalog WHERE {' AND '.join(clauses)} "
            "ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END, "
            "target_id, artifact_path LIMIT 500",
            tuple(params),
        )
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="q" value="{_e(q)}" placeholder="规则、路径或诊断" aria-label="搜索诊断">
          <select name="severity" aria-label="严重级别"><option value="">全部级别</option>{''.join(f'<option value="{value}"{" selected" if severity == value else ""}>{label}</option>' for value, label in (("blocker", "Blocker"), ("error", "Error"), ("warning", "Warning"), ("info", "Info")))}</select>
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["级别", "规则", "卡片", "位置", "诊断"],
            [[_badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"), f'<code>{_e(item["rule_id"])}</code>', f'<a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"])}</a>', f'<code>{_e(item["target_id"])}:{_e(item["artifact_path"])}</code>', _e(item["message"])] for item in rows],
        )
        return _layout("诊断", "findings", _page_header("开放诊断", f"{len(rows)} 项") + form + table)

    @app.get("/checks", response_class=HTMLResponse, include_in_schema=False)
    def checks_page(
        status: str = Query(default="", pattern="^(|passed|failed|error|skipped)$"),
        checker: str = Query(default="", max_length=200),
    ) -> HTMLResponse:
        rows = _check_runs(
            app.state.source_path,
            app.state.index_path,
            app.state.ledger_path,
            app.state.targets_path,
        )
        if status:
            rows = [item for item in rows if item["status"] == status]
        if checker.strip():
            needle = checker.strip().lower()
            rows = [item for item in rows if needle in str(item["checker_id"]).lower()]
        form = f"""<form class="filter-bar" method="get">
          <input type="search" name="checker" value="{_e(checker)}" placeholder="检测器 ID" aria-label="搜索检测器">
          <select name="status" aria-label="检测状态"><option value="">全部状态</option>{''.join(f'<option value="{value}"{" selected" if status == value else ""}>{label}</option>' for value, label in (("passed", "通过"), ("failed", "失败"), ("error", "错误"), ("skipped", "跳过")))}</select>
          <button type="submit">筛选</button>
        </form>"""
        table = _table(
            ["结果", "证据", "检测器", "选择原因", "完成时间", "耗时"],
            [
                [
                    _badge(str(item["status"]), "ok" if item["status"] == "passed" else "danger"),
                    _badge("当前" if item["is_current"] else "过期", "ok" if item["is_current"] else "warning"),
                    f'<a href="/checks/{quote(str(item["run_id"]), safe="")}"><code>{_e(item["checker_id"])}</code></a>',
                    _e(item["selection_reason"]),
                    _e(item["finished_at"]),
                    _e(f"{item['duration_ms']} ms"),
                ]
                for item in rows
            ],
            "没有检测证据",
        )
        return _layout("检测", "checks", _page_header("检测证据", f"{len(rows)} 次运行") + form + table)

    @app.get("/checks/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    def check_detail(run_id: str) -> HTMLResponse:
        runs = [
            item for item in _check_runs(
                app.state.source_path,
                app.state.index_path,
                app.state.ledger_path,
                app.state.targets_path,
            )
            if item["run_id"] == run_id
        ]
        if not runs:
            raise HTTPException(status_code=404, detail="check run not found")
        run = runs[0]
        dependencies = _rows(
            app.state.ledger_path,
            "SELECT * FROM evidence_dependency WHERE run_id = ? ORDER BY dependency_kind, subject_id",
            (run_id,),
        )
        diagnostics = _rows(
            app.state.ledger_path,
            "SELECT * FROM check_diagnostic WHERE run_id = ? ORDER BY rule_id, card_id, artifact_id",
            (run_id,),
        )
        rules = [
            json.loads(str(item["payload_json"]))
            for item in _rows(
                app.state.ledger_path,
                "SELECT payload_json FROM rule_result WHERE run_id = ? ORDER BY rule_id",
                (run_id,),
            )
        ]
        rule_table = _table(
            ["结果", "级别", "规则", "卡片", "失败含义"],
            [
                [
                    _badge(str(item["status"]), "ok" if item["status"] == "passed" else "danger"),
                    _badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"),
                    f'<code>{_e(item["rule_id"])}</code>',
                    f'<a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"])}</a>',
                    _e(item["failure_message"]),
                ]
                for item in rules
            ],
        )
        dependency_table = _table(
            ["依赖类型", "对象", "新鲜度角色", "选择原因", "摘要"],
            [
                [
                    _badge(str(item["dependency_kind"])),
                    f'<code>{_e(item["subject_id"])}</code>', _badge(str(item["freshness_role"]), "accent"),
                    _e(item["selection_reason"]),
                    f'<code>{_e(str(item["observed_digest"])[:16])}</code>',
                ]
                for item in dependencies
            ],
        )
        diagnostic_table = _table(
            ["规则 / 卡片", "文件", "原因", "预期", "实际", "边界"],
            [
                [
                    f'<code>{_e(item["rule_id"])}</code><br><a href="/cards/{quote(str(item["card_id"]), safe="")}">{_e(item["card_id"])}</a>',
                    f'<code>{_e(item["artifact_id"] or "-")}</code>',
                    _e(item["reason"]), _e(item["expected"]), _e(item["actual"]),
                    f'<a href="/cards/{quote(str(item["boundary_card_id"]), safe="")}">{_e(item["boundary_card_id"])}</a>' if item["boundary_card_id"] else "-",
                ]
                for item in diagnostics
            ],
            "本次运行没有失败诊断",
        )
        status_kind = "ok" if run["status"] == "passed" else "danger"
        output = (str(run.get("stdout_text") or "") + "\n" + str(run.get("stderr_text") or "")).strip() or "(无输出)"
        body = (
            _page_header(
                str(run["checker_id"]),
                f"完成于 {run['finished_at']} · {run['duration_ms']} ms · exit {run['exit_code']}",
                _badge(str(run["status"]), status_kind) + _badge("当前证据" if run["is_current"] else "过期证据", "ok" if run["is_current"] else "warning"),
            )
            + f'<section class="content-section"><div class="section-heading"><h2>规则结果</h2></div>{rule_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>结构化失败诊断</h2><span>{len(diagnostics)} 项</span></div>{diagnostic_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>精确证据足迹</h2><span>{len(dependencies)} 项</span></div>{dependency_table}</section>'
            + f'<section class="content-section"><div class="section-heading"><h2>执行命令</h2></div><div class="reader"><pre>{_e(" ".join(json.loads(str(run["command_json"]))))}</pre></div></section>'
            + f'<section class="content-section"><div class="section-heading"><h2>受控输出</h2></div><div class="reader"><pre>{_e(output)}</pre></div></section>'
        )
        return _layout("检测详情", "checks", body)

    @app.get("/context", response_class=HTMLResponse, include_in_schema=False)
    def context_page(
        paths: str = Query(default="", max_length=4000),
        contracts: str = Query(default="", max_length=4000),
        goal: str = Query(default="", max_length=1000),
        changed: bool = Query(default=False),
    ) -> HTMLResponse:
        path_specs = [item.strip() for item in paths.replace(",", "\n").splitlines() if item.strip()]
        contract_specs = [item.strip() for item in contracts.replace(",", "\n").splitlines() if item.strip()]
        form = f"""<form class="filter-bar context-filter" method="get">
          <label><span>任务目标</span><input type="text" name="goal" value="{_e(goal)}" placeholder="检查卡带交付边界"></label>
          <label><span>目标路径</span><textarea name="paths" placeholder="cartridgeflow:src/backend/main.py">{_e(paths)}</textarea></label>
          <label><span>公开合同</span><textarea name="contracts" placeholder="cartridgeflow:cartridgeflow.distribution.envelope@1.0.0">{_e(contracts)}</textarea></label>
          <label class="check-field"><input type="checkbox" name="changed" value="true"{' checked' if changed else ''}><span>当前变更</span></label>
          <button type="submit">编译上下文</button>
        </form>"""
        result = ""
        if path_specs or contract_specs or changed:
            try:
                context = compile_context(
                    app.state.source_path,
                    app.state.index_path,
                    path_specs,
                    targets_path=app.state.targets_path,
                    changed=changed,
                    goal=goal,
                    contract_specs=contract_specs,
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error, ContextCompilationError) as exc:
                result = f'<div class="inline-alert danger">{_e(exc)}</div>'
            else:
                artifact_table = _table(
                    ["目标", "文件", "状态", "所有者", "选择理由"],
                    [
                        [
                            _e(item["target_id"]),
                            f'<code>{_e(item["artifact_path"])}</code>',
                            _badge(str(item["worktree_state"]), "warning" if item["worktree_state"] != "tracked" else "neutral"),
                            f'<code>{_e(", ".join(item["primary_card_ids"]) or "UNOWNED")}</code>',
                            _e("; ".join(item["selection_reasons"])),
                        ]
                        for item in context["artifacts"]
                    ],
                )
                card_table = _table(
                    ["卡片", "类型", "选择理由"],
                    [
                        [
                            f'<a href="/cards/{quote(str(item["card_id"]), safe="")}"><code>{_e(item["card_id"])}</code><br>{_e(item["title"])}</a>',
                            _badge(str(item["card_type"]), "accent"),
                            _e("; ".join(item["selection_reasons"])),
                        ]
                        for item in context["cards"]
                    ],
                )
                finding_table = _table(
                    ["级别", "规则", "卡片", "诊断"],
                    [
                        [
                            _badge(str(item["severity"]), "danger" if item["severity"] in ("blocker", "error") else "warning"),
                            f'<code>{_e(item["rule_id"])}</code>',
                            f'<code>{_e(item["card_id"])}</code>',
                            _e(item["message"]),
                        ]
                        for item in context["findings"]
                    ],
                    "所选范围没有开放诊断",
                )
                contract_table = _table(
                    ["目标", "合同", "代际", "生命周期", "选择理由"],
                    [
                        [
                            _e(item["target_id"]),
                            f'<code>{_e(item["contract_id"])}@{_e(item["version"])}</code>',
                            _badge(str(item["generation"])),
                            _badge(str(item["lifecycle"]), "ok" if item["lifecycle"] == "active" else "neutral"),
                            _e(item["selection_reason"]),
                        ]
                        for item in context["contracts"]
                    ],
                    "未选择公开合同",
                )
                scenario_table = _table(
                    ["场景", "状态", "名称", "说明"],
                    [
                        [
                            f'<code>{_e(item["scenario_id"])}</code>',
                            _badge(str(item["status"]), "ok" if item["status"] == "active" else "neutral"),
                            _e(item["title"]),
                            _e(item["description"]),
                        ]
                        for item in context["scenarios"]
                    ],
                    "未选择跨边界场景",
                )
                routing_kind = "warning" if context["routing"]["state"] == "conservative" else "ok"
                result = (
                    f'<div class="context-meta"><span>上下文摘要</span><code>{_e(context["context_digest"])}</code>'
                    f'<span>路由</span>{_badge(str(context["routing"]["state"]), routing_kind)}</div>'
                    f'<section class="content-section"><div class="section-heading"><h2>所选卡片</h2><span>{len(context["cards"])} 张</span></div>{card_table}</section>'
                    f'<section class="content-section"><div class="section-heading"><h2>影响文件</h2><span>{len(context["artifacts"])} 个</span></div>{artifact_table}</section>'
                    f'<section class="content-section"><div class="section-heading"><h2>反向路由合同</h2><span>{len(context["contracts"])} 个</span></div>{contract_table}</section>'
                    f'<section class="content-section"><div class="section-heading"><h2>联动场景</h2><span>{len(context["scenarios"])} 个</span></div>{scenario_table}</section>'
                    f'<section class="content-section"><div class="section-heading"><h2>相关诊断</h2><span>{len(context["findings"])} 项</span></div>{finding_table}</section>'
                )
        return _layout(
            "上下文",
            "context",
            _page_header("任务上下文", "确定性卡片选择") + form + result,
        )

    return app


app = create_app()
