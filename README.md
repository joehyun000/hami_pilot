# HAMi Tail-Latency Pilot

MLPerf BERT Server에서 HAMi compute quota의 **Victim 자기 대기**와 **공동배치 GPU 경합**을 P0~P3 조건으로 분리하는 파일럿 하네스다.

이 실험은 공식 MLPerf 제출이 아니다. 첫 파일럿의 목적은 현상의 존재와 반복 가능성을 판정하는 것이며, CUPTI·TGS·제어주기 ablation은 GO 이후 범위다.

## 현재 가능한 것

- Mac: 설정 검증, 20-run 무작위 schedule, MLPerf/probe parser, 실행·재시작 정책, paired 분석, 보고서 생성을 모두 테스트한다.
- NVIDIA Linux 서버: 고정 소스에서 probe/vanilla image를 빌드하고, 보정·smoke·P0~P3 본 실험을 같은 CLI로 실행한다.
- HAMi v2.9.0은 HAMi-core tag가 아니라 submodule commit 5091a2fbe1816df1265490f771346730f29e2c8d를 사용한다.

Mac의 --dry-run은 코드 연결 점검용 합성 자료다. 여기서 나오는 GO는 연구 결과가 아니며 발표나 논문에 인용하면 안 된다.

## 조건의 의미

| 조건 | Victim | Neighbor | 분리하려는 효과 |
|---|---:|---:|---|
| P0 | SM limit 100 | 없음 | 기준선 |
| P1 | SM limit 50 | 없음 | Victim 자신의 quota 대기 |
| P2 | SM limit 100 | 있음, limit 100 | 공동배치 하드웨어 경합 |
| P3 | SM limit 50 | 있음, limit 50 | 실제 공유 형태의 결합 효과 |

소스 확인 결과 watcher는 process utilization과 등록 PID 필터를 사용한다. 따라서 이 버전에서 “이웃 사용률이 Victim 관측값에 합쳐져 Victim 토큰을 직접 깎는다”는 device-wide 교차 제어 주장은 하지 않는다.

## Mac에서 코드만 점검

~~~bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python -m hami_tail_pilot.cli validate --config configs/pilot.yaml
.venv/bin/python -m hami_tail_pilot.cli run --dry-run --config configs/pilot.yaml --output /tmp/hami-tail-dry-run
.venv/bin/python -m hami_tail_pilot.cli analyze --input /tmp/hami-tail-dry-run --probe-overhead-ratio 1.0
~~~

## GPU 서버 실행 순서

아래 세 입력은 서버의 실제 절대경로로 바꾼다.

- MODEL: MLPerf BERT reference가 읽을 BERT-Large PyTorch model
- DATASET: SQuAD dev-v1.1.json
- VOCAB: BERT vocab.txt

### 1. 설치와 고정 image 빌드

~~~bash
python3 -m venv .venv
.venv/bin/pip install -e . --no-build-isolation
bash scripts/bootstrap_sources.sh
bash scripts/build_images.sh
.venv/bin/python -m hami_tail_pilot.cli validate --config configs/pilot.yaml
~~~

build_images.sh는 계측 patch가 있는 probe image와 patch가 없는 vanilla image를 각각 만든다. 두 image의 차이를 이용해 probe 자체가 p99를 바꾸는지 확인한다.

### 2. 환경만 먼저 확인

~~~bash
.venv/bin/python -m hami_tail_pilot.cli run \
  --preflight-only \
  --config configs/pilot.yaml \
  --output runs/bert-pilot \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
~~~

Docker·CUDA·빈 GPU·입력 hash·source commit·image digest 중 하나라도 맞지 않으면 중단한다.

### 3. 요청률과 probe 오버헤드 자동 보정

~~~bash
.venv/bin/python -m hami_tail_pilot.cli calibrate \
  --config configs/pilot.yaml \
  --output runs/bert-pilot \
  --candidate-qps 1 2 4 8 \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
~~~

후보값은 서버 성능에 맞게 넓혀도 된다. 도달 QPS가 목표의 98% 이상인 가장 큰 점을 찾고 그 70%를 본 실험 QPS로 고정한다. 이어서 vanilla/probe P0를 순서를 교차해 3쌍 실행한다. paired median overhead가 1.05를 넘으면 본 실험을 막는다.

산출물은 calibration_measurements.json, calibration_decision.json, pilot.resolved.yaml이다. 이미 수집한 결과만 다시 판정하려면 configs/calibration_measurements.example.json 형식과 --measurements를 사용할 수 있다.

### 4. quota 경로 smoke

~~~bash
.venv/bin/python -m hami_tail_pilot.cli smoke \
  --config runs/bert-pilot/pilot.resolved.yaml \
  --output runs/bert-pilot \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
~~~

30초 측정으로 P0 wait=0, P1/P3 wait>0을 확인한다. 하나라도 어긋나면 smoke.json에 원인을 기록하고 본 실험을 막는다.

### 5. P0~P3 5-block 본 실험

~~~bash
.venv/bin/python -m hami_tail_pilot.cli run \
  --config runs/bert-pilot/pilot.resolved.yaml \
  --output runs/bert-pilot \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
~~~

각 block에서 P0~P3 순서를 seed로 무작위화해 총 20회를 실행한다. complete run은 재실행하지 않고, 실패 run은 원본을 보존한 채 --rerun-failed를 명시한 경우에만 새 attempt로 실행한다.

### 6. 분석

~~~bash
.venv/bin/python -m hami_tail_pilot.cli analyze --input runs/bert-pilot
~~~

분석기는 보정 파일의 probe overhead를 자동으로 사용하고 pilot_metrics.csv, pilot_decision.json, pilot_decision.md를 만든다.

## 판정의 의미

- GO: 사전 정의한 paired contrast 중 하나 이상에서 5 block 중 4개 이상 같은 방향이고, p99 ratio 중앙값이 1.10 이상이다.
- 부분 GO: wait 메커니즘과 방향은 반복되지만 중앙 slowdown이 10% 미만이다.
- NO-GO: 방향이 불안정하거나, probe overhead가 5%를 넘거나, smoke/20-run이 불완전하다.

10%는 학술적 유의성 기준이 아니라 CUPTI와 추가 공개 trace 실험에 비용을 투입할지 정하는 탐색 gate다.

랩미팅용 정리는 reports/7월28일_vs_현재.md, reports/워크로드_선정표.md, reports/파일럿_결과_요약.md에 있다.
