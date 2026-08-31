"""
scripts/paths.py — 데이터 파일 위치

경로를 저장소 루트 기준으로 잡는다. "./x.json"처럼 CWD에 기대면
python -m 을 어디서 실행했느냐에 따라 파일을 못 찾거나, 더 나쁘게는
엉뚱한 위치에 산출물을 만든다.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
