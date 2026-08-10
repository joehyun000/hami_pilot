# 여섯 조건 HAMi 파일럿 하네스 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 네 조건·20회 하네스를 승인된 여섯 조건·30회 설계로 바꾸고, HAMi가 없는 기준 조건·원인 분리 비교·양방향 판정·중단 재개를 GPU 없이 자동 시험한다.

**Architecture:** `config.py`가 승인된 여섯 조건을 유일하게 정의하고, `schedule.py`가 조건을 다섯 묶음으로 무작위화한다. `runner.py`는 조건에 따라 HAMi 연결 여부와 측정 대상·이웃의 사용 한도를 컨테이너 환경변수로 만들고, `smoke.py`와 `calibration.py`가 실제 조건이 만들어졌는지 확인한다. `analysis.py`는 묶음별 99% 완료 경계 비율을 계산하고 `experiment.py`가 30개 결과를 읽어 보고서를 만든다.

**Tech Stack:** Python 3.11+, dataclasses, PyYAML, pytest, Docker 명령 생성, MLPerf Inference v5.1.1 로그, HAMi-core `5091a2fbe1816df1265490f771346730f29e2c8d`

## Global Constraints

- 기준 설계: `docs/파일럿_실험설계안.md`
- C0은 HAMi를 연결하지 않는다.
- C1~C5는 같은 측정용 HAMi 라이브러리 경로를 사용한다.
- 조건은 C0(기준), C1(HAMi 연결만), C2(측정 대상 50%), C3(측정 대상 100%·이웃 100%), C4(측정 대상 50%·이웃 100%), C5(측정 대상 50%·이웃 50%)로 고정한다.
- 모든 조건은 같은 초당 요청 수를 사용한다.
- 본 실험은 여섯 조건 × 다섯 묶음 = 30회다.
- 중심 비교는 C2/C1, C4/C3, C5/C4다.
- 연구 계속 크기 기준은 비율 중앙값 1.10 이상 또는 `1 / 1.10` 이하이다.
- C5/C3은 두 요소가 동시에 바뀌므로 단일 원인 판정에 사용하지 않는다.
- 새 동작은 실패하는 시험을 먼저 확인한 뒤 최소 코드로 구현한다.
- 실제 GPU 실행과 GPU 동작 속도 제어는 장비 정보를 확인한 뒤 별도 실행 계획으로 다룬다.

---

### Task 1: 승인된 여섯 조건과 30회 실행 순서

**Files:**
- Modify: `src/hami_tail_pilot/config.py`
- Modify: `configs/pilot.yaml`
- Modify: `tests/test_config.py`
- Modify: `tests/test_schedule.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `Condition(name, hami_enabled, victim_sm_limit, neighbor_enabled, neighbor_sm_limit)`
- Produces: `PilotConfig.conditions` in exact order `C0` through `C5`
- Consumed by: runner, smoke, calibration, experiment, analysis

- [ ] **Step 1: Write failing configuration tests**

```python
assert [condition.name for condition in config.conditions] == [
    "C0", "C1", "C2", "C3", "C4", "C5"
]
assert config.conditions[0].hami_enabled is False
assert config.conditions[4].victim_sm_limit == 50
assert config.conditions[4].neighbor_sm_limit == 100
```

- [ ] **Step 2: Run the focused tests and verify the old four-condition code fails**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_schedule.py tests/test_cli.py -q`

Expected: FAIL because the current configuration only accepts P0~P3 and creates 20 entries.

- [ ] **Step 3: Implement the fixed condition schema and YAML**

```python
_FIXED_CONDITIONS = {
    "C0": (False, None, False, None),
    "C1": (True, 100, False, None),
    "C2": (True, 50, False, None),
    "C3": (True, 100, True, 100),
    "C4": (True, 50, True, 100),
    "C5": (True, 50, True, 50),
}
```

- [ ] **Step 4: Verify 30-entry deterministic schedules**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_schedule.py tests/test_cli.py -q`

Expected: PASS; every block contains C0~C5 exactly once.

- [ ] **Step 5: Commit**

```bash
git add src/hami_tail_pilot/config.py configs/pilot.yaml tests/test_config.py tests/test_schedule.py tests/test_cli.py
git commit -m "feat: define six-condition pilot schedule"
```

### Task 2: HAMi 없는 기준 조건과 역할별 사용 한도

**Files:**
- Modify: `src/hami_tail_pilot/runner.py`
- Modify: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Condition.hami_enabled`, `victim_sm_limit`, `neighbor_sm_limit`
- Produces: `build_container_command(...) -> list[str]`

- [ ] **Step 1: Write failing command tests**

