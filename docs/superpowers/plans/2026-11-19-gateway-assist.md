# Gateway Assist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When rule-based `detect` cannot settle how to connect to an endpoint, ask an LLM (a configured `assist:` API, or the installed Claude Code) for connection-format candidates and keep only those a real request verifies.

**Architecture:** Everything stays in the single-file CLI `llm_manager.py` (existing pattern). `detect_server` gains a probe log; new pure functions build a redacted evidence bundle, parse/validate candidates, verify candidates with real HTTP calls, and two judge adapters (OpenAI-compatible API, `claude -p`) turn evidence into candidates. `detect`/`discover` get an opt-in `--assist` flag.

**Tech Stack:** Python ≥3.10, stdlib (`urllib`, `subprocess`, `json`) + PyYAML; pytest with the existing `fake_server` helper.

**Spec:** `docs/superpowers/specs/2026-11-19-gateway-assist-design.md`

## Global Constraints

- No new dependencies; stdlib + `pyyaml` only. Tests run with `uv run --with pytest --with pyyaml pytest -q` from the repo root.
- A key value is never printed, written to a file, or sent to a judge. Redact to `<key>`.
- The LLM is called only when the user passes `--assist`; `build`/`diff`/`apply`/preflight never call it.
- LLM answers are proposals only; a candidate is `verified` only after a real request to the endpoint succeeds.
- At most 3 candidates; error-body excerpts at most 500 characters.
- `--assist` values: `auto` (default when the flag is given bare), `api`, `claude`.
- `auto` uses `assist:` when the catalog has it, else Claude Code — but Claude Code only when it points at a gateway (`ANTHROPIC_BASE_URL` set). `--assist claude` uses it regardless, printing a warning when it is not on a gateway.
- User-facing docs (README) are Korean; code, comments, CLI output are English (existing pattern).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Judge returns prose, fenced ```json, or broken JSON → unusable answer is reported, no crash, other endpoints still processed. (Task 2 `parse_candidates`, Task 3 judges)
- Candidate `api_base` is an absolute URL or starts with `//` → rejected, and redirects to another host are not followed, so the key is never sent to another host. (Task 2)
- `extra_headers` contains `Authorization`/`api-key`/`x-api-key`/`Cookie` or names with spaces/CR/LF → dropped. (Task 2)
- Gateway echoes the key inside an error body or header → redacted before it reaches the judge. (Task 1)
- Judge unreachable, times out, or `claude` not installed → one clear line, detection result still printed. (Task 3, Task 4)

---

### Task 1: Probe log, OpenAPI summary and redacted evidence

**Files:**
- Modify: `llm_manager.py` (`detect_server`, new helpers next to it)
- Test: `tests/test_llm_manager.py` (new section `# ---- assist: evidence`)

**Interfaces:**
- Produces: `detect_server(...)` result gains `"probes": list[dict]` (each `{"method","path","auth","status","headers","body"}`, headers lower-cased, body = first 500 chars of text/JSON-dumped body) and `"openapi": dict | None` (`{"paths": [str, ...max 60], "security_schemes": dict}`); `redact(text: str, key: str | None) -> str`; `build_evidence(det: dict, key: str | None) -> dict` returning `{"root", "kind", "auth", "api_base", "warnings", "probes", "openapi"}` with every string redacted and headers named `set-cookie`, `cookie`, `authorization`, `api-key`, `x-api-key`, `proxy-authorization` removed.

- [ ] **Step 1: Write failing tests**
  - `test_detect_records_each_probe`: Azure-style fake (`need=("api-key","k")`, `/openai/v1/models` 200) → `det["probes"]` contains an entry with `path == "/openai/v1/models"`, `auth == "api-key"`, `status == 200`, and at least one entry with `status == 401`.
  - `test_detect_summarizes_openapi`: fake serving `/openapi.json` = `{"paths": {"/v2/chat": {}, "/v2/models": {}}, "components": {"securitySchemes": {"tenant": {"type": "apiKey", "in": "header", "name": "X-Tenant-Id"}}}}` → `det["openapi"] == {"paths": ["/v2/chat", "/v2/models"], "security_schemes": {"tenant": {...same...}}}`.
  - `test_evidence_never_contains_the_key`: fake whose 401 body is `{"error": "bad key sk-leak-123"}` and which sets header `X-Debug: sk-leak-123` and `Set-Cookie: s=1` → `"sk-leak-123" not in json.dumps(lm.build_evidence(det, "sk-leak-123"))`, `"<key>"` appears, and no probe header named `set-cookie`.
  - `test_evidence_bodies_are_capped`: 401 body of 2000 chars → every `probe["body"]` length ≤ 500.
