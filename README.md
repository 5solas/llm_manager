# llm_manager

카탈로그 파일 하나(`catalog.yaml`)로 **opencode2, omo(Native), hermes, Claude Code** 네 에이전트의 설정을 만들어 설치합니다. 사내 LLM 게이트웨이(LiteLLM 등 OpenAI 호환) 한 곳만 쓰도록 맞추는 용도입니다.

- 게이트웨이 주소와 모델 목록은 `catalog.yaml`에만 있고, 이 파일은 git에서 제외됩니다.
- API 키는 어떤 파일에도 저장하지 않습니다. 환경변수 이름만 적습니다.
- 설치 전에 기존 설정을 `backups/<시각>/`에 복사해 둡니다.

## 준비물

- [uv](https://docs.astral.sh/uv/) (Python과 PyYAML은 uv가 알아서 받습니다)
- 설치돼 있는 에이전트: `opencode`, `omo`, `hermes`, `claude` 중 쓰는 것

## 처음 설정하는 순서

```powershell
git clone https://github.com/5solas/llm_manager
cd llm_manager

# 1. 키를 환경변수로 등록 (새 터미널부터 적용됩니다)
setx COMPANY_LLM_KEY "<내 키>"                         # Claude Code도 이 키를 씁니다 (apiKeyHelper)
opencode service set env COMPANY_LLM_KEY "<내 키>"     # opencode2는 백그라운드 서비스가 키를 읽습니다

# 2. 카탈로그를 만들고 게이트웨이 주소를 적기
copy catalog.example.yaml catalog.yaml
notepad catalog.yaml                                   # endpoints.corp.base_url 수정

# 3. 게이트웨이 서버 종류 확인 후 모델 목록과 지원 API 형식 조회
uv run llm_manager.py detect       # 서버 종류·인증 헤더·API 경로 (discover도 먼저 이걸 실행합니다)
uv run llm_manager.py discover
#    출력된 models 블록을 catalog.yaml에 붙여 넣고, tiers / default 를 정합니다.
#    context 가 비어 있는 모델은 직접 채워야 합니다.

# 4. 만들고, 바뀔 내용을 확인하고, 설치 (셋 다 게이트웨이 점검을 먼저 합니다)
uv run llm_manager.py build        # out/ 에 생성 (검토용)
uv run llm_manager.py diff         # 지금 설정과의 차이
uv run llm_manager.py apply --env  # 점검 통과 시 백업 후 설치 + 환경변수 설정

# 5. 확인
uv run llm_manager.py verify
```

모델이나 정책을 바꿀 때는 `catalog.yaml`을 고치고 `diff` → `apply`를 다시 실행합니다.

## 실행 전 게이트웨이 점검

`build`, `diff`, `apply`, `verify`는 시작할 때 카탈로그에서 모델이 배정된 endpoint를 모두 점검합니다. endpoint마다 아래를 확인합니다.

- 서버 종류: LiteLLM, vLLM, Ollama, Azure OpenAI, Anthropic, OpenAI, Bedrock, Vertex, 그 밖의 OpenAI 호환
- 받는 인증 헤더: `Authorization: Bearer`, `api-key`, `x-api-key`
- API 경로: `/v1`, `/openai/v1` 등
- 카탈로그의 모델을 실제로 제공하는지

아래 경우는 문제로 봅니다.

- 접속이 안 될 때
- 키 환경변수가 없거나 키가 거부될 때
- 생성되는 설정이 아직 쓸 수 없는 형식일 때 (Bearer + `<base_url>/v1`이 아닌 경우)
- 게이트웨이에 없는 모델이 카탈로그에 있을 때

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

### LLM 판단 지원 (`--assist`)

규칙 점검으로 연결 형식을 정하지 못한 endpoint는 LLM에게 판단을 맡길 수 있습니다. `detect`와 `discover`에 `--assist`를 붙였을 때만 동작하고, `build`/`diff`/`apply`/`verify`의 점검에서는 LLM을 부르지 않습니다.

```powershell
uv run llm_manager.py detect --assist          # auto
uv run llm_manager.py discover --assist api    # catalog.yaml의 assist: 블록 사용
uv run llm_manager.py detect --assist claude   # 설치된 Claude Code 사용
```

- 언제 부르나: 서버 종류가 `unknown`이거나, 키가 모든 인증 방식에서 거부됐거나, 생성되는 설정이 바로 쓸 수 없는 형식일 때(Bearer + `<base_url>/v1`이 아닐 때)만 부릅니다. 이미 정상인 endpoint, 접속이 안 되는 endpoint, Bedrock·Vertex는 부르지 않습니다.
- 누구에게 묻나:
  - `api`: `catalog.yaml`의 `assist:` 블록에 적은 API (OpenAI 호환 chat completions, Bearer 인증). 점검 대상과 따로 동작하므로 모든 endpoint가 막혀 있어도 쓸 수 있습니다.
  - `claude`: 설치된 Claude Code를 `claude -p`로 한 번 호출합니다. 도구·MCP·프로젝트 설정 없이 빈 임시 폴더에서 실행합니다.
  - `auto`(기본): `assist:` 블록이 있으면 `api`, 없으면 `claude`. 단 Claude Code가 게이트웨이를 쓸 때(`ANTHROPIC_BASE_URL` 설정됨)만 `claude`를 고릅니다. Anthropic에 직접 연결된 Claude Code에 사내 응답 정보를 보내려면 `--assist claude`로 명시해야 하고, 이때 경고를 출력합니다.
- 무엇을 보내나: 점검 때 보낸 요청(메서드, 경로, 인증 방식 이름, 상태 코드), 응답 헤더(쿠키·인증 헤더 제외), 응답 본문 앞 500자, `/openapi.json`이 있으면 경로 목록과 `securitySchemes`. **키 값은 보내지 않습니다.** 모든 텍스트에서 키를 `<key>`로 바꾸고, 보내기 직전에 키가 남아 있는지 다시 검사합니다.
- 결과: LLM이 제안한 후보(최대 3개)마다 `<api_base>/models`를 실제로 호출해 확인하고 상태를 붙입니다.
  - `verified`: 실제 요청이 성공했습니다. 붙여 넣을 수 있는 endpoint 설정 예시를 함께 출력합니다.
  - `failed`: 실제 요청이 실패했습니다 (HTTP 상태와 이유 표시).
  - `needs value`: 추가 헤더(예: `X-Tenant-Id`)가 필요한데 값이 없습니다. 값은 추측하지 않고 `LLM_HEADER_` + 헤더 이름(대문자, 영숫자 외 문자는 `_`) 환경변수에서만 읽습니다. 예: `X-Tenant-Id` → `LLM_HEADER_X_TENANT_ID`. 값을 넣고 다시 실행하면 그 값으로 확인합니다.

  ```powershell
  $env:LLM_HEADER_X_TENANT_ID = "my-tenant"
  ```

- 결과는 제안일 뿐이며 `catalog.yaml`은 고치지 않습니다. `discover --assist`는 결과를 `discovered.json`에도 저장합니다.
- 판단 API에 접속이 안 되거나, 시간이 초과되거나, `claude`가 설치돼 있지 않으면 한 줄로 알리고 점검 결과는 그대로 출력합니다.

## 무엇이 설치되나

| 툴 | 파일 | 방식 |
|---|---|---|
| opencode2 | `~/.config/opencode/opencode.json` | 전체 생성 |
| opencode2 | `~/.config/opencode/AGENTS.md` | `rules/AGENTS.md` 복사 |
| omo | `~/.omo/omo.jsonc`, `~/.omo/agent/models.json` | 전체 생성 |
| omo | `~/.omo/agent/settings.json`, `mcp.json` | 기존 파일에 병합 |
| hermes | `%LOCALAPPDATA%\hermes\config.yaml` | 기존 파일에 병합 (`model`은 통째로 교체) |
| Claude Code | `~/.claude/settings.json` | 기존 파일에 병합 |
| Claude Code | `~/.claude/statusline.ps1` | `claude/statusline.ps1` 복사 |
| Claude Code | `~/.claude/api-key.ps1` | `claude/api-key.ps1` 복사 (키를 환경변수에서 읽어 넘김) |
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

provider는 항상 카탈로그의 endpoint만 허용합니다(opencode2는 정책으로 차단, hermes는 보조 작업까지 주 provider로 고정).

## 알아 둘 점

- **Claude Code는 Claude 계열 모델만** 배정합니다. 게이트웨이를 거쳐도 다른 계열 모델은 공식 지원 대상이 아닙니다. 게이트웨이가 Anthropic 형식(`/v1/messages`)을 지원해야 하며 `discover`가 확인해 줍니다. 지원하지 않으면 `tools: {claude_code: false}`로 끄세요.
- **Claude Code는 endpoint 하나만** 씁니다 (`ANTHROPIC_BASE_URL`이 하나뿐). 메인 모델(`default`가 Claude 모델이면 그것, 아니면 tier 순서상 첫 Claude 모델)의 endpoint를 고르고, opus/sonnet/haiku 슬롯은 같은 endpoint의 Claude 모델로만 채웁니다. 해당 tier에 없으면 가까운 tier에서 가져옵니다. `claude_code`에 서로 다른 endpoint의 모델을 직접 적었을 때만 오류입니다.
- Claude Code의 키는 그 endpoint의 `key_env`에서 읽습니다. `settings.json`의 `apiKeyHelper`가 `api-key.ps1`을 실행하고, 이 스크립트는 환경변수 값만 출력합니다. `ANTHROPIC_AUTH_TOKEN`이나 `ANTHROPIC_API_KEY`가 설정돼 있으면 Claude Code가 그쪽을 먼저 쓰므로, `apply`가 지우라고 안내합니다.
- Claude Code가 모델 이름을 못 알아보면 컨텍스트 크기를 1M으로 가정한다는 경고가 나옵니다. 동작에는 문제가 없습니다.
- **hermes는 컨텍스트 64,000 토큰 미만 모델을 거부**하므로 그런 모델은 hermes 설정에서 빠집니다.
- 사내 인증서로 HTTPS를 검사하는 망에서 hermes가 인증서 오류를 내면 `SSL_CERT_FILE`에 사내 CA 파일 경로를 지정하세요. opencode2는 `opencode service set env NODE_EXTRA_CA_CERTS <경로>`, omo와 Claude Code는 `NODE_EXTRA_CA_CERTS` 환경변수를 씁니다.
- omo에는 다른 provider 목록이 내장돼 있지만 로그인(키)이 없으면 쓰이지 않습니다. `omo doctor --models`로 실제 연결을 볼 수 있습니다.
- `--home <폴더>`를 주면 그 폴더를 홈으로 보고 설치합니다. 실제 설정을 건드리지 않고 결과를 볼 때 씁니다.

## 개발

```powershell
uv run --with pytest --with pyyaml pytest -q
```
