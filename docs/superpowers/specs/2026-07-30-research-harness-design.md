# 리서치 하네스 (영업/데이터 리서치용 다단계 검증 루프) — 설계

**작성일:** 2026-07-30
**상태:** 설계 확정(스펙 검토 대기)

## 목표

AutoAgent 하네스에 **`--workflow research`** 를 신설한다. 영업/데이터 리서치를 위한
**다단계 검증 루프** 워크플로로, 코드 하네스의 오케스트레이션 골격(크로스모델 검증·
승인 게이트·run 아티팩트·`render_template`·opposite-model 라우팅)을 **그 자리 재사용**하고
리서치 전용 부분만 신설한다(옵션 ① — 별도 레포 아님).

한 번의 run = **단일 파이프라인**이 스테이지를 관통하며, 스테이지마다 **검증 게이트**를 두고,
**중첩 루프**로 품질 수렴 + 심화를 얻는다.

- 스테이지: `a 회사리서치 → b 시장분석 → c CSV 데이터정제 → d 웹 팩트리포트 → derive 도출`
- 중첩 루프: **바깥**(전체 심화, 최대 2회) × **안쪽**(스테이지별 리서치→검증→보정, 통과까지 최대 3회)
- 무료 소스만: 웹(Claude 네이티브 WebSearch/WebFetch) + CSV 파일덤프
- 산출: 인용 각주 붙은 **standalone HTML 리포트**(바탕화면, 내부 검토용) + `runs/` 감사추적

## 배경 — 왜 코드 하네스를 그대로 못 쓰나

코드 하네스의 검증 스테이지(pytest/build)·MCP(serena/context7)·프롬프트·워크스페이스(git 레포)
전제가 전부 코드 전용이라 리서치엔 헛돈다. 반면 **오케스트레이션 골격은 도메인 무관**이라
재사용 가치가 크다. deep-research 스킬 대비 이 하네스가 더 주는 것은 정확히 세 가지:
**① 크로스모델 적대적 검증(구현자≠리뷰어 이식) ② 인간 승인 게이트 ③ 영속 감사추적(runs/)** —
이 셋이 필요하므로 하네스 모양으로 만든다.

## 설계

### §1 아키텍처 — 중첩 루프 엔진

```
run_research_workflow()                         # autoagent/workflows/research.py (신설)
  ├─ preamble: canonical seed 확정 (Claude)      # §5 seed 계약 — 바깥 루프 불변식
  ├─ for outer_pass in 1..2:                     # 바깥 = 전체 심화
  │     for stage in [a, b, c, d, derive]:
  │        run_stage_loop(stage, outer_pass)     # 안쪽 = 리서치→검증→보정
  │        contract_check(stage 산출물)           # 스테이지 경계 계약(§6)
  │        maybe_human_gate(분기점만)             # §6 게이트
  │     convergence_gate(outer_pass)             # 수렴 판정 — 개선 없으면 조기 종료
  └─ final: 인용 HTML 리포트 렌더 + 커버리지 매트릭스

run_stage_loop(stage, outer_pass):              # 안쪽 루프 (최대 3)
  for inner_round in 1..3:
     researcher_out = call_researcher(stage)                 # Claude 또는 Codex(§3)
     verdict = VERIFY_ADAPTERS[stage](researcher_out)        # crossmodel | data_quality | source_grounding
     persist_status(...)                                     # 매 전이 영속 (재개용)
     if verdict.pass: return (resolved, out)
     if verdict.blocked: escalate_to_gate(); return
     if no_progress(researcher_out): break                   # 무진전 조기 종료(§8 F2)
     feedback = verdict.findings
  return (exhausted_unverified, out)                         # ★ silent pass-through 금지(§8 F1)
```

바깥 루프는 심화 전용(1회차 개괄 → 2회차 정밀), 안쪽 루프는 스테이지 품질 수렴 전용.
상한(2/3)은 **안전밸브지 목표 아님** — 수렴하면 조기 종료.

### §2 데이터/툴 층

