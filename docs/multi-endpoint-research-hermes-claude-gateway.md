# 멀티 엔드포인트 지원 리서치: Hermes Agent · Claude Code · LLM 게이트웨이

> 작성일: 2026-11-18  
> 조사 범위: `Hermes Agent` (`v0.19.0` / 빌드 `2026.9.24`), `Claude Code` (`v2.1.327`), LLM 게이트웨이 및 로컬 추론 서버 (`LiteLLM`, `vLLM`, `Ollama`, `llama.cpp`, `LM Studio`, `Azure OpenAI`, `OpenRouter` 호환 게이트웨이), 그리고 `llm_manager.py` (커밋 `b4db3f6`, `detect_server()` / `preflight()` 포함)

---

## 0. 핵심 요약 (Executive Summary)

1. **Hermes Agent (`config.yaml`)**:
   - 설정 스키마 v12부터 다중 커스텀 엔드포인트의 정식 위치는 최상위 **`providers:` 매핑(dict)** 이다 (`custom_providers:` 리스트는 레거시 마이그레이션 대상).
   - 각 엔드포인트는 `providers.<name>` 아래에 `base_url`(또는 `api`), `key_env`(또는 `api_key`, `key_cmd`), `transport`(또는 `api_mode`: `chat_completions` | `codex_responses` | `anthropic_messages` | `bedrock_converse`), `extra_headers`, `extra_body`, `discover_models`, `context_length`, `models` (`context_length`, `supports_vision`, `supports_reasoning`)를 선언하며, 활성 기본 모델은 `model.provider: "custom:<name>"` + `model.default: "<model_id>"`로 선택한다.
   - **치명적 주의점**: 현재 `render_hermes()`가 생성하는 `auxiliary.<task>.provider: "main"`은 Hermes 내부(`agent/auxiliary_client.py:577-582`)에서 `"custom"`(이름 없는 bare custom)으로 정규화되어 **`hermes doctor` 검증 실패**, **네이티브 비전(image input) 비활성화(`agent/image_routing.py:262-271`)**, 그리고 비전 실패 시 **`openrouter` → `nous` → `deepinfra` 외부 유출(`agent/auxiliary_client.py:5411, 5541-5567`)** 을 유발한다. 멀티 엔드포인트 환경에서는 각 `auxiliary.<task>`에 `provider: "custom:<ep>"` 및 `model: "<model_id>"`를 명시적으로 핀(pin) 고정해야 한다.
   - 동일 모델 ID가 2개 이상의 `providers.<name>.models`에 선언된 경우, `/model <id>` 단독 실행 시 Hermes가 모호성 에러를 내며 거부하므로(`hermes_cli/model_switch.py:1362-1366`), `/model custom:<name>:<id>` 또는 `--provider custom:<name>`을 사용해야 한다.

2. **Claude Code (`~/.claude/settings.json` `env` 블록)**:
   - 단일 프로세스당 **오직 하나의 `ANTHROPIC_BASE_URL`**(또는 단일 클라우드 모드 `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX` / `CLAUDE_CODE_USE_FOUNDRY`)만 수용한다.
   - 따라서 `opus`, `sonnet`, `haiku` 슬롯을 서로 다른 두 게이트웨이 URL로 직접 분산 라우팅하는 기능은 Claude Code 자체에 존재하지 않는다.
   - 현재 `_resolve_claude_code()`(`llm_manager.py:210-238`)는 `tiers`에서 슬롯별로 `first_claude(tier)`를 먼저 뽑은 뒤 엔드포인트가 2개 이상이면 즉시 `CatalogError`로 빌드를 중단한다. 이를 개선하여 **주 엔드포인트(`claude_code.endpoint` 또는 `claude_code.model`의 엔드포인트)를 먼저 결정한 뒤 해당 엔드포인트 소속 모델로만 슬롯을 자동 배정**하고, 타 엔드포인트 전용 모델은 경고 후 제외해야 한다.
   - 인증은 `detect_server()`의 `auth` 결과에 따라 `bearer` → `ANTHROPIC_AUTH_TOKEN`(동시에 `"ANTHROPIC_API_KEY": ""`로 충돌 방지), `x-api-key` → `ANTHROPIC_API_KEY`, `api-key` → `ANTHROPIC_CUSTOM_HEADERS: "api-key: ..."` 또는 `apiKeyHelper` / `headersHelper`로 분기해야 한다.

3. **게이트웨이 및 로컬 서버 (`detect_server()` / `discover_endpoint()`)**:
   - `LiteLLM` 전용 엔드포인트(`/v1/model/info`, `/model_group/info`)는 `vLLM`, `Ollama`, `llama.cpp`, `LM Studio`, `OpenRouter`에는 존재하지 않는다.
   - 서버별 컨텍스트 길이·비전 메타데이터 경로가 다르다: `vLLM`은 `/v1/models`의 `max_model_len`, `Ollama`는 `POST /api/show`의 `parameters.num_ctx` / `model_info.*.context_length` 및 `capabilities` (`"vision"`), `llama.cpp`는 `GET /v1/props`(또는 `/props`)의 `default_generation_settings.n_ctx`, `LM Studio`는 `GET /api/v1/models`의 `loaded_instances[].config.context_length`, `OpenRouter`류는 `/v1/models`의 `context_length` 및 `top_provider.max_completion_tokens`, `Anthropic`은 `/v1/models`의 `max_input_tokens`를 사용한다.
   - 현재 `preflight()`(`llm_manager.py:969-970`)는 `auth in ("bearer", "none") and api_base == "/v1"`만 `renderable = True`로 판정하지만, 4개 에이전트 모두 커스텀 헤더(`headers` / `extra_headers` / `ANTHROPIC_CUSTOM_HEADERS`), 비-`/v1` 베이스 경로(`/openai/v1` 등), keyless 엔드포인트를 설정 파일에서 표현할 수 있다.

---

## 1. `detect_server()` 결과의 4개 에이전트별 설정 표현 매트릭스

커밋 `b4db3f6`에서 추가된 `detect_server(url, key)`(`llm_manager.py:918-971`)는 엔드포인트마다 다음 프로파일을 반환한다:
- `kind`: `"litellm"` | `"vllm"` | `"ollama"` | `"azure-openai"` | `"anthropic"` | `"openai"` | `"bedrock"` | `"vertex"` | `"openai-compatible"`
- `auth`: `"bearer"` | `"api-key"` | `"x-api-key"` | `"none"`
- `api_base`: `"/v1"` | `"/openai/v1"` | `""`
- `models_url`, `model_count`, `renderable`

아래 표는 **비-Bearer 인증 헤더(`api-key`, `x-api-key`)**, **비-`/v1` API 경로(`/openai/v1`, `""`)**, **무인증(`none`, keyless)** 엔드포인트를 4개 에이전트 설정 파일에서 각각 어떻게 렌더링해야 하는지 정리한 매핑이다.

| 감지 항목 (`detect_server`) | `opencode2` (`opencode.json`) | `omo` (`~/.omo/models.json`) | `hermes` (`config.yaml`) | `claude_code` (`~/.claude/settings.json`) |
| :--- | :--- | :--- | :--- | :--- |
| **Base URL (`api_base: "/v1"` \| `"/openai/v1"` \| `""`)** | `provider.<ep>.options.baseURL: "<root><api_base>"` | `providers.<ep>.baseUrl: "<root><api_base>"` | `providers.<ep>.base_url: "<root><api_base>"` (`transport: anthropic_messages`여도 `/v1` 또는 루트 모두 수용; `agent/anthropic_endpoints.py:86-135`) | `env.ANTHROPIC_BASE_URL: "<root>"` (`api_base == "/v1"`이면 `<root>`만 기록; SDK가 `/v1/messages`를 붙임. 단, `api_base == "/openai/v1"` 등 프리픽스가 있으면 `"<root>/openai"`로 `/v1`만 제거) |
| **Bearer 인증 (`auth: "bearer"`)** | `provider.<ep>.options.apiKey: "{env:<KEY_ENV>}"` | `providers.<ep>.apiKey: "<KEY_ENV>"` (환경변수 이름 또는 값) | `providers.<ep>.key_env: "<KEY_ENV>"` | `env.ANTHROPIC_AUTH_TOKEN` 사용 (`key_env`가 다르면 `apiKeyHelper`로 브리지) + `env.ANTHROPIC_API_KEY: ""` |
| **Azure `api-key` 헤더 (`auth: "api-key"`)** | `options.headers: {"api-key": "{env:<KEY_ENV>}"}` | `providers.<ep>.headers: {"api-key": "<KEY_ENV>"}` + `authHeader: false` | `providers.<ep>.extra_headers: {"api-key": "${<KEY_ENV>}"}` + `api_key: "no-key-required"` (`config.py:1469`가 `${VAR}` 확장) | `env.ANTHROPIC_CUSTOM_HEADERS: "api-key: <값>"` 또는 `headersHelper` 스크립트 (Foundry 모드 시 `CLAUDE_CODE_USE_FOUNDRY: "1"`, `ANTHROPIC_FOUNDRY_API_KEY`) |
| **Anthropic `x-api-key` 헤더 (`auth: "x-api-key"`)** | `npm: "@ai-sdk/anthropic"` 사용 시 기본 헤더가 `x-api-key`; OpenAI SDK 사용 시 `options.headers: {"x-api-key": "{env:<KEY_ENV>}"}` | `api: "anthropic-messages"`이면 기본 `x-api-key`; OpenAI 포맷이면 `headers: {"x-api-key": "<KEY_ENV>"}` | `transport: anthropic_messages`이면 `api_key`/`key_env`를 자동으로 `x-api-key`로 전송(`anthropic_adapter.py:415-434`). OpenAI transport면 `extra_headers: {"x-api-key": "${<KEY_ENV>}"}` | `env.ANTHROPIC_API_KEY` 사용 (`key_env`가 다르면 `apiKeyHelper`로 브리지) |
| **Keyless (`auth: "none"`, `key_env` 생략/빈 값)** | `options.apiKey: "no-key-required"` (OpenAI 호환 SDK는 빈 문자열이면 에러를 내므로 더미 토큰 권장) | `providers.<ep>.apiKey: "no-key-required"` | `key_env` 생략 + **`api_key: "no-key-required"` 명시** (`key_env`를 아예 생략하면 호스트명 기반 환경변수 추측이 동작함; 아래 §2.2.4 참조) | `env.ANTHROPIC_AUTH_TOKEN: "no-key-required"` + `env.ANTHROPIC_API_KEY: ""` (Ollama `0.14+` `/v1/messages` 연동 공식 방식) |
| **Wire API 포맷 선택 (`formats`)** | `@ai-sdk/openai-compatible` (chat completions), `@ai-sdk/openai` (responses), `@ai-sdk/anthropic` (messages) | `providers.<ep>.api`: `"openai-completions"` \| `"openai-responses"` \| `"anthropic-messages"` | `providers.<ep>.transport`: `"chat_completions"` \| `"codex_responses"` \| `"anthropic_messages"` \| `"bedrock_converse"` | `"anthropic-messages"` (`POST <base>/v1/messages`) 고정 |

