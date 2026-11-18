# 게이트웨이 연결 형식 판단 지원 (assist) 설계

작성일: 2026-11-19 · 상태: 승인 대기

## 목적

`detect`(규칙 기반 점검)가 결론을 못 내는 endpoint에 대해, LLM에게 응답 증거를 보여 주고 연결 형식 후보를 받아 **실제 요청으로 검증한 것만** 사용자에게 제시한다. 회사 게이트웨이 형식(Azure OpenAI, Anthropic 직접, 추가 헤더 등)을 미리 몰라도 설정에 도달하게 하는 것이 목적이다.

## 목표 / 비목표 / 반목표

- 목표: 규칙으로 판정 못 한 endpoint의 연결 형식(API 형식, 인증 헤더, API 경로, 필요한 추가 헤더·쿼리) 후보를 얻고 검증한다.
- 비목표: 모델 tier·context 크기 판단(이번 범위 아님). `catalog.yaml` 자동 수정(제안만 출력).
- 반목표: LLM 답을 검증 없이 채택하는 것. 키가 LLM 요청에 섞이는 것. 사용자가 켜지 않았는데 LLM을 호출하는 것(`apply` 점검 중 자동 호출 금지).

## 방식 (사용자 선택: A + C)

| 방식 | 내용 | 채택 |
|---|---|---|
| A. 판단용 API 지정 | `catalog.yaml`의 `assist:` 블록 (주소, 키 환경변수, 모델). 점검 대상과 독립이라 모든 endpoint가 막혀도 동작 | ✔ |
| B. 이미 통하는 endpoint 재사용 | 설정 불필요하지만 전부 막힌 첫 설치 때 못 씀 | ✘ |
| C. 설치된 Claude Code 호출 (`claude -p`) | 형식 처리는 Claude Code가 함. Claude Code가 먼저 연결돼 있어야 함 | ✔ |

## 동작

1. 사용자가 `detect --assist` 또는 `discover --assist`로 켠다. 값은 `auto`(기본) / `api` / `claude`.
   - `auto`: `assist:`가 있으면 A, 없으면 C. *(내 결정)*
   - `auto`에서 C는 Claude Code가 게이트웨이(`ANTHROPIC_BASE_URL` 설정됨)를 쓸 때만 사용한다. Anthropic에 직접 연결된 경우 사내 응답 정보가 밖으로 나가므로, `--assist claude`로 명시했을 때만 경고와 함께 사용한다. *(내 결정)*
2. 규칙 점검 결과가 `unknown`이거나, 키가 모두 거부됐거나, `renderable`이 아닐 때만 LLM을 부른다. 이미 정상인 endpoint는 부르지 않는다.
3. 증거 수집: 시도한 요청별 (메서드, 경로, 인증 방식 이름, 상태 코드), 응답 헤더(쿠키·인증 관련 제외), 오류 본문 앞 500자, `/openapi.json`이 있으면 경로 목록과 `securitySchemes`. 키 값은 모든 텍스트에서 `<key>`로 치환하고, 보내기 직전에 키가 남아 있는지 한 번 더 검사한다.
4. LLM에 JSON으로 후보 최대 3개를 요청한다:
   `{"candidates": [{"api": "openai-chat|openai-responses|anthropic-messages|azure-openai", "auth": "bearer|api-key|x-api-key|none", "api_base": "/openai/v1", "query": {"api-version": "..."}, "extra_headers": ["X-Tenant-Id"], "reason": "..."}]}`
   형식이 틀린 답은 버린다.
5. 검증: 각 후보로 `<api_base>/models`(쿼리 포함)를 실제 호출해 200 + 모델 목록이면 `verified`. 값이 필요한 추가 헤더는 값을 추측하지 않고 `needs value`로 표시하고, 같은 이름의 환경변수가 있으면 그 값으로 검증한다.
6. 출력: 후보별 `verified` / `failed` / `needs value`와 이유, 그리고 검증된 후보의 endpoint 설정 예시. `discovered.json`에도 저장한다.

## 설정

```yaml
assist:                       # 선택. 없으면 --assist 는 Claude Code(C)를 씁니다
  base_url: https://llm-gateway.example.com   # OpenAI 호환 chat completions, Bearer 인증
  key_env: ASSIST_LLM_KEY
  model: some-strong-model
```

## C 호출 방식 *(내 결정, 구현 시 확인)*

`claude -p <prompt> --output-format json`으로 한 번 호출하고 도구 사용은 끈다. 결과의 `result` 텍스트에서 JSON을 꺼낸다. 정확한 플래그(도구 비활성화, 턴 제한)는 설치된 버전의 `claude --help`로 확인해 맞춘다.

## 제약

- A의 판단용 API는 OpenAI 호환 + Bearer여야 한다(그 자체가 막히면 `detect --url`로 먼저 맞춘다).
- 검증된 후보를 툴 설정으로 바꾸는 것은 별도 작업(endpoint별 `api`/`auth`/`headers` 필드, "2번")이다. 그 전까지 assist 결과는 "회사 형식이 무엇인지"를 알려 주는 용도이고, 2번 이후에는 출력 예시를 `catalog.yaml`에 그대로 붙여 넣을 수 있다.

## 테스트

- 가짜 게이트웨이 + 가짜 assist API(정해진 JSON 응답)로 A 경로 전체.
- `run()`을 대체해 C 경로(가짜 `claude` 출력) 검증, 게이트웨이 미설정 시 `auto`가 C를 건너뛰는지.
- LLM에 보내는 프롬프트에 키가 없는지, 형식이 틀린 답이 버려지는지, 검증 실패 후보가 `failed`로 나오는지, 추가 헤더가 `needs value`로 나오는지.

## 남겨 둔 것 (구현자 판단)

출력 문구, 프롬프트 문장, 증거 크기 상한의 세부 값.
