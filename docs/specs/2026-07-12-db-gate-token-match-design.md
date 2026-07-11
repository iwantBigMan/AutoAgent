# DB 게이트 토큰 매칭 설계

> 작성일: 2026-07-12 · 상태: 설계(승인 대기)

## 목표

DB 승인 게이트가 **substring 오발**로 뜨는 문제를 없앤다. `db_score` 같은 코드
심볼이 `"db"`에 걸려 subtype=db·risk=high로 불필요한 승인 게이트를 띄우던 것을
토큰 매칭으로 교정한다.

## 배경 — 현재 오발

`autoagent/routing.py`:

```python
db_score = sum(1 for term in DB_TERMS if term in lowered)   # substring
```

- `"db"`(2글자) → `db_score`·`mongodb`·`adblock` 등에 걸림 ← **관측된 오발**
  (라우팅 의도 가드 구현 요청에서 요청문의 `db_score` 언급이 게이트를 띄움)
- `"table"` → `comfortable`·`portable`, `"index"` → `index.html` 에도 걸림

`db_score > 0`이면 `subtype=db`·`risk_level=high` → `is_high_risk` → 승인 게이트.
즉 요청에 코드 심볼/일반 단어만 있어도 게이트가 뜬다.

## 안전 비대칭 (이 fix의 핵심 제약)

라우팅 오분류와 **정반대 방향**이다.

- **DB 게이트 과다발동**(불필요한 게이트) = 안전하지만 귀찮음.
- **DB 게이트 누락**(실제 DB 변경인데 게이트 없이 자동 실행) = **위험**.

→ 따라서 **명백히 가짜인 매치만 제거**하고, 실제 DB 요청(standalone·복수형)은
절대 놓치지 않게 보수적으로 간다. `HIGH_RISK_TERMS`는 손대지 않는다(과다발동이
안전 방향).

## 설계 — 식별자 토큰 매칭

`db_score` 계산만 substring → **토큰 매칭**으로 교체. `import re`는 라우팅 의도
가드(선행 PR)에서 이미 추가됨.

```python
def db_term_count(lowered: str) -> int:
    """DB_TERMS 중 요청에 실제 등장한 개수. 식별자 토큰 매칭으로 substring 오발 방지.

    'db'가 'db_score' 코드 심볼에, 'table'이 'comfortable'에 걸리던 것을 막는다.
    단어 용어는 토큰 완전일치(복수형 -s/-es 허용), 다단어 용어('foreign key')는 부분일치.
    """
    tokens = set(re.findall(r"[a-z0-9_]+", lowered))
    def hit(term: str) -> bool:
        if " " in term:            # 다단어 용어는 부분일치 유지
            return term in lowered
        return any(t == term or t == term + "s" or t == term + "es" for t in tokens)
    return sum(1 for term in DB_TERMS if hit(term))
```

호출부:
```python
db_score = db_term_count(lowered)   # 기존 sum(... in lowered) 대체
```

- `db_score` → 토큰 `db_score`는 통째로 한 토큰이라 `db`와 불일치 → **0** (오발 해소)
- `comfortable`/`portable` → `table` 불일치 (덤으로 교정)
- `users table`·`columns`·`schemas`·`indexes` → standalone·복수형 **정상 매치**(누락 없음)
- `DB를 수정` → `를`가 토큰 경계를 끊어 `db` 토큰 **유지** → 매치됨

`[a-z0-9_]+`가 언더스코어를 토큰에 포함하므로 `db_score`가 쪼개지지 않는 것이 핵심.
한국어 조사/문자는 `[a-z0-9_]`가 아니라 토큰을 끊어 standalone 영어 DB 용어를 살린다.

## 스코프

- **`DB_TERMS` 매칭만** 교체. `high_risk_score`(substring)는 **불변** — 여기선
  과다발동이 안전 방향이고, 토큰화하면 `authentication`·`migrations` 같은 진짜
  위험어를 놓칠 수 있어 오히려 위험.
- `db_score`가 쓰이는 하류 로직(subtype/risk/gate)은 **불변** — 입력 계산만 정밀화.

## 하위호환 / 회귀

- 실제 DB 요청(standalone·복수형 DB 용어) → `db_score>0` 유지 → subtype=db·risk=high·게이트 **유지**.
- 반환 dict 11키·순서·`route_task` 시그니처 불변.
- 명시 `--task-type` 경로·`high_risk_score`·`is_high_risk`·`approval_required` 로직 불변.
- 라우팅 의도 가드(선행 PR)와 무간섭(별개 변수).

## 엣지 (잔여, 수용)

- `index.html`의 `index`, 배열 `index` → DB `index`와 구분 불가(토큰 `index` 일치).
  현행 substring도 동일하게 매치하므로 **회귀 아님**. `index` 의미 중의성은 스코프 밖.
- `indices`(라틴 복수)·`mongodb`(식별자 결합) → 미매치. 드묾, 안전 비대칭상 게이트
  누락이지만 실사용 빈도 낮음. 필요 시 별도 보강.

## 검증 (테스트 스위트 없음 → 순수함수 표 체크)

`db_term_count`와 `route_task`를 직접 호출해 확인한다.

```python
from autoagent.routing import db_term_count, route_task

# 1) db_term_count 단위
assert db_term_count("routing.py의 db_score 오버라이드 앞에 둔다") == 0   # 오발 해소
assert db_term_count("make the button comfortable") == 0                  # table 오발 해소
assert db_term_count("add a column to the users table") >= 2              # column+table
assert db_term_count("create schemas and indexes") >= 2                   # 복수형 -s/-es
assert db_term_count("DB를 마이그레이션") >= 1                            # 조사 경계로 db 유지

# 2) 게이트 레벨 회귀 (route_task subtype/risk)
r_false = route_task("backend", "routing.py의 db_score 오버라이드 수정")
assert r_false["subtype"] != "db" and r_false["risk_level"] != "high"     # 오발 게이트 없음

r_real = route_task("backend", "add a column to the users table")
assert r_real["subtype"] == "db" and r_real["risk_level"] == "high"       # 실제 DB 게이트 유지
```

전 케이스 통과 + 반환 dict 계약 불변을 확인한다.