---

## 2. Hermes Agent (`%LOCALAPPDATA%\hermes\config.yaml` / `~/.hermes/config.yaml`)

### 2.1 N개 커스텀 Provider 확정 설정 스키마 및 2-Endpoint 최소 동작 예시

Hermes Agent(`v0.19.0` / config schema `v12`)에서 여러 커스텀 엔드포인트를 등록하는 정식 구조는 최상위 `providers:` 딕셔너리다(`%APPDATA%\uv\tools\hermes-agent\Lib\site-packages\hermes_cli\config_providers.py:114-123, 285-350`).

```yaml
# ~/.hermes/config.yaml 또는 %LOCALAPPDATA%\hermes\config.yaml
model:
  default: glm-5.1
  provider: custom:corp_gw
  base_url: https://llm-gw.internal.example.com/v1

providers:
  corp_gw:
    base_url: https://llm-gw.internal.example.com/v1
    key_env: CORP_LLM_API_KEY
    transport: chat_completions          # chat_completions | codex_responses | anthropic_messages | bedrock_converse
    default_model: glm-5.1
    context_length: 200000
    discover_models: false               # true(기본값)면 /models 라이브 조회 결과를 피커에 병합
    extra_headers:
      X-Department: engineering
    models:
      glm-5.1:
        context_length: 200000
        supports_vision: false
        supports_reasoning: true
      claude-sonnet-4-6:
        context_length: 200000
        supports_vision: true
        supports_reasoning: true

  local_vllm:
    base_url: http://127.0.0.1:8000/v1
    api_key: no-key-required             # keyless 엔드포인트: key_env 생략 + 더미 값으로 호스트 키 추측 차단
    transport: chat_completions
    default_model: qwen2.5-coder-32b
    context_length: 131072
    discover_models: false
    models:
      qwen2.5-coder-32b:
        context_length: 131072
        supports_vision: false
      qwen2.5-vl-72b:
        context_length: 131072
        supports_vision: true

fallback_providers:
  - provider: custom:local_vllm
    model: qwen2.5-coder-32b

auxiliary:
  vision:
    provider: custom:local_vllm
    model: qwen2.5-vl-72b
  compression:
    provider: custom:local_vllm
    model: qwen2.5-coder-32b
  title_generation:
    provider: custom:local_vllm
    model: qwen2.5-coder-32b
  approval:
    provider: custom:local_vllm
    model: qwen2.5-coder-32b
  mcp:
    provider: custom:local_vllm
    model: qwen2.5-coder-32b
  triage_specifier:
    provider: custom:local_vllm
    model: qwen2.5-coder-32b
  tts_audio_tags:
    provider: custom:local_vllm
    model: qwen2.5-coder-32b
```

---

### 2.2 항목별 상세 조사 결과 및 근거

#### 2.2.1 `providers:` 맵 vs `custom_providers:` 리스트, 세부 필드, `model.provider: custom:<name>` 선택 메커니즘

1. **`providers:` dict vs `custom_providers:` list**:
   - 설정 스키마 v12(`hermes_cli/config_migrations.py`, `hermes_cli/doctor_config.py:488-511`)부터 **`providers:` 딕셔너리가 정규(canonical) 포맷**이다.
   - `custom_providers:` 리스트(`[{name, base_url, api_key_env, model, api_mode}, ...]`)는 하위 호환용으로만 읽히며, `load_custom_providers_from_config()`(`hermes_cli/config_providers.py:258-350`)가 내부적으로 `providers:`와 `custom_providers:`를 병합하되 **동일 정규화 이름 충돌 시 `providers:` 항목이 우선**한다(`config_providers.py:338-340`).
   - `hermes doctor`는 `custom_providers:`에만 있고 `providers:`에 없는 항목이 있으면 `"custom_providers entries without a providers: twin (v12 migration drift)"` 경고를 출력한다(`hermes_cli/doctor_config.py:488-511`).

2. **`providers.<name>` 허용 키 및 파싱 규칙** (`hermes_cli/config_providers.py:114-123, 285-304, 531-601`):
   - **엔드포인트 URL**: `base_url`, `url`, `api` 중 첫 번째 비어 있지 않은 문자열을 사용한다(`_pick_provider_base_url`, `config_providers.py:114-123`). Hermes 자체 마이그레이션/문서는 `api`와 `base_url`을 모두 사용하며 현재 `render_hermes()`가 출력하는 `base_url`은 정상 인식된다.
   - **인증(`key_env` / `api_key` / `key_cmd`)**:
     - `key_env` (별칭 `api_key_env`): 환경변수 이름을 지정한다(`config_providers.py:296`).
     - `api_key`: 리터럴 키 또는 `"${ENV_VAR}"` 문법을 지원한다. `load_config()` 시점에 `_expand_env_vars()`(`hermes_cli/config.py:1469-1495`)가 설정 파일 전체의 `"${VAR}"`를 `os.getenv("VAR")`로 치환한다.
     - `key_cmd`: 단기 토큰을 stdout으로 출력하는 외부 명령어를 지정한다(`config_providers.py:238, 297`, `website/docs/integrations/providers.md:1328-1364`).
   - **Wire 프로토콜(`transport` / `api_mode`)**:
     - `transport` 또는 `api_mode` 키를 읽고 `_normalize_custom_provider_api_mode()`(`config_providers.py:40-63`)가 다음 4개 정규 값 중 하나로 변환한다:
       - `"chat_completions"` (기본값; 별칭 `"openai"`, `"chat"`, `"completions"`, `"chat-completions"`, `"openai-completions"`)
       - `"codex_responses"` (별칭 `"responses"`, `"codex"`, `"openai-responses"`, `"codex-responses"`)
       - `"anthropic_messages"` (별칭 `"anthropic"`, `"messages"`, `"anthropic-messages"`)
       - `"bedrock_converse"` (별칭 `"bedrock"`, `"converse"`, `"bedrock-converse"`)
     - 참고: URL 경로가 `/messages` 또는 `/v1/messages`로 끝나거나 호스트가 `api.anthropic.com`이면 `transport`를 생략해도 자동으로 `"anthropic_messages"`로 추론된다(`config_providers.py:66-76`).
   - **모델 맵(`models`) 및 컨텍스트 길이(`context_length`)**:
     - 엔드포인트 레벨 `context_length`: 하위 모델에 별도 지정이 없을 때의 기본 컨텍스트 윈도우(`config_providers.py:568`).
     - `models`: `{<model_id>: {context_length: <int>, supports_vision: <bool>, supports_reasoning: <bool>}}` 매핑(`config_providers.py:531-601`, `agent/image_routing.py:120-173`, `website/docs/integrations/providers.md:1405-1425`). 리스트 형태(`models: [id1, id2]`)도 허용하지만 메타데이터를 담으려면 dict 형태여야 한다.
     - **중요**: Hermes는 `providers.<name>.models.<id>` 아래의 `max_tokens` 또는 `max_output_tokens` 필드를 **읽지 않는다**(`website/docs/integrations/providers.md:1240-1244`).
     - **최소 컨텍스트 길이 제약**: `agent/model_metadata.py:278`에 `MINIMUM_CONTEXT_LENGTH = 64_000`이 하드코딩되어 있어, 세션 시작·`/model` 전환·크론 실행 시 `context_length < 64000`인 모델은 `"too little working memory"`로 거부된다.
   - **추가 헤더·바디·TLS**:
     - `extra_headers`: `{Header-Name: Value}` 딕셔너리(`config_providers.py:476-513`). OpenAI 클라이언트(`default_headers`)와 Anthropic 어댑터(`agent/anthropic_adapter.py:453`) 양쪽에 병합된다.
     - `extra_body`: 요청 JSON 본문에 병합할 추가 파라미터 딕셔너리(`config_providers.py:390-445`).
     - `ssl_ca_cert` (커스텀 CA 번들 경로), `ssl_verify` (`bool`, 자체 서명 인증서 검증 비활성화 시 `false`), `discover_models` (`bool`, 기본 `true`).

3. **`model.default` + `model.provider: custom:<name>`의 선택 메커니즘**:
   - `resolve_custom_runtime_provider()`(`hermes_cli/runtime_provider_custom.py:561-767`)는 `requested`가 `"custom:<name>"`이면 `get_custom_provider_by_name("<name>")`을 호출해 `providers.<name>` 엔트리를 찾는다.
   - `custom:` 접두사를 붙이면 엔드포인트 이름이 `"openai"`, `"anthropic"` 같은 빌트인 이름과 같더라도 빌트인에 가려지지 않고(`_shadowed_by_builtin()`이 `explicit_custom=True`일 때 항상 `False` 반환, `runtime_provider_custom.py:131-146`) 무조건 사용자의 `providers.<name>` 항목을 선택한다.
   - **현재 `render_hermes()`(`llm_manager.py:393-424`)가 출력하는 형태와의 비교**:
     - 현재 출력:
       ```yaml
       model:
         default: <default_model>
         provider: custom:<default_ep>
       providers:
         <ep>:
           base_url: <root>/v1
           key_env: <KEY_ENV>
           models:
             <mid>:
               context_length: <ctx>
       auxiliary:
         vision: {provider: main}
         ...
       ```
     - 부족한 점:
       1. `model.base_url`을 함께 기록하지 않아 일부 레거시 코드 경로(예: `hermes_cli/models_local.py:106`)에서 활성 `model.base_url` 조회 시 빈 값이 된다.
       2. `providers.<ep>.models.<mid>`에 `supports_vision: true/false`를 기록하지 않아, Hermes가 모델 이름 휴리스틱이나 라이브 프로브에 의존하게 된다(`agent/image_routing.py:114-185`).
       3. `providers.<ep>`에 `transport`(API 포맷), `extra_headers`(비-Bearer 인증 시), keyless 처리를 기록하지 않는다.
       4. `plan_files()`(`llm_manager.py:609-615`)에서 `replace = (("model",),) + tuple(("providers", n) for n in cat["endpoints"])`로만 지정하므로, 카탈로그에서 **삭제된 이전 엔드포인트**가 `config.yaml`의 `providers:` 아래에 고아(orphan) 상태로 남는다.

#### 2.2.2 런타임 Provider/모델 전환 및 동일 모델 ID 중복 시 동작

1. **런타임 전환 명령어** (`hermes_cli/model_switch.py:396-409`, `website/docs/integrations/providers.md:1427-1444`):
   - **3-파트 슬러그**: `/model custom:<provider_name>:<model_id>` (예: `/model custom:local_vllm:qwen2.5-coder-32b`)
   - **플래그 지정**: `/model <model_id> --provider custom:<provider_name>` (또는 `--provider <provider_name>`)
   - **모델명 단독 지정**: `/model <model_id>` — Hermes가 등록된 모든 `providers:`의 `models:` 목록과 `default_model`을 스캔하여 해당 모델을 선언한 provider를 자동 탐색한다(`_configured_provider_matches`, `model_switch.py:943-980`).

