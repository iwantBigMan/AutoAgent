# 리서치 워크플로 공공데이터 OpenAPI 연동 설계 (나라장터·data.go.kr)

날짜: 2026-09-23
상태: 설계 확정(구현 전)
브랜치: feature/openapi-research

## 목적

`--workflow research`(`/aar`)에 프롬프트가 들어오면 **공공데이터포털(data.go.kr)에서
활용신청 승인된 OpenAPI 전부**(나라장터 11종 + KISA 도메인/IP + 행안부 안전정보)를
리서치 근거로 자동 활용하고, 누리집(정부 웹사이트) 일반 웹 조사는 기존
WebSearch/WebFetch 경로로 함께 수행한다. 사용자가 API를 더 승인받으면 **레지스트리
파일만 추가**해 확장된다.

## 확정된 결정 사항

| 결정 | 선택 |
| --- | --- |
| API 호출 주체 | **하네스(Python)가 직접 호출**해 스냅샷으로 저장. Codex 리서치 스텝은 read-only 샌드박스라 네트워크 불가 → 모든 스테이지(Claude·Codex)가 같은 파일을 근거로 사용 |
| 무엇을 호출할지 | **모델이 수집계획을 세우고 코드가 실행**(레포 철학 "코드는 orchestrate, 프롬프트가 what"). Claude(light)가 요청+카탈로그를 보고 호출 목록 JSON 산출 |
| 카탈로그 원천 | 기계가 읽는 **레지스트리 JSON**(`autoagent/data/openapi_registry.json`) 단일 원천. 모델용 카탈로그 마크다운은 코드가 레지스트리에서 **렌더**(이중 관리 없음) |
| 계획 검증 | 레지스트리에 없는 service/operation은 **실행 거부·에러 기록**(모델 환각 엔드포인트·임의 URL 차단). 호스트는 `apis.data.go.kr`만 허용 |
| 인증키 보관 | gitignored `autoagent.config.json`의 `data_go_kr_service_key` > env `DATA_GO_KR_SERVICE_KEY`. 키 없으면 수집 스테이지 **경고 후 생략**(리서치는 계속) |
| 키 노출 방지 | 프롬프트·run 아티팩트·stdout에 키 미기록. manifest의 URL은 serviceKey 제거본 |
| 실행 시점 | seed pin 직후·바깥 루프 진입 전 1회(`01_openapi_plan` → `openapi/` 스냅샷). 재개 시 이미 있으면 스킵 |
| 게이트 | 신설 없음. 계획·결과는 run_dir 아티팩트로 확인 |
| 웹(누리집) 조사 | 기존 a/b 스테이지의 WebSearch/WebFetch(커밋 0acaa5b의 웹도구 수정 전제) 그대로 |
| Claude ad-hoc API 직접 호출 | **비목표**. Bash 미허용 환경에서 env 키를 안전하게 쓸 수 없고 WebFetch URL에 키를 넣으면 아티팩트에 노출됨 |

## 구성 요소

### C1. 레지스트리 + 카탈로그 렌더 (`autoagent/data/openapi_registry.json`, `autoagent/data/openapi.py`)

레지스트리 JSON 스키마(서비스 단위; 아래 값은 **형식 예시**이며 실제 id·URL·오퍼레이션은
구현 시 data.go.kr 상세 페이지에서 읽은 값으로 채운다):

```json
{
  "services": {
    "bid_notice": {
      "name": "조달청_나라장터 입찰공고정보서비스",
      "data_go_kr_id": "<data.go.kr 데이터 id>",
      "base_url": "https://apis.data.go.kr/<기관코드>/<서비스경로>",
      "format": "json",
      "operations": {
        "getBidPblancListInfoServcPPSSrch": {
          "purpose": "용역 입찰공고 목록(검색조건)",
          "params": {"inqryDiv": "1=공고일자 2=개찰일자", "inqryBgnDt": "YYYYMMDDHHMM", "inqryEndDt": "YYYYMMDDHHMM", "bidNtceNm": "공고명(선택)"}
        }
      }
    }
  },
  "common_params": {"numOfRows": "페이지 크기(기본 50)", "pageNo": "페이지(기본 1)", "type": "json"}
}
```

