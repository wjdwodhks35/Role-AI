"""Append only the new foundation test selectors to the existing Excel plan."""

import ast
from copy import copy
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / "qa/Role-AI_unit_tests.xlsx"
    workbook = load_workbook(path)
    sheet = workbook["단위테스트"]
    existing = {row[9] for row in sheet.iter_rows(min_row=2, values_only=True)}
    added = 0
    for filename, requirement, category in [
        ("test_agent_registry.py", "REQ-AGENT-CONFIG", "역할 구성·권한 경계"),
        ("test_baseline_manifest.py", "REQ-BASELINE", "읽기 전용 기준선"),
        ("test_coding_workspace.py", "REQ-CODING-WORKSPACE", "코딩 작업·격리 파일 변경"),
        ("test_strategy_events.py", "REQ-STRATEGY-RECORDER", "최소 전략 경험 기록"),
        ("test_coding_runtime.py", "REQ-CODING-RUNTIME", "코딩 실행·복구·체크포인트"),
        ("test_coding_evaluation.py", "REQ-CODING-EVAL", "컨텍스트·복구·평가 예산 경계"),
        ("test_coding_stability.py", "REQ-CODING-STABILITY", "코딩 모델 연동·패치 복구·토큰 계측"),
    ]:
        tree = ast.parse((ROOT / "tests" / filename).read_text())
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            selector = f"tests/{filename}::{node.name}"
            if selector in existing:
                continue
            order = sheet.max_row
            previous = sheet.max_row
            sheet.append(
                [
                    str(order),
                    f"UT-{order:03d}",
                    requirement,
                    category,
                    "단위/API",
                    node.name,
                    "합성 설정·임시 DB·로컬 fixture",
                    "설정·인증·격리·무변경 조건을 pytest로 검사",
                    "무권한 접근·실행 차단, 비밀 및 기존 상태 보존",
                    selector,
                    "미실행",
                    None,
                    None,
                    None,
                ]
            )
            for target, source in zip(sheet[sheet.max_row], sheet[previous]):
                target._style = copy(source._style)
                target.alignment = copy(source.alignment)
            existing.add(selector)
            added += 1
    changed = bool(added)
    for row in workbook["요약"]:
        if row[0].value == "계획":
            description = f"{sheet.max_row - 1}개 시나리오; 매개변수별 실행 수는 결과에 기록"
            if row[1].value != description:
                row[1].value = description
                changed = True
    coverage = workbook["요구사항커버리지"]
    names = {row[0].value for row in coverage}
    for name, scope in [
        (
            "REQ-AGENT-CONFIG",
            "여섯 역할 정의/설정 검증/권한 확대 차단/로컬 소유자 조회/기존 대화 유지",
        ),
        ("REQ-BASELINE", "소스·설정 해시/Git 식별/원문·비밀 제외/기존 보고서 덮어쓰기 방지"),
        (
            "REQ-CODING-WORKSPACE",
            "작업·변경 승인 해시/격리 worktree/원본 보존/경로·수정량 제한/실행 금지/diff",
        ),
        ("REQ-STRATEGY-RECORDER", "최소 메타데이터/범위/장애/비밀 미저장"),
        ("REQ-CODING-RUNTIME", "탐색/반복 수정/격리 명령/오류 복구/체크포인트/중단·재개/예산·승인"),
        (
            "REQ-CODING-STABILITY",
            "thinking 제어/응답 오류 분류/작업 메모리·반복 중단/패치 정규화·복구/실측 토큰 정산/keep_alive",
        ),
    ]:
        if name not in names:
            coverage.append([name, scope, "자동 검증; 실제 모델·학습·운영 반영 없음"])
            changed = True
    if changed:
        sheet.auto_filter.ref = sheet.dimensions
        workbook.save(path)
    print(f"Added {added} foundation scenarios; existing plan rows preserved.")


if __name__ == "__main__":
    main()
