import contextlib
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_manager as lm  # noqa: E402

EXAMPLE = lm.ROOT / "catalog.example.yaml"


def raw_example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def cat():
    return lm.normalize_catalog(raw_example())


def problems(raw) -> str:
    with pytest.raises(lm.CatalogError) as exc:
        lm.normalize_catalog(raw)
    return "\n".join(exc.value.problems)


# ---- catalog

def test_example_catalog_is_valid(cat):
    assert cat["endpoints"]["corp"]["root"] == "https://llm-gateway.example.com"
    assert cat["policy"]["approvals"] == "off"
    assert cat["claude_code"]["opus"] == "claude-opus-example"
    assert cat["claude_code"]["model"] == "claude-sonnet-example"


def test_base_url_v1_suffix_is_stripped():
    raw = raw_example()
    raw["endpoints"]["corp"]["base_url"] = "https://gw.example.com/v1/"
    assert lm.normalize_catalog(raw)["endpoints"]["corp"]["root"] == "https://gw.example.com"


def test_bare_yaml_off_means_no_approvals():
    raw = raw_example()
    raw["policy"]["approvals"] = False  # what YAML makes of a bare `off`
    assert lm.normalize_catalog(raw)["policy"]["approvals"] == "off"


def test_unknown_model_in_tier_is_reported():
    raw = raw_example()
    raw["tiers"]["tier1"] = ["nope"]
    assert "tiers.tier1: 'nope' is not in models" in problems(raw)


def test_missing_context_is_reported():
    raw = raw_example()
    raw["models"]["gpt-example"]["context"] = None
    assert "models.gpt-example.context" in problems(raw)


def test_non_claude_model_for_claude_code_is_reported():
    raw = raw_example()
    raw["claude_code"] = {"sonnet": "gpt-example"}
    assert "claude_code.sonnet: 'gpt-example' is not a Claude model" in problems(raw)


def test_small_context_default_is_rejected_for_hermes():
    raw = raw_example()
    raw["models"]["qwen-coder-example"]["context"] = 32000
    raw["default"] = "qwen-coder-example"
    assert "hermes rejects models under 64000" in problems(raw)
    raw["tools"] = {"hermes": False}
    lm.normalize_catalog(raw)


# ---- renderers

def test_opencode_config(cat):
    cfg = lm.render_opencode(cat)
    provider = cfg["providers"]["corp"]
    assert cfg["model"] == "corp/claude-sonnet-example"
    assert provider["package"] == "@opencode/ai/providers/openai-compatible"
    assert provider["settings"] == {"baseURL": "https://llm-gateway.example.com/v1", "apiKey": "{env:COMPANY_LLM_KEY}"}
    assert provider["models"]["qwen-coder-example"]["capabilities"]["input"] == ["text"]
    assert provider["models"]["gpt-example"]["limit"] == {"context": 400000, "output": 32000}
    assert cfg["agents"]["title"]["model"] == "corp/claude-haiku-example"
    assert cfg["update"] == "disable"
    assert cfg["experimental"]["policies"] == [
        {"action": "provider.use", "resource": "*", "effect": "deny"},
        {"action": "provider.use", "resource": "corp", "effect": "allow"},
    ]
    assert "policies" not in cfg and "permissions" not in cfg and "mcp" not in cfg


def test_omo_config_and_models(cat):
    cfg = lm.render_omo_config(cat)
    assert cfg["categories"]["ultrabrain"]["models"][0] == {"model": "corp/claude-opus-example", "reasoning": "high"}
    assert cfg["categories"]["quick"]["models"] == ["corp/claude-haiku-example", "corp/gemini-flash-example"]
    assert cfg["agents"]["explore"]["models"][0] == "corp/claude-haiku-example"
    assert cfg["telemetry"] == {"enabled": False}
    models = lm.render_omo_models(cat)["providers"]["corp"]
    assert models["apiKey"] == "$COMPANY_LLM_KEY" and models["api"] == "openai-completions"
    assert {"id": "qwen-coder-example", "name": "qwen-coder-example", "reasoning": False, "input": ["text"],
            "contextWindow": 131072, "maxTokens": 16000} in models["models"]
    settings = lm.render_omo_settings(cat)
    assert settings["defaultModel"] == "claude-sonnet-example" and settings["defaultProvider"] == "corp"
    assert cfg["model_profile"] == "corp/claude-sonnet-example"
    assert settings["permissionPreset"] == "full-access"


def test_omo_role_override():
    raw = raw_example()
    raw["omo_roles"] = {"categories": {"ultrabrain": {"tier": "tier1", "reasoning": "max"}, "quick": "tier2"}}
    cfg = lm.render_omo_config(lm.normalize_catalog(raw))
    assert cfg["categories"]["ultrabrain"]["models"][0]["reasoning"] == "max"
    assert cfg["categories"]["quick"]["models"][0]["model"] == "corp/claude-sonnet-example"


def test_hermes_config(cat):
    cfg, notes = lm.render_hermes(cat, "C:/x/skills")
    assert cfg["model"] == {"default": "claude-sonnet-example", "provider": "custom:corp"}
    assert cfg["providers"]["corp"]["key_env"] == "COMPANY_LLM_KEY"
    assert cfg["providers"]["corp"]["models"]["qwen-coder-example"] == {"context_length": 131072}
    assert cfg["auxiliary"]["vision"] == {"provider": "main"}
    assert cfg["approvals"] == {"mode": "off"}
    assert cfg["updates"] == {"check": False}
    assert notes == []


def test_hermes_skips_small_context_models():
    raw = raw_example()
    raw["models"]["qwen-coder-example"]["context"] = 32000
    cfg, notes = lm.render_hermes(lm.normalize_catalog(raw), "x")
    assert "qwen-coder-example" not in cfg["providers"]["corp"]["models"]
    assert len(notes) == 1


