# hermes-agent · opencode2 하네스 플러그인 심층 검토 (2026-11-17 기준)

> 범위: opencode2(v2.0.26), hermes-agent(0.19.0 표기, 빌드 2026.9.24), omo 5.1.63(senpi)에서 oh-my-opencode처럼 하네스를 조합할 수 있는 플러그인
> 전제: 사내 LiteLLM 게이트웨이 하나만 사용, 텔레메트리 off, 자동 업데이트 off, 외부 웹이 막힐 수 있음, 키는 env로만 주입, Windows 11
> 표기: "미확인"은 실행이나 소스로 확인하지 못한 항목입니다. 모든 평가는 반박 검증(skeptic)에서 나온 정정을 반영했습니다.

---

## 1. 핵심 결론

- **oh-my-opencode(oh-my-openagent) 플러그인은 opencode2에서 아무것도 하지 않습니다.**
  - 5.1.38~5.1.75의 v2 진입점은 로그를 남기고 토스트를 띄우는 것뿐입니다. 토스트 문구는 "agents, tools and hooks are not active here"입니다.
  - 근거: 5.1.75 tarball의 `dist/index.js` ~98378, 이슈 #9389와 #8485.
  - OmO 경험은 이미 설치된 **omo Native(5.1.63, senpi)**로 유지하는 것이 정답입니다. opencode2에 이 플러그인을 추가하면 안 됩니다(avoid).
- **omo는 이 스택의 중심 하네스로 채택하되 하드닝이 필요합니다.**
  - 현재 텔레메트리가 켜져 있습니다. `~/.omo/omo.jsonc`에 `telemetry` 키가 없고, `~/.omo/agent/omo-senpi/posthog/`에 2026-11-04 daily-active claim이 있습니다.
  - llm_manager 출력을 적용하고, env 변수를 보강하고, 버전을 고정해야 합니다.
- **opencode2(2.0.26)에서 바로 쓸 수 있는 것은 "방법론 레이어"입니다.**
  - obra/superpowers v7.0.2를 로컬 디렉터리에 벤더링하고 고정해 쓰는 것을 권장합니다(trial).
  - opencode 안에 오케스트레이터가 꼭 필요하면 아래 셋 중 **하나만** 시범 적용하십시오.
    - oh-my-opencode-slim 3.0.7: 가장 성숙했습니다. 공식 지원 범위는 v2.0.7+입니다.
    - opencode-teamwork-v2 0.5.6: 작성자가 2.0.26에서 검증했다고 주장합니다.
    - weave adapter 0.3.0
  - 단, **2.0.29 미만에서는 플러그인의 permission `ask`가 강제되지 않습니다.** 호스트를 2.0.29 이상으로 올려야 합니다.
- **hermes 조합은 다음 순서를 권장합니다.**
  1. superpowers 스킬을 `skills.external_dirs`로 연결합니다(skills-only, 오프라인).
  2. 공식 `advanced-delegation`의 model_map으로 역할별 모델을 게이트웨이 모델에 라우팅합니다.
  3. 필요하면 `hermes-teammates`(이름 붙은 서브에이전트 팀)를 **별도 프로파일에서** 시험합니다.
  - OmO를 가장 충실히 포팅한 rlaope **OMH**는 요구 버전(≥0.21.1, 테스트 기준 0.22.5)과 정책 마찰 때문에 **hermes 업그레이드 이후로 미루는 것**이 좋습니다.
- **네 에이전트가 공통으로 쓰는 레이어**는 플러그인이 아니라 표준입니다.
  - 표준: SKILL.md 공유 디렉터리, AGENTS.md, 로컬 stdio MCP.
  - opencode v2는 **AGENTS.md만** 읽고 CLAUDE.md는 읽지 않습니다. 대신 `~/.claude/skills`는 자동으로 스캔하며, 끄는 스위치가 없습니다.
- **선행 작업이 있습니다.** 현재 live 설정이 게이트웨이를 가리키지 않습니다.

  | 에이전트 | 현재 provider |
  |---|---|
  | opencode.json | deepinfra |
  | hermes config.yaml | gemini |
  | omo models.json | deepinfra |

  llm_manager 출력을 먼저 적용하지 않으면 플러그인 평가 자체가 게이트웨이 기준으로 검증되지 않습니다.

---

## 2. 플러그인 구조 비교