```python
native = env_values(build_container_command("victim", spec_for("C0"), config, run_dir, assets))
assert native["LD_PRELOAD"] == ""
assert "CUDA_DEVICE_SM_LIMIT" not in native
assert "HAMI_PROBE_OUTPUT" not in native

c4_victim = env_values(build_container_command("victim", spec_for("C4"), config, run_dir, assets))
c4_neighbor = env_values(build_container_command("neighbor", spec_for("C4"), config, run_dir, assets))
assert c4_victim["CUDA_DEVICE_SM_LIMIT"] == "50"
assert c4_neighbor["CUDA_DEVICE_SM_LIMIT"] == "100"
```

- [ ] **Step 2: Verify the tests fail because C0 and C4 do not exist**

Run: `.venv/bin/python -m pytest tests/test_runner.py -q`

- [ ] **Step 3: Make environment generation conditional on HAMi use**

For C0, explicitly pass `LD_PRELOAD=` to override the image default and omit all HAMi-specific variables. Keep model, data, MLPerf configuration and warm-up variables identical. For C1~C5, keep `/opt/hami/libvgpu.so`, independent cache files and `GPU_CORE_UTILIZATION_POLICY=force` only for roles limited below 100%.

- [ ] **Step 4: Run runner tests**

Run: `.venv/bin/python -m pytest tests/test_runner.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/hami_tail_pilot/runner.py tests/test_runner.py
git commit -m "feat: run native and role-specific HAMi conditions"
```

### Task 3: 조건 확인용 짧은 실험

**Files:**
- Modify: `src/hami_tail_pilot/preflight.py`
- Modify: `src/hami_tail_pilot/smoke.py`
- Modify: `tests/test_preflight.py`
- Modify: `tests/test_smoke.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: nested probe records `{condition: {role: ProbeMetrics}}`
- Produces: `smoke.json` with victim and neighbor records separated

- [ ] **Step 1: Write failing role-aware wait tests**

The expected paths are:

```text
C1 victim: no wait
C2 victim: wait
C3 victim: no wait, neighbor: no wait
C4 victim: wait, neighbor: no wait
C5 victim: wait, neighbor: wait
```

- [ ] **Step 2: Verify old P0/P1/P3 smoke tests fail**

Run: `.venv/bin/python -m pytest tests/test_preflight.py tests/test_smoke.py tests/test_cli.py -q`

- [ ] **Step 3: Implement nested role validation and reports**

Parse the neighbor `hami_probe.jsonl` only when the condition enables a neighbor. Do not expect a probe file for native C0.

- [ ] **Step 4: Verify focused tests**

Run: `.venv/bin/python -m pytest tests/test_preflight.py tests/test_smoke.py tests/test_cli.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/hami_tail_pilot/preflight.py src/hami_tail_pilot/smoke.py tests/test_preflight.py tests/test_smoke.py tests/test_cli.py
git commit -m "feat: validate six-condition wait paths"
```

### Task 4: 제한 조건을 기준으로 공통 요청량 선정

**Files:**
- Modify: `src/hami_tail_pilot/calibration.py`
- Modify: `configs/calibration_measurements.example.json`
- Modify: `tests/test_calibration.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: candidate results for C2, C4 and C5
- Produces: common `target_qps`, per-condition sustainable maxima, probe overhead ratio

- [ ] **Step 1: Write failing multi-condition calibration tests**

```python
measurements = {
    "C2": [(1.0, valid(1.0)), (2.0, valid(2.0)), (4.0, valid(4.0))],
    "C4": [(1.0, valid(1.0)), (2.0, valid(2.0)), (4.0, invalid_or_short(3.0))],
    "C5": [(1.0, valid(1.0)), (2.0, valid(2.0)), (4.0, valid(4.0))],
}
assert choose_common_target_qps(measurements, load_fraction=0.70) == 1.4
```

- [ ] **Step 2: Verify the current C1-only calibration fails the test**

Run: `.venv/bin/python -m pytest tests/test_calibration.py tests/test_cli.py -q`

- [ ] **Step 3: Implement the common target rule**

Find the largest sustainable candidate for each of C2, C4 and C5, take the smallest of those three maxima, and multiply it by 0.70. A candidate is sustainable only when the MLPerf result is valid and completed throughput is at least 98% of the requested rate.

- [ ] **Step 4: Preserve the separate three-pair measurement-device overhead check at C1**

The original and measurement-enabled HAMi images must alternate in three pairs at the selected common request rate.

- [ ] **Step 5: Verify calibration tests**

Run: `.venv/bin/python -m pytest tests/test_calibration.py tests/test_cli.py -q`

- [ ] **Step 6: Commit**

```bash
git add src/hami_tail_pilot/calibration.py configs/calibration_measurements.example.json tests/test_calibration.py tests/test_cli.py
git commit -m "feat: calibrate against constrained pilot conditions"
```

### Task 5: 양방향 묶음별 판정

