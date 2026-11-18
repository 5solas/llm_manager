# 다중 엔드포인트(Multi-Endpoint) 지원 조사 보고서 — OpenCode 2.x (`opencode2`) & OmO Native (`omo`)

> **작성일**: 2026-11-18
> **대상 저장소**: `llm_manager` (`commit b4db3f6`, `detect_server()` / `preflight()` / `cmd_detect()` 반영 기준)
> **조사 범위**: `opencode2` (OpenCode 2.x) 및 `omo` (oh-my-openagent Native / `senpi` 엔진)
> **조사 방법**: 공식 문서 및 스키마(`https://opencode.ai/config.json`, `https://opencode.ai/v2/docs/*`, `omo.schema.json`), 로컬 설치 바이너리(`opencode v2.0.26`, `omo 5.1.63` / `engine: senpi 2026.11.5-1`) 내장 번들 소스 및 `@code-yeongyu/senpi` · `@earendil-works/pi-ai` 패키지 정적 분석(읽기 전용)

---

## 요약: 두 도구의 다중 엔드포인트 핵심 비교

| 항목 | `opencode2` (OpenCode 2.x) | `omo` (OmO Native `5.1.63` / `senpi`) |
|---|---|---|
| 설정 파일 위치 | `~/.config/opencode/opencode.json` (+ 백그라운드 서비스 전용 `~/.config/opencode/service.json`) | `~/.omo/omo.jsonc` + `~/.omo/agent/models.json` + `~/.omo/agent/settings.json` |
| 다중 프로바이더 키 | 최상위 `"providers": { "<ep1>": {...}, "<ep2>": {...} }` (V1 `"provider"`는 deprecated이나 정규화 지원) | `models.json`의 `"providers": { "<ep1>": {...}, "<ep2>": {...} }` |
| Base URL 필드명 | `settings.baseURL` (`URL` 대문자) | `baseUrl` (`Url` 카멜케이스) |
| 환경변수 키 참조 | `"{env:VAR_NAME}"` (백그라운드 서비스 환경인 `service.json`에서 해석) | `"$VAR_NAME"`, `"${VAR_NAME}"`, `{"$env": "VAR_NAME"}`, `"!command"` (프로세스 `env`에서 해석) |
| 모델 참조 형식 | `"<provider>/<model>[#<variant>]"` (첫 번째 `/` 기준 분리, 모델 ID 내 `/` 허용) | `"<provider>/<model>"` (첫 번째 `/` 기준 분리, 모델 ID 내 `/` 허용) |
| 프로바이더 간 동일 모델 ID | 런타임은 `(providerID, modelID)`로 구분하여 **허용** (`modelID` 필드로 upstream ID 별칭 가능) | 런타임은 `(provider, id)`로 구분하여 **허용** (`upstreamModelId` 필드로 upstream ID 별칭 가능) |
| API 포맷 지정 | 프로바이더 또는 모델 단위의 `"package"` 필드 (`@opencode/ai/providers/openai-compatible`, `/openai-compatible/responses`, `/anthropic-compatible` 등) | 프로바이더 또는 모델 단위의 `"api"` 필드 (`"openai-completions"`, `"openai-responses"`, `"anthropic-messages"`, `"google-generative-ai"`) |
| 프로바이더 간 폴백(Fallback) | 일반 세션 모델은 폴백 체인 **미지원**(단일 모델 참조만 가능). 단, `agents.title`은 실패 시 현재 세션 주 모델로 폴백 | `agents.*.models` 및 `categories.*.models` 배열에 서로 다른 프로바이더 모델을 혼합 배치하여 **폴백 및 라운드로빈(`models_distribution`) 지원** |
| 프로바이더별 동시성 제한 | 없음 (요청 단위 `timeout` / `chunkTimeout`만 존재) | `omo.jsonc`의 `task.provider_concurrency` 및 `task.model_concurrency` 지원 (`[opencode].background_task`는 레거시 플러그인 전용) |

---

## Part 1. OpenCode 2.x (`opencode2`)

### 1. N개 프로바이더 확정 설정 구조 및 2개 엔드포인트 최소 동작 예시

OpenCode 2.x의 네이티브 스키마(`https://opencode.ai/config.json`)는 최상위 `"providers"`(복수형) 객체 아래에 엔드포인트 이름(`^[a-zA-Z0-9_-]+$`, `/` 및 `#` 불가)을 키로 하여 여러 커스텀 프로바이더를 나란히 선언한다.

아래는 **(A) 사내 LiteLLM 게이트웨이(`corp`, Bearer 인증, `/v1` 경로, Chat Completions + Responses 혼합)** 와 **(B) 로컬 vLLM/Ollama 서버(`local`, 무인증 keyless, `/v1` 경로)** 2개 엔드포인트를 동시에 구성한 최소 동작 예시이다.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "corp/glm-5.1",
  "agents": {
    "title": {
      "model": "local/Qwen/Qwen3-Coder-30B"
    }
  },
  "providers": {
    "corp": {
      "name": "corp",
      "package": "@opencode/ai/providers/openai-compatible",
      "env": ["CORP_API_KEY"],
      "settings": {
        "baseURL": "https://gw.internal.example.com/v1",
        "apiKey": "{env:CORP_API_KEY}",
        "timeout": 120000
      },
      "models": {
        "glm-5.1": {
          "name": "glm-5.1",
          "limit": { "context": 200000, "output": 32000 },
          "capabilities": {
            "reasoning": { "supported": true },
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          }
        },
        "gpt-5.4": {
          "name": "gpt-5.4",
          "package": "@opencode/ai/providers/openai-compatible/responses",
          "limit": { "context": 272000, "output": 64000 },
          "capabilities": {
            "reasoning": { "supported": true },
            "tools": true,
            "input": ["text", "image"],
            "output": ["text"]
          }
        },
        "claude-sonnet-4-6": {
          "name": "claude-sonnet-4-6",
          "compatibility": { "cacheFormat": "anthropic" },
          "limit": { "context": 200000, "output": 64000 },
          "capabilities": {
            "reasoning": { "supported": true },
            "tools": true,
            "input": ["text", "image"],
            "output": ["text"]
          }
        }
      }
    },
    "local": {
      "name": "local",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "http://127.0.0.1:8000/v1"
      },
      "models": {
        "Qwen/Qwen3-Coder-30B": {
          "name": "Qwen/Qwen3-Coder-30B",
          "limit": { "context": 128000, "output": 16384 },
          "capabilities": {
            "reasoning": { "supported": false },
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          }
        }
      }
    }
  },
  "policies": [
    { "action": "provider.use", "resource": "*", "effect": "deny" },
    { "action": "provider.use", "resource": "corp", "effect": "allow" },
    { "action": "provider.use", "resource": "local", "effect": "allow" }
  ],
  "experimental": {
    "policies": [
      { "action": "provider.use", "resource": "*", "effect": "deny" },
      { "action": "provider.use", "resource": "corp", "effect": "allow" },
      { "action": "provider.use", "resource": "local", "effect": "allow" }
    ]
  },
  "update": { "mode": "disabled" }
}
```

---

### 2. 조사 항목별 상세 답변 및 근거 출처

#### (1) 다중 커스텀 프로바이더 스키마 구조 (`providers` vs `provider`, `package` vs `npm`, `settings.baseURL`, `{env:NAME}`) 및 `render_opencode()` 현행 출력 대조
- **V2 네이티브 스키마 vs V1 레거시 스키마**:
  - **V2 네이티브 형식**(`https://opencode.ai/config.json`의 `$defs/Config.ProviderEncoded`, 2821~2862행 및 `https://opencode.ai/v2/docs/providers#custom-provider`):
    - 최상위 키: `"providers"` (복수형)
    - 프로바이더 필드: `package`, `name`, `env` (환경변수명 배열), `canonical`, `compatibility`, `settings` (`baseURL`, `apiKey`, `timeout`, `headerTimeout`, `chunkTimeout`, `compaction`, `cache`, `transport`, `encoding`, `tls`), `headers`, `body`, `models`
    - 모델 필드(`$defs/Config.ModelEncoded`, 2713~2820행): `modelID` (실제 요청 시 전송할 upstream 모델 ID), `name`, `family`, `package`, `releaseDate`, `attachment`, `cost`, `limit` (`context`, `input`, `output`), `capabilities` (`temperature`, `reasoning`, `tools`, `interleaved`, `input`, `output`), `compatibility` (`cacheFormat`, `thinkingFormat`, `reasoningEffort`, `maxTokensField`, `developerRole`), `settings`, `headers`, `body`, `variants`, `status`, `experimental`
  - **V1 레거시 형식**(`https://opencode.ai/config.json` 858~865행, 1141~1463행 및 `https://opencode.ai/v2/docs/migrate-v1`):
    - 최상위 키 `"provider"`(단수형), `"npm": "@ai-sdk/openai-compatible"`, `"options": {"baseURL": ..., "apiKey": ...}`, 모델 내 `"tool_call"`, `"modalities"` 등은 스키마상 `"deprecated": true`로 표시되어 있다.
    - 로컬 바이너리(`opencode.exe` v2.0.26 내장 번들 `bx(e)` / `Nx(e, r)` / `hd(e)`) 확인 결과, OpenCode 2.x는 런타임에 V1 `"provider"`를 읽어 V2 `"providers"`로 인메모리 정규화한다(`@ai-sdk/openai-compatible` → `aisdk:@ai-sdk/openai-compatible` → 매핑 테이블 `ks`를 통해 `@opencode/ai/providers/openai-compatible`로 변환).
    - 단, 동일한 프로바이더 키가 `"provider"`와 `"providers"` 양쪽에 존재하면 `"providers"`(V2 네이티브)가 우선하며(`"retained native value over legacy value"` 진단 발생), **단일 프로바이더나 단일 모델 엔트리 내부에서 V1 필드(`npm`, `options`)와 V2 필드(`package`, `settings`)를 혼용하면 파싱 오류가 발생**한다(`https://opencode.ai/v2/docs/migrate-v1`).