2. **동일 모델 ID가 2개 이상의 커스텀 Provider에 동시에 존재할 때의 문제점**:
   - `hermes_cli/model_switch.py:1349-1375`의 `_route_configured_provider()` 코드를 확인한 결과:
     1. **현재 활성 세션의 provider**가 해당 모델 ID를 선언하고 있으면, `_current_provider_match()`(`model_switch.py:1016-1025`)가 우선권을 가져 **다른 provider로 전환되지 않고 현재 provider에 머문다**.
     2. 현재 활성 provider가 그 모델을 갖고 있지 않은 상태에서 **2개 이상의 `providers:` 엔트리가 동일 모델 ID를 선언**하고 있으면, `len(match_slugs) > 1` 분기(`model_switch.py:1362-1366`)에 걸려 전환을 거부하고 다음 에러를 반환한다:
        ```text
        '<model_id>' is declared by multiple configured providers (<ep1>, <ep2>). Re-run with --provider <slug> to choose which one to use.
        ```
     3. 세션 복구 시 `base_url` 정보가 유실된 bare `"custom"` 세션을 치유하는 `find_custom_provider_identity_by_model(model)`(`hermes_cli/runtime_provider_custom.py:356-361`)은 `providers:`을 순회하며 **첫 번째로 일치하는 provider**를 무조건 반환한다.
   - 또한 Hermes는 `config.yaml` 최상위 `model_aliases:`(또는 `model.aliases:`)를 지원하므로(`hermes_cli/model_switch.py:203-252`, `skills/autonomous-ai-agents/hermes-agent/references/providers-and-models.md:41-62`), 동일 모델 ID를 두 엔드포인트에 등록할 때는 별칭을 함께 생성하면 `/model <alias>`로 즉시 전환할 수 있다:
     ```yaml
     model_aliases:
       sonnet-corp:
         provider: custom:corp_gw
         model: claude-sonnet-4-6
       sonnet-backup:
         provider: custom:backup_gw
         model: claude-sonnet-4-6
     ```

#### 2.2.3 `auxiliary.*` 태스크 라우팅과 `provider: main`의 문제점

1. **Hermes의 전체 `auxiliary` 태스크 목록** (`hermes_cli/config_defaults.py:725-803`, `agent/auxiliary_task_config.py:11-95`):
   - 총 19개 태스크가 존재한다:
     - 코어/빈번 호출 태스크: `vision`, `compression`, `title_generation`, `approval`, `mcp`, `triage_specifier`, `tts_audio_tags`, `skills_hub`, `memory_query_rewrite`, `review`, `background_review`
     - 에이전트/서브시스템 태스크: `voice_chat`, `kanban_decomposer`, `profile_describer`, `goal_judge`, `curator`, `monitor`, `moa_reference`, `moa_aggregator`
   - 각 태스크 블록이 허용하는 키(`agent/auxiliary_task_config.py:90-95`, `agent/auxiliary_client.py:6065-6079`):
     `provider`, `model`, `base_url`, `api_key`, `key_env`(또는 `api_key_env`), `api_mode`, `timeout`, `reasoning_effort`, `extra_body`, `fallback_chain`.

2. **현재 `render_hermes()`가 출력하는 `provider: "main"`의 3가지 심각한 버그**:
   - **버그 ① — `"main"`이 `"custom:<name>"`이 아니라 bare `"custom"`으로 뭉개짐**:
     - 에이전트 턴 시작 시 `set_runtime_main()`(`agent/auxiliary_client.py:2713-2737`)은 `runtime["provider"] = "custom"`(과금/어댑터 분류값)으로 저장하고 원래의 `"custom:<ep>"`는 `runtime["requested_provider"]`에 저장한다.
     - 그러나 `_read_main_provider()`(`auxiliary_client.py:2467-2474`)는 `runtime["provider"]`(`"custom"`)만 읽고, `_normalize_aux_provider("main")`(`auxiliary_client.py:577-582`)은 `"main"`을 `"custom"`으로 치환한다.
     - 런타임 컨텍스트(`_ ACTIVE_RUNTIME_MAIN`)가 바인딩되지 않은 백그라운드 호출이나 CLI 진단(`hermes doctor`)에서는 `"custom"`이 어떤 엔드포인트인지 찾지 못한다.
   - **버그 ② — `hermes doctor`에서 모든 `auxiliary.<task>.provider: main`이 검증 실패**:
     - `hermes_cli/doctor_config.py:308-336`의 `_validate_auxiliary_config()`는 `provider not in ("", "auto")`인 모든 보조 태스크에 대해 `resolve_runtime_provider(requested=provider)`를 호출한다.
     - `resolve_runtime_provider(requested="main")`은 `"main"`이라는 provider ID를 알지 못하므로 `hermes doctor` 실행 시 경고/에러가 발생한다. 반면 `provider: "custom:<ep>"`는 `_known_provider_ids(cfg)`에 존재하므로 `✓ auxiliary task routing resolves: vision→custom@<host>`로 정상 통과한다.
   - **버그 ③ — `auxiliary.vision.provider: "main"`이 메인 비전 모델의 네이티브 이미지 입력을 강제로 꺼버림**:
     - `agent/image_routing.py:262-271, 387-388`의 `_explicit_aux_vision_override(cfg)` 및 `decide_image_input_mode()`는 `auxiliary.vision.provider`가 `""` 또는 `"auto"`가 아니면 **"사용자가 외부 전용 비전 모델을 강제한 것"** 으로 판단하여 `image_input_mode = "text"`로 전환한다.
     - 그 결과 메인 모델 자체가 비전을 지원하더라도 이미지를 메시지에 직접 첨부(native multimodal)하지 못하고 `vision_analyze` 보조 도구를 경유하게 된다.

3. **멀티 엔드포인트에서 Vision 모델 또는 경량(Tier 3) 모델이 다른 엔드포인트에 있을 때의 올바른 설정**:
   - **비전(`auxiliary.vision`)**:
     - 카탈로그에 비전 지원(`vision: true`) 모델이 존재하면, 해당 모델이 속한 엔드포인트와 모델 ID로 명시 고정한다:
       ```yaml
       auxiliary:
         vision:
           provider: custom:<vision_endpoint>
           model: <vision_model_id>
       ```
     - 단, **메인 기본 모델(`model.default`) 자체가 비전을 지원하고 동일한 비전 모델을 쓸 때**는 `auxiliary.vision.provider: auto` 및 `model: <vision_model_id>`로 두거나 `providers.<ep>.models.<default_model>.supports_vision: true`를 명시해야 메인 턴에서 네이티브 이미지 첨부(`image_input_mode = "native"`)가 작동한다(`agent/image_routing.py:372-395`).
     - 메인 모델이 텍스트 전용(`vision: false`)이고 다른 엔드포인트(예: `local_vllm` 또는 `corp_gw`)에 비전 모델이 있다면 반드시 `auxiliary.vision: {provider: "custom:<vision_ep>", model: "<vision_mid>"}`로 핀 고정해야 한다.
   - **경량/빠른 보조 태스크(`compression`, `title_generation`, `approval`, `mcp`, `triage_specifier`, `tts_audio_tags`, `skills_hub`, `memory_query_rewrite`, `background_review`)**:
     - 카탈로그의 `tiers.tier3`(없으면 `tier2` → `default`) 첫 번째 모델과 그 모델의 `endpoint`를 조회하여 다음과 같이 핀 고정한다:
       ```yaml
       auxiliary:
         compression:
           provider: custom:<tier3_endpoint>
           model: <tier3_model_id>
       ```
     - 이렇게 하면 메인 모델은 엔드포인트 A의 고성능 모델(예: Opus / GLM-5.1)을 쓰고, 컨텍스트 압축·제목 생성·승인 분류는 엔드포인트 B의 경량/로컬 모델(예: Haiku / 로컬 Qwen)로 정확히 분리 라우팅된다(`agent/auxiliary_client.py:4862-4902`의 `_resolve_named_custom_branch`가 `custom:<name>`에서 `base_url`, `api_key`, `api_mode`, `default_model`을 직접 추출함).

#### 2.2.4 Fallback Provider, Credential Pool, 그리고 비-카탈로그 외부 Provider(OpenRouter 등) 트래픽 유출 경로

두 번째 엔드포인트를 추가했을 때 카탈로그 외부(OpenRouter, Nous Portal, DeepInfra 등)로 트래픽이나 인증 헤더가 유출될 수 있는 코드 경로를 전수 조사한 결과 **5가지 유출 벡터**가 확인되었다.

1. **유출 벡터 ① — `auxiliary.vision` 자동 폴백 체인 (`_VISION_AUTO_PROVIDER_ORDER`)**:
   - 근거: `agent/auxiliary_client.py:5411, 5541-5567` (`_VISION_AUTO_PROVIDER_ORDER = ("openrouter", "nous", "deepinfra")`).
   - 일반 텍스트 보조 태스크는 `_discovery_chain_allowed()`(`auxiliary_client.py:4529-4541`)가 `main_provider not in {"", "auto"}`일 때 빌트인 디스커버리 체인(OpenRouter/Nous) 진입을 차단한다.
   - **그러나 `_vision_auto_route()`(`auxiliary_client.py:5541-5567`)는 `_discovery_chain_allowed()`를 검사하지 않는다!** 따라서 `auxiliary.vision.provider`가 `"auto"`(또는 `"main"` 해석 실패 후 `"auto"`로 폴백, `auxiliary_client.py:7292-7297`)이고 메인 모델이 비전을 지원하지 않으면, 사용자 환경변수에 `OPENROUTER_API_KEY`, `NOUS_API_KEY`, `DEEPINFRA_API_KEY`가 존재할 경우 즉시 외부 클라우드로 이미지 분석 요청을 전송한다.
   - **차단 방법**:
     1. 카탈로그의 모든 모델에 대해 `providers.<ep>.models.<mid>.supports_vision: <bool>`을 명시 렌더링한다.
     2. 카탈로그에 비전 모델이 있으면 `auxiliary.vision`에 `provider: "custom:<vision_ep>"` + `model: "<vision_mid>"`를 명시한다(`_explicit not in {"auto", "openrouter", "custom"}`이면 실패 시 `"auto"`로 폴백하지 않고 즉시 `AuxiliaryClientUnavailable` 예외를 발생시켜 외부 유출을 막음, `auxiliary_client.py:7309-7312`).

2. **유출 벡터 ② — 텍스트 `auxiliary.<task>`에서 `provider: "main"` 해석 실패 시 `"auto"` 폴백**:
   - 근거: `agent/auxiliary_client.py:7292-7297, 7325-7330`.
   - `provider: "main"`은 `"custom"`으로 정규화되는데, 라인 7310에서 `_explicit not in {"auto", "openrouter", "custom"}` 조건 때문에 `"custom"`은 명시적 핀으로 인정받지 못하고 클라이언트 생성 실패 시 `provider="auto"`로 재시도한다.
   - **차단 방법**: `provider: "main"` 대신 항상 **`provider: "custom:<ep>"`** 및 **`model: "<mid>"`** 를 사용한다. `"custom:<ep>"`는 `resolved_provider`가 `"custom:<ep>"`이므로 실패해도 절대 `"auto"`로 폴백하지 않는다.

