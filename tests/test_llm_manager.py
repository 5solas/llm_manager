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