- **`llm_manager.py`의 `render_opencode()` 현행 출력 검증**:
  - 현재 `render_opencode()`(`llm_manager.py:446-484`)가 출력하는 `"providers"`, `"package": "@opencode/ai/providers/openai-compatible"`, `"settings": {"baseURL": ..., "apiKey": "{env:...}"}`, `"limit": {"context": ..., "output": ...}`, `"capabilities": {"tools": ..., "input": ..., "output": ["text"]}`는 모두 현행 V2 스키마에 부합한다.
  - **누락/차이점 3가지**:
    1. `capabilities.reasoning`: 카탈로그에 `reasoning: true`가 있어도 `render_opencode()`가 `"reasoning": {"supported": true}`(`Config.Model.Capabilities.Reasoning`, `config.json` 2556~2605행)를 출력하지 않아 OpenCode가 커스텀 모델의 추론(Thinking) 지원 여부를 알지 못한다.
    2. `compatibility.cacheFormat`: OpenAI 호환 게이트웨이(LiteLLM 등) 뒤의 Claude 모델은 `"compatibility": {"cacheFormat": "anthropic"}`을 설정해야 OpenCode가 메시지 블록에 `cache_control: {"type": "ephemeral"}`을 주입한다(`https://opencode.ai/v2/docs/models#prompt-caching`).
    3. `experimental.policies` vs `policies`: 아래 (4)항에서 상세 설명.

#### (2) 두 번째(N번째) 프로바이더의 API 키 전달 방식 및 백그라운드 서비스(`opencode service`) 동작
- **출처**: `https://opencode.ai/v2/docs/config#env-vars`, `https://opencode.ai/v2/docs/troubleshooting#service-files`, `https://opencode.ai/v2/docs/cli/providers#environment`, 로컬 `opencode.exe` v2.0.26 번들(`cli.service-config.read` / `cli.service-config.set` / `Id` 치환 함수)
- **동작 메커니즘 확인**:
  1. OpenCode 2.x는 클라이언트-서버 구조로 동작한다. `--standalone` 플래그 없이 `opencode` TUI나 `opencode run`, `opencode models`를 실행하면 백그라운드 데몬(`opencode serve --service`, 상태 파일 `~/.local/state/opencode/service.json`)에 연결한다.
  2. 설정 파일(`opencode.json`) 내의 `"{env:NAME}"` 치환(`Id` 함수)은 CLI 클라이언트가 아니라 **백그라운드 서비스 프로세스의 `process.env`** 에서 수행되며, 해당 환경변수가 없으면 빈 문자열(`""`)로 치환된다(`process.env[r] ?? ""`).
  3. 백그라운드 서비스 전용 환경변수는 `~/.config/opencode/service.json`의 `{"env": {"NAME": "value"}}`에 별도 저장된다.
  4. `opencode service set env <KEY_ENV> "<값>"`을 실행하면 실행 중인 백그라운드 서비스를 즉시 중지(`Mt.stop`)하고 `~/.config/opencode/service.json`의 `env` 맵에 해당 키를 기록한다. 이후 다음 `opencode` 명령 실행 시 서비스가 재기동되면서 `~/.config/opencode/service.json`의 모든 `env` 항목이 서버 프로세스 환경에 주입된다.
- **추가 엔드포인트당 필요한 조치**:
  - 인증이 필요한 엔드포인트마다 각자의 `key_env`를 **개별적으로** 백그라운드 서비스에 등록해야 한다:
    ```sh
    opencode service set env CORP_API_KEY "<key1>"
    opencode service set env AZURE_API_KEY "<key2>"
    ```
  - 또한 프로바이더 정의에 `"env": ["CORP_API_KEY"]` 배열을 함께 넣으면 `opencode auth list`의 Environment 섹션에서 해당 프로바이더의 환경변수 연동 상태를 확인할 수 있다(`https://opencode.ai/v2/docs/cli/providers#environment`).
  - 현재 `llm_manager.py`의 `key_instructions()`(`llm_manager.py:647-657`)는 모든 엔드포인트의 `key_env` 집합을 순회하며 안내하고 있으나, **무인증(keyless) 엔드포인트(`auth: none`)가 추가될 경우 `key_env`가 없는 엔드포인트는 건너뛰도록 수정**해야 한다.

#### (3) 두 프로바이더 간 동일 모델 ID 허용 여부 및 모델 참조(`provider/model`, `/` 포함 모델 ID) 규칙
- **출처**: `https://opencode.ai/v2/docs/models#references`, `https://opencode.ai/v2/docs/models#aliases`, `https://opencode.ai/config.json` 18행(`$defs/ModelRef`), `opencode.exe` 번들(`gd(e, r)` 파서 및 `hm` 프로바이더 서비스)
- **프로바이더 간 동일 모델 ID**:
  - OpenCode 2.x 자체는 각 프로바이더의 모델을 `providers.<providerID>.models.<modelID>` 아래에 독립된 `Map`으로 관리하며, 내부 모델 레지스트리도 `(providerID, modelID)` 쌍으로 조회하므로 **서로 다른 두 프로바이더(`corp`, `backup`) 아래에 동일한 모델 ID(`glm-5.1`)가 동시에 존재해도 충돌하지 않는다**.
  - 또한 `opencode.json`의 모델 키는 로컬 별칭(예: `"glm-5.1-backup"`)으로 두고 모델 객체 내부에 `"modelID": "glm-5.1"`을 지정하면 서버 요청 시에는 `"glm-5.1"`이 전송된다(`https://opencode.ai/v2/docs/models#aliases`).
  - **주의(`llm_manager.py` 측 제약)**: 현재 `catalog.yaml`은 최상위 `models:` 딕셔너리의 키를 곧바로 모델 ID(`mid`)로 사용하므로, `catalog.yaml`에서 두 엔드포인트에 같은 모델 이름을 등록하려면 모델 항목에 선택적 `model_id`(upstream ID) 필드를 도입해야 한다.
- **`/`가 포함된 모델 ID 참조 방식**:
  - `config.json` 18행의 `ModelRef` 정규식은 `^[^/#]+\/[^#]+(?:#[^#]+)?$`이며, 내부 파서 `gd(e, r)`는 `e.indexOf("/")`로 **첫 번째 `/`만** 기준으로 삼아 앞부분을 `providerID`, 뒷부분(`#` 이전까지)을 `model`로 분리한다.
  - 따라서 프로바이더 ID에는 `/`와 `#`을 쓸 수 없지만, **모델 ID에는 `/`가 여러 개 포함되어도 문자열 참조(`"local/Qwen/Qwen3-Coder-30B"` 또는 `"corp/deepseek-ai/DeepSeek-V3.2"`)와 객체 참조(`{"providerID": "local", "model": "Qwen/Qwen3-Coder-30B"}`) 모두 정상 동작**한다.
  - 단, 최상위 `"model"` 필드는 `#variant` 접미사를 유지하지 않으며, 에이전트/커맨드 모델(`agents.<name>.model`)에서만 `#variant`가 유지된다(`https://opencode.ai/v2/docs/models#defaults`).

#### (4) 다중 프로바이더 허용/차단 정책 (`policies`, `experimental.policies`, `enabled_providers`, `disabled_providers`)
- **출처**: `https://opencode.ai/v2/docs/policies`, `https://opencode.ai/v2/docs/config#deprecated-policies`, `https://opencode.ai/config.json` 600~661행 · 871~929행, `opencode.exe` v2.0.26 번들(`_x` 및 `Ax` 함수)
- **평가 규칙**:
  - 정책 배열을 위에서 아래로 평가하며 **마지막으로 매칭된 규칙이 최종 적용(`findLast`)** 된다. `resource`에는 `*`, `?` 글로브 패턴을 사용할 수 있다.
  - 다중 엔드포인트(`ep1`, `ep2`, ...) 사용 시 첫 줄에 `{"action": "provider.use", "resource": "*", "effect": "deny"}`를 두고 이어서 각 엔드포인트에 대해 `{"action": "provider.use", "resource": "<ep>", "effect": "allow"}`를 나열하면 카탈로그에 정의된 N개 프로바이더만 활성화되고 내장 외부 프로바이더(`opencode`, `anthropic`, `openai` 등)는 모두 차단된다.