**Files:**
- Modify: `src/hami_tail_pilot/analysis.py`
- Modify: `src/hami_tail_pilot/experiment.py`
- Modify: `tests/test_analysis.py`
- Modify: `tests/test_end_to_end_fake.py`

**Interfaces:**
- Produces: primary comparisons `C2/C1`, `C4/C3`, `C5/C4`
- Produces: descriptive comparisons `C1/C0`, `C3/C1`, `C5/C3`
- Produces: direction `increase` or `decrease`, ratios, median ratio, same-direction block count

- [ ] **Step 1: Write failing six-condition analysis tests**

Cover all of these behaviors:

```text
30 complete runs are required.
4 of 5 ratios must point in the same direction.
median >= 1.10 is an increase finding.
median <= 1 / 1.10 is a decrease finding.
C5/C3 never decides continuation by itself.
C0 has no HAMi probe and is represented as zero wait.
```

- [ ] **Step 2: Verify the old increase-only P0~P3 analysis fails**

Run: `.venv/bin/python -m pytest tests/test_analysis.py tests/test_end_to_end_fake.py -q`

- [ ] **Step 3: Implement paired ratios and direction**

For each comparison, count ratios above and below 1.0. The repeated direction is the side with at least four blocks. Apply the size threshold only to the median of the same five paired ratios.

- [ ] **Step 4: Update synthetic dry-run fixtures to show one clear controlled effect**

Synthetic values are only connection tests and must remain labeled `synthetic=true`.

- [ ] **Step 5: Verify analysis and end-to-end tests**

Run: `.venv/bin/python -m pytest tests/test_analysis.py tests/test_end_to_end_fake.py -q`

- [ ] **Step 6: Commit**

```bash
git add src/hami_tail_pilot/analysis.py src/hami_tail_pilot/experiment.py tests/test_analysis.py tests/test_end_to_end_fake.py
git commit -m "feat: evaluate paired six-condition effects"
```

### Task 6: 중단 재개와 전체 로컬 검증

**Files:**
- Modify: `tests/test_execution.py`
- Modify: `README.md`
- Modify: `reports/파일럿_결과_요약.md`
- Modify: `reports/워크로드_선정표.md`

**Interfaces:**
- Consumes: 30-entry schedule and existing `status.json`
- Produces: completed runs skipped, failed attempts archived, remaining runs resumed

- [ ] **Step 1: Change resume tests from 20 to 30 and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_execution.py -q`

- [ ] **Step 2: Verify existing resume implementation works unchanged with the 30-entry schedule**

Expected: completed entries are skipped; failed entries are rerun only with the explicit retry option; previous failures move under `attempts/`.

- [ ] **Step 3: Rewrite user-facing documentation in Korean-first terminology**

Document the six named conditions, calibration order, smoke paths, 30-run execution and the fact that dry-run output is not research evidence.

- [ ] **Step 4: Run the full local suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass with no warnings.

- [ ] **Step 5: Run local command checks**

```bash
.venv/bin/python -m hami_tail_pilot.cli validate --config configs/pilot.yaml
.venv/bin/python -m hami_tail_pilot.cli schedule --config configs/pilot.yaml --output /tmp/hami-six-schedule.json
.venv/bin/python -m hami_tail_pilot.cli run --dry-run --config configs/pilot.yaml --output /tmp/hami-six-dry-run
.venv/bin/python -m hami_tail_pilot.cli analyze --input /tmp/hami-six-dry-run --probe-overhead-ratio 1.0
```

Expected: validation reports 6 conditions and 5 blocks; schedule has 30 entries; dry-run analysis writes decision files and labels all rows synthetic.

- [ ] **Step 6: Commit**

```bash
git add tests/test_execution.py README.md reports/파일럿_결과_요약.md reports/워크로드_선정표.md
git commit -m "docs: explain six-condition pilot harness"
```

## 장비 확인 후 별도로 구현할 항목

다음 항목은 실제 GPU 모델, 권한과 드라이버를 확인해야 정확히 구현할 수 있으므로 이 계획에서 억지로 확정하지 않는다.

1. `nvidia-smi -lgc` 지원 여부와 고정할 동작 속도
2. 실험 사이 온도 기준과 냉각 대기 절차
3. CUDA 12.4 컨테이너와 서버 드라이버의 실제 호환성
4. 모델·데이터·단어 목록 파일의 실제 위치와 해시
5. 요청별 도착·완료 시각을 MLPerf 실행 코드에서 저장하는 방식
6. 시간에 따라 요청 적체가 증가하는지 판정하는 구체적인 기록 형식

위 여섯 항목은 내일 서버 사전 확인 결과를 근거로 별도 환경 실행 계획에 추가한다. 현재 로컬 구현은 이를 완료한 것처럼 주장하지 않는다.
