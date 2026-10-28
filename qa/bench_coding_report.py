"""Aggregate bench2 result files into markdown tables."""
import json
import sys
from collections import defaultdict


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


rows = []
for path in sys.argv[1:]:
    try:
        rows += [json.loads(line) for line in open(path)]
    except FileNotFoundError:
        pass
groups = defaultdict(list)
for r in rows:
    groups[(r["label"], r["model"])].append(r)

print("| 조건 | 모델 | 성공 | 코드 정확도(현재 코드 테스트 통과) | JSON 유효 | 패치 적용/시도 | 복구/실패발생 | 반복 plan·read | 호출/과제 | 실제 토큰 입력+출력 (과제 평균) | 예약 토큰 (과제 평균) | 시간/과제 | 로딩/과제 | VRAM 최대 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for (label, model), rs in groups.items():
    ok = [r for r in rs if r["status"] != "harness_error"]
    n = len(ok)
    if not n:
        print(f"| {label} | {model} | 미실행 |" + " |" * 11)
        continue
    done = sum(r["status"] == "completed" for r in ok)
    correct = sum(r["tested_current"] and r["tests"] == "passed" for r in ok)
    jv = sum(r["json_valid"] for r in ok)
    rep = sum(r["replies"] for r in ok)
    pa = sum(r["patch_applied"] for r in ok)
    pt = sum(r["patch_attempts"] for r in ok)
    fail = sum(r["had_failure"] for r in ok)
    rec = sum(r["recovered"] for r in ok)
    pin = sum(r["prompt_tokens"] for r in ok) / n
    pout = sum(r["output_tokens"] for r in ok) / n
    unm = sum(r["unmeasured_calls"] for r in ok)
    print(f"| {label} | {model} | **{pct(done, n)}** | {pct(correct, n)} | {pct(jv, rep)} | {pa}/{pt} | {rec}/{fail} | "
          f"{sum(r['repeats'] for r in ok)} | {sum(r['calls'] for r in ok) / n:.1f} | "
          f"{pin:,.0f}+{pout:,.0f}={pin + pout:,.0f}{f' (미측정 {unm})' if unm else ''} | "
          f"{sum(r['reserved_tokens'] for r in ok) / n:,.0f} | {sum(r['wall_sec'] for r in ok) / n:.0f}초 | "
          f"{sum(r['load_seconds'] for r in ok) / n:.1f}초 | {max(r['vram_peak_mib'] for r in ok):,}MiB |")

print("\n### 과제별 결과")
tasks = sorted({r["task"] for r in rows})
labels = list(dict.fromkeys((r["label"], r["model"]) for r in rows))
print("| 조건 | 모델 | " + " | ".join(tasks) + " |")
print("|---|---|" + "---|" * len(tasks))
for label, model in labels:
    cells = []
    for t in tasks:
        r = next((x for x in rows if x["label"] == label and x["model"] == model and x["task"] == t), None)
        if r is None:
            cells.append("미실행")
        elif r["status"] == "completed":
            cells.append(f"✅ {r['wall_sec']:.0f}s/{r['calls']}회")
        else:
            cells.append(f"❌ {r['status']} ({r['calls']}회)")
    print(f"| {label} | {model} | " + " | ".join(cells) + " |")