- **`enabled_providers` / `disabled_providers`의 존재 및 동작**:
  - `config.json` 스키마(871~886행)에 deprecated 필드로 존재한다.
  - `opencode.exe`의 `_x(e, r, i)` 함수를 확인한 결과, `enabled_providers: ["corp", "local"]`은 내부적으로 `[{action: "provider.use", resource: "*", effect: "deny"}, {action: "provider.use", resource: "corp", effect: "allow"}, {action: "provider.use", resource: "local", effect: "allow"}]`로 변환되어 정책 배열 앞단에 추가된다.
- **버전별 중요 차이 (`experimental.policies` vs 최상위 `policies`)**:
  - 로컬에 설치된 **OpenCode `v2.0.26`** 바이너리의 설정 정규화 함수 `bx(e)`는 **최상위 `"policies"` 키를 아직 읽지 않고 `"experimental": {"policies": [...]}`만 읽는다**!
  - 반면 **`v2.0.29` 이후(현행 v2.3 문서 및 `https://opencode.ai/config.json`)** 에서는 최상위 `"policies"`가 정식 키로 승격되었고 `"experimental.policies"`는 deprecated 처리되어 런타임에 `"policies"`로 병합된다(`https://opencode.ai/v2/docs/config#deprecated-policies`).
  - 따라서 `v2.0.26`과 최신 `v2.3+` 양쪽에서 모두 정책이 작동하려면 **`"policies"`와 `"experimental": {"policies": [...]}`에 동일한 배열을 둘 다 출력**하거나(v2.0.26은 미인식 최상위 키를 `onExcessProperty: "ignore"`로 무시하고, v2.3+는 두 배열을 순서대로 이어붙이므로 `deny *` → `allow ep1` → `allow ep2` 결과가 멱등임), 최소한 `experimental.policies`를 제거하지 말아야 한다.

#### (5) 혼합 엔드포인트를 위한 프로바이더별/모델별 옵션 (커스텀 헤더, 타임아웃, API 포맷 패키지, 모델별 오버라이드)
- **출처**: `https://opencode.ai/config.json` 2441~2862행, `https://opencode.ai/v2/docs/providers`, `https://opencode.ai/v2/docs/models`, `opencode.exe` 번들(`@opencode/ai/providers/*` 라우트 테이블)
- **지원되는 API 포맷(`package` 필드)**:
  `providers.<ep>.package`에 지정하며, 같은 엔드포인트 안에서 특정 모델만 다른 API 포맷을 쓰도록 `providers.<ep>.models.<mid>.package`에서 **모델별로 오버라이드**할 수 있다(`lO` 함수 확인).
  | 용도 / 프로토콜 | OpenCode V2 `package` 값 | 요청 경로 조합 방식 (`opencode.exe` 내부 라우트) | 기본 인증 헤더 |
  |---|---|---|---|
  | OpenAI Chat Completions | `"@opencode/ai/providers/openai-compatible"` | `settings.baseURL` + `"/chat/completions"` | `Authorization: Bearer <apiKey>` (`apiKey` 생략 시 무인증/커스텀 헤더 허용) |
  | OpenAI Responses API | `"@opencode/ai/providers/openai-compatible/responses"` (또는 `openai-compatible-responses`) | `settings.baseURL` + `"/responses"` | `Authorization: Bearer <apiKey>` |
  | Anthropic Messages API (호환 게이트웨이/프록시) | `"@opencode/ai/providers/anthropic-compatible"` | `settings.baseURL` + `"/messages"` | `x-api-key: <apiKey>` + `anthropic-version: 2023-06-01` 자동 추가 |
  | Anthropic 공식 API | `"@opencode/ai/providers/anthropic"` | 기본 `https://api.anthropic.com/v1` + `"/messages"` | `x-api-key: <apiKey>` |
  | OpenAI 공식 API | `"@opencode/ai/providers/openai"` (기본 `/responses`) 또는 `"@opencode/ai/providers/openai/chat"` | 기본 `https://api.openai.com/v1` + `"/responses"` 또는 `"/chat/completions"` | `Authorization: Bearer <apiKey>` |
  | Azure OpenAI | `"@opencode/ai/providers/azure/chat"` 또는 `"@opencode/ai/providers/azure/responses"` | 호스트명이 `*.openai.azure.com`이면 `/v1` 및 `?api-version=v1` 자동 부착 (주의 필요) | `api-key: <apiKey>` |
- **커스텀 헤더 / 바디 / 타임아웃 / TLS**:
  - `headers`: `{"Header-Name": "{env:VAR}"}` 형태로 프로바이더(`providers.<ep>.headers`) 및 모델(`providers.<ep>.models.<mid>.headers`) 양쪽에 지정 가능하며 병합된다.
  - `body`: 요청 JSON 바디에 병합할 추가 필드(`providers.<ep>.body` 및 `models.<mid>.body`).
  - `settings.timeout` (전체 요청 타임아웃 ms), `settings.headerTimeout` (응답 헤더 수신 타임아웃 ms), `settings.chunkTimeout` (스트리밍 청크 간 타임아웃 ms), `settings.tls` (`ca`, `cert`, `key`, `rejectUnauthorized`).
  - `compatibility`: 모델 또는 프로바이더 단위로 `cacheFormat: "anthropic"`, `thinkingFormat` (`"openai"` | `"zai"` | `"qwen"` | `"qwen-chat-template"`), `reasoningEffort` (`boolean`), `maxTokensField` (`"max_completion_tokens"` | `"max_tokens"`), `developerRole` (`boolean`) 설정 가능.

#### (6) Small-model / `title` 에이전트 선택 및 프로바이더 간 폴백 동작
- **출처**: `https://opencode.ai/v2/docs/migrate-v1`, `https://opencode.ai/v2/docs/agents#built-ins`, `opencode.exe` 번들(`SessionContext.selectTitle`, `SessionContext.load`, `SessionRunnerModel.resolve`)
- **`small_model` → `agents.title.model`**:
  - V1의 최상위 `small_model`은 V2에서 deprecated되었고 `bx(e)` 정규화 시 `agents.title.model`로 매핑된다. `render_opencode()`가 현재 생성하는 `"agents": {"title": {"model": "<ep>/<tier3_model>"}}`이 V2 정식 방식이다.
  - 그 외 내장 유지보수 에이전트로 `agents.summary`(세션 요약)와 `agents.compaction`(컨텍스트 압축)이 있으며, 별도 `model`을 지정하지 않으면 현재 세션의 주 모델을 그대로 사용한다.
- **`title` 에이전트와 주 모델이 서로 다른 프로바이더에 있을 때 및 폴백 동작**:
  - `SessionContext.selectTitle` 구현(`opencode.exe` 내장 번들) 확인 결과:
    1. `agents.title.model`이 설정되어 있으면 해당 `(providerID, modelID)`를 먼저 조회하고 `SessionRunnerModel.resolve`로 인증/활성화를 확인한다. (주 모델과 **다른 프로바이더**여도 무방하다.)
    2. 만약 `agents.title.model`이 지정된 프로바이더의 키가 없거나 비활성화되어 `resolve` 단계에서 실패하면, `.pipe(Ye(() => { return }))`로 에러를 잡은 뒤 `?? J`를 통해 **현재 세션의 주 모델(`J`)로 자동 폴백**한다!
    3. 반대로 `agents.title.model`을 아예 생략하면, 현재 세션 주 모델과 **동일한 프로바이더(`J.ref.providerID`)** 안에서 `i.small(J.ref.providerID)`로 경량 모델을 찾고, 없으면 주 모델(`J`)을 사용한다.
  - **일반 요청의 프로바이더 간 폴백 한계**:
    - OpenCode 2.x의 `model` 및 `agents.*.model`은 단일 모델 참조만 받으며 배열(폴백 체인)을 지원하지 않는다. 또한 `agents.title.model`의 폴백도 **모델/인증 해석(`resolve`) 단계**에서만 동작하며, 실제 HTTP 스트리밍 호출 중 원격 서버가 5xx/연결 거부로 실패할 때는 동일 엔드포인트에 최대 3회 재시도할 뿐 다른 프로바이더로 자동 전환하지 않는다.

---

### 3. OpenCode 2.x 주의사항 및 버전 차이 (Pitfalls)

1. **`opencode.json`과 `opencode.jsonc` 동시 존재 시 설정 덮어쓰기 함정**:
   - `opencode.exe`의 `Config.load`는 같은 디렉터리에서 `["opencode.json", "opencode.jsonc"]` 두 파일을 **순서대로 모두 읽는다**.
   - 이때 `providers` 배열(`fx`)은 두 파일의 내용을 병합(`flatMap`)하지만, `model`, `update` 같은 단일 스칼라 설정(`YE`)은 `findLast`로 **나중에 읽힌 `opencode.jsonc`의 값을 우선**한다.
   - 만약 사용자의 `~/.config/opencode/`에 구버전 `opencode.jsonc`가 남아 있으면 `llm_manager.py`가 `opencode.json`을 갱신해도 `opencode.jsonc`가 설정을 가로챌 수 있으므로, `llm_manager.py`는 `~/.config/opencode/opencode.jsonc`가 존재할 때 경고(warn)해야 한다.