| 항목 | opencode2 (v2.0.26) | hermes-agent (0.19.0 표기 / 빌드 2026.9.24) | omo Native 5.1.63 (senpi = pi 포크) |
|---|---|---|---|
| 형식 | `@opencode/plugin` v2 `Plugin.define({id, setup(ctx)})`. 호스트가 모듈을 제공합니다. V1 플러그인은 **실행되지 않습니다** | 디렉터리(`plugin.yaml` + `__init__.py register(ctx)`), pip entry-point, 또는 Agent Plugins v1(`plugin.json`). 기본은 opt-in(`plugins.enabled`) | pi extension(.ts), pi 패키지(`omo install <src>`, settings `packages`), 스킬, Claude Code 호환 로더 |
| 설정 키 | `plugins`(복수). 항목은 npm/git/로컬 **디렉터리**(단일 파일은 무시)/`{package, options}`. 자동 탐색: `~/.config/opencode/plugins/`, `.opencode/plugins/` | `config.yaml`의 `plugins.enabled`/`disabled`, `plugins.entries.<id>.settings` | `~/.omo/omo.jsonc`(`[native]`/`[opencode]` 스코프), `~/.omo/agent/{models,settings,mcp}.json` |
| 하네스 훅 지점 | `ctx.agent/tool/command/skill/mcp.transform`, `session.hook("context"/"prompt"/"compaction"/"retry"/…)`, `tool.hook("execute.before/after")`, `permission.hook("evaluate")`. `experimental.idle`은 2.3.0부터 | 27종 이상의 훅(`pre_tool_call` block/approve/modify, `pre_llm_call` context 주입, `pre_verify`로 "계속 일하게" 강제, `subagent_*`, `post_compaction` 관찰 전용), middleware, shell hook(Claude Code 호환 wire 포맷) | 약 40개 내장 컴포넌트(ultrawork, ulw-loop, task, lsp, memory 등)가 내장되어 있습니다. 서드파티는 pi 패키지로 확장합니다 |
| 에이전트 / 서브에이전트 | 내장 `subagent` 도구(2.3.0부터 기본 백그라운드). `AgentEditor`에 `add`는 없고 `update`가 사실상 upsert로 동작합니다(비문서화) | `delegate_task`(병렬, 깊이/동시성 제한), `DelegationProvider`(model_map/profile_routes), `subagent_lifecycle`, Kanban, 프로파일 | explore/librarian/plan-consultant/plan-reviewer와 category worker, 백그라운드 task/team |
| 합성 / 충돌 규칙 | 훅은 플러그인 순서대로 실행되고, transform은 등록 순서대로 재적용되며, 같은 이름 도구는 **나중 것이 이깁니다**. permission은 명시적 deny가 최종이고 마지막 매칭 규칙이 이깁니다. 자동 탐색과 config 항목 사이의 순서와 중복 제거 규칙은 **미확인** | 훅은 누적됩니다. 단일 선택 슬롯(memory.provider, context.engine, delegation.backend)이 있고, 같은 이름은 project > user > bundled 순입니다 | 단일 하네스이므로 다른 하네스와 같은 프로세스를 공유하지 않습니다 |
| 버전 게이트 | 플러그인마다 최소 호스트 버전을 직접 명시합니다(2.2.x, 2.3.x가 많음). TUI 플러그인은 OpenTUI 버전이 맞아야 합니다(2.2.0에서 0.7, 2.2.3에서 0.8) | `requires_hermes`. 단, 이 설치는 버전이 `unknown`이라 **게이트가 사실상 통과**합니다(`plugins_manifest.py:486-499`) | 해당 없음 |
| 공급망 통제 | npm/git을 받습니다. 버전을 고정하지 않은 항목은 **기동할 때마다 업데이트를 확인**합니다. 가장 확실한 것은 고정 + 로컬 디렉터리입니다 | 카탈로그가 40-hex SHA로 고정하고 사람이 머지합니다. 설치 시 스캔(dangerous는 차단, caution은 확인 요구). `plugins.isolation: host` 지원 | npm 고정, `OMO_OFFLINE`/`PI_OFFLINE` |
| 한계 | v2 플러그인 API는 아직 베타 성격입니다. LSP 내장이 없습니다. v1 생태계 대부분이 동작하지 않습니다 | 실제 semver가 불명확합니다. PyPI 최신은 0.19.0이고, 0.21 이후는 GitHub 태그로만 배포됩니다 | 라이선스 SUL-1.0(비 OSI). 일부 런타임(`bunshin-ts`)은 독점 EULA입니다 |

참고: `~/.config/opencode/package.json`의 `"@opencode-ai/plugin": "1.3.15"`는 **V1 잔재**입니다. v2 호스트는 이 패키지를 쓰지 않고, 아래 검토한 v2 플러그인들도 의존하지 않습니다. 실해는 없지만 herdr V1 경로에서만 의미가 있습니다.

---

## 3. 추천 조합

### 3-A. opencode2용

**단계 0 (선행)**
1. llm_manager가 렌더하는 v2 `opencode.json`을 적용합니다. 내용은 `providers`, `update: "disable"`, `experimental.policies`, permissions입니다.
2. `opencode.json`과 `opencode.jsonc`가 **둘 다 있습니다.** 병합 우선순위가 미확인이므로 한쪽은 정리하십시오.

**단계 1: 방법론 레이어 (trial, 2.0.26에서 가능)**
- superpowers v7.0.2를 벤더링된 로컬 디렉터리로 씁니다. 윈도우의 Bun git+https 이슈를 피하는 방법입니다.

```powershell
git clone --depth 1 --branch v7.0.2 https://github.com/obra/superpowers.git "$HOME\.config\opencode\vendor\superpowers"
# 오프라인이면 C:\Users\<you>\.claude\plugins\cache\claude-plugins-official\superpowers\7.0.2 를 복사
# (Claude 캐시를 직접 가리키지 말 것 — 업데이트 시 정리됨)
```

llm_manager catalog의 `extra.opencode`에 다음을 넣습니다(deep-merge, 리스트는 **교체** 의미).