**웹**: Claude 네이티브 `WebSearch`/`WebFetch`(MCP·allowlist 불필요), 긴 페이지는 `defuddle`로
클린화. **실측 제약**: 네트워크 MCP는 Codex exec 샌드박스에서 차단되므로 **Codex는 웹을 못 쓴다.**
→ **웹 fetch는 항상 Claude(또는 하네스 코드)가 수행해 `runs/sources/*.txt` 스냅샷으로 저장**하고,
이후 모든 대조(Codex 검증기 포함)는 **그 스냅샷만** 읽는다(재fetch 변동·링크썩음 배제).
제외: Exa(유료)·context7/mcp-fetch(Codex 차단)·serena(코드 분석용)·Playwright(미검증).

**데이터 파일 층** (`autoagent/data/` 신설):
- **CSV**: stdlib `csv`(의존성 0). `csv_validator.py: validate_csv(path) -> CSVQualityMetrics`.
- **XLSX**: 이번 슬라이스 **제외**(§7 결정2). 나중에 `openpyxl`(무료)로 확장자 분기 확장.
- **인코딩(cp949 gotcha)**: `utf-8 → utf-8-sig → cp949` 폴백 자동감지, 실패 시 **정직한 error**
  (조용한 skip 금지). 입력 파일 sha256 고정.
- **Google Sheets 무료 API**: `enabled:false` opt-in만 열어둠(지금은 CSV 덤프 기본).

`CSVQualityMetrics`: `row_count, column_count, columns[], null_ratio_by_column{},
duplicate_row_count, duplicate_ratio, format_anomalies[], encoding_detected`.

**산출물 층**: `render_template()`로 `prompts/research/final_html_report.md`를 채운 뒤
**내부 markdown→HTML 변환**(pandoc 의존 회피, 인라인 CSS)해 바탕화면 standalone HTML로 전달
(deliver-local-html 준수, 아티팩트 아님). 인용 메타 `[n] URL · 발행일자 · fetch_ts · sha`를
스냅샷에서 구조 주입. **커버리지 매트릭스 상단 강제**: 스테이지별 `verify_status`
(passed/exhausted_unverified/failed/skipped) 표, 100% 미만이면 경고 배너.

### §3 크로스모델 배정 (결정: 웹은 전부 Claude)

| 스테이지 | 리서처 | 검증기 | 어댑터 | 근거 |
|---|---|---|---|---|
| a 회사리서치 | Claude | Codex | crossmodel | 웹 종합·한국어 맥락=Claude |
| b 시장분석 | Claude | Codex | crossmodel | 정성 종합=Claude, Codex 수치 대조 |
| c CSV 정제 | Codex | **(코드 실측)** | data_quality | 스키마·타입·결측=Codex, 검증기 슬롯에 모델 무의미 |
| d 팩트리포트 | Claude | Codex | source_grounding | Claude 웹 서사, Codex 스냅샷 인용 대조 |
| derive 도출 | Claude | Codex | crossmodel | 종합·논리=Claude, Codex 과대추론 검증 |

**핵심 귀결**: 이 배정에서 **Codex는 웹이 전혀 필요 없다** — 모든 웹 리서치는 Claude가 하고,
Codex는 (c 데이터정제 리서처, 로컬 파일 대상) + (a·b·d·derive 검증기로서 스냅샷/로컬 파일만
읽어 대조)만 한다. §2의 샌드박스 웹 차단이 완전히 우회된다.

**불변식**: 검증기는 **항상 반대 모델**(`choose_implementer`의 구현자≠리뷰어와 동형).
바깥 심화 2회 사이에도 쌍 고정. 티어: 리서처=standard, 고위험 스테이지만 deep 승격
(`resolve_role` high_risk 패턴). c 코드검증=모델 0회.

**라우팅 매핑(최소 변경)**:
- `routing.py`에 `choose_researcher(stage) -> (researcher, verifier, reason)`
  (계약=`choose_implementer`와 동일). 테이블 `{a:claude, b:claude, c:codex, d:claude, derive:claude}`,
  verifier는 반대 모델을 **코드가 기계 계산**.