3. **유출 벡터 ③ — 기존 `config.yaml`에 남아 있던 `fallback_providers` / `fallback_model`**:
   - 근거: `hermes_cli/fallback_config.py:126-143`, `agent/agent_init_fallback.py:16-61`.
   - Hermes는 메인 엔드포인트가 429/5xx/연결 실패를 반환하면 최상위 `fallback_providers:` 리스트(및 레거시 `fallback_model:` dict)를 순서대로 시도한다.
   - 현재 `llm_manager.py`는 `config.yaml`을 병합(`merge_yaml`)할 때 `fallback_providers`와 `fallback_model`을 덮어쓰지 않으므로, 사용자가 과거에 설정한 OpenRouter 폴백이 남아 있으면 게이트웨이 장애 시 외부로 트래픽이 넘어간다.
   - **차단 및 활용 방법**: `render_hermes()`에서 `fallback_providers`를 명시적으로 렌더링하고 `plan_files`의 `replace` 대상에 `("fallback_providers",)`와 `("fallback_model",)`을 포함시킨다. 카탈로그에 2개 이상의 엔드포인트(또는 `tier1`/`tier2` 백업 모델)가 있으면 카탈로그 내부 엔드포인트를 `fallback_providers`로 구성할 수 있다:
     ```yaml
     fallback_providers:
       - provider: custom:<backup_ep>
         model: <backup_model>
     ```

4. **유출 벡터 ④ — Keyless 커스텀 엔드포인트의 호스트명 기반 환경변수 추측 (`_host_gated_env_key_candidates`)**:
   - 근거: `hermes_cli/runtime_provider_custom.py:750-753` 및 `hermes_cli/runtime_provider.py:425-455`.
   - `providers.<name>` 항목에 `key_env`, `api_key`, `key_cmd`가 **모두 생략**되어 있으면(`not entry_declares_credential(custom_provider)`), Hermes는 `base_url`의 호스트명에서 벤더 토큰을 추출해 `<VENDOR>_API_KEY` 환경변수를 뒤진다. 또한 `OPENAI_API_KEY`가 전역 폴백으로 읽힐 가능성을 막기 위해 keyless 엔드포인트에는 반드시 **`api_key: "no-key-required"`** 를 명시해야 `entry_declares_credential()`이 `True`가 되어 외부 환경변수 스캔을 완전히 건너뛴다(`runtime_provider_custom.py:55-65, 737-745`).

5. **유출 벡터 ⑤ — Credential Pool (`~/.hermes/auth.json`)**:
   - 근거: `hermes_cli/runtime_provider_custom.py:521-558, 737-745`, `agent/custom_entry_pool.py`.
   - Hermes는 다중 키 로테이션을 위해 `~/.hermes/auth.json`의 `credential_pool`을 조회하지만, `providers.<name>` 항목이 자체 `key_env` 또는 `api_key`를 선언하면 `entry_scoped_pool()`이 다른 빌트인 provider의 env-seeded 키를 빌려오지 않도록 격리한다. 따라서 각 `providers.<name>`마다 `key_env` 또는 `api_key: "no-key-required"`를 명시하는 것이 중요하다.

#### 2.2.5 `hermes config check` 및 각 Provider 검증 방법

로컬에 설치된 Hermes CLI 소스를 직접 확인한 결과(`hermes_cli/config.py:3666-3702`, `hermes_cli/subcommands/config.py:67-95`, `hermes_cli/doctor.py`, `hermes_cli/doctor_config.py`, `hermes_cli/doctor_connectivity.py`):

| 명령어 | 실제 수행 내용 (소스 근거) | 멀티 엔드포인트 검증 한계 |
| :--- | :--- | :--- |
| `hermes config check` | `config.yaml` 스키마 버전(`CONFIG_VERSION`), 누락된 기본 키(`get_missing_config_fields`), 비활성 플랫폼 플러그인만 검사(`hermes_cli/config.py:3666-3702`). | **`providers:` 블록의 문법, 환경변수 존재 여부, 게이트웨이 연결을 전혀 검사하지 않음.** |
| `hermes doctor` | 1. `_validate_Provider_config`(`doctor_config.py:251-354`): `model.provider`가 `_known_provider_ids(cfg)`에 있는지, 모든 `auxiliary.<task>`가 `resolve_runtime_provider()`로 해석되는지 검증.<br>2. `_drift_legacy_custom_providers`(`doctor_config.py:488-511`): `custom_providers` 드리프트 검사.<br>3. `_check_api_connectivity`(`doctor_connectivity.py:410-422`): API 연결성 체크. | 설정 구조와 `auxiliary` 라우팅 검증에는 매우 유용하나, **네트워크 연결 프로브(`_check_api_connectivity`)는 빌트인 `PROVIDER_REGISTRY`만 순회하고 사용자 `providers:` 커스텀 엔드포인트는 프로브하지 않음.** |
| `hermes verify` | 현재 작업 디렉터리의 소프트웨어 빌드/테스트 레시피 자동 감지 도구(`hermes_cli/verify_cmd.py:1-50`). | LLM 엔드포인트 검증과 무관함. |

따라서 커스텀 멀티 엔드포인트의 실제 연결·인증·모델 노출 검증은 **`llm_manager.py verify` (내부 `preflight()` + 엔드포인트별 `/models` 대조)에 더해 `hermes doctor`로 설정 정합성(`auxiliary` 라우팅 포함)을 함께 확인**하는 것이 가장 확실하다.

---

### 2.3 주의사항 및 버전 차이 (Pitfalls & Version Differences)

1. **`MINIMUM_CONTEXT_LENGTH = 64_000` 강제 제약**:
   - Hermes `v0.19.0`(`agent/model_metadata.py:278`)은 `context_length < 64000`인 모델을 세션 생성 및 `/model` 전환에서 거부한다. 로컬 `Ollama`나 `vLLM`, `llama.cpp`에서 기본 `num_ctx: 4096` 또는 `max_model_len: 32768`로 띄운 모델을 카탈로그에 그대로 넣으면 Hermes가 실행을 거부한다.
2. **Windows `localhost` IPv6 지연**:
   - Windows 듀얼스택 환경에서 `http://localhost:11434` 또는 `http://localhost:8000`은 `::1` 연결 타임아웃(~2초)을 매번 겪을 수 있다(`agent/model_metadata.py:715-720`). 로컬 엔드포인트는 `http://127.0.0.1:<port>` 표기를 권장한다.
3. **고아(Orphan) Provider 잔류**:
   - `llm_manager.py`가 `("providers", n)` 단위로만 `replace`하면 카탈로그에서 이름을 바꾸거나 제거한 이전 엔드포인트가 `config.yaml`에 계속 남아 `/model` 검색 시 중복 모델 충돌(`declared by multiple configured providers`)을 일으킨다.

---

### 2.4 Hermes에 대한 `llm_manager.py` 개선 권고안

1. **`render_hermes(cat)` 개선**:
   - `model:` 블록에 `base_url`도 함께 기록한다 (`default_ep`의 렌더링된 `base_url`).
   - 각 `providers.<ep>` 블록을 엔드포인트 설정 및 `detect_server()` 결과에 맞춰 렌더링한다:
     - `base_url`: `f"{ep['base_url']}{ep.get('api_base', '/v1')}"` (`api_base`가 `""`이면 루트 그대로).
     - **인증**:
       - `auth == "none"` 또는 `key_env`가 없는 경우: `key_env`를 넣지 말고 `api_key: "no-key-required"`를 출력한다.
       - `auth == "bearer"` (또는 `transport == "anthropic_messages"` + `auth == "x-api-key"`): `key_env: ep["key_env"]`를 출력한다.
       - `auth == "api-key"` (Azure 등) 또는 OpenAI transport에서의 `x-api-key`: `api_key: "no-key-required"`와 함께 `extra_headers: {header_name: f"${{{ep['key_env']}}}"}`를 출력한다.
       - 카탈로그 `endpoints.<ep>.headers`가 있으면 `extra_headers`에 병합한다.
     - **Wire 포맷(`transport`)**:
       - 해당 엔드포인트 소속 모델들의 포맷(또는 엔드포인트 기본 포맷)에 따라 `"openai-completions"` → `transport: "chat_completions"`, `"openai-responses"` → `transport: "codex_responses"`, `"anthropic-messages"` → `transport: "anthropic_messages"`를 명시한다.
     - `discover_models: false`를 기본 출력하여 카탈로그에 선언된 모델만 확정적으로 사용하게 하거나 옵션화한다.
     - `models.<mid>`에 `context_length: m["context"]`뿐만 아니라 **`supports_vision: bool(m["vision"])`** 도 반드시 출력한다 (`max_output`은 Hermes가 무시하므로 제외 유지).
   - **`auxiliary:` 블록**:
     - `provider: "main"`을 폐기한다.
     - `vision`: 카탈로그 내에서 `vision: true`인 모델을 우선 탐색(기본 모델이 `vision: true`면 기본 모델, 아니면 `tier1` → `tier2` → `tier3` → 전체 모델 순)하여 `{provider: f"custom:{v_ep}", model: v_mid}`로 고정한다.
     - 나머지 태스크(`compression`, `title_generation`, `approval`, `mcp`, `triage_specifier`, `tts_audio_tags`, `skills_hub`, `memory_query_rewrite`, `background_review`): `tiers.tier3[0]`(없으면 `tier2[0]` → `default`) 모델과 그 소속 엔드포인트로 `{provider: f"custom:{fast_ep}", model: fast_mid}`를 출력한다.
   - **`fallback_providers:` 블록**:
     - `tiers.tier1` / `tiers.tier2`에서 `default` 모델과 다른 차순위 모델(특히 다른 엔드포인트의 백업 모델)이 있으면 `fallback_providers: [{provider: f"custom:{ep}", model: mid}, ...]`로 렌더링하고, 없으면 `fallback_providers: []`로 비워 기존 외부 폴백 유출을 차단한다.
   - **`plan_files()`의 `replace` 튜플**:
     - `replace = (("model",), ("providers",), ("auxiliary",), ("fallback_providers",), ("fallback_model",))`로 변경하여 카탈로그에서 제거된 엔드포인트나 레거시 외부 폴백이 남지 않게 한다.

2. **검증 및 경고(`validate` / `verify`) 추가**:
   - Hermes 타겟에 렌더링될 모델 중 `context < 64000`인 모델이 있으면 `"Hermes rejects models with context_length < 64,000 (MINIMUM_CONTEXT_LENGTH)"` 경고를 출력한다.
   - 서로 다른 두 엔드포인트가 동일한 모델 ID를 제공할 경우, Hermes에서 `/model <id>` 단독 전환 시 모호성 에러가 발생하므로 `/model custom:<ep>:<id>`를 사용하거나 `model_aliases`를 생성하도록 안내한다.

---