```jsonc
{
  "plugins": [
    "C:\\Users\\<you>\\.config\\opencode\\vendor\\superpowers"   // 디렉터리, ~ 확장 안 됨
  ]
}
```

검증은 **필수**입니다. `ctx.skill`/`ctx.session.hook`이 없으면 `setup()`이 아무 로그 없이 그냥 반환합니다(superpowers.js:299-301).

```powershell
opencode run --standalone --print-logs "Tell me about your superpowers" 2>&1 | Select-String superpowers
opencode plugin list
```

**단계 2: 오케스트레이터 (선택, 하나만, 별도 테스트 후)**

| 후보 | 2.0.26 적합성 | 비고 |
|---|---|---|
| oh-my-opencode-slim **3.0.7** | 3.0.7 문서 기준 "v2.0.7+" 지원. master의 "2.3 필수"는 **미배포** 변경입니다 | 2.0.29 미만에서는 `ask`가 강제되지 않으므로 호스트를 **2.0.29~2.2.x**로 올리는 것을 권장합니다. **3.0.7과 2.3.0의 조합은 금지**합니다(미검증 조합). 2.3.x로 갈 경우 다음 릴리스(3.1.x)를 기다리십시오 |
| opencode-teamwork-v2 0.5.6 | README에 "Verified against OpenCode v2.0.26"이라고 적혀 있습니다(작성자 주장) | 기본 모델이 deepseek로 하드코딩되어 있어 재매핑이 필수입니다. 에이전트 frontmatter의 shell/webfetch allow를 직접 조여야 합니다 |
| weave adapter 0.3.0 | @opencode/* 2.0.16에 고정, 2.0.16/2.0.21에서 검증 | 공식 문서가 "stock Windows is not supported"라고 명시합니다. `/weave:start`는 깨질 가능성이 있습니다 |

slim 3.0.7을 시험할 때의 하드닝 예시입니다. 설정 오버레이 디렉터리에 둡니다. `OPENCODE_CONFIG_DIR`은 격리가 아니라 **오버레이**이며, 전역 `~/.config/opencode`도 함께 로드됩니다.

```jsonc
// oh-my-opencode-slim.jsonc
{
  "$schema": "https://unpkg.com/oh-my-opencode-slim@3.0.7/oh-my-opencode-slim.schema.json",
  "autoUpdate": false,
  "disabled_hooks": ["auto-update-checker"],
  "disabled_mcps": ["context7", "gh_grep"],
  "webfetch": { "enabled": false },
  "marketplace": { "enabled": false },
  "multiplexer": { "type": "none" },          // 이후 "herdr" (WezTerm은 --server 호스트에서 fail-closed 가능성)
  "preset": "gateway",
  "presets": { "gateway": {
    "orchestrator": { "model": "<endpoint>/<strong-model>" },
    "oracle":       { "model": "<endpoint>/<strong-model>" },
    "explorer":     { "model": "<endpoint>/<fast-model>" },
    "designer":     { "model": "<endpoint>/<fast-model>" },
    "fixer":        { "model": "<endpoint>/<fast-model>" } } }
}
```

- opencode 설정에는 `"plugins": ["oh-my-opencode-slim@3.0.7"]`처럼 **정확히 고정**해 넣습니다.
- 인스톨러(`bunx … install`)는 가급적 쓰지 마십시오. opencode.json을 수정하고, 기본 에이전트를 끄고, `lsp:true`를 넣습니다. TUI 모드에서는 GitHub star를 묻는 프롬프트도 뜹니다. 꼭 써야 하면 PowerShell에서 `--no-tui --dry-run`으로 미리 보십시오.
- hermes를 opencode의 @서브에이전트로 쓰려면 slim의 `acpAgents`를 씁니다.
  - `command`는 **`C:\Users\<you>\.local\bin\hermes.exe`**로 지정합니다. 이 실행 파일은 `acp --check`가 OK입니다.
  - PATH에서 먼저 잡히는 `%LOCALAPPDATA%\hermes\bin\hermes.exe`는 pydantic_core 오류로 실패합니다.
  - `acp_run`에 `model`을 넘기지 마십시오. hermes가 model configOption을 노출하지 않습니다.
  - 실제 승인 통제는 hermes 쪽 approvals입니다. slim의 `permissionMode: "reject"`는 hermes가 요청한 permission에만 적용됩니다.

### 3-B. hermes용

**단계 0 (선행)**
- llm_manager의 hermes 렌더를 적용합니다(`model.provider: custom:<endpoint>`, `key_env`). 현재 live 설정은 gemini를 가리킵니다.

**권장 config.yaml 형태** (catalog `extra.hermes`로 관리합니다)

```yaml
plugins:
  enabled:
    - herdr-agent-state          # llm_manager 패치가 리스트를 '덮어쓰므로' 반드시 포함
    - advanced-delegation
skills:
  external_dirs:                 # llm_manager의 union 경로
    - C:/tools/superpowers/skills
delegation:
  backend: model_map             # 플러그인 로드 확인 후에만 출력할 것 (fail-closed)
  backends:
    model_map:
      options:
        - name: fast
          description: "Cheap and quick: lookups, summaries, simple edits."
          provider: custom:<endpoint>
          model: <gateway-fast-model>
        - name: deep
          description: "Slow and careful: hard debugging, design, review."
          provider: custom:<endpoint>
          model: <gateway-strong-model>
          reasoning_effort: high
      default: fast
  inherit_mcp_toolsets: false
```

설치는 SHA로 고정합니다.

```powershell
hermes plugins install NousResearch/hermes-official-plugins#advanced-delegation --ref e404b6816b20237a82f6fbe2749b5a576cf75dba --enable
# 또는 카탈로그명(이미 같은 SHA 고정): hermes plugins install advanced-delegation --enable
hermes delegation config --show      # 실제 콘솔(TTY)에서
```

- route에 `api_key`를 넣지 마십시오. config.yaml에 평문으로 저장됩니다. `service_tier`와 `daybreak`도 쓰지 마십시오.

**superpowers 선택지**
- (권장) 위의 `skills.external_dirs` 방식은 skills-only, 오프라인, 훅이 없는 방식입니다.
- (선택) 플러그인 방식은 `hermes plugins install obra/superpowers --ref 22f0b463e7a5e579904dd6211a520321035526f7 --enable`입니다.
  - 0.19 스캐너로 직접 돌려 보면 결과는 **caution**(173건, critical 0)입니다. 차단되지는 않고 확인을 받으며, 비TTY 환경에서는 `--force`가 필요합니다.
  - `plugins.scan_on_install`을 전역으로 끄지 마십시오.
  - 플러그인 키는 `superpowers/.hermes-plugin`이지만, `enabled`는 manifest 이름 `superpowers`로도 매칭됩니다.
  - 압축(compaction) 이후 부트스트랩이 사라집니다. 0.19에도 `post_compaction` 훅은 있지만, 이를 쓰는 PR #2586이 아직 머지되지 않았습니다.
- 둘을 동시에 쓰면 스킬이 중복되므로 하나만 쓰십시오.

**이름 붙은 팀 (선택, 별도 프로파일)**
- `hermes-teammates`를 SHA `28234ba`로 고정해 씁니다. 처음에는 읽기 전용이나 todo 전용 roster로 시작하십시오.
- 주의할 점
  - `file` 토올셋에는 write/patch가 포함됩니다.
  - approvals가 off이면 teammate도 프롬프트 없이 실행됩니다.
  - 완료 알림은 이 빌드에서 동작하지 않으므로 `teammate_wait`을 쓰십시오.
- advanced-delegation과 함께 켜는 조합은 **별도로 시험한 뒤에** 쓰십시오.

**방법론 팩은 하나만 고르십시오.** superpowers, pstack, witt3rd OMH 중 하나입니다. 각각 "항상 먼저 X 하라"는 상위 지시를 넣기 때문에 겹치면 서로 경쟁합니다.

### 3-C. 공용 레이어 (MCP, Skills, AGENTS.md)

- **Skills**: llm_manager가 공유 디렉터리 하나(예: `~/.agents/skills` 또는 벤더링한 superpowers/skills)를 각 에이전트에 연결합니다.

  | 에이전트 | 연결 방법 |
  |---|---|
  | opencode2 | `.opencode/skills`와 `skills` 배열. `~/.claude/skills`, `~/.agents/skills`를 자동 스캔합니다 |
  | hermes | `skills.external_dirs` |
  | omo | `~/.omo/agent/settings.json`의 `"skills": ["~/.claude/skills"]`(opt-in) |
  | Claude Code | 기본 |

  같은 하네스 안에서 "플러그인 + external_dirs"로 같은 스킬 트리를 두 번 넣지 마십시오.
- **AGENTS.md**: opencode2는 AGENTS.md만 읽습니다. hermes와 omo는 AGENTS.md와 CLAUDE.md를 모두 읽습니다. 지침은 AGENTS.md를 단일 소스로 두십시오. 윈도우에서는 심볼릭 링크보다 복사가 안전합니다.
- **MCP**: 로컬 stdio(LSP, ast-grep)만 쓰십시오. context7, grep.app, Exa 같은 원격 MCP는 모든 하네스에서 꺼야 합니다.
- **herdr**: 세 에이전트에 로컬 상태 리포터가 설치되어 있습니다(로컬 소켓, 네트워크 없음). 유지해도 됩니다. omo 쪽 `~/.omo/agent/extensions/herdr-agent-state.ts`는 pi 통합의 **복사본**이라 herdr가 자동 갱신하지 않습니다.

### 3-D. 함께 쓰면 안 되는 조합

| 조합 | 이유 |
|---|---|
| omo Native + oh-my-openagent opencode 플러그인 | 같은 제품입니다. v2에서는 no-op이고, 설치 부작용만 생깁니다(postinstall 캐시 삭제, 약 52MB) |
| opencode2 안에 오케스트레이터 2개 이상 (slim, teamwork, weave, openchoreographer, pantheon, matrixx 중 둘) | `default_agent`, 같은 이름 에이전트(`explore` 등), `context`/`execute.before`/`permission` 훅이 중복됩니다. 나중 것이 이기거나 이중으로 주입됩니다 |
| superpowers 부트스트랩 + omo 오케스트레이션을 같은 세션에서 | "먼저 스킬을 호출하라"와 omo의 plan/execute 흐름이 경쟁합니다. 이중 계획과 토큰 증가가 생깁니다 |
| rlaope OMH와 witt3rd OMH | 플러그인 id `omh`, 도구명, `.omh/` 상태 디렉터리가 모두 충돌합니다 |
| hermes에서 여러 위임/오케스트레이션 플러그인 동시 사용 (advanced-delegation, teammates, OMH, pstack/setup-pstack) | 모두 delegation 설정이나 subagent 경로를 건드립니다 |
| DCP + Magic Context 등 컨텍스트 플러그인 다중 사용 | 압축(compaction) 충돌 |
| agent-importers `--sync`(cron) + llm_manager | `mcp_servers`와 `command_allowlist`가 계속 드리프트합니다 |

---

## 4. 플러그인별 평가

판정은 반박 검증의 adjusted_recommendation을 반영했습니다.

| 이름 | 대상 | 역할 | 호환성 (현 환경) | 게이트웨이 적합성 | 주요 위험 | 판정 |
|---|---|---|---|---|---|---|
| **omo Native** (omo-ai 5.1.63, senpi) | omo | 중심 하네스(오케스트레이터, 카테고리 워커, LSP, 메모리) | 설치되어 동작 중. 최신은 5.1.75 | 좋음. `models.json`에 `openai-completions` provider와 `$ENV` 키 | 텔레메트리 기본 ON이고 현재 켜져 있음. SUL-1.0과 독점 EULA. 릴리스가 매우 잦음. websearch/webfetch는 MCP와 **별개 내장 도구**이며 끄는 법은 미확인 | **adopt** (하드닝 조건) |
| **oh-my-openagent / oh-my-opencode** opencode 플러그인 5.1.75 | opencode2 | (v1에서는) 전체 하네스 | v2에서는 **no-op** | 해당 없음 | postinstall, PostHog, npm update 체크, 원격 MCP | **avoid** |
| **obra/superpowers** v7.0.2 | 둘 다 | 방법론, 스킬 레이어(15개 스킬과 부트스트랩) | opencode: ≥2.0.4 요구, 2.0.26 바이너리와 API 형태 일치(정적 확인, 실행은 미확인). hermes: API 일치, 스캐너 caution | 매우 좋음(모델, 계정 불필요) | v2에서 매 요청 부트스트랩 토큰. #2713(숨은 세션 주입), #2701(todo 매핑 오류). 무음 no-op 가능. 약한 모델은 스킬을 과하게 또는 적게 트리거 | **trial** (hermes skills-only는 사실상 adopt급) |
| **oh-my-opencode-slim** 3.0.7 | opencode2 | 오케스트레이터와 전문 서브에이전트, council, acpAgents | 3.0.7은 v2.0.7+. 2.0.26에서 `ask` 미강제(<2.0.29). session.list/remove 저하 | 좋음(preset을 수동 매핑) | 기본값 autoUpdate, 원격 MCP, webfetch가 ON. 인스톨러 부작용. 변경이 매우 빠름. 다음 릴리스는 2.3+ 필수 | **trial** |
| **opencode-teamwork-v2** 0.5.6 | opencode2 | Sentinel DAG, worktree, verify, 보안 게이트 | 작성자가 2.0.26 검증 주장. V2 네이티브 | 모델 재매핑 필수. cost 메타가 없어 $20 예산 캡이 무력 | 단일 메인테이너, 별 0개. shell/webfetch allow. "read-only"는 과장(shell은 falsifier만 차단). `--refresh`가 수정한 권한을 덮어쓸 가능성 | **trial** |
| **@weaveio/weave-adapter-opencode2** 0.3.0 | opencode2 | Loom/Pattern/Tapestry 등 8개 에이전트, 계획 실행 | 2.0.16 고정, 2.0.22/2.2.5 사용 보고 있음 | 좋음(모델이 없으면 호스트 기본 모델로 폴백) | 공식 문서가 Windows 미지원 명시, 베타, 소규모 | **trial** (assess 경계) |
| **openchoreographer** 1.5.0 | opencode2 | 오케스트레이터와 13개 서브에이전트, retry/fallback | plugin API 2.2.x 대상. 2.0.26 TUI는 미검증 | 좋음 | 워커 6종의 마지막 규칙이 `shell * allow`라서 last-match 규칙상 **ask 정책을 우회**함(rm, git push 포함). 내장 `explore`를 덮어씀. 별 2개 | **assess** (trial에서 하향) |
| **pantheon-opencode** 2.1.6 | opencode2 | Zeus 등 14개 에이전트, TDD 게이트 | 2.0.29만 실측 | Inherit preset이면 가능 | 네이티브 Windows에서 `.sh` 스폰 실패 시 fail-closed라 모든 도구 호출이 차단될 가능성. npm 업데이트 체크를 끄는 스위치 없음. vision 기본값이 OpenCode Zen | **assess** |
| **opencode-matrixx-v2** 1.4.2 | opencode2 | OmO 포크 계열 하네스 | v2 지향, 문서 요구사항이 서로 모순 | `global_model`로 가능 | `auto_update` 기본 true(bun install), 런타임 바이너리 다운로드, SUL-1.0, 3주 된 프로젝트 | **assess** |
| **advanced-delegation** (Nous 공식) | hermes | delegate_task 라우팅(model_map/profile_routes) | 코어 seam 존재, 심볼 일치. requires ≥0.22.5지만 버전이 `unknown`이라 통과 | 매우 좋음(`custom:<endpoint>`) | 버전이 정상 파싱되면 로드 거부, delegate_task가 fail-closed. profile_routes는 자격증명 범위를 넓힘 | **trial** |
| **hermes-teammates** 1.8.0 | hermes | 이름 붙은 서브에이전트 팀, Kanban 리뷰 레인 | 필요한 API가 로컬에 모두 있음. route/notice/follow_up은 저하 | 좋음 | 6주 된 프로젝트. 툴셋 단위 샌드박스가 거침. Windows 미언급 | **trial** |
| **OMH (rlaope/oh-my-hermes)** 4.0.8 | hermes | 가장 충실한 OmO 포트(ulw-*, 28 도구, 12 훅) | requires ≥0.21.1, 테스트 기준 0.22.5. 이 빌드에서 Hermes 게이트는 `unknown`이라 통과할 수 있으나, 플러그인 자체 `host_compat`가 거부할 수 있음(미확인) | 좋음(카테고리 체인 매핑 필요) | delegation 키를 config.yaml에 기록. 호스트 subprocess가 승인을 우회. omher.app `irm \| iex` 설치. 업그레이드는 PyPI가 아니라 git 태그로만 가능 | **trial** (hermes 업그레이드 후) |
| **witt3rd/oh-my-hermes** 0.1.0 | hermes | ralph/ralplan/deep-interview 스킬과 소형 플러그인 | 0.19 API 일치 | 좋음 | 정체(커밋 'wip'). ≥0.21.6에서 PyYAML 문제(#27). Windows에서 `.cmd` allowlist를 추가하면 cmd.exe 인자 주입 위험(`%`, `^` 미필터). deep-research는 웹 필요. 카탈로그 `omh`와 이름 충돌 | **trial** (소규모, 벤더링) |
| **pstack** (Cloeille) | hermes | poteto-mode 라우터와 17개 플레이북 | register_skill 일치. 카탈로그 요구 ≥0.21.0 | 좋음(inherit-parent) | autopilot/shipping이 push/merge/cron 수행. babysit의 `watch-pr` 스크립트가 **없어 깨짐**. kanban 툴셋 미활성. setup-pstack이 설정을 변경 | **trial** |
| **hades-coding-workflows** | hermes | 스킬 3개 | API 일치 | dual-model-harness가 DeepSeek/Z.AI 하드코딩 | 정체(2026-07), 내용 중복 | **assess** (writing-plans만 체리픽) |
| **agent-importers** (Nous 공식) 1.2.1 | hermes | Claude Code, Codex, Cursor 설정 1회성 이관 | requires ≥0.21.6. `unknown`이라 통과하지만 실동작 미확인 | 중립 | llm_manager 밖에서 `mcp_servers`/`command_allowlist`를 씀(드리프트, 자동 승인 범위 확대). opencode/omo 소스 없음 | **assess** (dry-run만) |
| **Claude Code 호환 shim** (omo rules/skills, opencode v2 `.claude/skills`, hermes importer) | 둘 다 | ~/.claude 자산 재사용 | omo는 내장. opencode v2는 skills만 지원(CLAUDE.md 없음). legacy `[opencode].claude_code`는 v2에서 비활성 | 중립 | 로컬에 `~/.claude/CLAUDE.md`와 `rules/`가 **없음**. `~/.claude.json`(188KB)의 mcpServers가 다수라 충돌 위험 | **trial** (조각별: omo skills는 adopt, legacy는 avoid) |
| **opencode-shepherd** 0.2.5 | 둘 다 | hermes가 opencode 세션 알림과 승인 처리(API 클라이언트) | opencode 2.0.28에서 테스트. 2.0.26은 인증 엔드포인트 미확인 | 완전 호환(LLM 미사용) | Python 3.14와 cp949 조합에서 이모지 출력 시 **UnicodeEncodeError로 알림 불발**(`--python 3.15` 또는 `PYTHONUTF8=1` 필요). SKILL.md가 pipx/cron으로 유도. `perm always`로 원격 승인 노출. Windows 비공식 | **trial** |

**정밀 검토하지 않은 주요 후보 (참고)**
- opencode2 오케스트레이션: opencode-swarm, opencode-ensemble, opencode-courier(2.3.0 테스트), thoth-agents(≥2.2.6), @fro.bot/systematic(≥2.2.5)
- opencode2 컨텍스트/메모리: DCP(@tarquinen/opencode-dcp, AGPL), Magic Context, opencode-mem
- opencode2 안전: cc-safety-net, envsitter-guard
- hermes: plan-mode, crew, hermes-workflows, hermes-verifier
- 위 항목은 v2 호스트 최소 버전이 2.2~2.3인 경우가 많습니다. 채택 전에 개별 확인이 필요합니다.
- 회피 권장: 클라우드 메모리(supermemory, mem0 cloud, retaindb, byterover), 구독 인증 플러그인(gemini/antigravity/codex auth), wakatime, curl|sh로 설치하는 것 전반

---

## 5. 주의와 리스크

- **텔레메트리**
  - omo
    - PostHog와 OTLP가 기본 ON이고 **현재 켜져 있습니다.**
    - 필요한 설정: `telemetry.enabled=false`, `device_service.enabled=false`, env `DO_NOT_TRACK=1`, `OMO_DISABLE_TELEMETRY=1`, `OMO_DISABLE_POSTHOG=1`, `OMO_DISABLE_FEEDBACK=1`, `OTEL_SDK_DISABLED=true`, `OMO_SKIP_VERSION_CHECK=1`.
    - 웹이 차단된 환경이면 `OMO_OFFLINE=1`도 넣습니다. pi.dev 카탈로그와 npm 체크를 막습니다.
    - 정정: omo에서는 pi.dev install ping이 **발생하지 않습니다**(BRAND 설정 시 조기 반환). 따라서 `enableInstallTelemetry=false`는 무해하지만 효과도 없습니다.
  - oh-my-openagent 계열은 `DO_NOT_TRACK` 단독으로 PostHog가 꺼지는지 평가가 서로 엇갈립니다. `OMO_DISABLE_POSTHOG=1`을 명시하십시오.
  - superpowers는 `SUPERPOWERS_DISABLE_TELEMETRY=1`을 설정하십시오. 대상은 visual companion의 로고 요청입니다.
- **자동 업데이트와 공급망**
  - opencode v2는 고정하지 않은 npm/git 플러그인을 기동할 때마다 확인합니다. 모든 항목을 **정확한 버전이나 SHA로 고정**하고, 가능하면 벤더링한 로컬 디렉터리를 쓰십시오.
  - 대부분 단일 메인테이너에 변경이 매우 잦은 프로젝트입니다(slim, OMH, omo는 하루에도 여러 번 릴리스).
  - 카탈로그를 거치지 않는 hermes 설치(`owner/repo`)는 스캔만 받고 리뷰는 받지 않습니다.
- **권한 우회**
  - opencode는 permission에서 **마지막 매칭 규칙이 이기고**, 에이전트 규칙이 전역 규칙 뒤에 덧붙습니다. 에이전트 frontmatter에 `shell * allow`가 마지막에 있으면 llm_manager의 `ask`가 무력화됩니다(openchoreographer에서 확인, teamwork도 shell allow).
  - 2.0.29 미만에서는 플러그인의 `permission.assert`가 없어서 `ask`가 로그만 남깁니다.
- **버전 드리프트**
  - opencode: 로컬은 2.0.26, 최신은 2.3.0(2026-11-13)입니다. 플러그인 생태계가 2.2~2.3으로 이동 중이라 **호스트 버전 전략을 하나로 정해야** 합니다.
    - (A) 2.0.29~2.2.x와 slim 3.0.7 / teamwork / weave
    - (B) 2.3.x와 그 이후 릴리스
  - TUI 플러그인은 OpenTUI 버전에 묶여 있습니다.
  - hermes: dist-info는 0.19.0인데 `--version`은 `vunknown (2026.9.24)`입니다. 코드에는 0.22.5 시기의 seam이 있습니다. 한 PR 작성자는 같은 빌드 날짜를 v0.22.4로, newreleases는 v0.21.5로 표기합니다.
  - 이 덕분에 `requires_hermes` 게이트가 **모두 통과**합니다. 재설치로 버전이 정상 기록되면 플러그인이 일괄 거부될 수 있습니다.
  - PyPI 최신은 0.19.0이라 `hermes-agent==0.22.5` 고정은 실패합니다. git 태그로 설치해야 합니다.
  - `@opencode-ai/plugin` 1.3.15(V1 SDK)는 v2 플러그인과 무관합니다. herdr의 V1 경로 외에는 의미가 없습니다.
- **Windows**
  - `.sh` 직접 스폰(pantheon), `.cmd` shim과 `shell=False`(witt3rd OMH), cp949 콘솔(shepherd), tmux 전제 기능, 심볼릭 링크 권한, `~`가 확장되지 않는 opencode 경로를 확인해야 합니다.
- **설정 소유권 충돌**
  - 플러그인 인스톨러(slim, pantheon, teamwork `init`, matrixx)가 opencode.json(c)을 직접 수정합니다. llm_manager가 다시 생성하면 사라지거나 드리프트가 생깁니다. 인스톨러를 피하고 catalog에서 생성하십시오.

---

## 6. llm_manager 반영 제안 (요약)

1. **catalog에 `plugins` 섹션을 추가합니다.** 에이전트별로 `{id, source: npm|git|vendored-dir, version|sha, enabled, options/settings}`를 둡니다. 렌더 규칙은 다음과 같습니다.
   - opencode에는 `plugins` 배열(디렉터리 또는 `name@x.y.z`)을 출력합니다. `deep_merge`가 리스트를 **교체**한다는 점을 기억하십시오.
   - hermes에는 `plugins.enabled`와 `plugins.entries.<id>.settings`를 출력합니다. `herdr-agent-state`는 항상 포함합니다. `plugins.enabled`를 union 경로로 바꾸는 것도 검토하십시오.
   - omo에는 `settings.json`의 `skills`/`packages`를 출력합니다.
2. **조건부 출력**
   - `delegation.backend: model_map`은 advanced-delegation이 활성일 때만 출력합니다(fail-closed 방지).
   - 웹 정책이 off이면 원격 MCP와 webfetch 비활성 키를 하네스 설정마다 자동으로 넣습니다.
3. **env_plan 보강**: `OMO_DISABLE_TELEMETRY`, `OMO_DISABLE_POSTHOG`, `OMO_DISABLE_FEEDBACK`, `OTEL_SDK_DISABLED`, `SUPERPOWERS_DISABLE_TELEMETRY`, (웹 off일 때) `OMO_OFFLINE`, (shepherd를 쓸 때) `PYTHONUTF8=1`.
4. **공유 skills 디렉터리 하나와 AGENTS.md 하나를 단일 소스로** 두고, 각 에이전트의 경로에 연결합니다.
5. **벤더 디렉터리 관리**: 지정한 SHA로 clone하거나 복사하고, 해시를 기록합니다. 오프라인 배포를 지원하기 위한 것입니다.
6. **선행 적용과 검증**: 현재 live 설정(opencode, hermes, omo)이 게이트웨이를 가리키지 않습니다. `apply` 후 `opencode plugin list`, `hermes plugins list`, omo PostHog claim 파일 생성 여부를 확인하는 테스트를 추가하십시오.

---

## 7. 검증되지 않은 부분과 신뢰도

| 항목 | 상태 |
|---|---|
| superpowers가 opencode **2.0.26에서 실제로** 동작하는지 | 정적 일치만 확인했습니다(바이너리 문자열과 Skill.Info 스키마). 런타임은 미확인 |
| slim 3.0.7이 2.0.26에서 동작하는지, 2.0.26이 `plugin`과 `plugins` 중 어느 키를 받는지 | 미확인. v2 문서상 `plugins` 권장 |
| `OPENCODE_CONFIG_DIR`의 v2 동작 | 문서화되지 않았습니다. 실제로는 오버레이로 추정되며, 격리가 필요하면 별도 `XDG_CONFIG_HOME`, 별도 사용자, VM을 쓰십시오 |
| opencode v2에서 자동 탐색 플러그인과 config 항목의 순서, 중복 제거 | 미확인 |
| `opencode.json`과 `opencode.jsonc`가 공존할 때의 병합 | 미확인 |
| hermes 실제 버전 (0.21.5~0.22.x로 추정) | 미확인 |
| rlaope OMH의 자체 `host_compat`가 이 빌드를 거부하는지 | 미확인 |
| advanced-delegation, teammates, agent-importers의 end-to-end 실행 | 미실행 |
| omo의 senpi 내장 websearch/webfetch를 끄는 방법 | 미확인 |
| omo가 superpowers pi 패키지를 정상 로드하는지 | 미확인 (`omo install <path>`는 존재) |
| slim과 hermes acp의 실제 왕복 | `--check`만 확인했습니다. initialize, prompt 왕복은 미확인 |
| openchoreographer와 weave의 2.0.26 TUI 호환 (호스트 번들 OpenTUI 버전) | 미확인 |
| opencode v2에서 todo 도구 유무 | 상충합니다. "제거됨"이라는 보고와 "experimental.todo 뒤에 있음"이라는 보고가 있습니다 |

**전체 신뢰도**
- 높음: OmO 플러그인이 v2에서 no-op이라는 점, omo 텔레메트리 상태, superpowers와 advanced-delegation의 API 일치(로컬 소스로 확인).
- 중간: opencode2 오케스트레이터 후보들의 2.0.26 실동작, hermes 플러그인의 버전 경계.

**주요 근거 위치**
- 로컬: `C:\Users\<you>\AppData\Roaming\uv\tools\hermes-agent\Lib\site-packages\hermes_cli\{plugins.py,plugins_manifest.py,plugins_content.py,plugins_cmd_catalog.py}`, `...\plugin-catalog\*.yaml`, `C:\Users\<you>\.config\opencode\{opencode.json,opencode.jsonc,cli.json,package.json}`, `C:\Users\<you>\.omo\omo.jsonc`, `C:\Users\<you>\.omo\agent\omo-senpi\posthog\`, `D:\work\llm_manager\llm_manager.py`(render_opencode 253-306, render_hermes 393-424, merge 599-640)
- 웹 (opencode): https://opencode.ai/v2/docs/build/plugins , https://opencode.ai/v2/docs/build/plugins/migrate-v1 , https://opencode.ai/docs/permissions
- 웹 (OmO 계열): https://github.com/code-yeongyu/oh-my-openagent (#8485, #9389, #10439) , https://github.com/alvinunreal/oh-my-opencode-slim (v3.0.7 docs, PR #1902)
- 웹 (hermes 계열): https://github.com/obra/superpowers , https://github.com/NousResearch/hermes-official-plugins , https://github.com/rlaope/oh-my-hermes , https://github.com/100yenadmin/hermes-teammates