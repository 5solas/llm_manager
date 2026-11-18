# llm_manager

카탈로그 파일 하나(`catalog.yaml`)로 **opencode2, omo(Native), hermes, Claude Code** 네 에이전트의 설정을 만들어 설치합니다. 사내 LLM 게이트웨이(LiteLLM 등)나 자체 호스팅 서버(vLLM, Ollama), Azure OpenAI 등 하나 또는 여러 엔드포인트를 함께 쓰도록 맞추는 용도입니다.

- 게이트웨이 주소와 모델 목록은 `catalog.yaml`에만 있고, 이 파일은 git에서 제외됩니다.
- API 키는 어떤 파일에도 저장하지 않습니다. 환경변수 이름만 적습니다(키가 필요 없는 로컬 서버는 `auth: none`).
- 설치 전에 기존 설정을 `backups/<시각>/`에 복사해 둡니다.

## 준비물

- [uv](https://docs.astral.sh/uv/) (Python과 PyYAML은 uv가 알아서 받습니다)
- 설치돼 있는 에이전트: `opencode`, `omo`, `hermes`, `claude` 중 쓰는 것

## 처음 설정하는 순서

```powershell
git clone https://github.com/5solas/llm_manager
cd llm_manager

# 1. 키를 환경변수로 등록 (새 터미널부터 적용됩니다; auth: none 엔드포인트는 생략)
setx COMPANY_LLM_KEY "<내 키>"
setx ANTHROPIC_AUTH_TOKEN "<내 키>"                    # Claude Code가 읽는 이름 (x-api-key 인증이면 ANTHROPIC_API_KEY)
opencode service set env COMPANY_LLM_KEY "<내 키>"     # opencode2는 백그라운드 서비스가 키를 읽습니다

# 2. 카탈로그를 만들고 게이트웨이 주소를 적기
copy catalog.example.yaml catalog.yaml
notepad catalog.yaml                                   # endpoints 수정

# 3. 게이트웨이 서버 종류 확인 후 모델 목록과 지원 API 형식 조회
uv run llm_manager.py detect       # 서버 종류·인증 헤더·API 경로 감지 및 endpoints 블록 출력
uv run llm_manager.py discover     # endpoints 블록 + 통합 models 블록 출력
#    출력된 endpoints / models 블록을 catalog.yaml에 붙여 넣고, tiers / default 를 정합니다.
#    context 가 비어 있는 모델은 직접 채워야 합니다.

# 4. 만들고, 바뀔 내용을 확인하고, 설치 (셋 다 게이트웨이 점검을 먼저 합니다)
uv run llm_manager.py build        # out/ 에 생성 (검토용)
uv run llm_manager.py diff         # 지금 설정과의 차이
uv run llm_manager.py apply --env  # 점검 통과 시 백업 후 설치 + 환경변수 설정

# 5. 확인
uv run llm_manager.py verify
```

모델이나 정책을 바꿀 때는 `catalog.yaml`을 고치고 `diff` → `apply`를 다시 실행합니다.

## 여러 엔드포인트 사용하기 (`endpoints`)

하나의 `catalog.yaml`에 서로 다른 모델을 제공하는 여러 엔드포인트를 등록할 수 있습니다. 예를 들어 사내 LiteLLM 게이트웨이(`corp`), 사내/로컬 무인증 vLLM·Ollama 서버(`local`), Azure OpenAI 스타일 서버(`azure`)를 동시에 쓸 수 있습니다.

### 엔드포인트 설정 필드

각 엔드포인트는 `base_url` 외에 아래 선택 필드를 가집니다. 생략하면 기존 단일 게이트웨이(`Bearer` + `/v1`)와 동일하게 동작합니다.

| 필드 | 기본값 | 설명 |
|---|---|---|
| `base_url` | (필수) | 서버 주소 (끝에 `/v1`이 있으면 떼어내고 앞 경로 프리픽스는 루트에 유지합니다) |
| `auth` | `"bearer"` | `"bearer"` \| `"api-key"` \| `"x-api-key"` \| `"none"` |
| `key_env` | (`auth: none` 외 필수) | API 키가 들어 있는 환경변수 이름 (`auth: none`이면 생략 가능) |
| `api_base` | `"/v1"` | 서버 루트 아래 API 경로 (`"/v1"`, `"/openai/v1"`, `""` 등) |
| `api` | `"openai-completions"` | 엔드포인트 기본 프로토콜: `"openai-completions"` \| `"openai-responses"` \| `"anthropic-messages"` (개별 모델에서 `api:`로 오버라이드 가능) |
| `kind` | 생략 가능 | `"litellm"`, `"vllm"`, `"ollama"`, `"azure-openai"`, `"anthropic"`, `"openai-compatible"` 등 (`vllm`/`ollama`는 호환성 플래그 자동 반영) |
| `headers` | `{}` | 해당 엔드포인트 요청에 추가할 정적 HTTP 헤더 맵 |
| `concurrency` | 생략 가능 | 엔드포인트별 동시 요청 한도 (`omo`의 `task.provider_concurrency`에 반영) |

엔드포인트가 2개 이상일 때는 모든 모델에 `endpoint: <이름>`을 적어야 합니다.

### `detect` → 붙여넣기 → `discover` → `build` 흐름

새 서버의 인증 방식이나 API 경로를 정확히 모를 때는 `detect`로 먼저 확인합니다.

```powershell
uv run llm_manager.py detect --url https://my-resource.openai.azure.com --key-env AZURE_OPENAI_KEY
```

`detect`와 `discover`는 감지된 `kind`, `auth`, `api_base`(그리고 `discover`에서는 프로브로 확인된 `api` 포맷)가 채워진 `endpoints:` YAML 블록을 바로 붙여 넣을 수 있게 출력합니다. 또한 `discover`는 여러 엔드포인트를 조회할 때 각 모델에 `endpoint:`가 기입된 **하나의 통합된 `models:` 블록**을 출력하고, LiteLLM뿐 아니라 vLLM(`max_model_len`), Ollama(`POST /api/show`), OpenRouter 호환 서버(`context_length`), Anthropic(`max_input_tokens`), llama.cpp(`meta.n_ctx`)에서 컨텍스트 크기와 비전 지원 여부를 자동으로 채웁니다.

### Claude Code의 단일 엔드포인트 규칙

`opencode2`, `omo`, `hermes`는 여러 엔드포인트를 동시에 등록해 모델별로 라우팅하지만, **Claude Code는 구조상 단 하나의 `ANTHROPIC_BASE_URL`만** 받습니다.

1. 사용할 엔드포인트는 `claude_code.endpoint` → `claude_code.model`의 엔드포인트 → `default` 모델이 Claude 계열이면 그 엔드포인트 → `tier1`~`tier3` 순서의 첫 Claude 모델 엔드포인트 순으로 자동 결정됩니다.
2. `opus`, `sonnet`, `haiku` 슬롯의 자동 선택은 결정된 그 엔드포인트 안의 Claude 모델에서만 고릅니다. 다른 엔드포인트에 있는 Claude 모델은 에러 대신 안내 메시지(`note`)만 출력하고 건너뜁니다. (슬롯에 다른 엔드포인트 모델을 직접 지정하면 카탈로그 에러로 알려 줍니다.)
3. 선택한 엔드포인트의 인증 방식에 따라 Claude Code용 환경변수 기본값이 정해집니다:
   - `auth: bearer` → `ANTHROPIC_AUTH_TOKEN`
   - `auth: x-api-key` → `ANTHROPIC_API_KEY`
   - `auth: none` → 설정 파일에 더미 토큰(`no-key-required`) 자동 입력
   - `auth: api-key` → 키를 파일에 적지 않고는 Claude Code에서 보낼 수 없으므로 카탈로그 오류로 안내합니다(다른 엔드포인트를 `claude_code.endpoint`로 고르거나 `tools: {claude_code: false}`로 끄세요).

## 실행 전 게이트웨이 점검

`build`, `diff`, `apply`, `verify`는 시작할 때 카탈로그에서 모델이 배정된 endpoint를 모두 점검합니다.

1. 먼저 카탈로그에 선언된 그대로(`root + api_base + /models`, 선언된 `auth` 헤더 및 `key_env`) 모델 목록을 호출합니다.
2. 응답이 정상이면 카탈로그의 모델을 실제로 제공하는지 확인합니다.
3. 실패하면 `detect_server()`로 서버가 실제로 받는 인증 헤더와 API 경로를 진단하여, 카탈로그 선언값과 서버 응답의 차이 및 수정해야 할 설정(`auth: ...`, `api_base: ...`)을 정확히 알려 줍니다. (`auth: none` 엔드포인트는 키 환경변수를 요구하지 않으며, Bedrock·Vertex처럼 클라우드 서명이 필요한 서버는 계속 차단합니다.)

문제가 있으면 명령마다 다르게 동작합니다.

| 명령 | 문제가 있을 때 |
|---|---|
| `apply` | 아무것도 설치하지 않고 종료 코드 3으로 끝냅니다 |
| `build`, `diff` | 문제를 알려 주고, 결과는 그대로 만듭니다 |
| `verify` | 실패 항목으로 셉니다 |

망이 끊긴 곳에서 설치해야 하면 `--skip-check`로 점검을 건너뜁니다 (`build`, `diff`, `apply`).

카탈로그 없이 주소 하나만 점검할 수도 있습니다. 키 값은 출력되지 않습니다.

```powershell
uv run llm_manager.py detect --url https://llm-gateway.example.com --key-env COMPANY_LLM_KEY
```

## 무엇이 설치되나

| 툴 | 파일 | 방식 |
|---|---|---|
| opencode2 | `~/.config/opencode/opencode.json` | 전체 생성 |
| opencode2 | `~/.config/opencode/AGENTS.md` | `rules/AGENTS.md` 복사 |
| omo | `~/.omo/omo.jsonc`, `~/.omo/agent/models.json` | 전체 생성 |
| omo | `~/.omo/agent/settings.json`, `mcp.json` | 기존 파일에 병합 |
| hermes | `%LOCALAPPDATA%\hermes\config.yaml` | 기존 파일에 병합 (`model`, `providers`, `fallback_providers`는 통째로 교체, 레거시 `custom_providers`·`fallback_model`은 제거) |
| Claude Code | `~/.claude/settings.json` | 기존 파일에 병합 |
| Claude Code | `~/.claude/statusline.ps1` | `claude/statusline.ps1` 복사 |
| Claude Code | `~/.claude/CLAUDE.md` | `llm_manager` 표시 구간만 교체 |
| 공통 | `~/.claude/skills/` | `skills/` 복사 |

"전체 생성" 파일은 툴 안에서 직접 고쳐도 다음 `apply` 때 덮어써집니다. 유지하려면 `catalog.yaml`의 `extra`에 적으세요.

공통 지침은 `~/.config/opencode/AGENTS.md` 한 곳에만 두고, omo는 그 파일을 직접 읽으며 Claude Code는 `CLAUDE.md`에서 가져옵니다. hermes는 전역 지침 파일이 없어서 프로젝트 폴더의 `AGENTS.md`만 읽습니다.

## 정책 (`policy`)

| 키 | 기본 | 내용 |
|---|---|---|
| `telemetry` | `false` | omo 텔레메트리·기기 연동 끔, Claude Code 비필수 통신 끔, `DO_NOT_TRACK=1` |
| `auto_update` | `false` | 네 툴의 자동 업데이트/업데이트 확인 끔 |
| `external_web` | `true` | `false`면 웹 검색·가져오기 도구와 omo 내장 context7/grep_app을 끔 |
| `approvals` | `"off"` | `"off"`는 묻지 않고 실행, `"ask"`는 명령·편집 전에 확인 |

provider는 항상 카탈로그의 endpoint만 허용합니다(opencode2는 정책으로 차단, hermes는 보조 작업과 `fallback_providers`까지 카탈로그 endpoint로만 고정).

- **hermes 폴백 및 이전 provider 정리**: hermes의 `fallback_providers`는 `default` 모델이 속한 tier의 나머지 모델(순서대로, 컨텍스트 64,000 미만 제외)로만 채워집니다. 기존 `config.yaml`에 남아 있던 카탈로그 밖의 `providers`(또는 레거시 `custom_providers`)와 외부 폴백(`fallback_providers`, `fallback_model`)은 병합 시 제거되며, `diff`와 `apply`가 제거된 항목 이름을 `note:`로 알려 줍니다. 의도적으로 별도 provider나 폴백을 유지해야 한다면 `extra.hermes`에 적어 덮어쓸 수 있습니다.

## 알아 둘 점

- **Claude Code는 Claude 계열 모델만** 배정합니다. 게이트웨이를 거쳐도 다른 계열 모델은 공식 지원 대상이 아닙니다. 게이트웨이가 Anthropic 형식(`/v1/messages`)을 지원해야 하며 `discover`가 확인해 줍니다. 지원하지 않으면 `tools: {claude_code: false}`로 끄세요.
- Claude Code가 모델 이름을 못 알아보면 컨텍스트 크기를 1M으로 가정한다는 경고가 나옵니다. 동작에는 문제가 없습니다.
- **hermes는 컨텍스트 64,000 토큰 미만 모델을 거부**하므로 그런 모델은 hermes 설정에서 빠집니다.
- 사내 인증서로 HTTPS를 검사하는 망에서 hermes가 인증서 오류를 내면 `SSL_CERT_FILE`에 사내 CA 파일 경로를 지정하세요. opencode2는 `opencode service set env NODE_EXTRA_CA_CERTS <경로>`, omo와 Claude Code는 `NODE_EXTRA_CA_CERTS` 환경변수를 씁니다.
- omo에는 다른 provider 목록이 내장돼 있지만 로그인(키)이 없으면 쓰이지 않습니다. `omo doctor --models`로 실제 연결을 볼 수 있습니다.
- `--home <폴더>`를 주면 그 폴더를 홈으로 보고 설치합니다. 실제 설정을 건드리지 않고 결과를 볼 때 씁니다.

## 개발

```powershell
uv run --with pytest --with pyyaml pytest -q
```