## 3. Claude Code (`~/.claude/settings.json` `env` 블록)

### 3.1 단일 `ANTHROPIC_BASE_URL` 구조 확증 및 2-Endpoint 카탈로그 대응 최소 동작 예시

로컬 설치된 Claude Code 바이너리(`~/.local/bin/claude.exe`, 버전 `2.1.327`) 심볼 분석 및 공식 문서(LLM Gateway Configuration, Model Configuration, Settings) 확인 결과, **Claude Code는 프로세스당 오직 하나의 `ANTHROPIC_BASE_URL`만 사용한다.**

따라서 카탈로그에 2개의 엔드포인트(예: 엔드포인트 A는 사내 LiteLLM 게이트웨이, 엔드포인트 B는 로컬 Ollama/vLLM 또는 제2 게이트웨이)가 있을 때 `~/.claude/settings.json`은 **선택된 단일 주(primary) Claude 엔드포인트**를 기준으로 렌더링해야 한다.

```json
{
  "apiKeyHelper": "powershell -NoProfile -NonInteractive -Command \"$env:CORP_LLM_API_KEY\"",
  "env": {
    "ANTHROPIC_BASE_URL": "https://llm-gw.internal.example.com",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4-5",
    "CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-4-5"
  }
}
```
*(참고: 선택한 엔드포인트의 `key_env`가 이미 `ANTHROPIC_AUTH_TOKEN`(Bearer) 또는 `ANTHROPIC_API_KEY`(`x-api-key`)라면 `apiKeyHelper` 없이 환경변수를 그대로 사용한다.)*

---

### 3.2 항목별 상세 조사 결과 및 근거

#### 3.2.1 단일 `ANTHROPIC_BASE_URL` 제약과 2개 이상 게이트웨이 분산 모델 처리 방식

1. **단일 `ANTHROPIC_BASE_URL` 확인**:
   - Claude Code 바이너리(`claude.exe` v2.1.327) 내 모든 `*_BASE_URL` 환경변수를 추출한 결과:
     - `ANTHROPIC_BASE_URL` (기본 Anthropic Messages API 및 LiteLLM 등 게이트웨이용)
     - `ANTHROPIC_BEDROCK_BASE_URL` (`CLAUDE_CODE_USE_BEDROCK=1` 활성화 시 Bedrock 프록시용)
     - `ANTHROPIC_VERTEX_BASE_URL` (`CLAUDE_CODE_USE_VERTEX=1` 활성화 시 Vertex 프록시용)
     - `ANTHROPIC_FOUNDRY_BASE_URL` (`CLAUDE_CODE_USE_FOUNDRY=1` 활성화 시 Azure Foundry용)
   - 슬롯별(`OPUS`, `SONNET`, `HAIKU`, `SUBAGENT`)로 별도의 Base URL이나 별도의 API Key를 지정하는 환경변수나 `settings.json` 키는 **존재하지 않는다**.
   - 참고: `claude.exe gateway --help` 서브커맨드가 바이너리에 존재하나(`Usage: claude gateway [options] [command]`, `-c, --config <path>`), 이는 로컬 브리지 서버를 띄우는 기능(`add/remove/list/status`)이며 `settings.json`에서 슬롯별로 다중 업스트림 URL을 분기하는 클라이언트 설정이 아니다.

2. **모델이 2개 이상의 게이트웨이에 나뉘어 있을 때 지원되는 우회·운용 방식**:
   - **방식 A (권장, `llm_manager.py` 자동 처리)**: Claude Code가 사용할 **단일 엔드포인트를 선택**(`claude_code.endpoint` 또는 `claude_code.model`의 엔드포인트)하고, `opus` / `sonnet` / `haiku` / `subagent` 슬롯에는 **그 엔드포인트에 존재하는 모델만 배정**한다.
   - **방식 B (업스트림 통합)**: 사내 `LiteLLM` 프록시 하나로 두 엔드포인트를 묶어 단일 `ANTHROPIC_BASE_URL` 뒤에서 모델 이름별로 라우팅한다.
   - **방식 C (셸 프로필 / 래퍼 분리)**: 두 게이트웨이를 동시에 번갈아 써야 한다면 별도 설정 디렉터리(`CLAUDE_CONFIG_DIR=~/.claude-ep2`) 또는 셸 별칭으로 프로세스를 분리한다.

#### 3.2.2 인증 방식 비교: `ANTHROPIC_AUTH_TOKEN` vs `ANTHROPIC_API_KEY` vs `apiKeyHelper`

| 구분 | 전송되는 HTTP 헤더 | 주 용도 | 멀티 엔드포인트 / 게이트웨이 주의점 |
| :--- | :--- | :--- | :--- |
| `ANTHROPIC_AUTH_TOKEN` (env) | `Authorization: Bearer <token>` | LiteLLM, 사내 LLM 게이트웨이, Ollama(`0.14+` `/v1/messages`), OAuth/JWT 프록시 | 사용자 셸에 `ANTHROPIC_API_KEY`가 동시에 설정되어 있으면 두 헤더가 함께 전송되거나 `x-api-key`가 간섭하여 게이트웨이에서 `401`이 발생할 수 있음. `settings.json`의 `env`에 `"ANTHROPIC_API_KEY": ""`를 넣어 초기화하는 것이 안전함. |
| `ANTHROPIC_API_KEY` (env) | `x-api-key: <key>` | Anthropic 공식 API (`api.anthropic.com`) 또는 `x-api-key`를 요구하는 프록시 (`detect_server`에서 `auth == "x-api-key"`) | `detect_server()`가 `auth == "x-api-key"`를 반환한 엔드포인트에서는 `ANTHROPIC_AUTH_TOKEN` 대신 `ANTHROPIC_API_KEY`를 사용해야 함. |
| `apiKeyHelper` (`settings.json` 최상위 키) | `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` 자격증명으로 동적 주입 | 동적 토큰 발급 스크립트, 또는 **`endpoints.<ep>.key_env`가 `ANTHROPIC_AUTH_TOKEN`이 아닐 때(예: `CORP_LLM_KEY`) 평문 키를 파일에 저장하지 않고 해당 환경변수를 읽어 전달**할 때 | `env` 블록 안이 아니라 `~/.claude/settings.json` **최상위(top-level)** 키임. 반환값은 기본적으로 캐시되며 `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` 환경변수로 갱신 주기를 제어함. 워크스페이스 신뢰(trust) 승인 후 실행됨(`claude.exe` 바이너리 로그: `apiKeyHelper requires workspace trust`). |

#### 3.2.3 커스텀 헤더: `ANTHROPIC_CUSTOM_HEADERS` 및 `headersHelper`

- **`ANTHROPIC_CUSTOM_HEADERS` (`env` 블록)**:
  - 줄바꿈(`\n`)으로 구분된 `"Header-Name: Value"` 문자열이다(출처: Claude Code 공식 LLM Gateway 문서 및 `claude.exe` v2.1.327 바이너리).
  - 예시 (단일 또는 다중 헤더):
    ```json
    {
      "env": {
        "ANTHROPIC_BASE_URL": "https://gateway.example.com",
        "ANTHROPIC_CUSTOM_HEADERS": "X-Routing-Group: backend\nX-Client-Env: prod"
      }
    }
    ```
  - `detect_server()`에서 `auth == "api-key"`(Azure APIM 등 비표준 헤더 인증)가 감지되었고 정적 헤더가 아닌 환경변수 참조가 필요하다면, `settings.json`은 환경변수 치환(`${VAR}`)을 자체 수행하지 않으므로 최상위 **`headersHelper`**(JSON 객체를 stdout으로 출력하는 셸 명령) 또는 `apply --env`를 통해 설정할 수 있다.
  - 주의: `claude.exe` 바이너리 확인 결과 조직 정책으로 실험적 베타가 비활성화된 경우 `ANTHROPIC_CUSTOM_HEADERS` 내의 `anthropic-beta` 헤더는 무시된다(`ANTHROPIC_CUSTOM_HEADERS anthropic-beta ignored: experimental betas are disabled by your organization's policy`).

#### 3.2.4 모델 슬롯 환경변수 전체 매핑 (`ANTHROPIC_DEFAULT_*`, `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`)

`claude.exe` v2.1.327 바이너리 및 공식 Model Configuration 문서에서 확인된 모델 지정 환경변수:

| 환경변수 | 역할 및 우선순위 | `llm_manager.py` 현재 지원 여부 |
| :--- | :--- | :--- |
| `ANTHROPIC_MODEL` | 세션 시작 시 기본 활성 모델 지정 | 지원 (`cc["model"]`) |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `/model opus` 별칭 및 `opusplan` 모드의 계획(plan) 단계에 매핑되는 모델 ID | 지원 (`cc["opus"]`) |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `/model sonnet` 별칭 및 `opusplan` 모드의 실행 단계에 매핑되는 모델 ID | 지원 (`cc["sonnet"]`) |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `/model haiku` 별칭 및 백그라운드 경량 기능에 매핑되는 모델 ID | 지원 (`cc["haiku"]`) |
| `ANTHROPIC_SMALL_FAST_MODEL` | 구버전 Haiku/경량 백그라운드 모델 변수 (`ANTHROPIC_DEFAULT_HAIKU_MODEL`로 대체되었으나 하위 호환을 위해 `haiku`와 동일 값 설정 권장) | 지원 (`cc["haiku"]`) |
| `CLAUDE_CODE_SUBAGENT_MODEL` | Claude Code가 서브에이전트(Task 도구)를 스폰할 때 사용하는 모델 ID (지정하지 않으면 내장 기본값 또는 Haiku/Sonnet 별칭을 시도하여 커스텀 게이트웨이에서 404 위험) | **미지원 (추가 필요: `cc["haiku"]` 또는 `cc["sonnet"]`으로 설정 권장)** |
| `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL_NAME` / `_DESCRIPTION` / `_SUPPORTED_CAPABILITIES` | `v2.1.x`에서 추가된 UI 표시용 이름·설명·기능 오버라이드 변수 (`claude.exe` 바이너리에서 확인) | 미지원 (선택 사항) |

*(참고: 게이트웨이에서 비-Claude 모델(예: `glm-5.1`)만 `/v1/messages` 어댑터로 서빙할 때도 위 6개 변수를 모두 해당 게이트웨이 모델 ID로 지정하지 않으면, Claude Code가 백그라운드 요약이나 서브에이전트 호출 시 하드코딩된 기본 Haiku/Sonnet ID로 요청을 보내 게이트웨이에서 `400/404 model not found` 에러가 발생한다.)*

#### 3.2.5 Bedrock / Vertex / Foundry Base URL 및 게이트웨이 연동

`detect_server()`가 `kind: "bedrock"`, `"vertex"`, `"azure-openai"`를 반환하거나 엔드포인트가 클라우드 프록시일 때 Claude Code가 요구하는 환경변수(`claude.exe` v2.1.327 및 공식 문서):

