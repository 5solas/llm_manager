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
setx COMPANY_LLM_KEY "<내 키>"
setx ANTHROPIC_AUTH_TOKEN "<내 키>"                    # Claude Code가 읽는 이름
opencode service set env COMPANY_LLM_KEY "<내 키>"     # opencode2는 백그라운드 서비스가 키를 읽습니다

# 2. 카탈로그를 만들고 게이트웨이 주소를 적기
copy catalog.example.yaml catalog.yaml
notepad catalog.yaml                                   # endpoints.corp.base_url 수정

# 3. 게이트웨이에서 모델 목록과 지원 API 형식 조회
uv run llm_manager.py discover
#    출력된 models 블록을 catalog.yaml에 붙여 넣고, tiers / default 를 정합니다.
#    context 가 비어 있는 모델은 직접 채워야 합니다.

# 4. 만들고, 바뀔 내용을 확인하고, 설치
uv run llm_manager.py build        # out/ 에 생성 (검토용)
uv run llm_manager.py diff         # 지금 설정과의 차이
uv run llm_manager.py apply --env  # 백업 후 설치 + 환경변수 설정

# 5. 확인
uv run llm_manager.py verify
```

모델이나 정책을 바꿀 때는 `catalog.yaml`을 고치고 `diff` → `apply`를 다시 실행합니다.

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
- Claude Code가 모델 이름을 못 알아보면 컨텍스트 크기를 1M으로 가정한다는 경고가 나옵니다. 동작에는 문제가 없습니다.
- **hermes는 컨텍스트 64,000 토큰 미만 모델을 거부**하므로 그런 모델은 hermes 설정에서 빠집니다.
- 사내 인증서로 HTTPS를 검사하는 망에서 hermes가 인증서 오류를 내면 `SSL_CERT_FILE`에 사내 CA 파일 경로를 지정하세요. opencode2는 `opencode service set env NODE_EXTRA_CA_CERTS <경로>`, omo와 Claude Code는 `NODE_EXTRA_CA_CERTS` 환경변수를 씁니다.
- omo에는 다른 provider 목록이 내장돼 있지만 로그인(키)이 없으면 쓰이지 않습니다. `omo doctor --models`로 실제 연결을 볼 수 있습니다.
- `--home <폴더>`를 주면 그 폴더를 홈으로 보고 설치합니다. 실제 설정을 건드리지 않고 결과를 볼 때 씁니다.

## 개발

```powershell
uv run --with pytest --with pyyaml pytest -q
```