2. **무인증(keyless) 및 커스텀 헤더 전용 인증은 `@opencode/ai/providers/openai-compatible` 패키지에서만 무키 동작이 보장됨**:
   - `opencode.exe`의 `ModelResolver.resolveModel`은 `settings.apiKey`가 없을 때 `bE(k.package)`(`r === "@opencode/ai/providers/openai-compatible"`)인 경우에만 기본 Bearer 인증을 no-op(`UA`)으로 대체한다.
   - `"@opencode/ai/providers/openai-compatible/responses"` 패키지는 `bE` 대상에 포함되지 않아 `settings.apiKey`를 생략하면 `"missing apiKey"` 오류가 발생한다. 따라서 Responses API를 쓰는 엔드포인트에서 무인증이나 커스텀 헤더 인증을 써야 한다면 `settings.apiKey`에 더미 값(예: `"unused"`) 또는 `{env:KEY}`를 함께 넣어야 한다.
3. **내장 로컬 프로바이더 자동 탐색(`ollama`, `vllm`, `lmstudio`)과의 이름 충돌**:
   - OpenCode 2.x는 `ollama`(`127.0.0.1:11434`), `vllm`(`127.0.0.1:8000`), `lmstudio`(`127.0.0.1:1234`)라는 이름의 내장 자동 탐색 플러그인을 가진다. 특히 내장 `vllm` 플러그인은 자동 탐색된 모델의 `capabilities.tools`를 기본 `false`로 설정한다.
   - 카탈로그에서 엔드포인트 이름을 `vllm`이나 `ollama`로 지을 경우 `providers.<name>.models`에 모델별 `capabilities.tools`를 명시적으로 선언해야 내장 기본값에 덮어쓰이지 않는다.

---

### 4. `llm_manager.py`의 OpenCode 2.x 렌더링 및 검증 권고안 (`detect_server()` 연동)

#### (1) `detect_server()` 감지 결과별 `render_opencode()` 표현 방법

| 감지 항목 (`detect_server()`) | 값 | OpenCode 2.x (`opencode.json`) 렌더링 방법 |
|---|---|---|
| **API 경로 (`det["api_base"]`)** | `"/v1"`, `"/openai/v1"`, `""` | `@opencode/ai/providers/openai-compatible`(`/chat/completions` 부착), `.../openai-compatible/responses`(`/responses` 부착), `.../anthropic-compatible`(`/messages` 부착) 모두 `settings.baseURL` 뒤에 엔드포인트 리소스 경로만 바로 붙인다. 따라서 하드코딩된 `ep["root"] + "/v1"` 대신 **`ep["root"] + (det["api_base"] or "/v1")`** (단, `det["api_base"] == ""`이면 `ep["root"]` 그대로)로 렌더링한다. |
| **인증 방식 (`det["auth"]`)** | `"bearer"` | 현행과 동일: `"settings": {"baseURL": ..., "apiKey": "{env:%s}" % ep["key_env"]}` |
| | `"api-key"` (예: Azure OpenAI / APIM 게이트웨이) | `package`를 `"@opencode/ai/providers/openai-compatible"`로 두고, **`settings.apiKey`를 생략**한 채 프로바이더 최상위에 `"headers": {"api-key": "{env:%s}" % ep["key_env"]}`를 설정한다. (`settings.apiKey`를 생략하면 `ModelResolver`가 `Authorization: Bearer` 헤더 생성을 건너뛰고 `headers`의 `api-key`만 전송함.) |
| | `"x-api-key"` | - **Anthropic Messages API(`kind == "anthropic"` 또는 모델 포맷이 `anthropic-messages`)인 경우**: `"package": "@opencode/ai/providers/anthropic-compatible"`, `"settings": {"baseURL": ep["root"] + det["api_base"], "apiKey": "{env:%s}" % ep["key_env"]}`로 설정하면 자동으로 `x-api-key` 헤더와 `anthropic-version: 2023-06-01`이 전송된다.<br>- **OpenAI Chat Completions 게이트웨이가 `x-api-key`를 요구하는 경우**: `"package": "@opencode/ai/providers/openai-compatible"`에서 `settings.apiKey`를 생략하고 `"headers": {"x-api-key": "{env:%s}" % ep["key_env"]}`로 설정한다. |
| | `"none"` (무인증 keyless: 로컬 `ollama`, `vllm` 등) | `"package": "@opencode/ai/providers/openai-compatible"`에서 **`settings` 내부의 `apiKey` 키 자체를 생략**한다(`"settings": {"baseURL": ep["root"] + det["api_base"]}`). OpenCode v2 공식 문서(`https://opencode.ai/v2/docs/models#local`) 및 `ModelResolver.resolveModel`에서 확인된 정식 keyless 표현 방식이다. |
| **엔드포인트 종류 (`det["kind"]`) 및 API 포맷** | `anthropic` / `openai-responses` / 혼합 게이트웨이 | - 엔드포인트 전체가 Anthropic 호환이면 프로바이더 `package`를 `"@opencode/ai/providers/anthropic-compatible"`로 설정한다.<br>- LiteLLM 등 하나의 게이트웨이 안에서 일부 모델(예: `gpt-5.4`)만 Responses API를 쓰거나 Claude 모델이 섞여 있으면, 모델 엔트리(`providers.<ep>.models.<mid>`)에 `"package": "@opencode/ai/providers/openai-compatible/responses"` 또는 `"compatibility": {"cacheFormat": "anthropic"}`을 모델별로 부여한다.<br>- 로컬 `vllm` / `ollama`(`kind in ("vllm", "ollama")`)의 경우 `developer` 역할을 거부하는 모델 호환성을 위해 프로바이더 또는 모델에 `"compatibility": {"developerRole": false}` 추가를 권장한다. |

> **Azure OpenAI(`kind == "azure-openai"`) 렌더링 시 주의사항**:
> `@opencode/ai/providers/azure/chat` 패키지는 `baseURL`의 호스트명이 `*.openai.azure.com`일 때만 내부적으로 `/v1`과 `?api-version=v1`을 강제 부착하고, 커스텀 도메인이나 `.cognitiveservices.azure.com`에서는 부착하지 않는 비일관적 동작(`chunk-w5h51q23.js`)을 보인다. 따라서 `detect_server()`가 `/openai/v1`을 감지한 Azure 엔드포인트는 `@opencode/ai/providers/openai-compatible` + `"baseURL": ep["root"] + "/openai/v1"` + `"headers": {"api-key": "{env:...}"}` 조합으로 렌더링하는 것이 도메인 형태에 무관하게 가장 일관되게 동작한다.

#### (2) `render_opencode()`에서 2개 이상 엔드포인트 시 추가로 변경/검증할 사항
1. **`policies`와 `experimental.policies` 동시 출력**: `v2.0.26`과 `v2.0.29+` 모두에서 다중 프로바이더 허용 정책이 적용되도록 동일한 `provider.use` 배열을 최상위 `"policies"`와 `"experimental": {"policies": ...}` 양쪽에 출력한다.
2. **모델 `capabilities.reasoning` 반영**: `m["reasoning"]`이 `True`이면 `capabilities`에 `"reasoning": {"supported": true}`를 포함하고, `False`이면 `"reasoning": {"supported": false}`를 명시한다.
3. **무인증 엔드포인트 지원**: `normalize_catalog()`에서 엔드포인트에 `auth: none`(또는 `key_env` 생략)을 허용하고, `key_env`가 없는 엔드포인트는 `settings.apiKey` 및 `opencode service set env` 안내에서 제외한다.
4. **검증 및 경고(`verify` / `preflight`)**:
   - `~/.config/opencode/opencode.jsonc` 파일이 함께 존재하면 설정 우선순위 충돌 경고를 출력한다.
   - `~/.config/opencode/service.json`을(읽기 가능한 경우) 확인하여 인증이 필요한 모든 엔드포인트의 `key_env`가 백그라운드 서비스의 `env` 맵에 등록되어 있는지 점검하고, 누락된 키마다 `opencode service set env <KEY_ENV> ...` 명령을 안내한다.

---

## Part 2. OmO Native (`omo` — oh-my-openagent `5.1.63` / `senpi` 엔진)

### 1. N개 프로바이더 확정 설정 구조 및 2개 엔드포인트 최소 동작 예시

OmO Native는 세 파일(`~/.omo/agent/models.json`, `~/.omo/agent/settings.json`, `~/.omo/omo.jsonc`)이 함께 맞물려 동작한다.
아래는 **(A) 사내 LiteLLM 게이트웨이(`corp`, Bearer 인증)** 와 **(B) 로컬 vLLM 서버(`local`, 무인증 keyless)** 2개 엔드포인트를 동시에 구성한 최소 동작 예시이다.

