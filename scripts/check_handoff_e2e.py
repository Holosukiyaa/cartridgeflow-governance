"""Exercise the real Workbench authoring/package API and Desktop Runner CLI."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT.parent / "CartridgeFlow"
DR = PRODUCT / "DR"
REPORT = ROOT / ".data" / "handoff-e2e-report.json"


class HandoffError(RuntimeError):
    pass


def _go() -> Path:
    configured = os.environ.get("CF_GOVERNANCE_GO", "").strip()
    if configured and Path(configured).is_file():
        return Path(configured).resolve()
    found = shutil.which("go")
    if found:
        return Path(found).resolve()
    if os.name == "nt":
        candidates = sorted(Path("C:/_HOLOLAB/toolchains").glob("go*/go/bin/go.exe"), reverse=True)
        if candidates:
            return candidates[0].resolve()
    raise HandoffError("Go toolchain is required for the real Desktop Runner handoff scenario")


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, expect: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=180,
    )
    if completed.returncode != expect:
        raise HandoffError(
            f"command returned {completed.returncode}, expected {expect}: {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _expect_response(response: Any, label: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise HandoffError(f"{label} returned HTTP {response.status_code}: {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise HandoffError(f"{label} did not return an object")
    return payload


def _manifest(flow_id: str, base: dict[str, Any]) -> dict[str, Any]:
    manifest = json.loads(json.dumps(base))
    manifest.update(
        {
            "version": "0.0.1",
            "base_contract": {"id": "CARTRIDGEFLOW-BASE", "version": "0.2"},
            "runtime_contract": {
                "protocol": "CF-FARP",
                "protocol_version": "1.1",
                "target_runtimes": [{"id": "CF-DRP", "version": "1.0"}],
                "required_profiles": [],
                "recommended_profiles": [],
                "required_capabilities": [],
                "optional_capabilities": [],
                "required_tools": [],
                "optional_tools": [],
            },
            "delivery_readiness": {
                "level": "production",
                "certification_target": "CF-FARP@1.1",
                "notes": "detachable governance handoff acceptance",
            },
            "runtime": {"type": "none", "adapter": "builtin:root_flow"},
            "permissions": [],
            "dependencies": [],
            "mcp_tools": [
                {
                    "id": "governance_lookup",
                    "type": "local_resource",
                    "server": "governance-resource",
                    "tool": "resolve",
                    "resource_role": "document_lookup",
                    "contract": {"side_effect": "read_only", "idempotent": True, "timeout_ms": 5000},
                }
            ],
            "llm_recipe": {"schema": "cartridgeflow.llm_recipe.v1", "roles": []},
            "resource_requirements": [
                {"role": "document_lookup", "kinds": ["remote_api"], "required": False}
            ],
            "inputs": [
                {
                    "id": "topic",
                    "label": "Topic",
                    "type": "string",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            ],
            "outputs": [
                {
                    "id": "brief",
                    "label": "Brief",
                    "type": "text",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            ],
            "artifacts": {
                "store_policy": "run_scoped",
                "visibility_default": "user",
                "allowed_types": ["text"],
            },
            "delivery": {"type": "summary", "primary_output": "brief", "show_artifacts": True},
            "release_envelope": {
                "publisher_id": "governance.e2e",
                "placement": "local",
                "required_capabilities": [],
                "required_permissions": [],
            },
            "root_flow": {"entry": "root.flow.json"},
            "asset_registry": "assets/registry.json",
            "interaction_components": "assets/components.json",
            "presentation": {
                "settings": {
                    "contract": "contracts/settings.contract.json",
                    "bindings": "settings/bindings.json",
                },
                "ui": {"contract": "contracts/ui.contract.json"},
            },
        }
    )
    manifest["id"] = flow_id
    return manifest


def _flow(flow_id: str) -> dict[str, Any]:
    failures = ["validation", "exception", "cancelled", "timeout", "resource", "retry_exhausted"]
    return {
        "schema_version": "1.0",
        "id": flow_id + ".root",
        "name": "Governance Handoff E2E",
        "mode": "lifecycle",
        "cartridge_id": flow_id,
        "protocol": {"id": "CF-FARP", "version": "1.1"},
        "start": "start",
        "states": {
            "start": {"type": "control", "title": "Start", "display_name": "Start", "locked": True},
            "collect": {
                "type": "process", "kind": "input", "executor": "user", "effect": "writes_store",
                "action": "collect_inputs", "title": "Collect", "display_name": "Collect",
                "inputs": {"topic": {"required": True, "schema": {"type": "string", "minLength": 1}, "binding": {"source": "run_input", "key": "topic"}}},
                "outputs": {"topic": {"schema": {"type": "string"}, "target": {"type": "store", "key": "topic"}, "write_policy": "replace_revision"}},
                "failure_policy": {"mode": "route", "terminal": "failed"},
            },
            "lookup": {
                "type": "process", "kind": "mcp_read", "executor": "mcp", "effect": "read_only",
                "action": "tool_call", "title": "Lookup", "display_name": "Lookup",
                "allowed_tools": ["governance_lookup"],
                "mcp_binding": {"mode": "read_only", "allowed_tools": ["governance_lookup"]},
                "inputs": {"topic": {"required": True, "schema": {"type": "string", "minLength": 1}, "binding": {"source": "run_input", "key": "topic"}}},
                "outputs": {"brief": {"schema": {"type": "string", "minLength": 1}, "target": {"type": "store", "key": "brief"}, "write_policy": "replace_revision"}},
                "tools": [{
                    "server": "governance-resource", "tool": "resolve", "type": "remote_api",
                    "output": "brief", "strict": True,
                    "params": {"topic": "input:topic"},
                    "contract": {"timeout_ms": 5000},
                }],
                "failure_policy": {"mode": "route", "terminal": "failed"},
            },
            "publish": {
                "type": "process", "kind": "delivery", "executor": "deterministic", "effect": "writes_store",
                "action": "pass_result", "title": "Publish", "display_name": "Publish", "primary_output": "brief",
                "inputs": {"brief": {"required": True, "schema": {"type": "string", "minLength": 1}, "binding": {"source": "store", "key": "brief"}}},
                "outputs": {"brief": {"schema": {"type": "string", "minLength": 1}, "target": {"type": "store", "key": "brief"}, "write_policy": "replace_revision"}},
                "params": {"length": "normal"},
                "failure_policy": {"mode": "route", "terminal": "failed"},
            },
            "delivery": {"type": "system", "title": "Delivery", "display_name": "Delivery", "locked": True},
            "complete": {"type": "terminal", "title": "Complete", "display_name": "Complete", "locked": True},
            "failed": {"type": "terminal", "title": "Failed", "display_name": "Failed", "locked": True, "terminal_status": "failed"},
        },
        "execution_plan": {
            "schema": "cartridgeflow.execution_plan.v1",
            "entry": "start",
            "edges": [
                {"id": "start-collect", "kind": "sequence", "from": "start", "to": "collect"},
                {"id": "collect-lookup", "kind": "sequence", "from": "collect", "to": "lookup"},
                {"id": "lookup-publish", "kind": "sequence", "from": "lookup", "to": "publish"},
                {"id": "publish-delivery", "kind": "sequence", "from": "publish", "to": "delivery"},
                {"id": "delivery-complete", "kind": "sequence", "from": "delivery", "to": "complete"},
                {"id": "collect-failed", "kind": "failure", "from": "collect", "to": "failed", "failure": {"id": "collect-f", "causes": failures}},
                {"id": "lookup-failed", "kind": "failure", "from": "lookup", "to": "failed", "failure": {"id": "lookup-f", "causes": failures}},
                {"id": "publish-failed", "kind": "failure", "from": "publish", "to": "failed", "failure": {"id": "publish-f", "causes": failures}},
            ],
        },
    }


def _presentation_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    settings = {
        "schema": "cartridgeflow.cartridge_settings.v1",
        "storage_scope": "cartridge",
        "fields": [
            {
                "id": "brief_length",
                "label": "Brief length",
                "type": "enum",
                "default": "normal",
                "options": [
                    {"value": "short", "label": "Short"},
                    {"value": "normal", "label": "Normal"},
                ],
            }
        ],
    }
    bindings = {
        "schema": "cartridgeflow.cartridge_settings_bindings.v1",
        "bindings": [
            {
                "setting_id": "brief_length",
                "target": {"kind": "process_param", "node_id": "publish", "param": "length"},
            }
        ],
    }
    ui = {
        "schema": "cartridgeflow.cartridge_ui.v1",
        "mode": "passive",
        "component_id": "governance.home",
        "host_capabilities": [],
    }
    return settings, bindings, ui


def _write_presentation_files(
    package_path: Path,
    settings: dict[str, Any],
    bindings: dict[str, Any],
    ui: dict[str, Any],
) -> None:
    html = b"<!doctype html><meta charset=utf-8><main data-governance-handoff>Governance handoff</main>"
    assets = {
        "schema": "cartridgeflow.asset_registry.v1",
        "assets": [
            {
                "id": "governance.home.html",
                "kind": "interaction_template",
                "path": "assets/governance-home.html",
                "media_type": "text/html",
                "sha256": hashlib.sha256(html).hexdigest(),
                "size": len(html),
                "executable": False,
            }
        ],
    }
    components = {
        "schema": "cartridgeflow.interaction_components.v1",
        "components": [
            {
                "id": "governance.home",
                "version": "1.0.0",
                "runtime": "passive",
                "entry": {"type": "asset", "ref": "asset:governance.home.html"},
                "supported_modes": ["display"],
                "input_schema": {"type": "object"},
                "actions": [],
                "host_capabilities": [],
            }
        ],
    }
    files: tuple[tuple[str, Any], ...] = (
        ("contracts/settings.contract.json", settings),
        ("contracts/ui.contract.json", ui),
        ("settings/bindings.json", bindings),
        ("assets/registry.json", assets),
        ("assets/components.json", components),
    )
    for relative, value in files:
        target = package_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (package_path / "assets" / "governance-home.html").write_bytes(html)


class _ResourceHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length) or b"{}")
            topic = str(payload.get("topic") or "")
        except (ValueError, json.JSONDecodeError):
            topic = ""
        if not topic:
            self.send_error(400)
            return
        body = topic.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, expect: int = 200) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
            content = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        content = exc.read().decode("utf-8")
    if status != expect:
        raise HandoffError(f"{method} {url} returned HTTP {status}, expected {expect}: {content}")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise HandoffError(f"{method} {url} did not return an object")
    return value


def _http_text(url: str, *, expect: int = 200) -> str:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            status = response.status
            content = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        content = exc.read().decode("utf-8")
    if status != expect:
        raise HandoffError(f"GET {url} returned HTTP {status}, expected {expect}: {content}")
    return content


def _http_install(
    url: str,
    archive_path: Path,
    installation_request: dict[str, Any],
    *,
    expect: int,
) -> dict[str, Any]:
    boundary = "----cartridgeflow-handoff-" + uuid.uuid4().hex
    request_json = json.dumps(installation_request, ensure_ascii=False).encode("utf-8")
    archive_bytes = archive_path.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="installation_request"\r\n',
            b"Content-Type: application/json; charset=utf-8\r\n\r\n",
            request_json,
            b"\r\n",
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="archive"; filename="{archive_path.name}"\r\n'.encode("ascii"),
            b"Content-Type: application/octet-stream\r\n\r\n",
            archive_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            content = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        content = exc.read().decode("utf-8")
    if status != expect:
        raise HandoffError(f"POST {url} returned HTTP {status}, expected {expect}: {content}")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise HandoffError(f"POST {url} did not return an object")
    return value


def _wait_runtime(url: str, process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise HandoffError(f"DR server exited during startup with code {process.returncode}")
        try:
            _http_json("GET", url + "/api/status")
            return
        except (OSError, ValueError, HandoffError):
            time.sleep(0.1)
    raise HandoffError("DR server did not become ready")


def _tamper(source: Path, target: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["payload/root.flow.json"] = b'{"tampered":true}'
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    sys.path.insert(0, str(PRODUCT / "src"))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from fastapi.testclient import TestClient
    from backend.main import PACKAGES_DIR, ROOT as PRODUCT_ROOT, app, registry
    from core.protocol import validate_clean_contract

    client = TestClient(app)
    simulation = _expect_response(
        client.post("/api/lab/flows/simulations/authoring", json={"keep_temporary_cartridge": False}),
        "authoring simulation",
    )
    if not simulation.get("ok"):
        raise HandoffError(f"authoring simulation failed: {simulation}")

    flow_id = "dev.governance-handoff-" + uuid.uuid4().hex[:10]
    archive_path: Path | None = None
    created = False
    report: dict[str, Any] = {"schema": "cartridgeflow.governance.handoff-evidence.v3", "flow_id": flow_id}
    try:
        _expect_response(
            client.post("/api/lab/flows", json={"flow_id": flow_id, "name": "Governance Handoff E2E", "description": "temporary detachable governance handoff probe"}),
            "create flow",
        )
        created = True
        files = _expect_response(client.get(f"/api/lab/flows/{flow_id}/files"), "read flow files")["files"]
        manifest = _manifest(flow_id, json.loads(files["manifest"]))
        flow = _flow(flow_id)
        for file_type, value in (("manifest", manifest), ("root_flow", flow)):
            _expect_response(
                client.put(f"/api/lab/flows/{flow_id}/files/{file_type}", json={"content": json.dumps(value, ensure_ascii=False, indent=2)}),
                f"save {file_type}",
            )
        _expect_response(client.get(f"/api/lab/flows/{flow_id}/tuning"), "initialize tuning")
        release = _expect_response(
            client.post(f"/api/lab/flows/{flow_id}/tuning/releases", json={"author": "governance-e2e", "message": "real handoff acceptance"}),
            "publish recipe release",
        )["release"]
        _expect_response(
            client.post(f"/api/lab/flows/{flow_id}/tuning/releases/{release['id']}/activate"),
            "activate recipe release",
        )
        cartridge = registry.get_packaging_cartridge(flow_id)
        package_path = Path(str(cartridge.get("package_path") or ""))
        if not package_path.is_dir():
            raise HandoffError("workbench did not retain the temporary package directory")
        settings, settings_bindings, ui = _presentation_contracts()
        _write_presentation_files(package_path, settings, settings_bindings, ui)
        validation = _expect_response(client.post(f"/api/lab/flows/{flow_id}/validate", json={"files": {}}), "validate flow")
        compatibility = _expect_response(client.post(f"/api/lab/flows/{flow_id}/compatibility", json={"files": {}}), "check compatibility")
        if not validation.get("valid") or not compatibility.get("ok"):
            raise HandoffError(f"workbench validation failed: {validation} {compatibility}")
        certification = _expect_response(
            client.post(f"/api/lab/flows/{flow_id}/certification/apply", json={"files": {}}),
            "apply certification",
        )
        preflight = _expect_response(client.get(f"/api/studio/release/{flow_id}/preflight"), "release preflight")
        if not preflight.get("production_ready"):
            raise HandoffError(f"production preflight is blocked: {preflight.get('issues')}")
        packaged = _expect_response(
            client.post(
                f"/api/cartridges/{flow_id}/package",
                json={"package_mode": "production", "requested_by": "governance-e2e"},
            ),
            "package production handoff",
        )
        archive_path = Path(PRODUCT_ROOT) / PACKAGES_DIR / str(packaged.get("filename") or "")
        installation_request = packaged.get("installation_request")
        installation_plan = packaged.get("installation_plan")
        if packaged.get("protocol") != "CF-CRE@2" or not packaged.get("activation_allowed") or not archive_path.is_file():
            raise HandoffError(f"Workbench did not publish an activation-ready CF-CRE@2 handoff: {packaged}")
        validate_clean_contract("cartridgeflow.installation.request", installation_request, root=PRODUCT)
        validate_clean_contract("cartridgeflow.installation.plan", installation_plan, root=PRODUCT)

        go = _go()
        with tempfile.TemporaryDirectory(prefix="cartridgeflow-governance-handoff-") as temporary:
            temp = Path(temporary)
            shell = temp / ("cf-shell.exe" if os.name == "nt" else "cf-shell")
            _run([str(go), "build", "-trimpath", "-o", str(shell), "."], cwd=DR / "shell" / "go")
            env = os.environ.copy()
            env["CF_SHELL_DATA_ROOT"] = str(temp / "data")
            signature = packaged["signature"]
            _run([str(shell), "trust", "add", str(signature["key_id"]), str(signature["public_key"])], cwd=DR, env=env)
            resource_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ResourceHandler)
            resource_thread = threading.Thread(target=resource_server.serve_forever, daemon=True)
            resource_thread.start()
            resource_url = f"http://127.0.0.1:{resource_server.server_port}/resolve"
            port = _free_port()
            server = subprocess.Popen(
                [str(shell), "serve", "--port", str(port), "--data-root", str(temp / "data")],
                cwd=DR,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            runtime_url = f"http://127.0.0.1:{port}"
            try:
                _wait_runtime(runtime_url, server)
                install_response = _http_install(
                    runtime_url + "/api/install",
                    archive_path,
                    installation_request,
                    expect=200,
                )
                installation_result = install_response.get("installation_result")
                validate_clean_contract("cartridgeflow.installation.result", installation_result, root=PRODUCT)
                install = install_response.get("cartridge") or {}
                result_payload = (installation_result or {}).get("payload") or {}
                request_payload = (installation_request or {}).get("payload") or {}
                plan_payload = (installation_plan or {}).get("payload") or {}
                if (
                    not install.get("active")
                    or result_payload.get("status") != "succeeded"
                    or result_payload.get("package_id") != flow_id
                    or result_payload.get("plan_id") != request_payload.get("plan_id")
                    or result_payload.get("plan_id") != plan_payload.get("plan_id")
                ):
                    raise HandoffError(f"clean-v1 install handoff was incomplete: {install_response}")
                runtime_settings = _http_json(
                    "PUT",
                    runtime_url + "/api/settings",
                    {
                        "resources": {
                            "tools": [
                                {
                                    "id": "governance-local-lookup",
                                    "role": "document_lookup",
                                    "server": "governance-resource",
                                    "kind": "remote_api",
                                    "endpoint": resource_url,
                                    "http_method": "POST",
                                }
                            ]
                        }
                    },
                )
                configured_tools = ((runtime_settings.get("resources") or {}).get("tools") or [])
                if not any(item.get("id") == "governance-local-lookup" for item in configured_tools):
                    raise HandoffError(f"DR did not persist the resource-role binding: {runtime_settings}")
                declared = _http_json("GET", runtime_url + "/api/cartridge-settings")
                if not declared.get("available") or declared.get("schema") != "cartridgeflow.cartridge_settings.v1":
                    raise HandoffError(f"DR did not expose CF-CRE@2 settings: {declared}")
                declared_ui = declared.get("ui") or {}
                if declared_ui.get("mode") != "passive" or declared_ui.get("component_id") != "governance.home":
                    raise HandoffError(f"DR did not expose the declared passive UI: {declared_ui}")
                home = _http_text(runtime_url + "/cartridge/")
                if "data-governance-handoff" not in home:
                    raise HandoffError("DR did not resolve the signed passive UI asset")
                configured = _http_json(
                    "PUT",
                    runtime_url + "/api/cartridge-settings",
                    {"values": {"brief_length": "short"}},
                )
                if (configured.get("values") or {}).get("brief_length") != "short":
                    raise HandoffError(f"DR did not persist the declared cartridge setting: {configured}")
                missing = _http_json("POST", runtime_url + "/api/run", {"inputs": {}}, expect=400)
                missing_text = json.dumps(missing, ensure_ascii=False).lower()
                if "missing_input" not in missing_text and "missing_required_input" not in missing_text:
                    raise HandoffError(f"DR invalid-input path was not specific: {missing_text}")
                completed_response = _http_json(
                    "POST",
                    runtime_url + "/api/run",
                    {"inputs": {"topic": "governance-e2e"}},
                )
                completed = completed_response.get("run") or {}
                delivery = completed.get("delivery") or {}
                if completed.get("status") != "completed" or delivery.get("status") != "produced" or delivery.get("value") != "governance-e2e":
                    raise HandoffError(f"DR happy path did not produce the expected delivery: {completed}")
                before = _http_json("GET", runtime_url + "/api/status").get("cartridge")
                tampered = temp / "tampered.cf-cre.zip"
                _tamper(archive_path, tampered)
                rejected = _http_install(
                    runtime_url + "/api/install",
                    tampered,
                    installation_request,
                    expect=400,
                )
                rejected_text = json.dumps(rejected, ensure_ascii=False).lower()
                if "digest" not in rejected_text and "release verification failed" not in rejected_text:
                    raise HandoffError(f"DR tampered-package path did not report an integrity failure: {rejected_text}")
                rejected_result = rejected.get("installation_result")
                validate_clean_contract("cartridgeflow.installation.result", rejected_result, root=PRODUCT)
                if ((rejected_result or {}).get("payload") or {}).get("status") != "failed":
                    raise HandoffError(f"DR did not return a failed clean-v1 result: {rejected_result}")
                after = _http_json("GET", runtime_url + "/api/status").get("cartridge")
                if before != after:
                    raise HandoffError("rejected installation mutated the active cartridge")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
                resource_server.shutdown()
                resource_server.server_close()
                resource_thread.join(timeout=5)
            report.update(
                {
                    "ok": True,
                    "authoring_simulation": simulation,
                    "validation": {"valid": validation["valid"], "compatibility": compatibility["status"]},
                    "certification": certification.get("label"),
                    "release": {
                        "release_id": packaged["release_id"],
                        "protocol": packaged["protocol"],
                        "activation_allowed": packaged["activation_allowed"],
                        "packaging_entrypoint": f"POST /api/cartridges/{flow_id}/package",
                    },
                    "clean_protocol": {
                        "request_contract": installation_request["contract_id"],
                        "plan_contract": installation_plan["contract_id"],
                        "result_contract": installation_result["contract_id"],
                        "result_status": result_payload["status"],
                        "tampered_result_status": rejected_result["payload"]["status"],
                    },
                    "desktop_runner": {
                        "installed": {
                            key: install.get(key)
                            for key in ("active", "cartridge_id", "version", "digest", "release_id", "publisher_id")
                        },
                        "invalid_input_rejected": True,
                        "settings_contract_consumed": True,
                        "settings_value": "short",
                        "passive_ui_resolved": True,
                        "resource_role_resolved": True,
                        "delivery": delivery,
                        "tampered_package_rejected": True,
                        "installation_entrypoint": "POST /api/install",
                    },
                }
            )
    finally:
        if created:
            response = client.delete(f"/api/lab/flows/{flow_id}")
            if response.status_code != 200:
                report.setdefault("cleanup_errors", []).append(f"flow cleanup HTTP {response.status_code}")
        if archive_path is not None and archive_path.is_file() and archive_path.name.startswith(flow_id + "-"):
            archive_path.unlink()

    args.report.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired, HandoffError) as exc:
        print(f"Real handoff scenario failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