- `load_registry(path) -> Registry`: 스키마 검증(필수 키, base_url 호스트 `apis.data.go.kr`).
- `render_catalog_md(registry) -> str`: 모델용 카탈로그 마크다운(서비스별 표: operation·용도·파라미터 + 공통 파라미터·날짜 형식 규약). 이 문자열이 계획 프롬프트의 `{{OPENAPI_CATALOG}}`에 들어간다.
- 초기 레지스트리 내용은 data.go.kr 상세 페이지에서 **실제로 읽은 오퍼레이션만** 기재(추정 금지). 상세기능이 미노출된 서비스는 `operations: {}` + `note`로 두고 카탈로그에 "참고문서 확인 필요"로 표시 → 모델은 그 서비스를 계획에 넣지 않는다.

### C2. 수집계획 프롬프트 (`prompts/research/openapi_plan.md`)

입력: `{{REQUEST}}`, `{{SEED_CONTRACT}}`, `{{OPENAPI_CATALOG}}`, `{{MAX_CALLS}}`.
규칙: 요청과 무관하면 빈 목록도 허용. 카탈로그에 있는 service/operation 이름만 사용.
날짜는 카탈로그 규약대로. 호출 수 상한 `{{MAX_CALLS}}`(config `openapi_max_calls`, 기본 12).

출력(엄격, 코드가 파싱):

```
OPENAPI_PLAN_JSON
```json
{"calls": [{"id": "c1", "service": "bid_notice", "operation": "getBidPblancListInfoServcPPSSrch",
            "params": {"inqryDiv": "1", "inqryBgnDt": "202509010000", "inqryEndDt": "202509232359", "numOfRows": "50"},
            "purpose": "최근 30일 용역 입찰공고"}]}
```
```

역할은 기존 `researcher`(Claude 라우팅) 재사용, 티어는 `light`(계획 한 번). 스텝 이름
`01_openapi_plan`. dry-run 출력은 `{"calls": []}`(HTTP 없음).

### C3. 실행기 + 스냅샷 (`autoagent/data/openapi.py`)

- `parse_plan(raw) -> Plan`: `OPENAPI_PLAN_JSON` 마커 뒤 fenced JSON 파싱(기존 `extract_json_block` 재사용). 실패 시 빈 계획 + 에러 기록.
- `validate_plan(plan, registry, max_calls) -> (valid_calls, rejected)`: 미등록 service/operation·상한 초과·id 중복은 거부 목록으로.
- `build_url(registry, call, key) -> str`: `base_url/operation?serviceKey=<key>&type=json&<params>`(`urllib.parse.urlencode`; 키는 Decoding 원문을 인코딩해 전달). `redact_url(url)`은 serviceKey 값을 `***`로.
- `execute_plan(plan, registry, key, out_dir, *, fetch=default_fetch, timeout=20) -> Manifest`:
  호출마다 `openapi/<id>.json`(응답 본문 원문; XML이면 `.xml`) 저장, 항목 메타
  `{id, service, operation, purpose, url_redacted, http_status, fetch_ts, sha256, result_code, result_msg, row_count, error}`.
  `fetch`는 주입 가능(테스트는 가짜 함수). 호출 실패·타임아웃·비JSON은 **해당 항목 error로 기록하고 계속**(수집 전체는 실패시키지 않음). 개별 호출 사이 0.2s 대기(개발계정 예의).
  data.go.kr 표준 응답(`response.header.resultCode`/`response.body.items`/`totalCount`)은 best-effort로 `result_code`·`row_count`를 채운다.
- `write_manifest(out_dir, manifest)` → `openapi_manifest.json`. `render_openapi_summary(manifest, run_dir) -> str`: 스테이지 프롬프트용 마크다운 표(id·service·operation·목적·상태·row_count·**절대경로**) + "인용 시 `openapi/<id>.json` 경로를 source_refs에 명시" 안내. 수집이 없었으면 "(공공데이터 스냅샷 없음)".

### C4. 워크플로 배선 (`autoagent/workflows/research.py`, `autoagent/config.py`, `autoagent/cli.py`)