- **Amazon Bedrock 프록시 (`kind == "bedrock"`)**:
  - `CLAUDE_CODE_USE_BEDROCK: "1"`
  - `ANTHROPIC_BEDROCK_BASE_URL: "<root>"` (사내 게이트웨이의 Bedrock 패스스루 엔드포인트 사용 시)
  - 인증: AWS 자격증명(`AWS_REGION`, `AWS_PROFILE`) 또는 게이트웨이 Bearer 토큰(`AWS_BEARER_TOKEN_BEDROCK` / `ANTHROPIC_AUTH_TOKEN` + `CLAUDE_CODE_SKIP_BEDROCK_AUTH: "1"`)
- **Google Vertex AI 프록시 (`kind == "vertex"`)**:
  - `CLAUDE_CODE_USE_VERTEX: "1"`
  - `ANTHROPIC_VERTEX_BASE_URL: "<root>"`
  - `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION` (게이트웨이 패스스루 시 `CLAUDE_CODE_SKIP_VERTEX_AUTH: "1"`)
- **Microsoft Azure Foundry (`kind == "azure-openai"` + Anthropic 모델)**:
  - `CLAUDE_CODE_USE_FOUNDRY: "1"`
  - `ANTHROPIC_FOUNDRY_BASE_URL: "<root>"` (또는 `ANTHROPIC_FOUNDRY_RESOURCE`)
  - `ANTHROPIC_FOUNDRY_API_KEY` (또는 `CLAUDE_CODE_SKIP_FOUNDRY_AUTH: "1"`)

#### 3.2.6 결론: Claude 모델이 2+ 엔드포인트에 분산되어 있을 때 `llm_manager.py`의 처리 방침 및 `key_env` 연동

1. **현재 `_resolve_claude_code()`(`llm_manager.py:210-238`)의 문제점**:
   - 현재 코드는 `tiers.tier1`, `tier2`, `tier3`에서 각각 `first_claude(tier)`를 호출해 `opus`, `sonnet`, `haiku`를 먼저 채운 뒤, 선택된 모델들의 `endpoint` 집합 크기가 2 이상이면 무조건 `CatalogError("claude_code: all Claude Code models must be on the same endpoint")`를 던진다.
   - 즉, 사용자가 엔드포인트 A에 `claude-opus-4-6`, 엔드포인트 B에 `claude-haiku-4-5`를 두었을 때 `claude_code:` 블록을 명시적으로 잘못 쓴 것이 아닌데도 **자동 추론(auto-pick)이 서로 다른 엔드포인트의 모델을 집어와 스스로 에러를 내며 죽는다.**
   - 또한 현재 `cc["key_env"]`는 엔드포인트의 `endpoints[ep]["key_env"]`를 무시하고 무조건 `"ANTHROPIC_AUTH_TOKEN"`으로 하드코딩되어 있다(`llm_manager.py:237`).

2. **`llm_manager.py`가 수행해야 할 정확한 알고리즘**:
   - **Step 1 — 주 엔드포인트(`target_ep`) 결정**:
     1. `claude_code.endpoint`가 명시되어 있으면 해당 엔드포인트 선택.
     2. 아니면 `claude_code.model`이 명시되어 있으면 그 모델의 `endpoint` 선택.
     3. 아니면 `tier1` → `tier2` → `tier3` → `default` 순서로 첫 번째 Claude 모델(또는 `anthropic-messages` 포맷 지원 모델)이 속한 `endpoint`를 `target_ep`로 선택.
   - **Step 2 — 명시적 슬롯 충돌 검증**:
     - 사용자가 `claude_code.{model, opus, sonnet, haiku}`에 **직접 명시한** 모델 중 `target_ep`가 아닌 다른 엔드포인트 소속 모델이 있으면 그때만 명확한 `CatalogError`(예: `"claude_code.haiku ('...') belongs to endpoint 'ep2', but Claude Code only supports one endpoint ('ep1')"`)를 발생시킨다.
   - **Step 3 — `target_ep` 범위 내 자동 슬롯 배정**:
     - 자동 배정(`first_claude(tier)`)은 **`models[m]["endpoint"] == target_ep`인 모델들로만 필터링**하여 `opus`, `sonnet`, `haiku`를 채운다.
     - 타 엔드포인트에 Claude 모델이 더 존재하더라도 에러를 내지 않고 `notes` / `stderr` 경고(`"claude_code: ignoring Claude models on secondary endpoint '<ep2>' (Claude Code supports a single ANTHROPIC_BASE_URL)"`)만 남긴다.
     - 만약 `target_ep` 내에 `haiku`용 경량 모델이 없으면 `sonnet` 또는 `model`(주 모델)을 `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`에 폴백 지정하여 Claude Code가 게이트웨이에 없는 기본 Haiku ID를 호출해 404가 나는 것을 방지한다.
   - **Step 4 — `key_env` 및 인증 방식을 `endpoints[target_ep]`와 연동**:
     - `cc["key_env"]`의 기본값을 `"ANTHROPIC_AUTH_TOKEN"` 고정이 아니라 **`cc_raw.get("key_env") or endpoints[target_ep].get("key_env")`** 로 설정한다.
     - `detect_server()` 또는 엔드포인트의 `auth` 속성에 따라:
       - `auth == "none"` (예: 로컬 Ollama `0.14+`): `env["ANTHROPIC_AUTH_TOKEN"] = "no-key-required"`, `env["ANTHROPIC_API_KEY"] = ""`를 출력한다.
       - `auth == "bearer"`:
         - `cc["key_env"] == "ANTHROPIC_AUTH_TOKEN"`이면 `env["ANTHROPIC_API_KEY"] = ""`만 설정하고 환경변수 `ANTHROPIC_AUTH_TOKEN`을 그대로 쓰게 한다.
         - `cc["key_env"]`가 다른 이름(예: `COMPANY_LLM_KEY`)이면, 사용자에게 두 개의 환경변수를 중복 관리하게 하지 말고 `settings.json` 최상위에 `"apiKeyHelper"`(Windows: `cmd /c echo %COMPANY_LLM_KEY%` 또는 `powershell -NoProfile -Command "$env:COMPANY_LLM_KEY"`, POSIX: `sh -c 'printf "%s" "$COMPANY_LLM_KEY"'`)를 생성하거나 `apply --env` 시 `os.environ[cc["key_env"]]` 값을 `ANTHROPIC_AUTH_TOKEN`에도 동기화한다.
       - `auth == "x-api-key"`: `ANTHROPIC_API_KEY`를 타겟으로 삼는다.
       - `auth == "api-key"`: `headersHelper`를 통해 `{"api-key": "..."}`를 주입하거나 `ANTHROPIC_CUSTOM_HEADERS`를 사용한다.

---

### 3.3 주의사항 및 버전 차이 (Pitfalls & Version Differences)

1. **`ANTHROPIC_BASE_URL`의 `/v1` 접미사 금지**:
   - Anthropic SDK(Claude Code 내장)는 `ANTHROPIC_BASE_URL` 뒤에 자동으로 `/v1/messages`를 붙인다. 따라서 `ANTHROPIC_BASE_URL`에 `https://host/v1`을 넣으면 `https://host/v1/v1/messages`로 요청이 나가 `404`가 발생한다.
   - 단, `detect_server()`가 `api_base == "/openai/v1"`을 감지한 서버라면 `/v1`만 떼어낸 `https://host/openai`가 되어야 하며,애초에 해당 서버가 `/v1/messages`(`anthropic-messages` 포맷)를 지원하지 않는다면(`formats`에 `"anthropic-messages"` 미포함) Claude Code 타겟 엔드포인트로 선택할 때 경고를 띄워야 한다.
2. **`ANTHROPIC_SMALL_FAST_MODEL` → `ANTHROPIC_DEFAULT_HAIKU_MODEL` 전환**:
   - Claude Code 최신 버전(`2.1.x`)에서는 `ANTHROPIC_DEFAULT_HAIKU_MODEL`이 공식 변수이며 `ANTHROPIC_SMALL_FAST_MODEL`은 deprecated 상태로 하위 호환만 유지된다. 두 변수를 모두 출력하고 `CLAUDE_CODE_SUBAGENT_MODEL`까지 함께 지정해야 모든 내부 호출이 게이트웨이 모델로 고정된다.
3. **비-Claude 모델을 Claude Code에서 사용할 때**:
   - LiteLLM이나 Ollama(`0.14+`)는 `/v1/messages` 엔드포인트를 통해 `glm-5.1`, `qwen2.5-coder` 같은 비-Claude 모델도 Claude Code에 연결할 수 있다. 현재 `_resolve_claude_code()`(`llm_manager.py:211-212`)는 `"claude" in m.lower()`로만 모델을 필터링하므로, `claude_code.model`이 명시되지 않았더라도 `target_ep`에 `"anthropic-messages"` 포맷을 지원하는 모델이 있으면 사용할 수 있게 허용 범위를 넓히는 것이 좋다.

---

## 4. 게이트웨이 및 로컬 추론 서버 비교 (Gateway Side)

### 4.1 서버 종류별 엔드포인트·메타데이터·인증·포맷 비교

`llm_manager.py`의 `discover_endpoint()`(`lines 806-865`)와 `detect_server()`(`lines 918-971`), 그리고 Hermes의 `agent/model_metadata.py`(`lines 723-1084, 1410-1654`), `agent/model_metadata_llamacpp.py`(`lines 19-64`), `hermes_cli/models_local.py`(`lines 205-260`)에서 확인한 서버별 실제 동작 차이:

| 서버 종류 (`kind`) | 모델 목록 엔드포인트 | LiteLLM 전용 `/v1/model/info` · `/model_group/info` | 컨텍스트 길이(`context`) 및 최대 출력(`max_output`) 획득 경로 | 비전(`vision`) 메타데이터 획득 경로 | 기본 인증(`auth`) | 지원 Wire API 포맷 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **LiteLLM Proxy** (`litellm`) | `GET /v1/models` | **지원** (`max_input_tokens`, `litellm_params.max_tokens`, `max_output_tokens`, `supports_vision`, `mode`) | `/v1/model/info` 및 `/model_group/info` | `/v1/model/info`의 `model_info.supports_vision` | `bearer` (`Authorization: Bearer`) 또는 `x-api-key` | `/v1/chat/completions`, `/v1/messages` (패스스루/어댑터), `/v1/responses` |
| **vLLM** (`vllm`) | `GET /v1/models` (핑거프린트: `GET /version` → `{"version": ...}`) | 미지원 (`404`) | **`GET /v1/models`의 각 항목 내 `max_model_len`** (`max_output`은 별도 미제공; `context` 내 공유) | `/v1/models`에 미표시 (모델명 휴리스틱 또는 카탈로그 수동 지정) | `none` 또는 `bearer` (`--api-key` 실행 시) | `/v1/chat/completions`, `/v1/responses` (최신 vLLM), `/v1/messages` (미지원 또는 최신 실험적) |
| **Ollama** (`ollama`) | `GET /v1/models` 및 네이티브 **`GET /api/tags`** (`{"models": [{"model": ...}]}`) | 미지원 (`404`) | **`POST /api/show` (`{"name": "<model>"}`)**:<br>- 런타임 할당값: `parameters` 문자열 내 `num_ctx`<br>- GGUF 최대값: `model_info["<arch>.context_length"]`<br>(`/v1/models`에는 컨텍스트 정보 없음!) | **`POST /api/show`** 응답의 `capabilities` 배열 내 `"vision"` 포함 여부 (Ollama `0.6.0+`) 또는 `model_info["*.vision.block_count"]` (`agent/model_metadata.py:1448-1463`) | `none` (로컬 기본; 리버스 프록시 구성 시 `bearer`) | `/v1/chat/completions`, `/v1/responses` (Ollama `0.13+`), **`/v1/messages`** (Ollama `0.14+` Anthropic 호환 지원) |
| **llama.cpp (`llama-server`)** (`openai-compatible` / `llamacpp`) | `GET /v1/models` (`owned_by: "llamacpp"`, 단일 모델 시 `id`가 GGUF 파일 경로일 수 있음) | 미지원 (`404`) | 1. **`GET /v1/props`** 또는 **`GET /props`** (라우터 모드는 `/props?model=<id>&autoload=false`) → `default_generation_settings.n_ctx`<br>2. `GET /v1/models` 항목 내 `meta.n_ctx` 또는 `meta.n_ctx_train` (`model_metadata.py:1568-1586`) | `GET /props`의 `modalities.vision` (최신 빌드) 또는 미제공 | `none` 또는 `bearer` (`--api-key`) | `/v1/chat/completions`, `/v1/messages` (최신 `llama-server` 지원) |
| **LM Studio** (`openai-compatible` / `lm-studio`) | `GET /v1/models` 및 네이티브 **`GET /api/v1/models`** (또는 `/api/v0/models`) | 미지원 (`404`) | **`GET /api/v1/models`** → `models[].loaded_instances[0].config.context_length` (언로드 상태면 `max_context_length`; `/v1/models`는 컨텍스트 생략) | `GET /api/v1/models`의 `type: "vlm"` 또는 `capabilities` / `vision: true` | `none` (기본 `lm-studio`) 또는 `bearer` | `/v1/chat/completions`, `/v1/responses` (`0.3.9+`), `/v1/messages` (`0.4.0+`) |
| **OpenRouter 호환 게이트웨이** (`openai-compatible`) | `GET /v1/models` (또는 `/api/v1/models`) | 미지원 (`404`) | **`GET /v1/models`의 각 항목 내**:<br>- 컨텍스트: `context_length`<br>- 최대 출력: `top_provider.max_completion_tokens` (또는 `max_completion_tokens`) | `/v1/models` 항목 내 `architecture.input_modalities`에 `"image"` 포함 여부 | `bearer` | `/v1/chat/completions`, `/v1/responses`, `/v1/messages` (`/api/v1/messages`) |
| **Azure OpenAI / Foundry** (`azure-openai`) | `GET /openai/v1/models` 또는 `/v1/models` (`apim-request-id` / `x-ms-region` 헤더) | 미지원 (`404`) | `/models`에 컨텍스트 미포함 (패밀리 기본값 또는 카탈로그 지정) | `/models`에 미포함 | `api-key` (`api-key: <key>`) 또는 `bearer` (Entra ID) | `/openai/v1/chat/completions`, `/openai/v1/responses`, Foundry의 경우 `/anthropic/v1/messages` |
| **Anthropic Direct / Proxy** (`anthropic`) | `GET /v1/models` (`x-api-key` + `anthropic-version: 2023-06-01`) | 미지원 (`404`) | **`GET /v1/models` 항목 내 `max_input_tokens`** (컨텍스트), `max_tokens` (출력 상한) (`model_metadata.py:821-830, 1634-1654`) | Claude 3/4/4.5/4.6 전 모델 비전 지원 | `x-api-key` | `/v1/messages` |

### 4.2 현재 `discover_endpoint()`의 한계와 개선 포인트

1. **LiteLLM 외 서버에서 모든 모델이 `128000 / 16384 / vision: false` 하드코딩 기본값으로 떨어지는 문제**:
   - 현재 `discover_endpoint()`(`llm_manager.py:806-865`)는 `/model_group/info`와 `/v1/model/info`만 읽고, `/v1/models`에서는 `id`만 추출한다(`line 836`).
   - 따라서 `vLLM`(`max_model_len`), `OpenRouter`류(`context_length`, `top_provider.max_completion_tokens`, `architecture.input_modalities`), `Anthropic`(`max_input_tokens`), `llama.cpp`(`meta.n_ctx`)가 `/v1/models` 응답 안에 이미 담아 보내는 메타데이터를 모두 버리고 `DEFAULT_CONTEXT = 128000`으로 덮어쓴다.
   - 또한 `Ollama`의 경우 `POST <root>/api/show`를 호출하면 정확한 `num_ctx`와 `capabilities` (`"vision"`)를 얻을 수 있고, `LM Studio`는 `GET <root>/api/v1/models`, `llama.cpp`는 `GET <root>/v1/props`로 런타임 `n_ctx`를 얻을 수 있다.

2. **엔드포인트당 단 1개 모델(`probe_model`)만 포맷 프로브하여 전체 모델에 일괄 적용하는 문제**:
   - 현재 `discover_endpoint()`(`lines 843-857`)는 엔드포인트의 첫 번째 모델 하나로만 `/chat/completions`, `/messages`, `/responses`를 찔러본 뒤, 성공한 `formats` 리스트를 해당 엔드포인트의 **모든 모델에 똑같이 부여**한다(`line 863`).
   - 그러나 LiteLLM 게이트웨이에서 `claude-sonnet-4-6`는 `/v1/messages`와 `/v1/chat/completions`만 되고 `gpt-5.4`는 `/v1/responses`와 `/v1/chat/completions`가 되는 경우가 일반적이다. 첫 번째 모델이 무엇이냐에 따라 전체 엔드포인트의 `formats`가 왜곡된다.

---

### 4.3 `catalog.yaml`의 `endpoints.<name>`에 필요한 속성 스키마 설계

혼합 엔드포인트(예: 사내 LiteLLM + 로컬 Ollama/vLLM + Azure OpenAI)가 4개 에이전트 모두에서 동작하려면 `endpoints.<name>`에 다음 속성을 허용해야 한다(생략 시 기본값 또는 `detect` 결과로 채움):

```yaml
endpoints:
  corp_litellm:
    base_url: https://llm-gw.internal.example.com
    key_env: CORP_LLM_API_KEY
    kind: litellm                  # 선택 (기본: openai-compatible 또는 detect 결과)
    api_base: /v1                  # 선택 (기본: "/v1"; "/openai/v1" 또는 "" 가능)
    auth: bearer                   # 선택: bearer (기본) | api-key | x-api-key | none
    headers:                       # 선택: 엔드포인트 공통 추가 HTTP 헤더
      X-Department: engineering
    formats:                       # 선택: 엔드포인트 기본 지원 포맷 (모델별 formats 미지정 시 상속)
      - openai-completions
      - anthropic-messages
      - openai-responses

  local_ollama:
    base_url: http://127.0.0.1:11434
    kind: ollama
    api_base: /v1
    auth: none                     # auth: none 이면 key_env 생략 가능!
    formats:
      - openai-completions
      - anthropic-messages         # Ollama 0.14+ 지원

  azure_east:
    base_url: https://my-resource.openai.azure.com
    key_env: AZURE_OPENAI_KEY
    kind: azure-openai
    api_base: /openai/v1
    auth: api-key
    formats:
      - openai-completions
      - openai-responses
```

#### 필드별 정규화(`normalize_catalog`) 규칙 권고:
1. **`auth` 및 `key_env` 선택적 허용**:
   - `auth`의 기본값은 `key_env`가 있으면 `"bearer"`, `key_env`가 없고 `auth: none`이 명시되었거나 `kind in ("ollama", "vllm", "llamacpp", "lm-studio")`이면 `"none"`으로 설정한다.
   - `auth == "none"`일 때는 `key_env`를 필수 요구하지 않는다(현재 `llm_manager.py:99-100`은 무조건 `CatalogError("missing key_env")`를 발생시킴).
2. **`api_base` 보존**:
   - 현재 `endpoint_root(url)`(`llm_manager.py:878-880`)은 `/v1`만 떼어내고 `render_*`에서 무조건 `f"{base_url}/v1"`을 다시 붙인다.
   - 만약 사용자가 `base_url: https://.../openai/v1`을 입력하거나 `api_base: /openai/v1`을 지정하면, 현재 코드는 `/v1`만 떼어낸 뒤 다시 `/v1`을 붙여 `.../openai/v1`이 우연히 유지되지만 Claude Code(`ANTHROPIC_BASE_URL`)나 `api_base: ""`인 서버에서는 경로가 깨진다.
   - 따라서 `base_url`(서버 루트)과 `api_base`(기본 `"/v1"`)를 분리하여 정규화하고, 각 `render_*`는 `ep_api_url(ep) = f"{ep['base_url']}{ep['api_base']}"`를 사용해야 한다.
3. **`headers` (커스텀 헤더)**:
   - `endpoints.<name>.headers: dict[str, str]`를 허용하고, `auth == "api-key"`나 `auth == "x-api-key"`일 때 각 도구의 `headers` / `extra_headers` / `ANTHROPIC_CUSTOM_HEADERS`로 자동 변환한다.

---

### 4.4 두 엔드포인트가 동일한 모델 ID를 서빙할 때의 네이밍/별칭(Aliasing) 패턴

현재 `catalog.yaml`의 `models:`는 **모델 ID를 딕셔너리 키로 사용하는 평면 맵(flat dict)** 이다(`llm_manager.py:103-147`). 따라서 YAML 문법상 동일한 키(예: `claude-sonnet-4-6` 또는 `qwen2.5-coder-32b`)를 `ep1`과 `ep2` 두 엔드포인트에 동시에 선언할 수 없다.

멀티 엔드포인트에서 동일 모델이 두 서버에 존재할 때의 표준 해결 패턴은 다음과 같다:

1. **`catalog.yaml`에서 카탈로그 키(별칭)와 실제 전송 모델 ID(`id` / `model_id`) 분리**:
   - `models.<catalog_key>`에 선택적 `id:`(또는 `wire_id:` / `model:`) 필드를 도입한다(생략 시 `<catalog_key>`가 곧 wire ID).
   - 또는 `<endpoint>/<model_id>` 형태의 키를 허용하고, `id`가 명시되면 wire 요청에는 `id`를 사용한다.
   ```yaml
   models:
     claude-sonnet-4-6:
       endpoint: corp_litellm
       context: 200000
       max_output: 64000
       vision: true

     claude-sonnet-4-6@backup:
       id: claude-sonnet-4-6          # 실제 API에 전송할 모델 ID
       endpoint: backup_litellm
       context: 200000
       max_output: 64000
       vision: true
   ```