def test_claude_settings(cat):
    cfg, _ = lm.render_claude_settings(cat, r"C:\Users\me\.claude\statusline.ps1")
    assert cfg["env"]["ANTHROPIC_BASE_URL"] == "https://llm-gateway.example.com"
    assert cfg["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "claude-haiku-example"
    assert cfg["env"]["DISABLE_AUTOUPDATER"] == "1"
    assert cfg["permissions"] == {"defaultMode": "bypassPermissions"}
    assert cfg["statusLine"]["command"].endswith(r'-File "C:\Users\me\.claude\statusline.ps1"')


def test_claude_settings_without_claude_models():
    raw = raw_example()
    for mid in list(raw["models"]):
        if mid.startswith("claude"):
            del raw["models"][mid]
    raw["tiers"] = {"tier1": ["gpt-example"], "tier2": ["qwen-coder-example"], "tier3": ["gemini-flash-example"]}
    raw["default"] = "gpt-example"
    cfg, notes = lm.render_claude_settings(lm.normalize_catalog(raw), "s.ps1")
    assert "ANTHROPIC_BASE_URL" not in cfg["env"] and notes


def test_strict_policy_and_mcp():
    raw = raw_example()
    raw["policy"] = {"external_web": False, "approvals": "ask"}
    raw["mcp"] = {"docs": {"url": "https://mcp.example.com/mcp"},
                  "fs": {"command": "npx", "args": ["-y", "server-fs"], "env": {"A": "1"}}}
    cat = lm.normalize_catalog(raw)
    oc = lm.render_opencode(cat)
    assert {"action": "shell", "resource": "*", "effect": "ask"} in oc["permissions"]
    assert {"action": "webfetch", "resource": "*", "effect": "deny"} in oc["permissions"]
    assert oc["mcp"]["servers"]["fs"] == {"type": "local", "command": ["npx", "-y", "server-fs"], "environment": {"A": "1"}}
    assert oc["mcp"]["servers"]["docs"] == {"type": "remote", "url": "https://mcp.example.com/mcp"}
    omo_mcp = lm.render_omo_mcp(cat)["mcpServers"]
    assert omo_mcp["context7"] == {"enabled": False} and omo_mcp["fs"]["command"] == "npx"
    hermes, _ = lm.render_hermes(cat, "x")
    assert hermes["approvals"]["mode"] == "manual" and hermes["web"] == {"keyless_fallback": False}
    claude, _ = lm.render_claude_settings(cat, "s.ps1")
    assert claude["permissions"] == {"defaultMode": "default", "deny": ["WebFetch", "WebSearch"]}
    assert lm.render_omo_settings(cat)["permissionPreset"] == "ask"
    cmd = lm.claude_mcp_commands(cat)[0]
    assert cmd[:4] == ["claude", "mcp", "add-json", "docs"] and json.loads(cmd[4])["type"] == "http"


def test_extra_is_merged(cat):
    cat["extra"]["omo"] = {"git_master": {"include_co_authored_by": False}, "task": {"default_concurrency": 9}}
    cfg = lm.render_omo_config(cat)
    assert cfg["git_master"] == {"include_co_authored_by": False}
    assert cfg["task"]["default_concurrency"] == 9


# ---- helpers

def test_deep_merge_replace_and_union():
    base = {"model": {"default": "old", "base_url": "https://old"}, "skills": {"external_dirs": ["a"]}, "keep": 1}
    patch = {"model": {"default": "new"}, "skills": {"external_dirs": ["a", "b"]}}
    out = lm.deep_merge(base, patch, replace=(("model",),), union=(("skills", "external_dirs"),))
    assert out == {"model": {"default": "new"}, "skills": {"external_dirs": ["a", "b"]}, "keep": 1}
    assert base["model"]["base_url"] == "https://old"


def test_strip_jsonc():
    text = '{\n // note\n "a": "http://x//y", /* c */ "b": [1, 2,],\n}'
    assert json.loads(lm.strip_jsonc(text)) == {"a": "http://x//y", "b": [1, 2]}


def test_replace_block_is_idempotent():
    once = lm.replace_block("# mine\n", "@file")
    assert once.startswith("# mine\n") and "@file" in once
    assert lm.replace_block(once, "@file") == once
    assert "@other" in lm.replace_block(once, "@other") and "@file" not in lm.replace_block(once, "@other")


# ---- apply

def test_apply_installs_merges_and_backs_up(cat, tmp_path, capsys):
    home = tmp_path / "home"
    dirs = lm.tool_dirs(home)
    dirs["hermes"].mkdir(parents=True)
    (dirs["hermes"] / "config.yaml").write_text(
        "model:\n  default: gemini\n  provider: gemini\n  base_url: https://outside.example\n"
        "plugins:\n  enabled: [orca-status]\n", encoding="utf-8")
    dirs["claude"].mkdir(parents=True)
    (dirs["claude"] / "settings.json").write_text(
        '{"env": {"MINE": "1"}, "enabledPlugins": {"x": true}, "statusLine": {"type": "command", "command": "old"}}',
        encoding="utf-8")
    (dirs["claude"] / "CLAUDE.md").write_text("# my notes\n", encoding="utf-8")

    backups = tmp_path / "backups"
    written = lm.cmd_apply(cat, home, backups, set_env=False)

    hermes = yaml.safe_load((dirs["hermes"] / "config.yaml").read_text(encoding="utf-8"))
    assert hermes["model"] == {"default": "claude-sonnet-example", "provider": "custom:corp"}
    assert hermes["plugins"] == {"enabled": ["orca-status"]}
    assert hermes["skills"]["external_dirs"] == [str(dirs["claude"] / "skills")]

    claude = json.loads((dirs["claude"] / "settings.json").read_text(encoding="utf-8"))
    assert claude["env"]["MINE"] == "1" and claude["env"]["ANTHROPIC_MODEL"] == "claude-sonnet-example"
    assert claude["enabledPlugins"] == {"x": True}
    assert str(dirs["claude"] / "statusline.ps1") in claude["statusLine"]["command"]
    assert (dirs["claude"] / "statusline.ps1").exists()
    memo = (dirs["claude"] / "CLAUDE.md").read_text(encoding="utf-8")
    assert memo.startswith("# my notes") and "@~/.config/opencode/AGENTS.md" in memo

    assert (dirs["opencode"] / "AGENTS.md").exists()
    assert json.loads((dirs["opencode"] / "opencode.json").read_text(encoding="utf-8"))["update"] == "disable"
    omo = (dirs["omo"] / "omo.jsonc").read_text(encoding="utf-8")
    assert omo.startswith("//") and json.loads(lm.strip_jsonc(omo))["agents"]
    assert json.loads((dirs["omo"] / "agent" / "settings.json").read_text(encoding="utf-8"))["skills"] == ["~/.claude/skills"]

    saved = sorted(p.name for p in backups.rglob("*") if p.is_file())
    assert saved == ["CLAUDE.md", "config.yaml", "settings.json"]
    assert "base_url: https://outside.example" in next(backups.rglob("config.yaml")).read_text(encoding="utf-8")

    # second run changes nothing and makes no new backup
    assert lm.cmd_apply(cat, home, backups, set_env=False) == []
    assert len(written) > 0 and len(list(backups.iterdir())) == 1
    assert lm.cmd_diff(cat, home) == 0
    assert "COMPANY_LLM_KEY" in capsys.readouterr().out


def test_disabled_tool_is_not_installed(tmp_path):
    raw = raw_example()
    raw["tools"] = {"hermes": False, "claude_code": False}
    ops, _ = lm.plan_files(lm.normalize_catalog(raw), tmp_path)
    rels = {op.rel for op in ops}
    assert "opencode/opencode.json" in rels and "omo/omo.jsonc" in rels
    assert not any(r.startswith(("hermes/", "claude/")) for r in rels)


def test_env_plan(cat):
    user, service = lm.env_plan(cat)
    assert user["DO_NOT_TRACK"] == "1" and user["OMO_SKIP_VERSION_CHECK"] == "1"
    assert service == {"OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}


# ---- discover

class FakeGateway(BaseHTTPRequestHandler):
    seen_auth = []

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeGateway.seen_auth.append(self.headers.get("Authorization"))
        if self.path == "/model_group/info":
            self._send(200, {"data": [
                {"model_group": "claude-a", "max_input_tokens": 200000, "max_output_tokens": 32000, "mode": "chat",
                 "supports_function_calling": True, "supports_vision": True, "supports_reasoning": True},
                {"model_group": "embed-a", "mode": "embedding"},
            ]})
        elif self.path == "/v1/model/info":
            self._send(403, {"error": "admin only"})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": "claude-a"}, {"id": "embed-a"}, {"id": "mystery"}]})
        else:
            self._send(404, {})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/v1/responses":
            self._send(404, {"error": "not here"})
        else:
            self._send(200, {"ok": True})


def test_discover_against_fake_gateway():
    server = HTTPServer(("127.0.0.1", 0), FakeGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        res = lm.discover_endpoint(f"http://127.0.0.1:{server.server_port}", "sk-test")
    finally:
        server.shutdown()
    assert set(res["models"]) == {"claude-a", "mystery"}
    assert res["models"]["claude-a"]["context"] == 200000
    assert res["models"]["mystery"].get("context") is None
    assert res["formats"]["openai-completions"]["ok"] and res["formats"]["anthropic-messages"]["ok"]
    assert res["formats"]["openai-responses"] == {"ok": False, "status": 404, "detail": '{"error": "not here"}'}
    assert "Bearer sk-test" in FakeGateway.seen_auth
    block = yaml.safe_load(lm.models_yaml("corp", res["models"]))["models"]
    assert block["claude-a"] == {"endpoint": "corp", "family": "claude", "context": 200000, "max_output": 32000,
                                 "reasoning": True, "tools": True, "vision": True}
    assert block["mystery"]["context"] is None


# ---- detect

@contextlib.contextmanager
def fake_server(routes: dict, need: tuple[str, str] | None = None, bodies: list | None = None):
    """routes: {path: (status, body, headers)}. need: (header, value) every request must carry, else 401.
    bodies: when given, each request body (decoded text) is appended to it."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if bodies is not None:
                bodies.append(raw.decode("utf-8", "replace"))
            seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))
            if need and self.headers.get(need[0]) != need[1]:
                status, body, headers = 401, {"error": "unauthorized"}, {}
            else:
                status, body, headers = routes.get(self.path, (404, {"error": "not found"}, {}))
            data = json.dumps(body).encode()
            self.send_response(status)
            for k, v in {"Content-Type": "application/json", "Content-Length": str(len(data)), **headers}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _reply

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()


MODELS = (200, {"data": [{"id": "m1"}]}, {})


def test_detect_litellm_by_header_and_model_group():
    routes = {"/v1/models": (200, {"data": [{"id": "m1"}]}, {"x-litellm-version": "1.50.0"}),
              "/model_group/info": (200, {"data": [{"model_group": "m1"}]}, {})}
    with fake_server(routes, need=("Authorization", "Bearer sk-1")) as (url, _):
        res = lm.detect_server(url, "sk-1")
    assert (res["kind"], res["auth"], res["api_base"]) == ("litellm", "bearer", "/v1")
    assert res["renderable"] and not res["warnings"]


def test_detect_azure_uses_api_key_header_and_openai_path():
    routes = {"/openai/v1/models": (200, {"data": [{"id": "gpt-4o"}]}, {"apim-request-id": "abc"})}
    with fake_server(routes, need=("api-key", "k")) as (url, _):
        res = lm.detect_server(url, "k")
    assert (res["kind"], res["auth"], res["api_base"]) == ("azure-openai", "api-key", "/openai/v1")
    assert not res["renderable"]
    assert any("Bearer" in w for w in res["warnings"])


def test_detect_anthropic_uses_x_api_key():
    routes = {"/v1/models": (200, {"data": [{"id": "claude-x"}]}, {"anthropic-ratelimit-requests-limit": "50"})}
    with fake_server(routes, need=("x-api-key", "k")) as (url, seen):
        res = lm.detect_server(url, "k")
    assert (res["kind"], res["auth"]) == ("anthropic", "x-api-key")
    assert seen[-1][2].get("anthropic-version")


def test_detect_vllm_by_version_endpoint():
    routes = {"/v1/models": MODELS, "/version": (200, {"version": "0.6.3"}, {})}
    with fake_server(routes) as (url, _):
        res = lm.detect_server(url, "k")
    assert res["kind"] == "vllm" and res["renderable"]


def test_detect_ollama_without_key():
    routes = {"/v1/models": MODELS, "/api/tags": (200, {"models": [{"name": "qwen"}]}, {})}
    with fake_server(routes) as (url, seen):
        res = lm.detect_server(url, None)
    assert (res["kind"], res["auth"]) == ("ollama", "none")
    assert all("authorization" not in h for _, _, h in seen)


def test_detect_plain_openai_compatible_and_proxy_layer():
    routes = {"/v1/models": (200, {"data": [{"id": "m1"}]}, {"Via": "1.1 kong/3.4.0"})}
    with fake_server(routes) as (url, _):
        res = lm.detect_server(url, "k")
    assert res["kind"] == "openai-compatible"
    assert any("Kong" in e for e in res["evidence"])


def test_detect_reports_rejected_key():
    with fake_server({"/v1/models": MODELS}, need=("Authorization", "Bearer right")) as (url, _):
        res = lm.detect_server(url, "wrong")
    assert res["kind"] == "unknown" and res["auth"] is None
    assert any("401" in w for w in res["warnings"])


def test_detect_unreachable_stops_early():
    with fake_server({}) as (url, _):
        pass  # server is shut down: the port now refuses connections
    res = lm.detect_server(url, "k")
    assert res["kind"] == "unreachable" and len(res["evidence"]) == 1


@pytest.mark.parametrize("url,kind", [
    ("https://corp.openai.azure.com", "azure-openai"),
    ("https://x.cognitiveservices.azure.com/openai", "azure-openai"),
    ("https://bedrock-runtime.us-east-1.amazonaws.com", "bedrock"),
    ("https://us-central1-aiplatform.googleapis.com", "vertex"),
    ("https://api.openai.com", "openai"),
    ("https://api.anthropic.com", "anthropic"),
    ("https://llm-gateway.example.com", None),
])
def test_kind_from_host(url, kind):
    assert lm.kind_from_host(url) == kind


def test_cloud_signed_hosts_are_not_probed():
    calls = []
    real = lm.http_call
    lm.http_call = lambda *a, **k: calls.append(a) or real(*a, **k)
    try:
        res = lm.detect_server("https://bedrock-runtime.us-east-1.amazonaws.com", "k")
    finally:
        lm.http_call = real
    assert res["kind"] == "bedrock" and not calls and not res["renderable"]
    assert any("LiteLLM" in w for w in res["warnings"])


def test_discover_uses_detected_auth_and_path():
    routes = {"/openai/v1/models": (200, {"data": [{"id": "gpt-4o"}]}, {}),
              "/openai/v1/chat/completions": (200, {"ok": True}, {})}
    with fake_server(routes, need=("api-key", "k")) as (url, seen):
        res = lm.discover_endpoint(url, "k", auth="api-key", api_base="/openai/v1")
    assert set(res["models"]) == {"gpt-4o"}
    assert res["formats"]["openai-completions"]["ok"]
    assert ("POST", "/openai/v1/chat/completions") in [(m, p) for m, p, _ in seen]


def test_detect_command_without_catalog(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-secret-123")
    with fake_server({"/v1/models": MODELS}, need=("Authorization", "Bearer sk-secret-123")) as (url, _):
        code = lm.main(["--catalog", str(tmp_path / "none.yaml"), "detect", "--url", url,
                        "--key-env", "TEST_LLM_KEY"])
    out = capsys.readouterr().out
    assert code == 0
    assert "openai-compatible" in out and "Bearer" in out and "sk-secret-123" not in out


# ---- preflight (runs before build / diff / apply / verify)

EXAMPLE_IDS = ["claude-opus-example", "claude-sonnet-example", "claude-haiku-example",
               "gpt-example", "gemini-flash-example", "qwen-coder-example"]


def gateway(ids=EXAMPLE_IDS, key="sk-pre"):
    """LiteLLM-like fake serving `ids` behind Bearer `key`."""
    return fake_server({"/v1/models": (200, {"data": [{"id": i} for i in ids]}, {"x-litellm-version": "1"})},
                       need=("Authorization", f"Bearer {key}"))


def catalog_at(url: str, key_env: str = "PRE_KEY") -> dict:
    raw = raw_example()
    raw["endpoints"]["corp"] = {"base_url": url, "key_env": key_env}
    return lm.normalize_catalog(raw)


def test_detect_reports_served_model_ids():
    with gateway(["a", "b"]) as (url, _):
        assert lm.detect_server(url, "sk-pre")["model_ids"] == ["a", "b"]


def test_preflight_passes_for_working_gateway(monkeypatch):
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    with gateway() as (url, _):
        results, problems = lm.preflight(catalog_at(url))
    assert problems == [] and results["corp"]["kind"] == "litellm"


def test_preflight_reports_models_the_gateway_does_not_serve(monkeypatch):
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    with gateway(EXAMPLE_IDS[:-1]) as (url, _):
        _, problems = lm.preflight(catalog_at(url))
    assert problems == ["corp: the gateway does not serve qwen-coder-example"]


def test_preflight_reports_unsupported_auth(monkeypatch):
    monkeypatch.setenv("PRE_KEY", "k")
    routes = {"/openai/v1/models": (200, {"data": [{"id": i} for i in EXAMPLE_IDS]}, {})}
    with fake_server(routes, need=("api-key", "k")) as (url, _):
        _, problems = lm.preflight(catalog_at(url))
    assert len(problems) == 1 and "api-key" in problems[0]


def test_preflight_names_the_missing_key_variable(monkeypatch):
    monkeypatch.delenv("PRE_KEY", raising=False)
    with gateway() as (url, _):
        _, problems = lm.preflight(catalog_at(url))
    assert problems == ["corp: environment variable PRE_KEY is not set"]


def test_preflight_reports_unreachable_gateway(monkeypatch):
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    _, problems = lm.preflight(catalog_at("http://127.0.0.1:9"))
    assert len(problems) == 1 and "unreachable" in problems[0]


def test_preflight_skips_endpoints_without_models(monkeypatch):
    raw = raw_example()
    raw["endpoints"]["spare"] = {"base_url": "http://127.0.0.1:9", "key_env": "X"}
    for m in raw["models"].values():
        m["endpoint"] = "corp"
    probed = []
    monkeypatch.setattr(lm, "detect_server", lambda url, key: probed.append(url) or
                        {"root": url, "kind": "litellm", "auth": "bearer", "api_base": "/v1", "evidence": [],
                         "warnings": [], "renderable": True, "model_ids": EXAMPLE_IDS})
    lm.preflight(lm.normalize_catalog(raw))
    assert probed == ["https://llm-gateway.example.com"]


def write_catalog(tmp_path, url) -> Path:
    raw = raw_example()
    raw["endpoints"]["corp"] = {"base_url": url, "key_env": "PRE_KEY"}
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_apply_refuses_to_install_when_preflight_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lm, "ROOT", tmp_path)
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    home = tmp_path / "home"
    code = lm.main(["--catalog", str(write_catalog(tmp_path, "http://127.0.0.1:9")), "apply", "--home", str(home)])
    assert code == 3
    assert not home.exists()
    assert "--skip-check" in capsys.readouterr().err


def test_apply_installs_after_passing_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "ROOT", tmp_path)
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    home = tmp_path / "home"
    with gateway() as (url, _):
        code = lm.main(["--catalog", str(write_catalog(tmp_path, url)), "apply", "--home", str(home)])
    assert code == 0
    assert (home / ".config" / "opencode" / "opencode.json").exists()


def test_apply_skip_check_installs_without_probing(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "ROOT", tmp_path)
    monkeypatch.setattr(lm, "detect_server", lambda *a: pytest.fail("must not probe"))
    home = tmp_path / "home"
    code = lm.main(["--catalog", str(write_catalog(tmp_path, "http://127.0.0.1:9")), "apply",
                    "--home", str(home), "--skip-check"])
    assert code == 0
    assert (home / ".config" / "opencode" / "opencode.json").exists()


def test_build_still_builds_when_preflight_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lm, "ROOT", tmp_path)
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    code = lm.main(["--catalog", str(write_catalog(tmp_path, "http://127.0.0.1:9")), "build",
                    "--home", str(tmp_path / "home")])
    assert code == 0
    assert (tmp_path / "out" / "opencode" / "opencode.json").exists()
    assert "apply will refuse" in capsys.readouterr().out


def test_verify_counts_preflight_problems(monkeypatch, capsys):
    monkeypatch.setenv("PRE_KEY", "sk-pre")
    raw = raw_example()
    raw["endpoints"]["corp"] = {"base_url": "http://127.0.0.1:9", "key_env": "PRE_KEY"}
    raw["tools"] = {"opencode": False, "omo": False, "hermes": False, "claude_code": False}
    assert lm.cmd_verify(lm.normalize_catalog(raw)) == 1
    assert "[FAIL] corp" in capsys.readouterr().out


# ---- Claude Code across several endpoints

def two_endpoint_raw() -> dict:
    """prod and rnd both serve Claude models; tiers mix the two endpoints."""
    return {
        "endpoints": {"prod": {"base_url": "https://prod.example.com", "key_env": "PROD_KEY"},
                      "rnd": {"base_url": "https://rnd.example.com", "key_env": "RND_KEY"}},
        "models": {
            "opus-prod": {"endpoint": "prod", "family": "claude", "context": 200000},
            "sonnet-rnd": {"endpoint": "rnd", "family": "claude", "context": 200000},
            "sonnet-prod": {"endpoint": "prod", "family": "claude", "context": 200000},
            "haiku-rnd": {"endpoint": "rnd", "family": "claude", "context": 200000},
            "gpt-rnd": {"endpoint": "rnd", "family": "gpt", "context": 200000},
        },
        "tiers": {"tier1": ["opus-prod", "gpt-rnd"], "tier2": ["sonnet-rnd", "sonnet-prod"], "tier3": ["haiku-rnd"]},
        "default": "opus-prod",
    }


def test_claude_code_auto_pick_stays_on_the_main_models_endpoint():
    cc = lm.normalize_catalog(two_endpoint_raw())["claude_code"]
    assert cc["endpoint"] == "prod"
    assert (cc["model"], cc["opus"], cc["sonnet"]) == ("opus-prod", "opus-prod", "sonnet-prod")


def test_claude_code_slot_borrows_from_another_tier_on_the_same_endpoint():
    cc = lm.normalize_catalog(two_endpoint_raw())["claude_code"]
    assert cc["haiku"] == "sonnet-prod"  # tier3 has no prod Claude model; the nearest tier on prod does


def test_claude_code_endpoint_follows_an_explicit_main_model():
    raw = two_endpoint_raw()
    raw["claude_code"] = {"model": "sonnet-rnd"}
    cc = lm.normalize_catalog(raw)["claude_code"]
    assert cc["endpoint"] == "rnd"
    assert (cc["opus"], cc["sonnet"], cc["haiku"]) == ("sonnet-rnd", "sonnet-rnd", "haiku-rnd")


def test_claude_code_endpoint_follows_a_non_claude_default():
    raw = two_endpoint_raw()
    raw["default"] = "gpt-rnd"
    cc = lm.normalize_catalog(raw)["claude_code"]
    assert cc["endpoint"] == "prod" and cc["model"] == "opus-prod"  # first Claude model in tier order


def test_claude_code_explicit_mix_of_endpoints_is_still_reported():
    raw = two_endpoint_raw()
    raw["claude_code"] = {"model": "opus-prod", "haiku": "haiku-rnd"}
    assert "claude_code: all Claude Code models must be on the same endpoint" in problems(raw)


# ---- Claude Code key follows its endpoint

HELPER = r"C:\Users\me\.claude\api-key.ps1"


def test_claude_code_key_defaults_to_its_endpoints_key():
    assert lm.normalize_catalog(two_endpoint_raw())["claude_code"]["key_env"] == "PROD_KEY"


def test_claude_settings_read_the_endpoint_key_through_the_helper():
    cat = lm.normalize_catalog(two_endpoint_raw())
    cfg, _ = lm.render_claude_settings(cat, "s.ps1", HELPER)
    assert cfg["apiKeyHelper"] == f'powershell -NoProfile -ExecutionPolicy Bypass -File "{HELPER}" PROD_KEY'
    assert cfg["env"]["ANTHROPIC_BASE_URL"] == "https://prod.example.com"


def test_native_claude_key_variable_needs_no_helper():
    raw = two_endpoint_raw()
    raw["claude_code"] = {"key_env": "ANTHROPIC_AUTH_TOKEN"}
    cfg, _ = lm.render_claude_settings(lm.normalize_catalog(raw), "s.ps1", HELPER)
    assert "apiKeyHelper" not in cfg


def test_key_helper_script_is_installed(tmp_path):
    ops, _ = lm.plan_files(lm.normalize_catalog(two_endpoint_raw()), tmp_path)
    helper = [op for op in ops if op.rel == "claude/api-key.ps1"]
    assert helper and helper[0].target == tmp_path / ".claude" / "api-key.ps1"


def test_key_instructions_warn_when_a_claude_variable_would_override_the_helper(monkeypatch):
    cat = lm.normalize_catalog(two_endpoint_raw())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert not any("ANTHROPIC_" in line for line in lm.key_instructions(cat))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "old")
    warning = [line for line in lm.key_instructions(cat) if "ANTHROPIC_AUTH_TOKEN" in line]
    assert warning and "PROD_KEY" in warning[0]


@pytest.mark.skipif(not shutil.which("powershell"), reason="needs Windows PowerShell")
def test_key_helper_script_prints_only_the_key(monkeypatch):
    script = lm.ROOT / "claude" / "api-key.ps1"
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "LLM_MANAGER_TEST_KEY"]
    monkeypatch.setenv("LLM_MANAGER_TEST_KEY", "sk-helper-1")
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert (ok.returncode, ok.stdout) == (0, "sk-helper-1")
    monkeypatch.delenv("LLM_MANAGER_TEST_KEY")
    missing = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert missing.returncode != 0 and missing.stdout == "" and "LLM_MANAGER_TEST_KEY" in missing.stderr


# ---- assist: evidence

def test_detect_records_each_probe():
    routes = {"/openai/v1/models": (200, {"data": [{"id": "gpt-4o"}]}, {"apim-request-id": "abc"})}
    with fake_server(routes, need=("api-key", "k")) as (url, _):
        det = lm.detect_server(url, "k")
    assert any(p["path"] == "/openai/v1/models" and p["auth"] == "api-key" and p["status"] == 200
               for p in det["probes"])
    assert any(p["status"] == 401 for p in det["probes"])
    assert all(p["method"] == "GET" for p in det["probes"])


def test_detect_summarizes_openapi():
    schemes = {"tenant": {"type": "apiKey", "in": "header", "name": "X-Tenant-Id"}}
    spec = {"paths": {"/v2/chat": {}, "/v2/models": {}}, "components": {"securitySchemes": schemes}}
    with fake_server({"/openapi.json": (200, spec, {})}) as (url, _):
        det = lm.detect_server(url, "k")
    assert det["openapi"] == {"paths": ["/v2/chat", "/v2/models"], "security_schemes": schemes}


def test_evidence_never_contains_the_key():
    routes = {"/v1/models": (401, {"error": "bad key sk-leak-123"},
                             {"X-Debug": "sk-leak-123", "Set-Cookie": "s=1"})}
    with fake_server(routes) as (url, _):
        det = lm.detect_server(url, "sk-leak-123")
    text = json.dumps(lm.build_evidence(det, "sk-leak-123"))
    assert "sk-leak-123" not in text and "<key>" in text
    assert all("set-cookie" not in p["headers"] for p in lm.build_evidence(det, "sk-leak-123")["probes"])
    # build_evidence scrubs on its own too, whatever detect_server recorded
    det["probes"].append({"method": "GET", "path": "/x", "auth": "bearer", "status": 401,
                          "headers": {"Authorization": "Bearer sk-leak-123", "x-echo": "sk-leak-123"},
                          "body": "sk-leak-123"})
    ev = lm.build_evidence(det, "sk-leak-123")
    assert "sk-leak-123" not in json.dumps(ev)
    assert "authorization" not in {h.lower() for h in ev["probes"][-1]["headers"]}


def test_evidence_bodies_are_capped():
    with fake_server({"/v1/models": (401, "x" * 2000, {})}) as (url, _):
        det = lm.detect_server(url, "k")
    ev = lm.build_evidence(det, "k")
    assert ev["probes"] and all(len(p["body"]) <= 500 for p in ev["probes"])


KEY = "sk-cutkey-0123456789abcdefghij"


def test_evidence_hides_key_cut_by_http_call():
    # http_call cuts error bodies to 300 characters, so the key arrives with its tail missing
    with fake_server({"/v1/models": (401, "x" * 280 + KEY, {})}) as (url, _):
        det = lm.detect_server(url, KEY)
    assert all(KEY[:3] not in p["body"] for p in det["probes"])
    text = json.dumps(lm.build_evidence(det, KEY))
    assert KEY[:3] not in text and "<key>" in text


def test_evidence_hides_key_cut_by_body_cap():
    det = {"probes": [{"method": "GET", "path": "/x", "auth": "bearer", "status": 401, "headers": {},
                       "body": "y" * 480 + KEY + "z" * 100}]}
    ev = lm.build_evidence(det, KEY)
    body = ev["probes"][0]["body"]
    assert KEY[:3] not in body and "y" * 480 + "<key>z" in body and len(body) == lm.PROBE_BODY_MAX


def test_redact_body_strips_trailing_key_prefix():
    assert lm.redact_body("abc " + KEY[:10], KEY) == "abc <key>"
    assert lm.redact_body("abc " + json.dumps("a\"" + KEY)[1:6], "a\"" + KEY) == "abc <key>"
    assert lm.redact_body("abc sk", KEY) == "abc sk"  # too short to tell apart from ordinary text
    assert lm.redact_body("z" * 600, KEY) == "z" * lm.PROBE_BODY_MAX
    capped = lm.redact_body("z" * 498 + KEY[:10], KEY)
    assert KEY[:2] not in capped and len(capped) == lm.PROBE_BODY_MAX


def test_redact_short_key_is_left_alone():
    assert lm.redact("abc sk-1234 abc", "sk-1234") == "abc <key> abc"
    assert lm.redact("abc", "abc") == "abc"
    assert lm.redact("abc", None) == "abc"


# ---- assist: candidates

def cand(**kw) -> dict:
    return {"api": "azure-openai", "auth": "api-key", "api_base": "/openai/v1", **kw}


def test_parse_candidates_from_fenced_text():
    text = ('Here:\n```json\n{"candidates":[{"api":"azure-openai","auth":"api-key",'
            '"api_base":"/openai/v1","reason":"apim"}]}\n```')
    cands, probs = lm.parse_candidates(text)
    assert probs == []
    assert cands == [{"api": "azure-openai", "auth": "api-key", "api_base": "/openai/v1",
                      "query": {}, "extra_headers": [], "reason": "apim"}]


def test_parse_candidates_rejects_bad_answers():
    cands, probs = lm.parse_candidates("no idea")
    assert cands == [] and len(probs) == 1
    cands, probs = lm.parse_candidates({"candidates": [cand(api="grpc"), cand()]})
    assert [c["api"] for c in cands] == ["azure-openai"] and len(probs) == 1 and "grpc" in probs[0]
    cands, probs = lm.parse_candidates({"candidates": [cand(reason=str(i)) for i in range(5)]})
    assert [c["reason"] for c in cands] == ["0", "1", "2"]
    for bad in (None, 42, {"candidates": "x"}, "{broken json", "```json\n{\"candidates\": [\n```"):
        cands, probs = lm.parse_candidates(bad)
        assert cands == [] and probs


def test_parse_candidates_rejects_foreign_hosts():
    payload = {"candidates": [cand(api_base="https://evil.example/v1"), cand(api_base="//evil/v1"),
                              cand(api_base="v1"), cand(api_base="/a://b")]}
    cands, probs = lm.parse_candidates(payload)
    assert cands == [] and len(probs) == 4


def test_parse_candidates_drops_credential_and_malformed_headers():
    headers = ["Authorization", "api-key", "Cookie", "X Bad", "X-Ok\r\nY: z", "X-Tenant-Id\n", "X-Tenant-Id"]
    cands, probs = lm.parse_candidates({"candidates": [cand(extra_headers=headers)]})
    assert cands[0]["extra_headers"] == ["X-Tenant-Id"]
    assert len(probs) == 6


def test_parse_candidates_drops_secret_named_and_routing_headers():
    # the header name picks the env var whose value is sent, so names that point at secrets are refused
    headers = ["Openai-Api-Key", "Github-Token", "Aws-Secret-Access-Key", "X-Db-Password", "X-Session-Id",
               "X-Credentials", "Proxy-Authorization", "Host", "content-length", "Transfer-Encoding",
               "Connection", "Proxy-Foo", "X-Tenant-Id", "Ocp-Apim-Trace"]
    cands, probs = lm.parse_candidates({"candidates": [cand(extra_headers=headers)]})
    assert cands[0]["extra_headers"] == ["X-Tenant-Id", "Ocp-Apim-Trace"]
    assert len(probs) == 12


def test_header_env_name():
    assert lm.header_env_name("X-Tenant-Id") == "LLM_HEADER_X_TENANT_ID"
    assert lm.header_env_name("x.trace id") == "LLM_HEADER_X_TRACE_ID"
    assert lm.header_env_name("Database-Url") == "LLM_HEADER_DATABASE_URL"


def test_http_call_appends_query():
    routes = {"/m?api-version=2024-10-21&a=b+c": MODELS}
    with fake_server(routes) as (url, seen):
        status, _, _ = lm.http_call(url + "/m", None, query={"api-version": "2024-10-21", "a": "b c"})
    assert status == 200


def test_verify_candidate_verified_with_query():
    routes = {"/openai/models?api-version=2024-10-21": (200, {"data": [{"id": "gpt-4o"}]}, {})}
    with fake_server(routes, need=("api-key", "k")) as (url, _):
        res = lm.verify_candidate(url, "k", cand(api_base="/openai", query={"api-version": "2024-10-21"},
                                                 extra_headers=[], reason="r"))
    assert res["status"] == "verified" and res["reason"] == "r"


def test_verify_candidate_failed():
    with fake_server({"/openai/v1/models": MODELS}, need=("api-key", "k")) as (url, _):
        res = lm.verify_candidate(url, "k", cand(auth="bearer", query={}, extra_headers=[]))
    assert res["status"] == "failed" and "401" in res["detail"]
    with fake_server({"/openai/v1/models": (200, {"object": "list"}, {})}) as (url, _):
        res = lm.verify_candidate(url, "k", cand(query={}, extra_headers=[]))
    assert res["status"] == "failed"


def test_verify_candidate_needs_value_then_uses_env(monkeypatch):
    c = cand(auth="bearer", query={}, extra_headers=["X-Tenant-Id"])
    monkeypatch.delenv("LLM_HEADER_X_TENANT_ID", raising=False)
    monkeypatch.setenv("X_TENANT_ID", "t1")  # a variable outside the LLM_HEADER_ namespace is never read
    with fake_server({"/openai/v1/models": MODELS}, need=("X-Tenant-Id", "t1")) as (url, seen):
        res = lm.verify_candidate(url, "k", c)
        assert res["status"] == "needs value" and "LLM_HEADER_X_TENANT_ID" in res["detail"]
        assert seen == []
        monkeypatch.setenv("LLM_HEADER_X_TENANT_ID", "t1")
        res = lm.verify_candidate(url, "k", c)
    assert res["status"] == "verified"


KOREAN_TENANT = "".join(map(chr, (0xD14C, 0xB10C, 0xD2B8)))
E_ACUTE = chr(0xE9)


def test_parse_candidates_rejects_non_ascii_api_base():
    payload = {"candidates": [cand(api_base="/op" + E_ACUTE + "nai/v1"), cand(api_base="/openai/" + E_ACUTE),
                              cand(api_base="/a b"), cand(api_base="/a" + chr(0xA0) + "b")]}
    cands, probs = lm.parse_candidates(payload)
    assert cands == [] and len(probs) == 4


def test_verify_candidate_rejects_header_values_http_cannot_send(monkeypatch):
    c = cand(auth="bearer", query={}, extra_headers=["X-Tenant-Id"])
    with fake_server({"/openai/v1/models": MODELS}) as (url, seen):
        for bad in (KOREAN_TENANT, "t1" + chr(13) + chr(10) + "X-Evil: 1", "a" + chr(9) + "b"):
            monkeypatch.setenv("LLM_HEADER_X_TENANT_ID", bad)
            res = lm.verify_candidate(url, "k", c)
            assert res["status"] == "needs value" and "LLM_HEADER_X_TENANT_ID" in res["detail"]
    assert seen == []


def test_http_call_reports_unsendable_request_without_crashing():
    with fake_server({"/m": MODELS}) as (url, seen):
        status, _, headers = lm.http_call(url + "/m", None, headers={"X-Tenant-Id": KOREAN_TENANT})
        assert status is None and headers == {}
        status, _, _ = lm.http_call(url + "/op" + E_ACUTE + "nai/models", None)
        assert status is None
    assert seen == []


def test_redirect_to_another_host_does_not_carry_the_key():
    with fake_server({"/openai/v1/models": MODELS}) as (other, other_seen):
        moved = (302, {"error": "login"}, {"Location": other + "/openai/v1/models"})
        with fake_server({"/openai/v1/models": moved}) as (url, seen):
            res = lm.verify_candidate(url, "sk-secret-xyz", cand(query={}, extra_headers=[]))
    assert other_seen == []
    assert len(seen) == 1
    assert res["status"] == "failed" and "302" in res["detail"]


def test_redirect_on_the_same_server_is_followed():
    with fake_server({"/old/models": (301, {}, {"Location": "/new/models"}), "/new/models": MODELS},
                     need=("api-key", "k")) as (url, seen):
        res = lm.verify_candidate(url, "k", cand(api_base="/old", query={}, extra_headers=[]))
    assert res["status"] == "verified" and [s[1] for s in seen] == ["/old/models", "/new/models"]


# ---- assist: judges

ASSIST_RAW = {"assist": {"base_url": "https://judge.example", "key_env": "ASSIST_KEY", "model": "judge-1"}}


def target_evidence() -> dict:
    """Evidence from a target gateway that echoes the key sk-target in its error body."""
    with fake_server({"/v1/models": (401, {"error": "bad key sk-target"}, {})}) as (url, _):
        det = lm.detect_server(url, "sk-target")
    return lm.build_evidence(det, "sk-target")


def test_run_passes_input_on_stdin():
    code, out = lm.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], input="hello")
    assert (code, out) == (0, "HELLO")
    code, out = lm.run([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"])
    assert (code, out) == (0, "''")


def test_run_uses_cwd(tmp_path):
    code, out = lm.run([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=str(tmp_path))
    assert code == 0 and Path(out).resolve() == tmp_path.resolve()


def test_ask_api_returns_model_content(monkeypatch):
    monkeypatch.setenv("ASSIST_KEY", "ak")
    evidence = target_evidence()
    answer = {"choices": [{"message": {"content": "{\"candidates\":[]}"}}]}
    bodies = []
    with fake_server({"/v1/chat/completions": (200, answer, {})}, need=("Authorization", "Bearer ak"),
                     bodies=bodies) as (url, seen):
        cfg, probs = lm.assist_config({"assist": {"base_url": url, "key_env": "ASSIST_KEY", "model": "judge-1"}})
        payload, reason = lm.ask_api(cfg, evidence)
    assert probs == [] and (payload, reason) == ("{\"candidates\":[]}", "")
    assert [(m, p) for m, p, _ in seen] == [("POST", "/v1/chat/completions")]
    sent = json.loads(bodies[0])
    assert sent["model"] == "judge-1" and sent["temperature"] == 0
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"] == lm.ASSIST_PROMPT
    user = sent["messages"][1]["content"]
    assert "<key>" in user and "sk-target" not in user and "sk-target" not in bodies[0]
    assert json.loads(user) == evidence


def test_ask_api_reports_bad_answers(monkeypatch):
    monkeypatch.setenv("ASSIST_KEY", "ak")
    with fake_server({"/v1/chat/completions": (200, {"choices": []}, {})}) as (url, _):
        payload, reason = lm.ask_api({"root": url, "key_env": "ASSIST_KEY", "model": "m"}, {})
    assert payload is None and reason
    for content in ([{"text": 5}], [{"text": {"nested": "x"}}], [{"text": None}, "loose"]):
        answer = {"choices": [{"message": {"content": content}}]}
        with fake_server({"/v1/chat/completions": (200, answer, {})}) as (url, _):
            payload, reason = lm.ask_api({"root": url, "key_env": "ASSIST_KEY", "model": "m"}, {})
        assert payload is None and "no message content" in reason
    parts = [{"text": "{\"candidates\""}, {"text": 7}, {"text": ":[]}"}]
    with fake_server({"/v1/chat/completions": (200, {"choices": [{"message": {"content": parts}}]}, {})}) as (url, _):
        payload, reason = lm.ask_api({"root": url, "key_env": "ASSIST_KEY", "model": "m"}, {})
    assert (payload, reason) == ("{\"candidates\":[]}", "")
    with fake_server({}, need=("Authorization", "Bearer other")) as (url, _):
        payload, reason = lm.ask_api({"root": url, "key_env": "ASSIST_KEY", "model": "m"}, {})
    assert payload is None and "401" in reason
    monkeypatch.delenv("ASSIST_KEY")
    payload, reason = lm.ask_api({"root": url, "key_env": "ASSIST_KEY", "model": "m"}, {})
    assert payload is None and "ASSIST_KEY" in reason


def test_ask_api_unreachable(monkeypatch):
    monkeypatch.setenv("ASSIST_KEY", "ak")
    cfg, _ = lm.assist_config({"assist": {"base_url": "http://127.0.0.1:9", "key_env": "ASSIST_KEY", "model": "m"}})
    payload, reason = lm.ask_api(cfg, {}, timeout=5)
    assert payload is None and reason


def fake_run(calls: list, code, out):
    def run(cmd, timeout=60, input=None, cwd=None):
        calls.append((cmd, input, cwd))
        return code, out
    return run


def test_ask_claude_uses_structured_output(monkeypatch):
    calls = []
    monkeypatch.setattr(lm, "run", fake_run(calls, 0, json.dumps({"structured_output": {"candidates": []},
                                                                  "result": "ignored"})))
    evidence = target_evidence()
    payload, reason = lm.ask_claude(evidence)
    assert (payload, reason) == ({"candidates": []}, "")
    cmd, stdin, cwd = calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd and "--mcp-config" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "user"
    assert cwd and Path(cwd).resolve() != Path.cwd().resolve() and not Path(cwd).exists()
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == lm.ASSIST_SCHEMA
    assert "--no-session-persistence" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert stdin.startswith(lm.ASSIST_PROMPT) and "<key>" in stdin and "sk-target" not in stdin


def test_ask_claude_falls_back_to_result_text(monkeypatch):
    monkeypatch.setattr(lm, "run", fake_run([], 0, json.dumps({"result": "```json\n{\"candidates\": []}\n```"})))
    payload, reason = lm.ask_claude({})
    assert reason == "" and lm.parse_candidates(payload) == ([], [])
    monkeypatch.setattr(lm, "run", fake_run([], 0, "not json at all"))
    payload, reason = lm.ask_claude({})
    assert payload is None and reason
    monkeypatch.setattr(lm, "run", fake_run([], 0, json.dumps({"is_error": True, "result": "API Error: 401"})))
    payload, reason = lm.ask_claude({})
    assert payload is None and "401" in reason


def test_ask_claude_not_installed(monkeypatch):
    monkeypatch.setattr(lm, "run", fake_run([], None, "claude not found on PATH"))
    payload, reason = lm.ask_claude({})
    assert payload is None and "not found" in reason
    monkeypatch.setattr(lm, "run", fake_run([], 1, "boom"))
    payload, reason = lm.ask_claude({})
    assert payload is None and "boom" in reason


def test_claude_gateway_url(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert lm.claude_gateway_url(tmp_path) is None
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert lm.claude_gateway_url(tmp_path) is None
    (tmp_path / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gw"}}),
                                            encoding="utf-8")
    assert lm.claude_gateway_url(tmp_path) == "https://gw"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://from-env")
    assert lm.claude_gateway_url(tmp_path) == "https://from-env"


def test_choose_judge_auto_prefers_assist_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw")
    name, judge, msgs = lm.choose_judge("auto", ASSIST_RAW, tmp_path)
    assert name == "api" and callable(judge)
    name, judge, msgs = lm.choose_judge("api", ASSIST_RAW, tmp_path)
    assert name == "api" and callable(judge)


def test_choose_judge_auto_skips_claude_without_gateway(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    name, judge, msgs = lm.choose_judge("auto", {}, tmp_path)
    assert name is None and judge is None
    assert any("--assist claude" in m for m in msgs)


def test_choose_judge_auto_uses_claude_on_gateway(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    (tmp_path / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gw"}}),
                                            encoding="utf-8")
    name, judge, msgs = lm.choose_judge("auto", {}, tmp_path)
    assert name == "claude" and callable(judge)
    assert not any("warning" in m for m in msgs)


def test_choose_judge_explicit_claude_warns_off_gateway(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    name, judge, msgs = lm.choose_judge("claude", ASSIST_RAW, tmp_path)
    assert name == "claude" and callable(judge)
    assert any("Anthropic" in m for m in msgs)


def test_choose_judge_api_without_usable_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw")
    name, judge, msgs = lm.choose_judge("api", {}, tmp_path)
    assert name is None and judge is None and any("assist" in m for m in msgs)
    # a broken assist: block is reported, not silently replaced by Claude Code
    name, judge, msgs = lm.choose_judge("auto", {"assist": {"base_url": "ftp://x"}}, tmp_path)
    assert name is None and judge is None and any("base_url" in m for m in msgs)


def test_assist_config_problems():
    assert lm.assist_config({}) == (None, [])
    cfg, probs = lm.assist_config({"assist": {"base_url": "ftp://x"}})
    assert cfg is None
    text = " ".join(probs)
    assert "base_url" in text and "key_env" in text and "model" in text
    cfg, probs = lm.assist_config({"assist": "nope"})
    assert cfg is None and probs
    cfg, probs = lm.assist_config({"assist": {"base_url": "https://judge.example/v1/", "key_env": "K", "model": "m"}})
    assert probs == [] and cfg == {"root": "https://judge.example", "key_env": "K", "model": "m"}


# ---- assist: command

TARGET_KEY = "sk-corp-target-1"
AZURE_ROUTES = {"/openai/v1/models": (200, {"data": [{"id": "gpt-4o"}]}, {"apim-request-id": "abc"})}
AZURE_CAND = {"api": "azure-openai", "auth": "api-key", "api_base": "/openai/v1", "query": {},
              "extra_headers": [], "reason": "apim-request-id header and /openai/v1/models answers"}


def judge_answer(*cands) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"candidates": list(cands)})}}]}


def assist_catalog(tmp_path, target: str, judge: str | None = None) -> Path:
    raw = {"endpoints": {"corp": {"base_url": target, "key_env": "CORP_KEY"}}}
    if judge:
        raw["assist"] = {"base_url": judge, "key_env": "ASSIST_KEY", "model": "judge-1"}
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.fixture
def assist_env(tmp_path, monkeypatch):
    """Target and judge keys set; Claude Code looks off-gateway (no real ~/.claude is read)."""
    monkeypatch.setenv("CORP_KEY", TARGET_KEY)
    monkeypatch.setenv("ASSIST_KEY", "ak")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    claude_dir = tmp_path / "claude-home"
    monkeypatch.setattr(lm, "tool_dirs", lambda home: {"claude": claude_dir})
    return claude_dir


def test_needs_assist():
    base = {"kind": "litellm", "renderable": True}
    assert not lm.needs_assist(base)
    assert lm.needs_assist({**base, "kind": "unknown", "renderable": False})
    assert lm.needs_assist({**base, "kind": "azure-openai", "renderable": False})
    assert not lm.needs_assist({"kind": "unreachable", "renderable": False})
    assert not lm.needs_assist({"kind": "bedrock", "renderable": False})


def test_detect_assist_end_to_end(tmp_path, assist_env, capsys):
    bodies = []
    judge_routes = {"/v1/chat/completions": (200, judge_answer(AZURE_CAND), {})}
    with fake_server(AZURE_ROUTES, need=("api-key", TARGET_KEY)) as (target, _):
        with fake_server(judge_routes, need=("Authorization", "Bearer ak"), bodies=bodies) as (judge, judge_seen):
            code = lm.main(["--catalog", str(assist_catalog(tmp_path, target, judge)), "detect", "--assist"])
    out = capsys.readouterr().out
    assert code == 0
    assert len(judge_seen) == 1 and TARGET_KEY not in bodies[0]
    assert "[verified] azure-openai" in out and "api-key: <key>" in out
    block = out[out.index("[verified]"):]
    assert "auth: api-key" in block and "api: azure-openai" in block and "key_env: CORP_KEY" in block
    assert f"base_url: {target}/openai/v1" in block
    assert TARGET_KEY not in out


def test_assist_is_not_called_for_working_endpoint(tmp_path, assist_env, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(lm, "ask_api", lambda cfg, ev, timeout=0: calls.append(ev) or (None, "x"))
    monkeypatch.setattr(lm, "ask_claude", lambda ev, timeout=0: calls.append(ev) or (None, "x"))
    with fake_server({"/v1/models": MODELS}, need=("Authorization", f"Bearer {TARGET_KEY}")) as (target, _):
        code = lm.main(["--catalog", str(assist_catalog(tmp_path, target, "https://judge.example")),
                        "detect", "--assist"])
    out = capsys.readouterr().out
    assert code == 0 and calls == []
    assert "assist" not in out


def test_assist_without_judge_keeps_detection_output(tmp_path, assist_env, capsys):
    with fake_server({"/v1/models": MODELS}, need=("Authorization", "Bearer right")) as (target, _):
        path = str(assist_catalog(tmp_path, target))
        plain = lm.main(["--catalog", path, "detect"])
        plain_out = capsys.readouterr().out
        code = lm.main(["--catalog", path, "detect", "--assist"])
    out = capsys.readouterr().out
    assert code == plain == 1
    assert out.startswith(plain_out) and "server:  unknown" in out
    assert "--assist claude" in out


def test_detect_without_assist_flag_never_calls_judge(tmp_path, assist_env, monkeypatch, capsys):
    monkeypatch.setattr(lm, "ask_api", lambda *a, **k: pytest.fail("ask_api called"))
    monkeypatch.setattr(lm, "ask_claude", lambda *a, **k: pytest.fail("ask_claude called"))
    with fake_server(AZURE_ROUTES, need=("api-key", TARGET_KEY)) as (target, _):
        code = lm.main(["--catalog", str(assist_catalog(tmp_path, target, "https://judge.example")), "detect"])
    out = capsys.readouterr().out
    assert code == 0 and "server:  azure-openai" in out and "assist" not in out


def test_assist_reports_unusable_answer_and_keeps_going(tmp_path, assist_env, capsys):
    answer = {"choices": [{"message": {"content": "I am not sure, sorry."}}]}
    with fake_server(AZURE_ROUTES, need=("api-key", TARGET_KEY)) as (target, _):
        with fake_server({"/v1/chat/completions": (200, answer, {})}) as (judge, _):
            raw = yaml.safe_load(assist_catalog(tmp_path, target, judge).read_text(encoding="utf-8"))
            raw["endpoints"]["second"] = {"base_url": target, "key_env": "CORP_KEY"}
            res = lm.cmd_detect([(n, ep["base_url"], ep["key_env"]) for n, ep in raw["endpoints"].items()],
                                assist_mode="auto", raw=raw, claude_dir=assist_env)
    out = capsys.readouterr().out
    assert set(res) == {"corp", "second"}
    for det in res.values():
        assert det["assist"]["candidates"] == [] and det["assist"]["problems"]
    assert out.count("no JSON object") == 2 and "[second]" in out


def test_assist_endpoint_verifies_and_reports_judge_failure():
    with fake_server(AZURE_ROUTES, need=("api-key", TARGET_KEY)) as (target, _):
        det = lm.detect_server(target, TARGET_KEY)
        bad = dict(AZURE_CAND, auth="bearer", reason="guess")
        res = lm.assist_endpoint(det, TARGET_KEY, lambda ev: ({"candidates": [AZURE_CAND, bad]}, ""))
        failed = lm.assist_endpoint(det, TARGET_KEY, lambda ev: (None, "judge timed out"))
        crashed = lm.assist_endpoint(det, TARGET_KEY, lambda ev: 1 / 0)
    assert [c["status"] for c in res["candidates"]] == ["verified", "failed"]
    assert res["judge_reason"] == "" and res["problems"] == []
    assert failed == {"judge_reason": "judge timed out", "problems": [], "candidates": []}
    assert crashed["candidates"] == [] and "ZeroDivisionError" in crashed["judge_reason"]


def test_print_assist_lines_and_yaml(capsys):
    result = {"judge_reason": "", "problems": ["candidate 2: unknown api 'x'"], "root": "https://gw", "key_env": "K",
              "candidates": [
                  {**AZURE_CAND, "api_base": "/openai", "query": {"api-version": "2024-10-21"},
                   "extra_headers": ["X-Tenant-Id"], "status": "verified", "detail": "1 models listed"},
                  {**AZURE_CAND, "auth": "bearer", "status": "failed", "detail": "HTTP 401: nope"}]}
    lm.print_assist("corp", result)
    out = capsys.readouterr().out
    assert "  assist: [verified] azure-openai, api-key: <key>, /openai?api-version=2024-10-21 - apim" in out
    assert "  assist: [failed] azure-openai, Authorization: Bearer <key>, /openai/v1 - " in out
    assert "HTTP 401: nope" in out and "unknown api" in out
    assert "reference only" in out and "suggested endpoint" not in out
    yaml_text = out[out.index("corp:"):out.index("[failed]")].rsplit("\n", 1)[0]
    block = yaml.safe_load(yaml_text)["corp"]
    assert block == {"base_url": "https://gw/openai", "key_env": "K", "api": "azure-openai", "auth": "api-key",
                     "query": {"api-version": "2024-10-21"}, "headers": {"X-Tenant-Id": "${LLM_HEADER_X_TENANT_ID}"}}


def test_discover_assist_saves_candidates(tmp_path, assist_env, capsys):
    routes = {**AZURE_ROUTES, "/openai/v1/chat/completions": (200, {"ok": True}, {})}
    with fake_server(routes, need=("api-key", TARGET_KEY)) as (target, _):
        with fake_server({"/v1/chat/completions": (200, judge_answer(AZURE_CAND), {})}) as (judge, _):
            raw = yaml.safe_load(assist_catalog(tmp_path, target, judge).read_text(encoding="utf-8"))
            out_file = tmp_path / "discovered.json"
            res = lm.cmd_discover(raw, out_file, None, assist_mode="api", claude_dir=assist_env)
    saved_text = out_file.read_text(encoding="utf-8")
    assert res["corp"]["assist"]["candidates"][0]["status"] == "verified"
    assert json.loads(saved_text)["corp"]["assist"]["candidates"][0]["status"] == "verified"
    assert TARGET_KEY not in saved_text
    assert "[verified]" in capsys.readouterr().out


def test_parse_candidates_prefers_the_answer_over_a_quoted_body():
    answer = json.dumps({"candidates": [cand(reason="apim")]})
    text = f'The gateway said {{"error": "Access denied"}} so:\n```json\n{answer}\n```'
    cands, probs = lm.parse_candidates(text)
    assert probs == [] and [c["reason"] for c in cands] == ["apim"]
    cands, probs = lm.parse_candidates('Only {"error": "Access denied"} here')
    assert cands == [] and probs == ["answer has no 'candidates' list"]


def test_assist_endpoint_reads_answer_after_quoted_body():
    with fake_server(AZURE_ROUTES, need=("api-key", TARGET_KEY)) as (target, _):
        det = lm.detect_server(target, TARGET_KEY)
        text = ('The gateway said {"error": "unauthorized"} so:\n```json\n'
                + json.dumps({"candidates": [AZURE_CAND]}) + "\n```")
        res = lm.assist_endpoint(det, TARGET_KEY, lambda ev: (text, ""))
    assert res["problems"] == [] and [c["status"] for c in res["candidates"]] == ["verified"]


def test_assist_skipped_when_the_key_is_not_set(tmp_path, assist_env, monkeypatch, capsys):
    monkeypatch.delenv("CORP_KEY")
    monkeypatch.setattr(lm, "ask_api", lambda *a, **k: pytest.fail("ask_api called"))
    monkeypatch.setattr(lm, "ask_claude", lambda *a, **k: pytest.fail("ask_claude called"))
    with fake_server(AZURE_ROUTES, need=("api-key", TARGET_KEY)) as (target, _):
        code = lm.main(["--catalog", str(assist_catalog(tmp_path, target, "https://judge.example")),
                        "detect", "--assist"])
    out = capsys.readouterr().out
    assert code == 1 and "server:  unknown" in out
    assert "assist: skipped" in out and "CORP_KEY" in out and "[failed]" not in out


def test_assist_without_key_still_asks_when_nothing_was_rejected(tmp_path, assist_env, monkeypatch, capsys):
    monkeypatch.delenv("CORP_KEY")
    calls = []
    monkeypatch.setattr(lm, "ask_api", lambda cfg, ev, timeout=0: calls.append(ev) or (None, "no answer"))
    with fake_server({}) as (target, _):
        lm.main(["--catalog", str(assist_catalog(tmp_path, target, "https://judge.example")), "detect", "--assist"])
    assert len(calls) == 1 and "assist: skipped" not in capsys.readouterr().out