- config: `data_go_kr_service_key: str | None`(config > env), `openapi_max_calls: int = 12`, `openapi_registry_path`(기본 패키지 내 JSON, 구현 보류 — 기본 레지스트리 경로만 사용, 필요 시 후속). `autoagent.config.example.json`에 키 필드 예시(빈 문자열) 추가.
- `run_research_workflow`: seed pin 직후, `state.get("openapi")`가 없으면
  1. 키 없음 → `print("[openapi] 인증키 없음 - 공공데이터 수집 생략")`, `state["openapi"]={"skipped":"no_key"}`.
  2. 있음 → `_run_agent_step(name="01_openapi_plan", prompt_name="openapi_plan.md", ...)` → parse/validate → `execute_plan` → manifest → `state["openapi"]={"plan":..., "rejected":..., "manifest":"openapi_manifest.json"}` persist.
  dry-run은 계획 프롬프트·커맨드만 렌더하고 빈 계획으로 manifest(0건)를 기록해 전 경로를 통과한다.
- 스테이지 프롬프트 a/b/c/d/derive에 `{{OPENAPI_DATA}}` 섹션 추가("## 하네스가 수집한 공공데이터 스냅샷"). `_run_agent_step` 호출부의 prompt_values에 `OPENAPI_DATA=render_openapi_summary(...)`를 공급(수집 없으면 "(없음)" — 미치환 잔존 금지).
- env 전파: 키가 config에서 왔으면 `os.environ.setdefault("DATA_GO_KR_SERVICE_KEY", key)`는 **하지 않는다**(비목표 ad-hoc 호출을 위한 전파 불필요; 서브프로세스에 키를 흘리지 않음).

### C5. 라이브 스모크 (`scripts/openapi_smoke.py`)

키(config/env)로 레지스트리의 지정 오퍼레이션 1건을 `numOfRows=1`로 호출해 `http_status`·`result_code`·`row_count`를 ASCII+한글로 출력. pytest 대상 아님. 최종 검증에서 **1회 실제 호출**로 키·엔드포인트 동작을 확인한다(레포 관례 "라이브 미실증"을 이 부분만큼은 해소).

## 데이터 흐름

```
request → seed(00) → [openapi_plan(01, Claude light) → validate → execute → openapi/*.json + manifest]
        → outer loop(a,b,c,d,derive; 각 프롬프트에 OPENAPI_DATA 요약) → 최종 HTML
```

## 오류 처리

- 키 없음/레지스트리 로드 실패: 수집 생략 + 경고, 리서치 진행.
- 계획 파싱 실패: 빈 계획, `01_openapi_plan_error.txt`에 사유.
- 개별 호출 실패: manifest 항목 `error`, 나머지 계속. 전부 실패해도 리서치 진행.
- 미등록 오퍼레이션: `rejected`에 기록, 스테이지 요약에 "거부된 계획 N건" 표기.

## 무회귀 계약

- 키 미설정 머신에서 research dry-run/실행 결과 구조는 기존과 동일(+ `state["openapi"].skipped`, 프롬프트의 OPENAPI_DATA="(없음)").
- 다른 워크플로(simple/routed/decompose) 영향 없음.
- 기존 212+11 pytest 전부 통과.

## 검증 계획

- pytest 신규 `tests/data/test_openapi.py`(+ `tests/research/test_openapi_prompt_render.py`):
  레지스트리 로드·호스트 검증·카탈로그 렌더 / 계획 파싱(정상·마커 없음·JSON 깨짐) /
  validate(미등록·상한·중복) / build_url·redact / execute_plan(가짜 fetch: 성공 JSON·HTTP 500·타임아웃·XML) /
  manifest·summary 렌더 / 키 없음 스킵 / 프롬프트 렌더에 미치환 `{{` 잔존 없음.
- dry-run: `python run.py --dry-run --workflow research --workspace . --request "..."` exit 0, `01_openapi_plan_prompt.md`·`openapi_manifest.json`(0건) 생성.
- 라이브: `scripts/openapi_smoke.py` 1회 실제 호출 성공(http 200, resultCode 00) 확인 후 보고. 전체 리서치 라이브 런은 여전히 사용자 인계.

## 비목표

- Claude 리서처의 ad-hoc API 직접 호출(키 노출 위험) — 대신 후속으로 "심화 pass에서 재계획·재수집" 검토.
- 응답 스키마별 정규화/CSV 변환(원문 JSON 스냅샷까지만; `csv_validator` 연계는 후속).
- 운영계정 전환·트래픽 관리.