#### (1) `~/.omo/agent/models.json`
```json
{
  "providers": {
    "corp": {
      "baseUrl": "https://gw.internal.example.com/v1",
      "apiKey": "$CORP_API_KEY",
      "api": "openai-completions",
      "models": [
        {
          "id": "glm-5.1",
          "name": "glm-5.1",
          "reasoning": true,
          "input": ["text"],
          "contextWindow": 200000,
          "maxTokens": 32000
        },
        {
          "id": "gpt-5.4",
          "name": "gpt-5.4",
          "api": "openai-responses",
          "reasoning": true,
          "input": ["text", "image"],
          "contextWindow": 272000,
          "maxTokens": 64000
        },
        {
          "id": "claude-sonnet-4-6",
          "name": "claude-sonnet-4-6",
          "reasoning": true,
          "input": ["text", "image"],
          "contextWindow": 200000,
          "maxTokens": 64000,
          "compat": {
            "cacheControlFormat": "anthropic"
          }
        }
      ]
    },
    "local": {
      "baseUrl": "http://127.0.0.1:8000/v1",
      "apiKey": "local-no-key",
      "api": "openai-completions",
      "compat": {
        "supportsDeveloperRole": false
      },
      "models": [
        {
          "id": "Qwen/Qwen3-Coder-30B",
          "name": "Qwen/Qwen3-Coder-30B",
          "reasoning": false,
          "input": ["text"],
          "contextWindow": 128000,
          "maxTokens": 16384
        }
      ]
    }
  }
}
```

#### (2) `~/.omo/agent/settings.json`
```json
{
  "defaultProvider": "corp",
  "defaultModel": "glm-5.1"
}
```

#### (3) `~/.omo/omo.jsonc`
```json
{
  "$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json",
  "model_profile": "custom",
  "agents": {
    "sisyphus": {
      "model": "corp/glm-5.1",
      "models": ["corp/glm-5.1", "corp/gpt-5.4", "local/Qwen/Qwen3-Coder-30B"]
    },
    "explore": {
      "model": "local/Qwen/Qwen3-Coder-30B",
      "models": ["local/Qwen/Qwen3-Coder-30B", "corp/glm-5.1"]
    }
  },
  "categories": {
    "quick": {
      "model": "local/Qwen/Qwen3-Coder-30B",
      "models": ["local/Qwen/Qwen3-Coder-30B", "corp/glm-5.1"]
    },
    "deep": {
      "model": "corp/gpt-5.4",
      "models": ["corp/gpt-5.4", "corp/glm-5.1"]
    }
  },
  "task": {
    "default_concurrency": 5,
    "provider_concurrency": {
      "corp": 5,
      "local": 2
    }
  }
}
```

---

### 2. 조사 항목별 상세 답변 및 근거 출처

#### (1) `models.json` 프로바이더 엔트리 전체 지원 필드 (`baseUrl`, `apiKey`, `api`, `headers`, `authHeader`, `compat` 등)
- **출처**: `@code-yeongyu/senpi/dist/core/model-config-schema.js` (15~230행), `@code-yeongyu/senpi/docs/models.md` (1~736행)
- **`models.json` 최상위 필드 (`ModelsConfigSchema`, 226~229행)**:
  - `providers`: `Record<string, ProviderConfig>` — 여러 프로바이더를 `"providers": { "ep1": {...}, "ep2": {...} }` 형태로 나란히 배치한다.
  - `disabledProviders`: `string[]` (선택) — 비활성화할 내장/확장 프로바이더 ID 목록.
- **프로바이더 객체 필드 (`ProviderConfigSchema`, 203~224행)**:
  | 필드명 | 타입 | 설명 및 동작 (`senpi` 소스 기준) |
  |---|---|---|
  | `name` | `string` | 표시용 이름 (생략 시 프로바이더 키 사용) |
  | `disabled` | `boolean` | `true`일 경우 해당 프로바이더 전체 비활성화 (`model-runtime.js:337`) |
  | `baseUrl` | `string` | 엔드포인트 Base URL (**주의**: `baseUrl` 카멜케이스. `models` 정의 시 필수) |
  | `apiKey` | `NonemptyConfigValue` |인증 키. 지원 형식:<br>1) `"$ENV_VAR"` (`^[A-Z_][A-Z0-9_]*$` 패턴일 때만 환경변수 참조로 인식)<br>2) `"${ENV_VAR}"` (문자열 보간)<br>3) `{"$env": "ENV_VAR"}` 또는 `{"$file": "~/path"}` 또는 `{"$concat": [...]}` 객체 참조<br>4) `"!command"` (셸 명령 표준출력 실행 결과)<br>5) 그 외 일반 문자열은 리터럴 API 키로 취급 (`resolve-config-value.js`, `config-reference.js`) |
  | `api` | `string` | 프로바이더 기본 와이어 프로토콜. 허용 enum: `"openai-completions"`, `"openai-responses"`, `"anthropic-messages"`, `"google-generative-ai"` (`ApiEnum`, 52~57행) |
  | `headers` | `Record<string, ConfigValue>` | 모든 요청에 추가할 커스텀 HTTP 헤더. 값에 `"$ENV_VAR"`, `"${ENV_VAR}"`, `{"$env": ...}`, `"!cmd"` 모두 사용 가능 |
  | `authHeader` | `boolean` | **주의: 문자열이 아니라 `boolean` 타입!** `true`로 설정하면 해석된 `apiKey`를 가지고 `Authorization: Bearer <apiKey>` 헤더를 강제로 주입한다 (`provider-api-key-auth.js:186-190`) |
  | `extraBody` | `Record<string, unknown>` | 요청 JSON 페이로드에 병합할 추가 필드 |
  | `cacheRetention` | `"none"` \| `"short"` \| `"long"` | 프롬프트 캐시 보존 정책 |
  | `compat` | `ProviderCompat` | 프로바이더 단위 호환성 플래그 객체 (모델 단위 `compat`와 병합됨) |
  | `models` | `ModelDefinition[]` | 커스텀 모델 배열 |
  | `modelOverrides` | `Record<string, ModelOverride>` | 내장 카탈로그 모델의 속성(`baseUrl`, `headers`, `compat`, `contextWindow` 등)만 부분 수정할 때 사용 |
  | `whitelist` / `blacklist` | `string[]` | 노출할 모델 ID 허용/차단 필터 |
  | `hideFreeModels` | `boolean` | 무료 모델 숨김 여부 |
  | `credentials` | `object` | OAuth/저장 자격증명 정책 (`allowStoredCredentials`, `selectionPolicy` 등) |

- **모델 배열 원소 필드 (`ModelDefinitionSchema`, 156~178행)**:
  - 필수: `id` (`string`)
  - 선택: `name`, `upstreamModelId` (로컬 `id`와 다른 모델명을 서버로 보낼 때 사용, `provider-composer.js:440`), `api` (**모델별 프로토콜 오버라이드 가능**), `baseUrl` (**모델별 Base URL 오버라이드 가능**), `reasoning` (`boolean`, 기본 `false`), `defaultThinkingLevel` (`"off"`|`"minimal"`|`"low"`|`"medium"`|`"high"`|`"xhigh"`|`"max"`), `thinkingLevelMap`, `input` (`("text"|"image"|"video")[]`, 기본 `["text"]`), `contextWindow` (기본 `128000`), `maxTokens` (기본 `16384`), `cost` (`{input, output, cacheRead, cacheWrite}`), `headers`, `extraBody`, `cacheRetention`, `compat`, `recoverTextToolCalls` (`boolean`), `serviceTier`, `samplingParams`.
- **혼합 엔드포인트에 유용한 핵심 `compat` 플래그 (`model-config-schema.js:82-154`, `docs/models.md:40-64`)**:
  - `supportsDeveloperRole` (`boolean`): `system` 메시지를 `developer` 역할로 변환할지 여부. 로컬 vLLM/Ollama/SGLang 등 `developer` 역할을 거부하는 서버에서는 `false`로 설정.
  - `supportsReasoningEffort` (`boolean`) / `thinkingFormat` (`"openai"` | `"openrouter"` | `"deepseek"` | `"zai"` | `"qwen"` | `"qwen-chat-template"` | `"chat-template"` 등): 추론 파라미터 전달 방식 지정.
  - `maxTokensField` (`"max_completion_tokens"` | `"max_tokens"`): 구형 OpenAI 호환 서버가 `max_completion_tokens`를 거부할 때 `"max_tokens"`로 강제.
  - `cacheControlFormat` (`"anthropic"`): LiteLLM 등 OpenAI 호환 프록시 뒤의 Claude 모델 호출 시 Anthropic 형식의 `cache_control` 마커를 추가.
  - `supportsEagerToolInputStreaming` (`boolean`, `anthropic-messages` 전용): 기본 `true`. 서드파티 Anthropic 호환 프록시가 툴 정의의 `eager_input_streaming: true` 필드를 400 에러로 거부할 때 `false`로 설정.

