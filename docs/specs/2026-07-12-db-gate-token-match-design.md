# DB 게이트 매칭 정밀화 설계

> 작성일: 2026-07-12 · 상태: 설계(승인 대기)
> (초기 "토큰 매칭" 안은 하네스 리뷰에서 안전 회귀가 드러나 substring-except-db로 교체됨)

## 목표

DB 승인 게이트가 **substring 오발**로 뜨는 문제를 없앤다. `db_score` 같은 코드
심볼이 `"db"`에 걸려 subtype=db·risk=high로 불필요한 승인 게이트를 띄우던 것을,
**실제 DB 용어는 하나도 놓치지 않으면서** 좁게 교정한다.

## 배경 — 현재 오발

`autoagent/routing.py`:

```python
db_score = sum(1 for term in DB_TERMS if term in lowered)   # substring
```

- `"db"`(2글자) → `db_score`·`score_db_cache` 등 snake_case 식별자에 걸림 ← **관측된 오발**
  (라우팅 의도 가드 구현 요청에서 요청문의 `db_score` 언급이 게이트를 띄움)

`db_score > 0`이면 `subtype=db`·`risk_level=high` → `is_high_risk` → 승인 게이트.

## 안전 비대칭 (이 fix의 핵심 제약)

이건 **안전 게이트**의 입력이라 라우팅 오분류와 방향이 반대다.

- **과다발동**(불필요한 게이트) = 안전하지만 귀찮음.
- **누락**(실제 DB 변경인데 게이트 없이 자동 실행) = **위험**.

→ 따라서 매칭을 **전역적으로 조이면 안 된다**(조일수록 누락↑=위험). 느슨한
substring을 유지하되, **관측된 유일한 오발원 `"db"`만 좁게** 배제한다.

### 왜 "토큰 완전일치"가 아닌가 (기각된 초기 안)

처음엔 모든 DB 용어를 식별자 토큰 완전일치(+복수형)로 바꾸려 했으나, 하네스
리뷰가 **안전 회귀**를 잡았다: `postgresql`≠`postgres`, `mysql`≠`sql`,
`mongodb`≠`db` 가 되어 **실제 DB 요청이 게이트를 우회**한다. 게이트를 더
타이트하게 만드는 방향이라 안전 비대칭에 정면으로 반한다 → 기각.

## 설계 — substring 유지 + `db`만 언더스코어 배제

```python
def db_term_count(text: str) -> int:
    """DB_TERMS 중 등장 개수. 'db' 코드 심볼(db_score) 오발만 좁게 배제.

    안전 게이트 입력이라 실제 DB 용어 누락이 최우선 위험이다. 대부분 용어는 느슨한
    부분일치를 그대로 써서 postgres→postgresql, sql→mysql, db→mongodb 결합어까지
    계속 잡는다(과다발동은 안전 방향). 유일한 짧은 오발원 'db'만 snake_case
    식별자(언더스코어 인접)일 때 제외한다. 입력은 어떤 대소문자든 내부에서 소문자화.
    """
    lowered = text.lower()
    count = 0
    for term in DB_TERMS:
        if term == "db":
            if re.search(r"(?<!_)db(?!_)", lowered):   # 언더스코어 비인접 'db'만
                count += 1
        elif term in lowered:                          # 나머지는 느슨한 substring
            count += 1
    return count
```

호출부:
```python
db_score = db_term_count(lowered)   # 기존 sum(... in lowered) 대체
```

- `db_score`·`score_db_cache` → `db`가 언더스코어 인접 → **미포함** (오발 해소)
- `mongodb`·`the db` → `db`가 언더스코어 비인접 → **포함** (누락 없음)
- `postgresql`(postgres+sql)·`mysql`(sql)·`columns`·`schemas`·`indexes`·`DB를` →
  substring/내부 lower로 **전부 포함** (안전 회귀 없음)
- `import re`는 라우팅 의도 가드(선행 PR)에서 이미 추가됨.

## 스코프

- `db_score` 계산(=`db_term_count`)만 교체. **`DB_TERMS` 내용·`HIGH_RISK_TERMS`
  (substring)·하류 subtype/risk/게이트·반환 dict·`route_task` 시그니처·명시
  `--task-type` 경로·라우팅 의도 가드 전부 불변.**

## 하위호환 / 회귀

- `db` 외 모든 DB 용어는 substring 그대로 → 실제 DB 요청 매칭 **동일**(누락 신규 없음).
- `db`는 언더스코어 비인접일 때만 → standalone·결합어(mongodb)는 유지, 코드 식별자만 제외.
- 반환 dict 11키·순서 불변.

## 엣지 (잔여, 수용)

- **`comfortable`→`table`** 등 짧은 일반단어 substring 과다발동은 **원 동작 그대로 유지**
  (이번 스코프 밖, 안전 방향이라 무해). 이번 fix는 `db`만 다룬다.
- **`migrate my_db`** 처럼 snake_case DB 이름의 `db`는 배제됨(부수효과). 저빈도이고,
  같은 요청에 다른 DB 용어(table/column 등)가 있으면 여전히 게이트됨.

## 검증 (테스트 스위트 없음 → 순수함수 표 체크)

`db_term_count`·`route_task`를 직접 호출해 확인한다(정상 python).

```python
from autoagent.routing import db_term_count, route_task

# 오발 제거
assert db_term_count("routing.py의 db_score 오버라이드 앞에 둔다") == 0
assert db_term_count("save it to score_db_cache") == 0
# 안전 회귀 방지 (실제 DB는 반드시 매치)
assert db_term_count("use postgresql for storage") >= 1     # postgres/sql substring
assert db_term_count("switch to mysql") >= 1                # sql substring
assert db_term_count("use mongodb") >= 1                    # db 결합어(언더스코어 비인접)
assert db_term_count("add a column to the users table") >= 2
assert db_term_count("create schemas and indexes") >= 2
assert db_term_count("DB를 마이그레이션") >= 1              # 대문자→내부 lower
# 게이트 레벨
r_false = route_task("backend", "routing.py의 db_score 오버라이드 수정")
assert r_false["subtype"] != "db" and r_false["risk_level"] != "high"
for req in ["use postgresql for storage", "switch to mysql", "DB를 마이그레이션"]:
    r = route_task("backend", req)
    assert r["subtype"] == "db" and r["risk_level"] == "high"
```

전 케이스 통과 + 반환 dict 계약·라우팅 의도 가드 회귀 없음을 확인한다.