- `route`에 `stage_assignments:{a:{researcher,verifier},...}`.
- `roles.default.json`에 `researcher`(tier 표준)·`verifier`(`mutating:false`, `permission:"plan"` —
  리뷰어 posture, 편집·실행 불가) 2종.
- c 코드검증=기존 verification 스테이지를 c 안쪽 루프 '검증' 자리에 끼움(모델 0회).

### §4 3종 검증 어댑터

공통 계약: `verify(stage_out) -> Verdict`. verdict를 `runs/` JSON으로 남기고, **모델 자유서술이
아니라 코드가 findings를 집계해 verdict를 재계산**한다. 3값:
`pass`(다음 스테이지) / `needs_changes`(안쪽 루프 반송, feedback=findings) / `blocked`(판정 불가 → 인간 게이트).

#### §4.1 `crossmodel` — 적대적 (a·b·derive)

- **입력**: 산출물 + **원문 evidence_bundle**(요약본 아님).
  `{stage_id, claims[]:{id,text,kind(fact|inference|recommendation),source_refs[],confidence},
  narrative_md, evidence_bundle:{sources[]:{ref_id,url|file,fetched_text_excerpt,fetch_ts}},
  loop_ctx:{outer_pass,inner_round,prior_verdict}}`.
- **메커니즘**: 반대 모델 1회. "깐깐한 반박 검증자"(`codex_review.md` 계보) — "방어 말고 공격:
  (1)인용 소스가 실제 지지하나 (2)추론이 사실을 넘나 (3)누락 축. **최소 N개 약점 강제**
  (N=config `crossmodel_min_findings`, 기본 3), 없으면 소스 ref로 증명."
  티어=backend high-risk 동급(codex deep / claude opus).
- **verdict 스키마**: 첫 줄 마커 `CROSSMODEL_VERDICT: pass|needs_changes|blocked` + fenced JSON:
  `{schema_version, adapter:"crossmodel", stage_id, verdict, findings[]:{claim_id|null,
  severity(critical|major|minor), category(unsupported|overreach|logic_gap|scope_miss|stale|
  contradiction|hallucinated_source), quote, rebuttal, fix_directive, evidence_pointer},
  coverage:{axes_checked[], axes_missing[]}, unchallenged_but_weak[], reviewer_model, tokens_seen}`.
  코드는 마커+JSON만 파싱(free-text 무시), `artifacts.extract_json_block` 재사용.
- **pass 기준(코드 재계산)**: severity∈{critical,major} 0건 AND `axes_missing` 비어있음 AND blocked 없음.
  검증기가 "pass"라 적어도 major finding 있으면 **코드가 needs_changes로 강등**(자기모순 방지).
- **false-pass 완화**: ①공모→"오직 첨부 `fetched_text`만 근거, 모델 지식으로 채운 주장=unsupported".
  ②gaming→약점 쿼터 N + `unchallenged_but_weak` 필수 + `tokens_seen` 교차검사(bundle 넘겼는데
  findings가 소스 미참조면 코드 자동 needs_changes). ③아첨→"약점 0 선언 시 축별 소스 근거 대야 pass".

#### §4.2 `data_quality` — 코드 실측 (c)

- **검증기=코드**(stdlib `csv`, pandas 불필요). 모델 0회. `verification.run_verification` 골격 재사용하되
  shell allowlist 대신 **하네스 소유 고정 파이썬 체크 세트**를 워크스페이스 venv python으로 실행.
  임계값은 **config로만** 조정(에이전트 못 바꿈 → tautology 차단).
- **입력**: `{cleaned_files[]:{path,source_dump_path}, transform_manifest:{steps[]:{op(dedup|type_cast|
  null_fill|filter|join|derive_col),target_cols[],params}}, derived_claims[]:{id,text,
  backing_stat:{metric,value,col,filter}}, schema_expectations:{col:dtype}}`.