2. **4개 에이전트별 렌더링 방식**:
   - **`opencode2` (`opencode.json`)**:
     - `provider.<ep>.models` 딕셔너리가 이미 엔드포인트별로 격리되어 있으며, 모델 참조가 항상 `"<ep>/<wire_id>"` 형태다(`llm_manager.py:259`). 따라서 `provider.<ep>.models` 아래에는 실제 `wire_id`(`m.get("id") or mid`)를 키로 넣고, `opencode2.model` 및 `omo` 에이전트 참조는 `f"{m['endpoint']}/{m['wire_id']}"`로 렌더링하면 두 엔드포인트가 동일한 `wire_id`를 가져도 완벽히 구분된다!
   - **`omo` (`~/.omo/models.json` & `config.json`)**:
     - `models.json`의 `providers.<ep>.models` 배열 역시 엔드포인트별로 격리되어 있으므로 각 배열 항목의 `"id"`에는 `wire_id`를 넣고, `config.json`의 모델 참조(`model`, `agent_models`)는 `f"{m['endpoint']}/{m['wire_id']}"`를 사용한다.
   - **`hermes` (`config.yaml`)**:
     - `providers.<ep>.models` 딕셔너리도 엔드포인트별로 격리되어 있으므로 `wire_id`를 키로 넣는다.
     - 단, 두 엔드포인트에 동일한 `wire_id`가 등록되면 `/model <wire_id>` 단독 실행 시 모호성 에러가 나므로(§2.2.2), `catalog_key != wire_id`인 항목은 `config.yaml` 최상위 `model_aliases:`에 `{<catalog_key>: {provider: f"custom:{ep}", model: wire_id}}`로 자동 등록해 주면 `/model claude-sonnet-4-6@backup`으로 즉시 전환된다!
   - **`claude_code` (`~/.claude/settings.json`)**:
     - 단일 `target_ep`만 사용하므로 선택된 모델의 `wire_id`를 `ANTHROPIC_MODEL` 등에 기록한다.

---

### 4.5 게이트웨이/디스커버리 측 `llm_manager.py` 구체적 개선 권고안

1. **`discover_endpoint()` 메타데이터 추출 강화**:
   - `/v1/models`의 각 `item`에서 LiteLLM info가 없더라도 다음 키를 순서대로 읽어 `context`와 `max_output`, `vision`을 채운다:
     - `context`: `item.get("context_length")` (OpenRouter) → `item.get("max_model_len")` (vLLM) → `item.get("max_input_tokens")` (Anthropic) → `(item.get("meta") or {}).get("n_ctx")` (llama.cpp) → `item.get("max_context_length")` (LM Studio).
     - `max_output`: `(item.get("top_provider") or {}).get("max_completion_tokens")` (OpenRouter) → `item.get("max_completion_tokens")` → `item.get("max_output_tokens")`.
     - `vision`: `"image" in ((item.get("architecture") or {}).get("input_modalities") or [])` (OpenRouter).
   - `detect_server()` 결과 `kind == "ollama"`이면 모델별로 `POST <root>/api/show` (`{"name": mid}`)를 호출하여 `parameters`의 `num_ctx`(없으면 `model_info`의 `*.context_length`)와 `capabilities`의 `"vision"` 여부를 반영한다.
   - `kind == "lm-studio"`(또는 `/api/v1/models` 응답 성공)이면 `loaded_instances[0].config.context_length`를 읽는다.
   - `discover`가 생성하는 `catalog.yaml`의 `endpoints.<ep>`에 감지된 `kind`, `auth`, `api_base`를 함께 기록한다 (`auth == "none"`이면 `key_env` 생략).

2. **`preflight()`의 `renderable` 조건 확대**:
   - 현재 `detect_server()`(`llm_manager.py:969-970`)는 `res["renderable"] = res["auth"] in ("bearer", "none") and res["api_base"] == "/v1"`일 때만 `True`로 설정하고, 그렇지 않으면 `preflight()`가 `apply`를 차단한다(종료 코드 3).
   - §1의 매트릭스대로 각 `render_*` 함수가 `api_base`(`/openai/v1`, `""`), `auth`(`api-key`, `x-api-key`, `none`)를 렌더링하도록 업데이트한 뒤, `res["renderable"] = bool(res["models_url"])`로 완화하여 Azure OpenAI(`/openai/v1` + `api-key`)나 Anthropic 프록시(`x-api-key`)도 차단 없이 렌더링되게 한다.

---

## 5. 미확인 항목 (UNVERIFIED) 및 전체 출처 목록 (Sources)

### 5.1 미확인 항목 (`미확인 / UNVERIFIED`)

- **Claude Code의 `claude gateway` 서브커맨드 상세 설정 스키마**:
  - 확인 시도: `claude.exe gateway --help`, `claude.exe gateway status --help` 실행 및 바이너리 문자열 검색 수행. `-c, --config <path>` 옵션과 `add/remove/list/status` 서브커맨드가 존재함은 확인했으나, 로컬 환경에 게이트웨이 브리지 설정 파일 샘플이 없고 공식 문서에도 상세 스키마가 공개되어 있지 않아 슬롯별 다중 엔드포인트 라우팅 용도로 쓸 수 있는지는 **미확인 (UNVERIFIED)** 으로 분류함. (단, 기본 Claude Code 클라이언트가 단일 `ANTHROPIC_BASE_URL`만 읽는다는 점은 바이너리 심볼과 공식 문서로 확정됨.)

### 5.2 로컬 소스 및 바이너리 출처 (익명화 경로)

- **Repository 내부 파일**:
  - `llm_manager.py` (커밋 `b4db3f6`, lines 1-1187: `normalize_catalog`, `_resolve_claude_code`, `render_opencode2`, `render_omo_config`, `render_omo_models`, `render_hermes`, `render_claude_settings`, `discover_endpoint`, `detect_server`, `preflight`, `verify`)
  - `README.md`, `catalog.example.yaml`, `docs/harness-plugin-review.md`
- **Hermes Agent (`v0.19.0`, `%APPDATA%\uv\tools\hermes-agent\Lib\site-packages\`)**:
  - `hermes_cli/config_providers.py` (lines 40-76, 114-123, 238-350, 476-601: `providers:` 스키마, `_API_MODE_ALIASES`, `extra_headers`, `models` 정규화)
  - `hermes_cli/runtime_provider_custom.py` (lines 48-65, 131-146, 356-361, 521-767: `custom:<name>` 해석, keyless 처리, credential pool, `_host_gated_env_key_candidates`)
  - `hermes_cli/runtime_provider.py` (lines 425-455: 호스트명 기반 환경변수 추측)
  - `hermes_cli/model_switch.py` (lines 203-252, 396-409, 943-980, 1016-1025, 1349-1375: 런타임 `/model` 전환 및 중복 모델 ID 충돌 거부 로직)
  - `hermes_cli/config_defaults.py` (lines 725-803: 19개 `auxiliary` 태스크 기본값)
  - `agent/auxiliary_task_config.py` (lines 11-95: `AUXILIARY_TASK_KEYS`, `_TASK_CONFIG_FIELDS`)
  - `agent/auxiliary_client.py` (lines 577-582, 2467-2474, 2713-2737, 4529-4594, 4862-4902, 5411, 5541-5617, 7292-7330: `provider: main` 정규화 버그 및 `_VISION_AUTO_PROVIDER_ORDER` 유출 경로)
  - `agent/image_routing.py` (lines 114-185, 262-271, 372-395: `supports_vision` 및 `decide_image_input_mode`)
  - `hermes_cli/fallback_config.py` (lines 126-143) 및 `agent/agent_init_fallback.py` (lines 16-61: `fallback_providers` 체인)
  - `hermes_cli/config.py` (lines 1469-1495, 3666-3702: `${VAR}` 확장 및 `hermes config check`)
  - `hermes_cli/doctor_config.py` (lines 251-354, 488-511) 및 `hermes_cli/doctor_connectivity.py` (lines 410-422: `hermes doctor` 검증 범위)
  - `agent/model_metadata.py` (lines 278, 420-424, 574-594, 723-779, 815-830, 918-1084, 1410-1654: `MINIMUM_CONTEXT_LENGTH = 64_000`, 로컬/원격 서버별 메타데이터 프로브)
  - `agent/model_metadata_llamacpp.py` (lines 19-64: llama.cpp `/v1/props`, `/props`, `--ctx-size`)
  - `hermes_cli/models_local.py` (lines 138-171, 205-260: Ollama `/api/tags` 및 헤더)
  - 번들 문서: `website/docs/integrations/providers.md` (lines 1200-1460), `website/docs/user-guide/features/fallback-providers.md` (lines 1-140)
- **Claude Code (`v2.1.327`, `~/.local/bin/claude.exe`)**:
  - `claude.exe --version`, `claude.exe --help`, `claude.exe gateway --help`, 바이너리 내 `ANTHROPIC_*` / `CLAUDE_CODE_*` / `apiKeyHelper` / `headersHelper` 심볼 테이블

### 5.3 웹 공식 문서 출처 (Web Sources)

- Claude Code Documentation — *LLM Gateway Configuration*: `https://code.claude.com/docs/en/llm-gateway` (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_CUSTOM_HEADERS`, `apiKeyHelper`, LiteLLM 통합)
- Claude Code Documentation — *Model Configuration*: `https://code.claude.com/docs/en/model-config` (`ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`)
- Claude Code Documentation — *Settings & Environment Variables*: `https://code.claude.com/docs/en/settings` (`~/.claude/settings.json` `env`, `apiKeyHelper`, `CLAUDE_CODE_API_KEY_HELPER_TTL_MS`, `ANTHROPIC_BEDROCK_BASE_URL`, `ANTHROPIC_VERTEX_BASE_URL`, `ANTHROPIC_FOUNDRY_BASE_URL`)
- Ollama Documentation — *Claude Code / Anthropic Compatibility (`/v1/messages`)*: `https://docs.ollama.com/integrations/claude-code` (`ANTHROPIC_BASE_URL=http://localhost:11434`, `ANTHROPIC_AUTH_TOKEN=ollama`, `ANTHROPIC_API_KEY=""`)
- Ollama Documentation — *OpenAI Compatibility & Native API (`/api/tags`, `/api/show`)*: `https://github.com/ollama/ollama/blob/main/docs/api.md`
- LiteLLM Documentation — *Proxy Endpoints (`/v1/models`, `/v1/model/info`, `/model_group/info`, `/v1/messages`, `/v1/responses`)*: `https://docs.litellm.ai/docs/proxy/model_management`
- vLLM Documentation — *OpenAI-Compatible Server (`/v1/models`, `max_model_len`, `/version`)*: `https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html`
- llama.cpp Server Documentation — *Endpoints (`/v1/models`, `/props`, `/v1/messages`)*: `https://github.com/ggml-org/llama.cpp/tree/master/tools/server`
- LM Studio Documentation — *REST API (`/api/v0/models`, `/api/v1/models`, `loaded_instances`)*: `https://lmstudio.ai/docs/api/rest-api`
- Hermes Agent Documentation — *Providers & Custom Endpoints*: `https://hermes-agent.nousresearch.com/docs/integrations/providers`