#### (2) 모델 참조(`provider/model`) 해석 방식, 프로바이더 간 중복 모델 ID, `/` 포함 모델 ID
- **출처**: `omo-ai/bin/lib/engine-models.js` (4~12행), `@code-yeongyu/senpi/dist/core/model-runtime.js` (569~576행, 742~756행)
- **`provider/model` 분리 규칙**:
  - `splitModelRef(value)`(`engine-models.js:4-11`)는 `value.indexOf("/")`로 **첫 번째 `/` 위치만** 찾아 `provider = value.slice(0, slash)`, `id = value.slice(slash + 1)`로 분리한다.
  - 따라서 프로바이더 이름에 `/`가 없으면 **모델 `id` 내부에 `/`가 포함된 경우(예: `"local/Qwen/Qwen3-Coder-30B"`, `"corp/deepseek-ai/DeepSeek-V3.2"`)에도 `provider="local"`, `id="Qwen/Qwen3-Coder-30B"`로 정확히 분리**된다.
- **프로바이더 간 동일 모델 ID**:
  - `senpi`의 `model-runtime.js`는 모든 모델을 `modelKey(model.provider, model.id)`(즉 `${provider}/${id}`) 및 `find(provider, modelId)`로 관리하므로 **두 프로바이더(`corp`, `backup`)가 동일한 모델 `id`(`"glm-5.1"`)를 가져도 전혀 충돌하지 않는다**.
  - 만약 한 프로바이더 내부에서 같은 upstream 모델을 서로 다른 설정으로 두 번 등록하거나 `catalog.yaml`에서 고유 키를 부여하고 싶다면, 모델 정의의 `id`는 별칭으로 두고 `"upstreamModelId": "실제-모델-ID"`를 지정하면 된다(`provider-composer.js:440`).

#### (3) 다중 프로바이더 혼합 폴백 체인(`agents.*.models`, `categories.*.models`) 및 프로바이더별 동시성 제어
- **출처**: `omo-ai/bin/lib/model-chains.js` (1~87행), `omo-ai/bin/lib/category-coverage.js` (1~157행), `omo-ai/plugin/CHANGELOG.md` (199행, 274행), `omo-ai/plugin/extensions/omo.js`, `omo.schema.json`
- **폴백 체인 작성 형식 및 정규화**:
  - `omo.jsonc`의 `agents.<name>.models`와 `categories.<name>.models` 배열에는 **서로 다른 프로바이더의 모델 참조(`["corp/glm-5.1", "backup/glm-5.1", "local/Qwen/Qwen3-Coder-30B"]`)를 자유롭게 혼합**할 수 있다.
  - 배열 원소는 문자열(`"corp/glm-5.1"`) 또는 객체(`{"model": "corp/glm-5.1", "reasoning": "high"}` 또는 `{"model": "corp/glm-5.1", "variant": "high"}`)를 모두 지원한다.
  - `model-chains.js`의 `canonicalizeChainHolder(entry)`(47~69행) 확인 결과, `omo`는 `models` 배열이 있으면 첫 번째 항목을 기본으로 삼고, 레거시 단수 `"model"`이나 `"fallback_models"`가 함께 있으면 `[model, ...models, ...fallback_models]` 순서로 중복 없이 합쳐 정규화한다. (단, 하위 호환성 및 `llm_manager.py`의 현행 패턴 유지를 위해 `"model": chain[0]`과 `"models": chain`을 함께 출력하는 것이 안전하다.)
  - 또한 `models_distribution: "sequential" | "round_robin"` 필드를 지원하여(`CHANGELOG.md:274`), `"round_robin"` 설정 시 서브태스크들을 체인 내 여러 프로바이더 모델로 분산 시작시키고 실패 시 체인의 다음 모델로 순환 폴백시킬 수 있다.
- **한 프로바이더에 키가 없거나(No Key) 서버가 다운(Down)되었을 때의 동작**:
  1. **키/인증이 없는 경우(사전 필터링)**:
     - `senpi`의 `getAvailable()`(`model-runtime.js:689`) 및 `omo.js`의 카테고리/에이전트 모델 해석기(`EE(i.getAvailable())`)는 현재 환경변수가 설정되어 있거나 인증이 완료된 프로바이더의 모델만 `availableModels` 집합에 넣는다.
     - 체인 중 첫 번째 프로바이더(`corp`)의 환경변수 키가 없으면, 해당 모델은 `availableModels`에서 제외되므로 **체인 내 다음 가용 프로바이더(`local`)의 모델이 즉시 선택**된다(`category-coverage.js:90-108`).
  2. **런타임 호출 중 한 프로바이더가 다운/과부하/쿼터 초과된 경우(런타임 폴백)**:
     - 첫 번째 프로바이더 호출이 쿼터 제한(429), 과부하(503/529), 인증 거부, 연결 실패 등으로 실패하면 **체인에 지정된 다음 프로바이더 모델로 자동 폴백**하여 태스크를 재시도한다.
     - 만약 체인의 모든 모델이 실패하면 부모 세션 모델로 암묵적 폴백하지 않고 `failure_kind: "model_chain_exhausted"` 에러와 함께 각 모델의 실패 사유를 보고하며 종료한다(`CHANGELOG.md:199`).
- **프로바이더별 / 모델별 동시성(Concurrency) 설정 — 현행 `llm_manager.py`의 중대한 버그 발견**:
  - 현재 `llm_manager.py`의 `render_omo_config()`(`llm_manager.py:524-530`)는 동시성 설정을 `"[opencode]": {"background_task": {"defaultConcurrency": 5, "providerConcurrency": {...}}}` 아래에만 출력하고 있다.
  - 그러나 `omo.schema.json` 및 `omo.js` 확인 결과, `"[opencode]"` 섹션은 **OpenCode 플러그인 모드 전용**이며, **OmO Native(`senpi` 호스트)는 최상위 `"task"` 객체를 사용**한다!
  - OmO Native의 동시성 설정 키(`omo.schema.json`의 `properties.task.properties`):
    ```json
    "task": {
      "default_concurrency": 5,
      "global_concurrency": 8,
      "provider_concurrency": {
        "corp": 5,
        "local": 2
      },
      "model_concurrency": {
        "corp/gpt-5.4": 2
      }
    }
    ```
  - 따라서 다중 엔드포인트 렌더링 시 `task.provider_concurrency`에 모든 엔드포인트별 동시성 한도를 기록해야 OmO Native에서 실제로 적용된다(레거시 호환을 위해 `[opencode].background_task.providerConcurrency`도 함께 유지 가능).

#### (4) 다중 프로바이더 환경에서 `defaultProvider` / `defaultModel` (`settings.json`) 및 `model_profile` (`omo.jsonc`)의 의미
- **출처**: `omo-ai/bin/lib/doctor-pi-config.js` (106~147행), `omo-ai/bin/lib/setup-models.js` (194~208행), `@code-yeongyu/senpi/dist/core/settings-manager.js`, `omo-ai/plugin/extensions/omo.js`
- **`~/.omo/agent/settings.json`의 `defaultProvider`와 `defaultModel`**:
  - `senpi` 엔진이 새 대화 세션을 시작할 때 기본으로 선택하는 주 모델이다.
  - 여러 프로바이더가 `models.json`에 정의되어 있더라도 `defaultProvider`는 그중 **단 하나의 프로바이더 ID**(예: `"corp"`)를, `defaultModel`은 **접두사 없는 해당 프로바이더 내 모델 ID**(예: `"glm-5.1"`, 절대 `"corp/glm-5.1"`이 아님!)를 가리켜야 한다(`doctor-pi-config.js:116-143`).
  - `defaultProvider`로 지정된 프로바이더가 `models.json`에 존재하고 해당 `defaultModel`이 그 프로바이더의 `models` 배열 안에 있어야 `omo doctor`의 `default model` 검사를 통과한다.
- **`~/.omo/omo.jsonc`의 `model_profile`과 `model_profiles`**:
  - `omo`에는 내장 프로파일(`"claude-max"`, `"chatgpt-pro"`, `"copilot"`, `" balanced"` 등)과 커스텀 프로파일(`"custom"`) 개념이 있다(`setup-models.js:201`).
  - `model_profile: "custom"`으로 설정하면 내장 프리셋 체인 대신 `omo.jsonc`의 `agents` 및 `categories`에 사용자가 직접 명시한 모델 체인(`models`)을 최우선으로 사용한다.
  - 추가로 `omo.jsonc`는 `"model_profiles": { "<profile_name>": { "agents": {...}, "categories": {...} } }` 형태의 명명된 멀티 프로파일 맵도 지원한다(`omo.js`의 `Su`/`ku` 스키마).