- **verdict 스키마**(`04b_verification.json` 계승): `{schema_version, adapter:"data_quality",
  stage_id:"c", overall_ok(bool), checks[]:{name,status(pass|fail|error|skipped),metric_expected,
  metric_actual,detail,file,col}, recompute[]:{claim_id,claimed_value,recomputed_value,tolerance,
  match(bool)}, row_delta:{source_rows,cleaned_rows,dropped,drop_reason_breakdown{}},
  schema_diff[]:{col,expected_dtype,actual_dtype,ok}, provenance:{source_hash,cleaned_hash,files_read[]}}`.
- **pass 기준**: `overall_ok` = checks 전부∈{pass,skipped} AND `recompute[].match` 전부 AND
  schema_diff 전부 ok. 핵심 체크: (1)행수 보존/설명(dropped가 manifest로 100% 설명, 미설명 유실=fail),
  (2)**claim 재계산**(원본에서 독립 재산출, tolerance 내), (3)스키마 정합, (4)sanity(중복키·범위이탈·
  미래날짜·음수매출). `error`(파일 못 읽음)=fail, `skipped`(해당없음)와 구분.
- **false-pass 완화**: 로직 공모→manifest 재실행 말고 **원본에서 독립 경로로 claim 재계산**.
  tolerance 느슨함→kind별 고정(합계·행수=정확일치, 비율·CAGR만 1%). garbage-in→sanity 강제,
  raw 이상치는 provenance와 함께 d·게이트로. cp949→해시 고정·인코딩 명시·실패는 error 노출.

#### §4.3 `source_grounding` — 하이브리드 (d)

- **입력**: `{claims[]:{id,text,kind,cited_source_refs[],quoted_span}, sources[]:{ref_id,url,
  fetched_text(runs/sources/*.txt 스냅샷),fetch_ts,http_status}, report_md}`. 검증기는 **하네스 저장
  원문 스냅샷**을 받는다(재fetch 변동 배제) — Codex도 로컬 파일이라 읽을 수 있음.
- **메커니즘(하이브리드)**: ① **코드가 먼저 결정적 실측**: `fabricated_sources`(ref∉sources),
  `dead_sources`(status≠200 or 본문 빈), `orphan_claims`(fact인데 무인용),
  `matched_quote ⊆ fetched_text` 부분문자열. ② **반대 모델(Codex) 의미 대조**(지지 여부·paraphrase 왜곡).
  ③ 코드 병합, **결정적 위반(fabricated/dead/orphan)은 모델 pass여도 강등**.
- **verdict 스키마**: 첫 줄 `GROUNDING_VERDICT: pass|needs_changes|blocked` + fenced JSON:
  `{schema_version, adapter:"source_grounding", stage_id:"d", verdict, claim_checks[]:{claim_id,
  grounding(supported|partially_supported|unsupported|contradicted|no_source), matched_quote(verbatim),
  claim_span, notes, source_ref}, orphan_claims[], dead_sources[], fabricated_sources[]}`.
- **pass 기준**: fact-kind claim 전부 grounding∈{supported,partially_supported} AND orphan/fabricated
  비어있음 AND contradicted 0건. 무인용 사실=자동 needs_changes; 인용했는데 근거 없으면 unsupported;
  소스가 반대면 contradicted(critical); 추천/추론은 backing fact supported면 직접인용 면제.
  `matched_quote ⊆ fetched_text`를 **코드 검증**(날조 방지). dead/fabricated=`blocked`.
- **false-pass 완화**: 근거 날조→부분문자열 검증(verbatim⊄면 unsupported 강제). paraphrase 격상
  (may→will·추정→확정·상관→인과)→overreach 분류. cherry-pick→인용 span 컨텍스트도 넘겨 contradicted 탐지.
  rubber-stamp 공모→결정론 코드 게이트 이중화, approved는 **구조화 근거(quote+URL) 채워졌을 때만** 인정.

### §5 바깥 루프 · seed 계약 (계통 표류 차단)

