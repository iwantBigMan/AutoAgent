"""AutoAgent 실행 진입점(entry point).

`python run.py ...` 로 호출하면 CLI(main)를 구동한다. 실제 인자 파싱과
워크플로우 분기는 autoagent/cli.py 의 main()에서 이뤄진다.
"""
from autoagent.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