#### (5) `omo doctor --models` 출력 구조 및 프로바이더별 검증 방법
- **출처**: `omo-ai/bin/lib/doctor.js` (56~116행), `omo-ai/bin/lib/doctor-pi-config.js` (1~157행), `omo-ai/bin/lib/engine-models.js` (29~151행), `omo-ai/bin/lib/category-coverage.js` (77~148행), 로컬 `omo doctor --models` 실제 실행 결과
- **`omo doctor --models`가 수행하는 5단계 오프라인 검증**:
  1. **`config` (`omo.jsonc`)**: JSONC 문법 및 Zod/JSON 스키마 유효성 검사 (`validateOmoConfigFile`).
  2. **`models.json` (`~/.omo/agent/models.json`)**: 각 프로바이더의 `baseUrl`, `api`, `models` 배열 존재 여부와 인증 설정 상태를 점검하고, 프로바이더마다 1줄씩 출력한다(`inspectModelsJson`):
     - 환경변수가 현재 셸에 설정되어 있으면: `✓ models.json (<ep>)  <N> custom model(s) → <baseUrl>`
     - 환경변수가 현재 셸에 없으면: `! models.json (<ep>)  <N> model(s) configured, but $<KEY_ENV> is not set in environment`
  3. **`default model` (`~/.omo/agent/settings.json`)**: `defaultProvider`/`defaultModel`이 `models.json`의 모델과 일치하는지 확인(`inspectSettingsJson`):
     - 정상 시: `✓ default model  <ep>/<model> (from models.json)`
  4. **`auth`**: `~/.omo/agent/auth.json`의 OAuth/저장 키와 `models.json`의 프로바이더 중 현재 인증이 유효한 프로바이더 목록 요약.
  5. **`model chains` (`inspectCategoryCoverage` + `formatModelVerificationReport`)**:
     - 모든 내장 카테고리(`deep`, `quick`, `ultrabrain`, `visual-engineering`, `artistry`, `writing`, `unspecified-low`, `unspecified-high`)와 에이전트(`sisyphus`, `hephaestus`, `prometheus`, `oracle`, `librarian`, `explore`, `multimodal-looker`, `momus`, `metis`, `atlas`)의 `models` 체인을 순회하며 각 모델이 **(a) 카탈로그/`models.json`에 존재하는지**, **(b) 해당 프로바이더의 인증(환경변수 또는 헤더)이 현재 준비되어 있는지(`providerReady`)** 검사한다.
     - 체인 내 첫 번째 모델의 프로바이더 키가 없더라도 두 번째 프로바이더의 키가 있으면 `active: < 두 번째 모델 >`로 폴백됨을 보여주며, 미인증 프로바이더 모델 옆에는 `[no login for <ep>]`를 표시한다.
- **주의할 점**: `omo doctor --models`는 **네트워크 요청을 보내지 않는 정적/환경변수 검증 도구**이다. 따라서 실제 엔드포인트 연결 및 모델 호출 가능 여부는 `llm_manager.py`의 `detect_server()` / `preflight()` / `verify`에서 HTTP로 함께 검증해야 한다.

---

### 3. OmO Native 주의사항 및 버전 차이 (Pitfalls)

1. **무인증(Keyless) 로컬 서버도 `models.json`에서는 비어 있지 않은 더미 `apiKey`가 필요함**:
   - `@code-yeongyu/senpi/docs/models.md`(38행)에 명시된 바대로:
     > `"apiKey": "ollama" // senpi still treats models as requiring auth before they appear in /model, so keyless local servers should keep a dummy value`
   - 또한 `omo-ai/bin/lib/engine-models.js`의 `providerReady()`(54~71행)와 `doctor-pi-config.js`의 `describeApiKeyState()`(16~26행)는 `apiKey`와 자격증명 `headers`가 둘 다 없으면 해당 프로바이더를 **미인증(`no login` / `no apiKey field`)** 으로 판정하여 에이전트/카테고리 체인에서 제외해 버린다!
   - 반면 `apiKey`에 `"$"`나 `"!"`로 시작하지 않는 일반 리터럴 문자열(예: `"apiKey": "none"` 또는 `"apiKey": "ollama"`)을 넣으면, `senpi`와 `omo doctor --models` 모두 `"literal apiKey"`(인증 완료)로 판정한다. (단, 이 경우 요청 시 `Authorization: Bearer none` 헤더가 나가며, vLLM/Ollama/LM Studio는 이를 무시한다. 만약 `Authorization` 헤더 자체를 보내면 안 되는 서버라면 `"headers": {"X-Local-Auth-Token": "none"}`처럼 `*-auth-token` 접미사를 가진 더미 자격증명 헤더를 넣고 `apiKey`를 생략하면 `@earendil-works/pi-ai/dist/api/openai-client-auth.js`가 `Authorization: null`로 Bearer 헤더를 완전히 제거한다!)
2. **`api: "anthropic-messages"` 사용 시 `baseUrl`에 `/v1`을 넣으면 `/v1/v1/messages`가 호출됨**:
   - `openai-completions`와 `openai-responses`는 `baseUrl` 뒤에 각각 `/chat/completions`, `/responses`를 붙이므로 `baseUrl`에 `/v1`(또는 `/openai/v1`)이 **포함되어야** 한다.
   - 반면 `anthropic-messages`는 공식 `@anthropic-ai/sdk`(`PiAnthropic`)를 사용하며, SDK 자체가 `baseURL` 뒤에 **`/v1/messages`를 자동으로 붙인다**(`omo-ai/bin/lib/setup-opencode-providers.js:55` 및 `@earendil-works/pi-ai/dist/api/anthropic-messages.js:1681`).
   - 따라서 `api: "anthropic-messages"`인 프로바이더(또는 모델)의 `baseUrl`은 `det["api_base"] == "/v1"`일 때 **`/v1`을 제외한 `ep["root"]`** 여야 한다! (이 점은 `/messages`만 붙여서 `baseURL`에 `/v1`이 필요한 `opencode2`의 `@opencode/ai/providers/anthropic-compatible`과 정반대이므로 특히 주의해야 한다.)
3. **`omo.jsonc`의 동시성 설정 위치 (`task.provider_concurrency` vs `[opencode].background_task.providerConcurrency`)**:
   - 앞서 확인했듯 OmO Native는 `task.provider_concurrency` / `task.model_concurrency`(snake_case)를 읽고, `[opencode].background_task`(camelCase)는 레거시 OpenCode 플러그인만 읽는다.
4. **Windows 환경에서 환경변수 영구 등록 필요**:
   - OpenCode 2.x는 `opencode service set env`로 자체 `service.json`에 키를 저장하지만, OmO Native는 실행 시점의 프로세스 환경변수(`process.env`)에서 `$KEY_ENV`를 직접 읽는다. 따라서 N개 엔드포인트 사용 시 각 엔드포인트의 환경변수가 사용자/시스템 환경변수에 모두 설정되어 있어야 한다.

---

### 4. `llm_manager.py`의 OmO Native 렌더링 및 검증 권고안 (`detect_server()` 연동)

#### (1) `detect_server()` 감지 결과별 `render_omo_models()` 표현 방법

| 감지 항목 (`detect_server()`) | 값 | OmO Native (`~/.omo/agent/models.json`) 렌더링 방법 |
|---|---|---|
| **API 포맷 (`api`)** | 기본 / `chat_completions` | `"api": "openai-completions"` |
| | `responses` 전용 엔드포인트 또는 모델 | 프로바이더 또는 개별 모델 엔트리에 `"api": "openai-responses"` 설정 |
| | `kind == "anthropic"` 또는 `messages` 전용 | `"api": "anthropic-messages"` 설정 |
| **API 경로 (`det["api_base"]`)** | `"/v1"`, `"/openai/v1"`, `""` | - `api`가 `"openai-completions"` 또는 `"openai-responses"`인 경우: **`"baseUrl": ep["root"] + (det["api_base"] or "/v1")`** (단, `det["api_base"] == ""`이면 `ep["root"]`)<br>- `api`가 `"anthropic-messages"`인 경우: `@anthropic-ai/sdk`가 `/v1/messages`를 자체 부착하므로, `det["api_base"] == "/v1"`이면 **`/v1`을 뺀 `ep["root"]`** 를 `"baseUrl"`로 설정한다. (만약 `det["api_base"]`가 `"/anthropic/v1"`처럼 프리픽스가 있다면 마지막 `"/v1"`만 제거한 `ep["root"] + "/anthropic"`으로 설정) |
| **인증 방식 (`det["auth"]`)** | `"bearer"` | - `api`가 `"openai-completions"` / `"openai-responses"`일 때: 현행과 동일하게 `"apiKey": "$%s" % ep["key_env"]`<br>- `api`가 `"anthropic-messages"`인데 게이트웨이가 Bearer 인증을 요구할 때: `"apiKey": "$%s" % ep["key_env"]`와 함께 **`"authHeader": true`** 를 추가한다(`provider-api-key-auth.js:189`에서 `Authorization: Bearer` 주입). |
| | `"api-key"` (예: Azure OpenAI / APIM) | **`apiKey` 필드를 생략**하고 **`"headers": {"api-key": "$%s" % ep["key_env"]}`** 로 설정한다.<br>- 근거: `@earendil-works/pi-ai/dist/auth/headers.js`의 `isCredentialHeaderName("api-key")`가 `true`를 반환하므로 `senpi`(`provider-header-auth.js`)와 `omo doctor --models`(`engine-models.js:64`) 모두 이를 정식 인증으로 인정하며, `resolveOpenAIClientAuth`(`openai-client-auth.js:10-13`)가 `Authorization: null`을 설정해 불필요한 Bearer 헤더를 제거하고 `api-key` 헤더만 전송한다. |
| | `"x-api-key"` | - `api`가 `"anthropic-messages"`인 경우: `"apiKey": "$%s" % ep["key_env"]`로 설정하면 `@anthropic-ai/sdk`가 기본으로 `x-api-key` 헤더를 전송한다.<br>- `api`가 `"openai-completions"` / `"openai-responses"`인 경우: **`apiKey`를 생략**하고 **`"headers": {"x-api-key": "$%s" % ep["key_env"]}`** 로 설정한다(`"x-api-key"` 역시 `endsWith("-api-key")`로 `isCredentialHeaderName`을 통과하여 Bearer 없이 `x-api-key`만 전송됨). |
| | `"none"` (무인증 keyless: 로컬 `ollama`, `vllm`) | - 일반적인 로컬 서버(vLLM/Ollama 등 불필요한 Authorization 헤더를 무시하는 경우): **`"apiKey": "none"`**(또는 프로바이더 이름)처럼 `$` 없는 리터럴 더미 값을 넣는다(`@code-yeongyu/senpi/docs/models.md:38` 권장 방식).<br>- 만약 `Authorization` 헤더 자체를 거부하는 무인증 서버라면: `apiKey`를 생략하고 `"headers": {"x-local-auth-token": "none"}`을 넣으면 `Authorization` 헤더가 완전히 제거되면서도 `senpi`의 인증 체크를 통과한다. |
| **호환성 플래그 (`compat`)** | `kind in ("vllm", "ollama")` 또는 Claude 프록시 | - `kind in ("vllm", "ollama")`: 프로바이더에 `"compat": {"supportsDeveloperRole": false}` 추가 권장.<br>- `api: "openai-completions"` 엔드포인트 소속 모델 중 ID에 `"claude"`가 포함된 경우: 모델 엔트리에 `"compat": {"cacheControlFormat": "anthropic"}` 추가 권장. |