- **canonical seed(불변식)**: 첫 pass에서 `회사 식별자·시장 정의·기준통화·기간·단위` 확정해
  **read-only로 checkpoint pin**. pass 2는 seed 못 바꾸고 심화만 허용.
- **pass 간 diff**: 검증된 claim 값이 뒤집히면 심화 아닌 **모순**으로 분류 → 게이트.
  deepen 목표를 명시 delta로 좁혀 자유 재작성 차단.
- **수렴 게이트**: pass N vs N-1 검증 claim delta가 임계 이하면 **조기 종료**.
- **as-of 메타**: 시점 의존 claim(주가·환율·시장규모)엔 `as-of 날짜` 필수, pass 간 비교 정렬.

### §6 스테이지 계약 · 게이트 · 재개 · 예산

- **6.1 기계판독 계약**: 자유 markdown 대신 JSON front-matter + 스키마, 전이 직후 코드가 계약 검증
  (필드·타입·enum·엔티티 참조 무결성). 필드명 **영문 고정**(크로스모델 포맷차 흡수).
  `verification.py`처럼 예외로 런 안 죽임, 위반 시 inner 반송 or 게이트.
- **6.2 게이트=분기점 전용**(무인 deadlock 차단): (1)고비용 심화 진입 (2)모순 승격
  (3)`exhausted_unverified` 다수 (4)`blocked`에서만 트리거, 나머지 전이는 자동. 무인 실행 시 도달을
  stdout 고정 라인 + `PushNotification`, 정지 이유·resume_command를 산출물에 기록.
  `--auto-approve-nonbranch`는 분기점 아닌 전이만, **고비용/모순 게이트는 절대 생략 안 함**.
- **6.3 재개**: `research_state.json`에 매 전이 영속(`outer_pass, stage, inner_round, seed_pin,
  verified_claims, per-stage status`). `--resume`는 done 건너뛰고 미완 inner만 이어감, seed pin 고정,
  스냅샷 캐시 재사용(task_exec `persist_status` 패턴).
- **6.4 예산**: 최악 `5×3×2≈90+콜`. **계층 예산**(전역+스테이지별+outer별) 소진 시 `stopped_by_budget`.
  컨텍스트는 **요약+포인터 외부화**(각 스테이지는 seed+확정 claim 요약만). fetch/CSV는
  `MAX_CAPTURE_CHARS`·tail 절단, 원본은 `runs/`. dry-run으로 프롬프트 크기 사전 점검.

### §7 재사용 매핑 · 신설/수정 파일 지도

**재사용(레포 실측 확인)**:
| 관심사 | 재사용 대상 |
|---|---|
| 에이전트 구동/예산 | `runner.py: claude_command·codex_exec_command·run_process·AgentCallBudget` |
| 크로스모델 라우팅 | `routing.py: choose_implementer`, `roles.py: resolve_role`, `roles.default.json` |
| 인간 게이트 | `routed_common.py: block_for_human_approval·resume_command_for·write_checkpoint·approval_required` |
| 데이터 품질 실측 | `verification.py: run_verification·run_verification_or_skip` |
| verdict 파싱 | `artifacts.py: extract_json_block`, `safety.py: review_needs_changes`(마커 우선) |
| 아티팩트/보고 | `artifacts.py: write_text/write_json/render_template/make_run_dir`, `routed_common.run_final_report` |

**신설**: `autoagent/workflows/research.py`(오케스트레이터·게이트), `autoagent/research/adapters.py`
(crossmodel·source_grounding·디스패치), `autoagent/data/csv_validator.py`, `prompts/research/*.md`
(researcher·verifier·final_html_report·seed_contract), `research_state.json`(런타임).
**확장(최소)**: `routing.py`(`choose_researcher`), `roles.default.json`(researcher·verifier 2종),
`verification.py`(data_quality 체크 세트를 c에 연결), `routed_common.py`(게이트/checkpoint 루프 상태 필드),
`run.py`/`cli.py`(`--workflow research`, `--auto-approve-nonbranch`).