- [ ] **Step 2: Run, expect FAIL** (`KeyError: 'probes'` / missing `build_evidence`).
- [ ] **Step 3: Implement** — record every `http_call` made inside `detect_server` into `res["probes"]` (one small local helper); fetch `root + "/openapi.json"` with the probe auth during fingerprinting; `redact` replaces the key (when ≥4 chars) case-sensitively.
- [ ] **Step 4: Run full suite, expect PASS** (existing 64 + new).
- [ ] **Step 5: Commit** `Record detection probes and build redacted evidence for assist`.

### Task 2: Candidate parsing and verification

**Files:**
- Modify: `llm_manager.py` (`http_call` gains `query`, new functions after `build_evidence`)
- Test: `tests/test_llm_manager.py` (section `# ---- assist: candidates`)

**Interfaces:**
- Consumes: `auth_headers(auth, key)`, `http_call(...)`.
- Produces:
  - `http_call(url, key, body=None, headers=None, timeout=30, auth="bearer", query: dict | None = None)` — query is url-encoded and appended.
  - `ASSIST_APIS = ("openai-chat", "openai-responses", "anthropic-messages", "azure-openai")`
  - `ASSIST_SCHEMA: dict` — JSON Schema for `{"candidates": [{"api","auth","api_base","query","extra_headers","reason"}]}` (enums from `ASSIST_APIS` and `AUTH_STYLES + ("none",)`), used by Task 3.
  - `parse_candidates(payload: object) -> tuple[list[dict], list[str]]` — accepts a dict or a string (extracts the first `{...}` JSON object, also inside ``` fences); returns (valid candidates, max 3, normalized with defaults `query={}`, `extra_headers=[]`, `reason=""`), problems).
  - `header_env_name(header: str) -> str` — `"X-Tenant-Id" -> "LLM_HEADER_X_TENANT_ID"` (fixed `LLM_HEADER_` prefix + upper-case, non-alphanumerics → `_`). The prefix keeps a judge-chosen header name from picking an unrelated variable such as `DATABASE_URL`; the secret-name denylist stays as a second guard.
  - `verify_candidate(root: str, key: str | None, cand: dict, timeout: int = 10) -> dict` — returns `{**cand, "status": "verified"|"failed"|"needs value", "detail": str}`. `needs value` when an extra header's env var is unset or holds anything but printable ASCII (no request sent). Otherwise GET `root + api_base + "/models"` with `auth_headers(cand["auth"], key)`, extra header values from env, and `query`; `verified` iff 200 with a `data` list.
- [ ] **Step 1: Write failing tests**
  - `test_parse_candidates_from_fenced_text`: "Here:\n```json\n{\"candidates\":[{\"api\":\"azure-openai\",\"auth\":\"api-key\",\"api_base\":\"/openai/v1\",\"reason\":\"apim\"}]}\n```" → one candidate, `query == {}`, `extra_headers == []`, no problems.
  - `test_parse_candidates_rejects_bad_answers`: `"no idea"` → `([], [problem])`; unknown `api` value dropped with a problem; 5 valid candidates → 3 kept.
  - `test_parse_candidates_rejects_foreign_hosts`: `api_base` `"https://evil.example/v1"` and `"//evil/v1"` → dropped.
  - `test_parse_candidates_drops_credential_and_malformed_headers`: `extra_headers ["Authorization", "api-key", "Cookie", "X Bad", "X-Ok\r\nY: z", "X-Tenant-Id"]` → `["X-Tenant-Id"]`.
  - `test_verify_candidate_verified_with_query`: fake requiring `api-key` and path `/openai/models?api-version=2024-10-21` → `status == "verified"`.
  - `test_verify_candidate_failed`: wrong auth → `"failed"` with the HTTP status in `detail`.
  - `test_verify_candidate_needs_value_then_uses_env(monkeypatch)`: candidate with `extra_headers ["X-Tenant-Id"]`; env unset (an unprefixed `X_TENANT_ID` is ignored) → `"needs value"` and `"LLM_HEADER_X_TENANT_ID"` in `detail`; `setenv("LLM_HEADER_X_TENANT_ID","t1")` with fake requiring that header → `"verified"`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Header-name rule: `^[A-Za-z0-9][A-Za-z0-9-]{0,63}$` and not in the credential set (case-insensitive). `api_base` rule: must start with `/` and not `//`, no `://`, printable ASCII only, no `?`/`#`/`\`. `http_call` follows redirects only within the same scheme/host/port (plus http→https on the same host), so the key never follows a redirect to another host; a request that cannot be sent (e.g. a non-latin-1 header) returns `(None, reason, {})` instead of raising.
- [ ] **Step 4: Run full suite, expect PASS.** (The fake server routes on `self.path`, which includes the query string.)
- [ ] **Step 5: Commit** `Parse and verify assist connection candidates`.

### Task 3: Judges — assist API and Claude Code

**Files:**
- Modify: `llm_manager.py` (`run` gains `input`, new judge functions)
- Test: `tests/test_llm_manager.py` (section `# ---- assist: judges`)

**Interfaces:**
- Consumes: `build_evidence` (Task 1), `ASSIST_SCHEMA`, `parse_candidates` (Task 2), `http_call`.
- Produces:
  - `run(cmd, timeout=60, input: str | None = None)` — when `input` is given it is passed on stdin instead of `DEVNULL`.
  - `ASSIST_PROMPT: str` — instructions: you are given HTTP probe evidence for an LLM gateway; propose at most 3 connection formats as JSON matching the schema; never invent header values; prefer formats the evidence supports. The evidence JSON is appended.
  - `assist_config(raw: dict) -> tuple[dict | None, list[str]]` — reads `raw["assist"]`; requires `base_url` (http/https), `key_env`, `model`; returns `({"root", "key_env", "model"}, problems)` or `(None, [])` when absent.
  - `ask_api(cfg: dict, evidence: dict, timeout: int = 120) -> tuple[object | None, str]` — POST `root + "/v1/chat/completions"` with Bearer key from `cfg["key_env"]`, body `{"model", "messages": [{"role":"system","content":ASSIST_PROMPT},{"role":"user","content":json evidence}], "temperature": 0}`; returns (message content, "") or (None, reason).
  - `claude_gateway_url(claude_dir: Path) -> str | None` — `ANTHROPIC_BASE_URL` from the process env, else from `claude_dir / "settings.json"` `env`.
  - `ask_claude(evidence: dict, timeout: int = 300) -> tuple[object | None, str]` — `run(["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--json-schema", json.dumps(ASSIST_SCHEMA)], timeout, input=ASSIST_PROMPT + evidence json)`; from the CLI JSON take `structured_output` if present, else `result`; non-zero exit / not installed → (None, reason).
  - `choose_judge(mode: str, raw: dict, claude_dir: Path) -> tuple[str | None, object, list[str]]` — returns (judge name `"api"`/`"claude"`/None, callable `evidence -> (payload, reason)`, messages to print). Rules exactly as Global Constraints.
- [ ] **Step 1: Write failing tests**
  - `test_ask_api_returns_model_content(monkeypatch)`: fake assist API at `/v1/chat/completions` requiring `Bearer ak` returning `{"choices":[{"message":{"content":"{\"candidates\":[]}"}}]}` → payload is that string; the received request body's user message contains `"<key>"`-redacted evidence and no `"sk-target"` (pass evidence built from a det whose key was `sk-target`).
  - `test_ask_api_unreachable`: base_url `http://127.0.0.1:9` → `(None, reason)` with non-empty reason.
  - `test_ask_claude_uses_structured_output(monkeypatch)`: monkeypatch `lm.run` capturing `(cmd, input)` and returning `(0, json.dumps({"structured_output": {"candidates": []}}))` → payload `{"candidates": []}`; `cmd` includes `"--tools", ""` and `"--json-schema"`; `input` starts with `ASSIST_PROMPT`.
  - `test_ask_claude_not_installed(monkeypatch)`: `run` returns `(None, "claude not found on PATH")` → `(None, ...)`.
  - `test_choose_judge_auto_prefers_assist_api(tmp_path)`: raw with valid `assist:` → name `"api"`.
  - `test_choose_judge_auto_skips_claude_without_gateway(tmp_path, monkeypatch)`: no `assist:`, no `ANTHROPIC_BASE_URL` env, no settings → name `None` and a message mentioning `--assist claude`.
  - `test_choose_judge_auto_uses_claude_on_gateway(tmp_path, monkeypatch)`: `tmp_path/settings.json` with `{"env": {"ANTHROPIC_BASE_URL": "https://gw"}}` → `"claude"`.
  - `test_choose_judge_explicit_claude_warns_off_gateway(tmp_path, monkeypatch)`: mode `"claude"`, no gateway → `"claude"` and a warning message containing `Anthropic`.
  - `test_assist_config_problems`: `assist: {base_url: "ftp://x"}` → problems name `base_url`, `key_env`, `model`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, expect PASS.**
- [ ] **Step 5: Commit** `Add assist judges: configured API and Claude Code`.

### Task 4: `--assist` on detect and discover, output, docs

**Files:**
- Modify: `llm_manager.py` (`cmd_detect`, `cmd_discover`, `main`, new `assist_endpoint`, `print_assist`)
- Modify: `README.md` (new subsection under "실행 전 게이트웨이 점검"), `catalog.example.yaml` (commented `assist:` block)
- Test: `tests/test_llm_manager.py` (section `# ---- assist: command`)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `needs_assist(det: dict) -> bool` — True when `det["kind"] in ("unknown",)` or `not det["renderable"]`, False for `unreachable` and cloud-signed kinds (nothing to learn).
  - `assist_endpoint(det: dict, key: str | None, judge) -> dict` — `{"judge_reason": str, "problems": list[str], "candidates": list[dict]}` (candidates already passed through `verify_candidate`).
  - `print_assist(name: str, result: dict) -> None` — one line per candidate: `  assist: [verified] azure-openai, api-key: <key>, /openai/v1?api-version=... - <reason>`; for `verified` also print the suggested endpoint block as YAML (`base_url`, `key_env`, `api`, `auth`, `query`, `headers` as `{Header: ${ENV_NAME}}`).
  - `cmd_detect(targets, assist_mode: str | None = None, raw: dict | None = None, claude_dir: Path | None = None) -> dict` and `cmd_discover(raw, out_file, probe_model, assist_mode=None, claude_dir=None)`; results gain `"assist"` when it ran.
  - CLI: `--assist` with `nargs="?"`, `const="auto"`, `choices=["auto","api","claude"]` on `detect` and `discover`. `claude_dir` = `tool_dirs(None)["claude"]`.
- [ ] **Step 1: Write failing tests**
  - `test_detect_assist_end_to_end(tmp_path, monkeypatch, capsys)`: Azure-style target fake (`api-key`) + fake assist API returning one `azure-openai/api-key//openai/v1` candidate; catalog file with `endpoints.corp` → target, `assist:` → judge; `main(["--catalog", p, "detect", "--assist"])` → output contains `[verified]` and `auth: api-key` in the YAML block; the target key string never appears in output.
  - `test_assist_is_not_called_for_working_endpoint(...)`: Bearer `/v1/models` target; judge fake records calls → zero calls.
  - `test_assist_without_judge_keeps_detection_output(...)`: no `assist:`, no gateway → exit code unchanged from plain detect, output has the detection block and the `--assist claude` hint.
  - `test_detect_without_assist_flag_never_calls_judge(monkeypatch)`: monkeypatch `ask_api`/`ask_claude` to `pytest.fail` → plain `detect` works.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement, plus README (Korean) subsection "LLM 판단 지원 (`--assist`)" covering: when it runs, A/C choice and the `auto` rule, what is sent (no key), `needs value` and the `LLM_HEADER_X_TENANT_ID` env naming, that results are suggestions and catalog is not modified; and the commented `assist:` block in `catalog.example.yaml`.**
- [ ] **Step 4: Run full suite, expect PASS.**
- [ ] **Step 5: Commit** `Add --assist to detect and discover`.