#### (2) `render_omo_config()` 및 `render_omo_settings()`에서 2개 이상 엔드포인트 시 변경/검증할 사항
1. **`render_omo_config()`에 네이티브 `task.provider_concurrency` 추가**:
   ```python
   "task": {
       "default_concurrency": 5,
       "provider_concurrency": {ep_name: 5 for ep_name in cat["endpoints"]},
   },
   ```
   기존 `"[opencode]": {"background_task": ...}`와 함께 최상위 `"task"` 블록을 렌더링하여 OmO Native에서 다중 프로바이더 동시성 제한이 실제로 작동하게 한다.
2. **에이전트/카테고리 폴백 체인의 다중 프로바이더 구성**:
   - 현재 `tier_chain()`(`llm_manager.py:440-442`)은 `model_ref(cat, m)`(`"<endpoint>/<mid>"`) 리스트를 반환하므로, 카탈로그의 `tiers.tier1` / `tier2` / `tier3`에 서로 다른 엔드포인트의 모델을 배치하면(예: 1순위 사내 게이트웨이 모델, 2순위 백업 게이트웨이/로컬 모델) `agents.*.models`와 `categories.*.models`가 자동으로 다중 프로바이더 폴백 체인이 된다.
3. **`render_omo_settings()`의 `defaultProvider` / `defaultModel`**:
   - 현행 코드(`llm_manager.py:558-563`)는 `primary = cat["tiers"]["tier1"][0]`의 엔드포인트를 `defaultProvider`, 모델 ID를 `defaultModel`로 설정한다. 다중 엔드포인트에서도 `settings.json`은 단일 기본 프로바이더/모델만 가지므로 이 로직은 유효하다. 단, `primary` 모델이 속한 엔드포인트가 `renderable == False`(예: Bedrock/Vertex 등)가 아닌지 `preflight()`에서 검증해야 한다.
4. **검증 및 경고(`verify` / `preflight`)**:
   - `verify_omo()`는 현재 첫 번째 모델만 확인하고 있으나, 다중 엔드포인트에서는 `cat["endpoints"]`의 **모든 엔드포인트 키**가 `models.json`의 `providers`에 존재하는지 확인해야 한다.
   - `auth != "none"`인 모든 엔드포인트의 `key_env`가 현재 프로세스 환경변수(`os.environ`)에 설정되어 있는지 확인하고, 누락 시 `omo doctor --models`에서 해당 프로바이더가 `[no login for <ep>]`로 비활성화됨을 경고해야 한다.

---

## Part 3. `llm_manager.py` 공통 데이터 모델 개선 요약 (`opencode2` & `omo` 공통)

1. **`endpoints.<name>` 스키마 확장 (`normalize_catalog`)**:
   - `detect_server()` 결과를 카탈로그에 영속화하거나 수동 오버라이드할 수 있도록 엔드포인트별 선택 필드로 `kind`, `auth` (`bearer` | `api-key` | `x-api-key` | `none`), `api_base` (`"/v1"` | `"/openai/v1"` | `""`), `api_format` (`chat_completions` | `responses` | `anthropic_messages`)을 허용한다.
   - `auth == "none"`일 때는 `key_env`를 필수 항목에서 제외한다(`key_env: null` 허용).
2. **`models.<mid>` 스키마 확장**:
   - 두 엔드포인트에 동일한 upstream 모델 ID가 존재할 수 있도록 모델 항목에 선택적 `model_id`(실제 서버로 보낼 upstream ID) 필드를 허용한다.
     - `opencode2`: 모델 키는 `<mid>`, 내부 필드에 `"modelID": m.get("model_id", mid)` 출력 (`model_id != mid`일 때).
     - `omo`: 모델 `"id": mid`, 내부 필드에 `"upstreamModelId": m["model_id"]` 출력 (`model_id != mid`일 때).
   - 모델별 API 포맷 오버라이드(`api_format: responses | anthropic_messages | chat_completions`)를 허용하여 하나의 게이트웨이 안에서 `/v1/chat/completions`와 `/v1/responses` 모델을 혼합 렌더링할 수 있게 한다.

---

## 출처 목록 (Sources)

### 1. 웹 공식 문서 및 스키마
- OpenCode v2 JSON Schema: `https://opencode.ai/config.json`
- OpenCode v2 Config 문서: `https://opencode.ai/v2/docs/config`
- OpenCode v2 Providers 문서: `https://opencode.ai/v2/docs/providers`
- OpenCode v2 Models 문서 (References, Aliases, Local, Prompt Caching): `https://opencode.ai/v2/docs/models`
- OpenCode v2 Migrate from v1 문서: `https://opencode.ai/v2/docs/migrate-v1`
- OpenCode v2 Policies 문서: `https://opencode.ai/v2/docs/policies`
- OpenCode v2 Agents 문서: `https://opencode.ai/v2/docs/agents`
- OpenCode v2 CLI Providers & Service 문서: `https://opencode.ai/v2/docs/cli/providers`, `https://opencode.ai/v2/docs/troubleshooting#service-files`
- Oh-My-OpenAgent (`omo`) JSON Schema: `https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/omo.schema.json`

### 2. 로컬 설치 패키지 및 바이너리 분석 (읽기 전용)
- `llm_manager` 저장소 파일:
  - `README.md`, `catalog.example.yaml`, `llm_manager.py` (`detect_server`, `preflight`, `cmd_detect`, `render_opencode`, `render_omo_config`, `render_omo_models`, `render_omo_settings`, `key_instructions`, `verify`), `docs/harness-plugin-review.md`
- OpenCode `v2.0.26` 로컬 바이너리:
  - `<scoop_dir>/persist/bun/install/cache/@opencode/cli-windows-x64@2.0.26@@@1/bin/opencode.exe` 내장 JS 번들 (`Config.Provider`, `Config.Model`, `bx` V1→V2 정규화, `_x`/`Ax` 정책 평가, `ModelResolver.resolveModel`, `SessionContext.selectTitle`, `cli.service-config`, `@opencode/ai/providers/*` 라우트 정의)
- OmO Native `5.1.63` (`engine: senpi 2026.11.5-1`) 로컬 패키지:
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/doctor.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/doctor-pi-config.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/engine-models.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/model-chains.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/category-coverage.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/setup-models.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/bin/lib/setup-opencode-providers.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/plugin/CHANGELOG.md`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/plugin/extensions/omo.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/docs/models.md`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/model-config-schema.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/model-config.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/model-runtime.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/provider-composer.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/provider-request-auth.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/provider-api-key-auth.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/provider-header-auth.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@code-yeongyu/senpi/dist/core/resolve-config-value.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@earendil-works/pi-ai/dist/auth/headers.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@earendil-works/pi-ai/dist/api/openai-client-auth.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@earendil-works/pi-ai/dist/api/openai-responses.js`
  - `<scoop_dir>/persist/nodejs/bin/node_modules/omo-ai/node_modules/@earendil-works/pi-ai/dist/api/anthropic-messages.js`