## 파급 효과

- `choose_implementer`(routing.py)에 5번째 경로가 아니라 **쌍둥이 함수** `choose_researcher`를 더한다
  (기존 코드 경로 불변). `roles.default.json`에 역할 2종 추가.
- `run.py`/`cli.py`에 `--workflow research` 분기 + `--auto-approve-nonbranch` 플래그.
- 리서치 워크플로는 **serena/context7 MCP를 안 물린다**(코드용). 웹은 Claude 네이티브 툴.

## 비목표 (Out of scope)

- XLSX·Google Sheets·라이브 CRM API(Salesforce/HubSpot) — 이번 슬라이스 밖(무료·CSV 우선).
- 대외 배포용 리포트(레닥션 강화) — 내부 검토용이라 이번엔 완화. 대외 전환 시 별도.
- 에이전트 샌드박스/네트워크 개방 — 하지 않음(Codex는 웹 없이 스냅샷만).
- 코드 하네스(simple/routed/decompose) 동작 변경 — 없음(신규 워크플로만 추가).

## 검증 전략

하네스는 테스트 스위트가 없다. 리서치 워크플로도 동일 원칙:
1. **dry-run**: `python run.py --dry-run --workflow research ...`로 모든 프롬프트/커맨드/스테이지
   렌더를 CLI 호출 없이 검증(기존 규약). dry-run은 `--max-agent-calls`에 안 셈.
2. **결정론 코드 조각 단위 테스트**(신규 — 이 부분은 순수 함수라 테스트 가치 큼):
   `csv_validator.validate_csv`(품질지표), verdict **재계산 로직**(검증기 "pass"인데 major finding이면
   needs_changes 강등), 스테이지 계약 검증, `matched_quote ⊆ fetched_text` 부분문자열 검사,
   행수 보존/claim 재계산.
3. **라이브 실증(백그라운드)**: 소규모 리서치 1건(회사 1곳 + 작은 CSV)으로
   `a→…→derive → HTML` 흐름과 검증 게이트 동작을 산출물로 확인.

## 빌드 슬라이스 순서 (플랜에서 태스크로 확정)

1. **엔진 + 최소 경로**: 중첩 루프 오케스트레이터 + `a 회사리서치 → crossmodel 검증 → derive → HTML`
   한 줄기를 끝까지(재사용 골격 위에서 도는 최소 vertical slice).
2. `data_quality` 어댑터 + `csv_validator` + c 스테이지.
3. `source_grounding` 어댑터 + d 스테이지 + 스냅샷 파이프라인.
4. b 시장분석 스테이지 + 바깥 심화 루프(§5 seed 계약) + 수렴 게이트.
5. 인간 게이트(§6.2) + 재개(§6.3) + 커버리지 매트릭스/경고 배너.

## 위험 / 완화 (적대적 스트레스테스트 반영)

| # | 실패 모드 | 완화 |
|---|---|---|
| F1 | silent pass-through(검증 못 넘긴 걸 조용히 통과) | `exhausted_unverified` 상태 + 리포트 `UNVERIFIED` 배지 격리, derive·신뢰도서 제외 |
| F2 | 무진전 핑퐁 | `no_progress` 조기 종료(정규화 해시) + severity 계약(MINOR fix 제외) |
| F3 | seed drift | canonical seed read-only pin + pass 간 diff 모순 감지→게이트 |
| F4 | gaming/rubber-stamp | 결정론 코드 게이트 이중화 + 구조화 근거 강제 + `tokens_seen` 교차검사 |
| F5 | 예산 폭주(곱연산) | 계층 예산 + 컨텍스트 외부화 + capture 상한 |
| F6 | 무인 deadlock(게이트에서 멈춤) | 분기점 전용 게이트 + PushNotification + `--auto-approve-nonbranch` |
| (med) | adversarial 과잉엄격 거짓발산 | 통과 rubric 명시, 확인불가 claim=`UNVERIFIABLE` 표기 후 통과, 3라운드 못 막으면 approved |
