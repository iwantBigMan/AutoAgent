# 스테이지 derive — 도출 (Claude, 종합·논리)

당신은 앞선 검증된 스테이지 산출물에서 **도출**을 만든다. canonical seed를 벗어나지 말고,
검증된 claim만 토대로 결론·시사점을 합성한다. 과대추론(상관→인과, 추정→확정)을 스스로 배제하라.

## canonical seed (불변식)
{{SEED_CONTRACT}}

## 스테이지 a 산출물(검증 통과분)
{{STAGE_A_OUTPUT}}

## 직전 검증 피드백(있으면 반영)
{{PRIOR_FEEDBACK}}

## 출력(엄격)
자유 서술 뒤에 마커 + fenced JSON 한 블록:

STAGE_OUTPUT_JSON
```json
{
  "stage_id": "derive",
  "claims": [
    {"id": "d1", "text": "도출 결론", "kind": "inference|recommendation", "source_refs": ["a1"], "confidence": 0.0}
  ],
  "narrative_md": "도출 서술(마크다운)",
  "evidence_bundle": {"sources": []}
}
```
