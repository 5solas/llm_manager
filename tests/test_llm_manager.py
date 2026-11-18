import contextlib
import json
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
    assert cfg["providers"]["corp"]["models"]["claude-sonnet-example"] == {"context_length": 200000, "supports_vision": True}
    assert cfg["auxiliary"]["vision"] == {"provider": "custom:corp", "model": "claude-sonnet-example"}
    assert cfg["auxiliary"]["compression"] == {"provider": "custom:corp", "model": "claude-haiku-example"}
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
def fake_server(routes: dict, need: tuple[str, str] | None = None):
    """routes: {path: (status, body, headers)}. need: (header, value) every request must carry, else 401."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
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
    assert res["renderable"] and not res["warnings"]
    assert "api_base: /openai/v1" in lm.endpoint_yaml("azure", res, "AZURE_KEY")


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
    monkeypatch.setenv("COMPANY_LLM_KEY", "sk-test")
    monkeypatch.setattr(lm, "http_call", lambda url, *a, **k: probed.append(url) or
                        (200, {"data": [{"id": i} for i in EXAMPLE_IDS]}, {}))
    lm.preflight(lm.normalize_catalog(raw))
    assert probed == ["https://llm-gateway.example.com/v1/models"]


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


# ---- multi-endpoint design tests

def multi_endpoint_raw() -> dict:
    return {
        "endpoints": {
            "corp": {
                "base_url": "https://gw.example.com",
                "key_env": "CORP_KEY",
                "kind": "litellm",
            },
            "local_vllm": {
                "base_url": "http://127.0.0.1:8000",
                "auth": "none",
                "kind": "vllm",
                "concurrency": 2,
            },
            "azure": {
                "base_url": "https://my-azure.openai.azure.com/openai/v1",
                "key_env": "AZURE_KEY",
                "auth": "api-key",
                "kind": "azure-openai",
                "headers": {"X-Dept": "ai"},
                "concurrency": 5,
            },
        },
        "models": {
            "claude-opus-4-6": {
                "endpoint": "corp",
                "family": "claude",
                "context": 200000,
                "max_output": 32000,
                "reasoning": True,
                "vision": True,
            },
            "claude-sonnet-4-6": {
                "endpoint": "corp",
                "family": "claude",
                "context": 200000,
                "max_output": 32000,
                "reasoning": True,
                "vision": True,
            },
            "gpt-5.4": {
                "endpoint": "azure",
                "family": "gpt",
                "api": "openai-responses",
                "context": 272000,
                "max_output": 64000,
                "reasoning": True,
                "vision": True,
            },
            "qwen-coder-32b": {
                "endpoint": "local_vllm",
                "family": "open",
                "context": 131072,
                "max_output": 16384,
                "reasoning": False,
                "vision": False,
            },
        },
        "tiers": {
            "tier1": ["claude-opus-4-6", "gpt-5.4"],
            "tier2": ["claude-sonnet-4-6", "qwen-coder-32b"],
            "tier3": ["qwen-coder-32b"],
        },
        "default": "claude-sonnet-4-6",
        "policy": {"telemetry": False, "auto_update": False, "external_web": True, "approvals": "off"},
    }


def test_multi_endpoint_catalog_normalization():
    cat = lm.normalize_catalog(multi_endpoint_raw())
    assert cat["endpoints"]["corp"]["auth"] == "bearer"
    assert cat["endpoints"]["corp"]["api_base"] == "/v1"
    assert cat["endpoints"]["corp"]["api"] == "openai-completions"
    assert cat["endpoints"]["local_vllm"]["auth"] == "none"
    assert cat["endpoints"]["local_vllm"]["key_env"] is None
    assert cat["endpoints"]["local_vllm"]["concurrency"] == 2
    assert cat["endpoints"]["azure"]["root"] == "https://my-azure.openai.azure.com/openai"
    assert cat["endpoints"]["azure"]["api_base"] == "/v1"
    assert lm.endpoint_api_url(cat["endpoints"]["azure"]) == "https://my-azure.openai.azure.com/openai/v1"
    assert cat["endpoints"]["azure"]["headers"] == {"X-Dept": "ai"}
    assert cat["models"]["gpt-5.4"]["api"] == "openai-responses"


def test_prefix_and_api_base_url_handling():
    raw = raw_example()
    # 1. prefix + /v1
    raw["endpoints"]["corp"]["base_url"] = "https://host.example.com/llm/v1"
    ep1 = lm.normalize_catalog(raw)["endpoints"]["corp"]
    assert ep1["root"] == "https://host.example.com/llm" and ep1["api_base"] == "/v1"
    assert lm.endpoint_api_url(ep1) == "https://host.example.com/llm/v1"

    # 2. prefix without /v1
    raw["endpoints"]["corp"]["base_url"] = "https://host.example.com/llm"
    ep2 = lm.normalize_catalog(raw)["endpoints"]["corp"]
    assert ep2["root"] == "https://host.example.com/llm" and ep2["api_base"] == "/v1"
    assert lm.endpoint_api_url(ep2) == "https://host.example.com/llm/v1"

    # 3. bare host
    raw["endpoints"]["corp"]["base_url"] = "https://host.example.com"
    ep3 = lm.normalize_catalog(raw)["endpoints"]["corp"]
    assert ep3["root"] == "https://host.example.com" and ep3["api_base"] == "/v1"

    # 4. explicit api_base wins
    raw["endpoints"]["corp"]["base_url"] = "https://host.example.com"
    raw["endpoints"]["corp"]["api_base"] = "/openai/v1"
    ep4 = lm.normalize_catalog(raw)["endpoints"]["corp"]
    assert ep4["root"] == "https://host.example.com" and ep4["api_base"] == "/openai/v1"
    assert lm.endpoint_api_url(ep4) == "https://host.example.com/openai/v1"

    # 5. detect_server on a prefixed URL (with or without /v1)
    routes = {
        "/llm/v1/models": (200, {"data": [{"id": "m-pref"}]}, {"x-litellm-version": "1.50"}),
        "/llm/model_group/info": (200, {"data": [{"model_group": "m-pref"}]}, {}),
    }
    with fake_server(routes, need=("Authorization", "Bearer sk-p")) as (url, _):
        det_a = lm.detect_server(url + "/llm/v1", "sk-p")
        det_b = lm.detect_server(url + "/llm", "sk-p")
    assert det_a["root"] == url + "/llm" and det_a["api_base"] == "/v1"
    assert det_a["kind"] == "litellm" and det_a["model_ids"] == ["m-pref"]
    assert det_b["root"] == url + "/llm" and det_b["api_base"] == "/v1"
    assert det_b["kind"] == "litellm" and det_b["model_ids"] == ["m-pref"]


def test_api_base_empty_string_is_preserved():
    raw = raw_example()
    raw["endpoints"]["corp"]["api_base"] = ""
    cat = lm.normalize_catalog(raw)
    assert cat["endpoints"]["corp"]["api_base"] == ""
    assert lm.endpoint_api_url(cat["endpoints"]["corp"]) == "https://llm-gateway.example.com"


def test_endpoint_field_validation_errors():
    raw = multi_endpoint_raw()
    raw["endpoints"]["corp"]["auth"] = "magic"
    raw["endpoints"]["corp"]["api"] = "grpc"
    raw["endpoints"]["corp"]["api_base"] = "v1"
    raw["endpoints"]["local_vllm"]["concurrency"] = 0
    raw["endpoints"]["azure"]["key_env"] = None
    raw["models"]["qwen-coder-32b"]["endpoint"] = None
    raw["models"]["gpt-5.4"]["api"] = "soap"
    err = problems(raw)
    assert "endpoints.corp.auth" in err
    assert "endpoints.corp.api:" in err
    assert "endpoints.corp.api_base" in err
    assert "endpoints.local_vllm.concurrency" in err
    assert "endpoints.azure.key_env" in err
    assert "models.qwen-coder-32b.endpoint" in err and "corp, local_vllm, azure" in err
    assert "models.gpt-5.4.api" in err


def test_opencode_multi_endpoint_rendering():
    cat = lm.normalize_catalog(multi_endpoint_raw())
    cfg = lm.render_opencode(cat)
    p_corp = cfg["providers"]["corp"]
    assert p_corp["package"] == "@opencode/ai/providers/openai-compatible"
    assert p_corp["settings"] == {"baseURL": "https://gw.example.com/v1", "apiKey": "{env:CORP_KEY}"}

    p_vllm = cfg["providers"]["local_vllm"]
    assert p_vllm["settings"] == {"baseURL": "http://127.0.0.1:8000/v1"}
    assert "apiKey" not in p_vllm["settings"]
    assert p_vllm["compatibility"] == {"developerRole": False}

    p_azure = cfg["providers"]["azure"]
    assert p_azure["settings"] == {"baseURL": "https://my-azure.openai.azure.com/openai/v1"}
    assert "apiKey" not in p_azure["settings"]
    assert p_azure["headers"] == {"X-Dept": "ai", "api-key": "{env:AZURE_KEY}"}
    assert p_azure["models"]["gpt-5.4"]["package"] == "@opencode/ai/providers/openai-compatible/responses"
    assert p_azure["models"]["gpt-5.4"]["settings"] == {"apiKey": "unused"}

    assert cfg["agents"]["title"]["model"] == "local_vllm/qwen-coder-32b"
    assert len(cfg["experimental"]["policies"]) == 4

    _, notes = lm.plan_files(cat)
    assert any("azure/gpt-5.4" in n and "Authorization: Bearer unused" in n for n in notes)


def test_opencode_anthropic_and_responses_auth_variants():
    raw = raw_example()
    raw["endpoints"]["corp"]["api"] = "anthropic-messages"
    raw["endpoints"]["corp"]["auth"] = "x-api-key"
    cfg = lm.render_opencode(lm.normalize_catalog(raw))
    assert cfg["providers"]["corp"]["package"] == "@opencode/ai/providers/anthropic-compatible"
    assert cfg["providers"]["corp"]["settings"]["apiKey"] == "{env:COMPANY_LLM_KEY}"

    raw2 = raw_example()
    raw2["endpoints"]["corp"]["api"] = "openai-responses"
    raw2["endpoints"]["corp"]["auth"] = "none"
    del raw2["endpoints"]["corp"]["key_env"]
    cat2 = lm.normalize_catalog(raw2)
    cfg2 = lm.render_opencode(cat2)
    assert cfg2["providers"]["corp"]["settings"]["apiKey"] == "unused"
    _, notes2 = lm.plan_files(cat2)
    assert any("corp" in n and "Authorization: Bearer unused" in n for n in notes2)


def test_omo_multi_endpoint_rendering():
    cat = lm.normalize_catalog(multi_endpoint_raw())
    omo_cfg = lm.render_omo_config(cat)
    assert omo_cfg["task"] == {
        "default_concurrency": 4,
        "provider_concurrency": {"local_vllm": 2, "azure": 5},
    }
    assert omo_cfg["categories"]["ultrabrain"]["models"] == [
        {"model": "corp/claude-opus-4-6", "reasoning": "high"},
        {"model": "azure/gpt-5.4", "reasoning": "high"},
    ]

    providers = lm.render_omo_models(cat)["providers"]
    assert providers["corp"]["baseUrl"] == "https://gw.example.com/v1"
    assert providers["corp"]["apiKey"] == "$CORP_KEY"
    assert providers["local_vllm"]["apiKey"] == "none"
    assert providers["local_vllm"]["compat"] == {"supportsDeveloperRole": False}
    assert "apiKey" not in providers["azure"]
    assert providers["azure"]["baseUrl"] == "https://my-azure.openai.azure.com/openai/v1"
    assert providers["azure"]["headers"] == {"X-Dept": "ai", "api-key": "$AZURE_KEY"}
    gpt_entry = providers["azure"]["models"][0]
    assert gpt_entry["id"] == "gpt-5.4" and gpt_entry["api"] == "openai-responses"


def test_omo_anthropic_messages_base_url_and_bearer_auth_header():
    raw = raw_example()
    raw["endpoints"]["corp"]["api"] = "anthropic-messages"
    raw["endpoints"]["corp"]["auth"] = "bearer"
    p = lm.render_omo_models(lm.normalize_catalog(raw))["providers"]["corp"]
    assert p["baseUrl"] == "https://llm-gateway.example.com"
    assert p["api"] == "anthropic-messages"
    assert p["apiKey"] == "$COMPANY_LLM_KEY"
    assert p["authHeader"] is True


def test_hermes_multi_endpoint_rendering():
    cat = lm.normalize_catalog(multi_endpoint_raw())
    cfg, notes = lm.render_hermes(cat, "C:/skills")
    assert notes == []
    assert cfg["model"] == {"default": "claude-sonnet-4-6", "provider": "custom:corp"}

    p_corp = cfg["providers"]["corp"]
    assert p_corp["base_url"] == "https://gw.example.com/v1"
    assert p_corp["key_env"] == "CORP_KEY"
    assert p_corp["models"]["claude-sonnet-4-6"] == {"context_length": 200000, "supports_vision": True}

    p_vllm = cfg["providers"]["local_vllm"]
    assert p_vllm["api_key"] == "no-key-required" and "key_env" not in p_vllm
    assert p_vllm["models"]["qwen-coder-32b"] == {"context_length": 131072}

    p_azure = cfg["providers"]["azure"]
    assert p_azure["base_url"] == "https://my-azure.openai.azure.com/openai/v1"
    assert p_azure["api_key"] == "no-key-required" and "key_env" not in p_azure
    assert p_azure["extra_headers"] == {"X-Dept": "ai", "api-key": "${AZURE_KEY}"}

    assert cfg["auxiliary"]["vision"] == {"provider": "custom:corp", "model": "claude-sonnet-4-6"}
    for task in lm.HERMES_AUX_TASKS:
        if task != "vision":
            assert cfg["auxiliary"][task] == {"provider": "custom:local_vllm", "model": "qwen-coder-32b"}


def test_claude_code_single_endpoint_resolution_and_notes():
    raw = multi_endpoint_raw()
    raw["endpoints"]["corp2"] = {"base_url": "https://gw2.example.com", "key_env": "CORP2_KEY", "auth": "x-api-key"}
    raw["models"]["claude-haiku-4-5"] = {
        "endpoint": "corp2",
        "family": "claude",
        "context": 200000,
        "max_output": 16000,
        "vision": True,
    }
    raw["tiers"]["tier3"].insert(0, "claude-haiku-4-5")
    cat = lm.normalize_catalog(raw)
    cc = cat["claude_code"]
    assert cc["endpoint"] == "corp"
    assert cc["opus"] == "claude-opus-4-6"
    assert cc["sonnet"] == "claude-sonnet-4-6"
    assert cc["haiku"] is None
    cfg, notes = lm.render_claude_settings(cat, "s.ps1")
    assert cfg["env"]["ANTHROPIC_BASE_URL"] == "https://gw.example.com"
    assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in cfg["env"]
    assert any("claude-haiku-4-5" in n and "corp2" in n for n in notes)

    # Explicit claude_code.endpoint switches to corp2
    raw["claude_code"] = {"endpoint": "corp2"}
    cat2 = lm.normalize_catalog(raw)
    assert cat2["claude_code"]["endpoint"] == "corp2"
    assert cat2["claude_code"]["model"] == "claude-haiku-4-5"
    assert cat2["claude_code"]["key_env"] == "ANTHROPIC_API_KEY"

    # Explicit slot on another endpoint is a catalog error naming both endpoints
    raw["claude_code"] = {"endpoint": "corp", "haiku": "claude-haiku-4-5"}
    err = problems(raw)
    assert "claude_code.haiku" in err and "corp2" in err and "corp" in err


def test_claude_code_keyless_and_api_key_auth():
    raw = multi_endpoint_raw()
    raw["models"]["claude-local"] = {
        "endpoint": "local_vllm",
        "family": "claude",
        "context": 200000,
    }
    raw["claude_code"] = {"endpoint": "local_vllm"}
    cat = lm.normalize_catalog(raw)
    cfg, _ = lm.render_claude_settings(cat, "s.ps1")
    assert cfg["env"]["ANTHROPIC_AUTH_TOKEN"] == "no-key-required"

    # api-key endpoint is rejected for Claude Code unless tools.claude_code is false
    raw["models"]["claude-azure"] = {"endpoint": "azure", "family": "claude", "context": 200000}
    raw["claude_code"] = {"endpoint": "azure"}
    err = problems(raw)
    assert "claude_code" in err and "api-key" in err and "tools.claude_code: false" in err
    raw["tools"] = {"claude_code": False}
    lm.normalize_catalog(raw)


def test_preflight_direct_check_and_diagnosis_on_mismatch(monkeypatch):
    monkeypatch.setenv("AZURE_KEY", "az-secret")
    monkeypatch.setenv("CORP_KEY", "corp-secret")
    raw = multi_endpoint_raw()
    routes_vllm = {"/v1/models": (200, {"data": [{"id": "qwen-coder-32b"}]}, {}),
                   "/version": (200, {"version": "0.6.3"}, {})}
    routes_azure = {"/openai/v1/models": (200, {"data": [{"id": "gpt-5.4"}]}, {"apim-request-id": "1"})}
    routes_corp = {"/v1/models": (200, {"data": [{"id": "claude-opus-4-6"}, {"id": "claude-sonnet-4-6"}]}, {})}
    with fake_server(routes_corp, need=("Authorization", "Bearer corp-secret")) as (u_corp, _), \
         fake_server(routes_vllm) as (u_vllm, _), \
         fake_server(routes_azure, need=("api-key", "az-secret")) as (u_azure, _):
        raw["endpoints"]["corp"]["base_url"] = u_corp
        raw["endpoints"]["local_vllm"]["base_url"] = u_vllm
        raw["endpoints"]["azure"]["base_url"] = u_azure + "/openai/v1"
        cat = lm.normalize_catalog(raw)
        _, probs = lm.preflight(cat)
        assert probs == []

        # Mismatch: catalog declares default bearer + /v1 for azure, server wants api-key + /openai/v1
        raw["endpoints"]["azure"] = {"base_url": u_azure, "key_env": "AZURE_KEY"}
        cat_bad = lm.normalize_catalog(raw)
        _, probs_bad = lm.preflight(cat_bad)
        assert len(probs_bad) == 1
        assert "auth: api-key" in probs_bad[0] and "api_base: /openai/v1" in probs_bad[0]


def test_preflight_same_settings_failure_reports_headers_not_redundant_set(monkeypatch):
    monkeypatch.setenv("CORP_KEY", "corp-secret")
    raw = raw_example()
    raw["endpoints"]["corp"] = {
        "base_url": "https://gw.example.com",
        "key_env": "CORP_KEY",
        "headers": {"X-Bad-Header": "reject-me"},
    }
    calls = []

    def fake_http_call(url, key, body=None, headers=None, timeout=30, auth="bearer"):
        calls.append((url, headers))
        if headers and "X-Bad-Header" in headers:
            return 403, "forbidden header", {}
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": i} for i in EXAMPLE_IDS]}, {}
        return 404, "not found", {}

    monkeypatch.setattr(lm, "http_call", fake_http_call)
    cat = lm.normalize_catalog(raw)
    _, probs = lm.preflight(cat)
    assert len(probs) == 1
    assert "HTTP 403" in probs[0]
    assert "detection with the same settings" in probs[0]
    assert "endpoints.corp.headers" in probs[0]
    assert "set:" not in probs[0]


def test_discover_metadata_extraction_and_merged_models(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("K1", "k1")
    routes1 = {
        "/v1/models": (200, {"data": [
            {"id": "vllm-m", "max_model_len": 65536},
            {"id": "or-m", "context_length": 131072,
             "top_provider": {"max_completion_tokens": 16384},
             "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "shared-m", "max_input_tokens": 200000, "max_tokens": 8192},
            {"id": "llama-m", "meta": {"n_ctx": 32768}},
        ]}, {}),
        "/v1/chat/completions": (200, {"ok": True}, {}),
    }
    routes2 = {
        "/v1/models": (200, {"data": [{"id": "ollama-m"}, {"id": "shared-m"}]}, {}),
        "/api/tags": (200, {"models": [{"name": "ollama-m"}]}, {}),
        "/api/show": (200, {"parameters": "num_ctx 131072\n", "capabilities": ["completion", "vision"]}, {}),
        "/v1/responses": (200, {"ok": True}, {}),
    }
    with fake_server(routes1, need=("Authorization", "Bearer k1")) as (u1, _), \
         fake_server(routes2) as (u2, _):
        raw = {
            "endpoints": {
                "ep1": {"base_url": u1, "key_env": "K1"},
                "ep2": {"base_url": u2, "auth": "none"},
            }
        }
        lm.cmd_discover(raw, tmp_path / "discovered.json", None)
    out = capsys.readouterr().out
    assert "warning: model 'shared-m' is served by both ep1 and ep2; keeping ep1" in out
    assert out.count("\nmodels:\n") + (1 if out.startswith("models:\n") else 0) == 1
    assert "api: openai-responses" in out
    discovered = json.loads((tmp_path / "discovered.json").read_text(encoding="utf-8"))
    m1 = discovered["ep1"]["models"]
    assert m1["vllm-m"]["context"] == 65536
    assert m1["or-m"]["context"] == 131072 and m1["or-m"]["max_output"] == 16384 and m1["or-m"]["vision"] is True
    assert m1["shared-m"]["context"] == 200000 and m1["shared-m"]["max_output"] == 8192
    assert m1["llama-m"]["context"] == 32768
    m2 = discovered["ep2"]["models"]
    assert m2["ollama-m"]["context"] == 131072 and m2["ollama-m"]["vision"] is True


def test_key_instructions_and_verify_multi_endpoint(monkeypatch, capsys):
    cat = lm.normalize_catalog(multi_endpoint_raw())
    lines = "\n".join(lm.key_instructions(cat))
    assert "CORP_KEY" in lines and "corp" in lines
    assert "AZURE_KEY" in lines and "azure" in lines
    assert "local_vllm" not in lines
    assert "ANTHROPIC_AUTH_TOKEN" in lines and "corp" in lines

    monkeypatch.setenv("CORP_KEY", "k")
    monkeypatch.delenv("AZURE_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    cat["tools"] = {"opencode": False, "omo": False, "hermes": False, "claude_code": True}
    monkeypatch.setattr(lm, "run", lambda *a, **k: (0, "claude 2.1"))
    monkeypatch.setattr(lm, "detect_server", lambda url, key: {
        "root": url, "kind": "litellm", "auth": "bearer", "api_base": "/v1",
        "evidence": [], "warnings": [], "renderable": True, "model_ids": ["claude-opus-4-6", "claude-sonnet-4-6"],
    })
    monkeypatch.setattr(lm, "http_call", lambda *a, **k: (200, {"data": [{"id": "claude-opus-4-6"}, {"id": "claude-sonnet-4-6"}, {"id": "qwen-coder-32b"}]}, {}))
    failures = lm.cmd_verify(cat)
    out = capsys.readouterr().out
    assert failures == 2
    assert out.count("AZURE_KEY") == 1
    assert "[FAIL] azure gateway" in out and "environment variable AZURE_KEY is not set" in out
    assert "[FAIL] environment variable ANTHROPIC_AUTH_TOKEN is set (Claude Code -> corp)" in out


def test_hermes_fallback_providers_from_catalog_tiers(cat):
    # Single-endpoint catalog: default is claude-sonnet-example in tier2 [claude-sonnet-example, qwen-coder-example]
    cfg, _ = lm.render_hermes(cat, "C:/x/skills")
    assert cfg["fallback_providers"] == [
        {"provider": "custom:corp", "model": "qwen-coder-example"},
    ]

    # Multi-endpoint catalog: default is claude-sonnet-4-6 (corp) in tier2 [claude-sonnet-4-6, qwen-coder-32b (local_vllm)]
    m_cat = lm.normalize_catalog(multi_endpoint_raw())
    m_cfg, _ = lm.render_hermes(m_cat, "C:/x/skills")
    assert m_cfg["fallback_providers"] == [
        {"provider": "custom:local_vllm", "model": "qwen-coder-32b"},
    ]

    # Small-context peer in the same tier is skipped
    raw_small = raw_example()
    raw_small["models"]["qwen-coder-example"]["context"] = 32000
    cfg_small, _ = lm.render_hermes(lm.normalize_catalog(raw_small), "C:/x/skills")
    assert cfg_small["fallback_providers"] == []

    # Default model not in any tier -> empty fallback_providers
    raw_notier = raw_example()
    raw_notier["tiers"]["tier2"] = ["qwen-coder-example"]
    raw_notier["default"] = "claude-sonnet-example"
    cfg_notier, _ = lm.render_hermes(lm.normalize_catalog(raw_notier), "C:/x/skills")
    assert cfg_notier["fallback_providers"] == []


def test_hermes_merge_prunes_orphan_providers_and_legacy_fallbacks(cat, tmp_path, capsys):
    home = tmp_path / "home"
    dirs = lm.tool_dirs(home)
    dirs["hermes"].mkdir(parents=True)
    existing = {
        "model": {"default": "old-model", "provider": "openrouter"},
        "providers": {
            "corp": {"base_url": "https://old.example.com/v1", "key_env": "OLD_KEY"},
            "stale_ep": {"base_url": "https://stale.example.com/v1", "key_env": "STALE_KEY"},
        },
        "custom_providers": [
            {"name": "legacy_gw", "base_url": "https://legacy.example.com/v1", "api_key": "x"},
        ],
        "fallback_providers": [
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        ],
        "fallback_model": {
            "provider": "nous",
            "model": "deepseek-r1",
        },
        "display": {"theme": "dark"},
        "agent": {"max_turns": 42, "disabled_toolsets": ["custom_tool"]},
    }
    (dirs["hermes"] / "config.yaml").write_text(
        yaml.safe_dump(existing, sort_keys=False), encoding="utf-8"
    )

    ops, notes = lm.plan_files(cat, home)
    hermes_op = next(op for op in ops if op.rel == "hermes/config.yaml")
    merged = yaml.safe_load(hermes_op.final().decode("utf-8"))

    assert list(merged["providers"].keys()) == ["corp"]
    assert "stale_ep" not in merged["providers"]
    assert "custom_providers" not in merged
    assert "fallback_model" not in merged
    assert merged["fallback_providers"] == [
        {"provider": "custom:corp", "model": "qwen-coder-example"},
    ]
    assert merged["display"] == {"theme": "dark"}
    assert merged["agent"]["max_turns"] == 42

    notes_str = "\n".join(notes)
    assert "stale_ep" in notes_str
    assert "legacy_gw" in notes_str
    assert "openrouter/anthropic/claude-sonnet-4" in notes_str
    assert "nous/deepseek-r1" in notes_str

    # cmd_diff also prints the removal notes
    lm.cmd_diff(cat, home)
    diff_out = capsys.readouterr().out
    assert "note:" in diff_out and "stale_ep" in diff_out and "openrouter/anthropic/claude-sonnet-4" in diff_out


def test_hermes_extra_override_survives_replace(tmp_path):
    raw = raw_example()
    raw["extra"] = {
        "hermes": {
            "providers": {
                "manual_ep": {"base_url": "https://manual.example.com/v1", "api_key": "no-key-required"},
            },
            "fallback_providers": [
                {"provider": "custom:manual_ep", "model": "manual-model"},
            ],
        }
    }
    cat = lm.normalize_catalog(raw)
    home = tmp_path / "home"
    dirs = lm.tool_dirs(home)
    dirs["hermes"].mkdir(parents=True)
    (dirs["hermes"] / "config.yaml").write_text(
        "providers:\n  orphan: {base_url: https://orphan.example.com/v1}\n"
        "fallback_providers:\n  - {provider: openrouter, model: x}\n",
        encoding="utf-8",
    )
    ops, notes = lm.plan_files(cat, home)
    hermes_op = next(op for op in ops if op.rel == "hermes/config.yaml")
    merged = yaml.safe_load(hermes_op.final().decode("utf-8"))

    assert set(merged["providers"].keys()) == {"corp", "manual_ep"}
    assert "orphan" not in merged["providers"]
    assert merged["fallback_providers"] == [
        {"provider": "custom:manual_ep", "model": "manual-model"},
    ]
    assert any("orphan" in n for n in notes)
    assert not any("manual_ep" in n for n in notes)
