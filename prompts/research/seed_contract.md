# preamble: canonical seed 확정 (Claude)

당신은 리서치 파이프라인의 **불변식 seed**를 확정하는 계획자다. 아래 요청에서
바깥 루프 전체가 공유할 **canonical seed**를 뽑아 고정한다. 이후 pass는 이 seed를
바꿀 수 없고 심화만 허용된다(계통 표류 차단).

## 요청
{{REQUEST}}

## 작업공간
{{WORKSPACE}}

## 확정할 seed 필드(전부 채워라)
- **회사/대상 식별자**: 정확한 법인/제품/시장 대상명
- **시장 정의**: 분석 대상 시장의 범위·세그먼트 경계
- **기준통화**: 예 KRW/USD
- **기간**: 분석 대상 기간(예 2023–2025)
- **단위**: 매출·수량 등의 표기 단위

## 출력(엄격)
첫 줄에 마커, 이어서 fenced JSON 한 블록만 출력하라(자유서술 금지):

SEED_CONTRACT_JSON
```json
{"company": "...", "market": "...", "base_currency": "...", "period": "...", "unit": "..."}
```
