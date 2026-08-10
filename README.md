# HAMi 느린 요청 응답시간 파일럿 하네스

이 폴더는 다음 질문을 확인하기 위한 실험 도구다.

> GPU를 함께 쓰는 추론 환경에서 HAMi의 사용량 제한 과정이 일부 요청을 유난히 느리게 만드는가? 반대로 이웃 작업의 과도한 사용을 막아 느린 요청을 줄이는가?

현재 코드는 여섯 조건과 총 30회의 본 실험을 실행하고 분석하도록 수정됐다. 다만 **실제 GPU 결과는 아직 없으며**, 로컬에서 만든 합성 결과는 코드 연결 확인용일 뿐 연구 증거가 아니다.

## 여섯 조건

| 조건 | 측정 대상 작업 | 이웃 작업 | 이 조건으로 보는 것 |
|---|---:|---:|---|
| C0 | HAMi 없음 | 없음 | HAMi를 연결하지 않은 기준 |
| C1 | HAMi 연결, 100% | 없음 | 이 부하 수준에서 HAMi 연결 자체의 추가 비용 |
| C2 | HAMi 연결, 50% | 없음 | 이웃 없이 측정 대상의 사용량만 제한한 영향 |
| C3 | HAMi 연결, 100% | HAMi 연결, 100% | 제한 대기 없이 두 작업이 함께 실행될 때의 영향 |
| C4 | HAMi 연결, 50% | HAMi 연결, 100% | 이웃 조건을 고정한 채 측정 대상만 제한한 영향 |
| C5 | HAMi 연결, 50% | HAMi 연결, 50% | C4와 비교해 이웃 작업까지 제한한 영향 |

C4는 한 번에 하나만 바꾸기 위해 추가한 조건이다. C3에서 C4로 갈 때 이웃 작업은 100%로 그대로 두고 측정 대상만 50%로 바뀐다.

## 하네스가 하는 일

1. 설정이 승인된 여섯 조건과 정확히 일치하는지 확인한다.
2. 조건 여섯 개를 한 묶음으로 만들고, 묶음 안 순서를 무작위로 섞어 다섯 번 반복한다.
3. 제한 조건 C2·C4·C5가 각각 안정적으로 처리할 수 있는 초당 요청 수를 찾는다.
4. 세 조건 중 처리 능력이 가장 낮은 값을 고르고 그 70%를 모든 조건의 공통 요청량으로 사용한다.
5. 짧은 사전 실험으로 작업별 제한 대기가 설계와 같은지, 목표 요청량의 98% 이상을 처리하는지 확인한다.
6. 사전 확인을 통과한 경우에만 30회의 본 실험을 실행한다.
7. 같은 묶음 안의 두 조건을 비교해 느린 일부 요청의 응답시간 비율을 계산한다.
8. 중간에 멈추면 완료된 실험은 건너뛰고 남은 실험부터 이어서 실행한다. 실패한 결과는 지우지 않고 별도 보관한다.

더 쉬운 설명은 [하네스 쉬운 설명](docs/하네스_쉬운설명.md)에 있다.

## 계속 여부에 사용하는 비교

원인을 하나씩 분리하는 다음 세 비교만 연구 계속 여부 판정에 사용한다.

- C2 ÷ C1: 이웃이 없을 때 측정 대상의 사용량 제한 영향
- C4 ÷ C3: 이웃을 100%로 고정했을 때 측정 대상의 사용량 제한 영향
- C5 ÷ C4: 측정 대상은 50%로 고정했을 때 이웃 제한의 보호 또는 방해 영향

C1 ÷ C0, C3 ÷ C1, C5 ÷ C3도 기록하지만 여러 요소가 함께 바뀌므로 설명용이다.

다섯 묶음 중 네 묶음 이상에서 같은 방향이고, 비율 다섯 개의 가운데 값이 1.10 이상이거나 약 0.91 이하이면 후속 연구 후보로 본다. 1.10은 논문의 통계적 유의성을 확정하는 값이 아니라 더 정밀한 실험에 비용을 쓸지 정하는 파일럿 기준이다.

## 오늘 GPU 없이 확인할 것

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m hami_tail_pilot.cli validate --config configs/pilot.yaml
.venv/bin/python -m hami_tail_pilot.cli run \
  --dry-run \
  --config configs/pilot.yaml \
  --output /tmp/hami-tail-dry-run
.venv/bin/python -m hami_tail_pilot.cli analyze \
  --input /tmp/hami-tail-dry-run \
  --probe-overhead-ratio 1.0
```

`--dry-run` 결과는 가짜 값으로 실행 흐름만 확인한다. 여기서 나온 판정을 연구 결과로 사용하면 안 된다.

## 내일 GPU에서 확인할 순서

### 1. 장비와 입력 확인

- GPU 종류, 드라이버, CUDA 접근 가능 여부
- 다른 GPU 작업이 실행 중이지 않은지
- BERT 모델, SQuAD 자료, 단어 목록 파일의 실제 경로
- 고정할 GPU 동작 속도와 실험 시작 허용 온도

GPU 동작 속도 고정값은 장비가 지원하는 범위를 확인한 뒤 정한다. 현재 하네스는 실행 중 동작 속도·사용률·전력·메모리를 기록하지만, 동작 속도를 자동으로 고정하거나 온도가 내려갈 때까지 기다리는 기능은 아직 확정하지 않았다.

### 2. 실행 환경 제작

```bash
python3 -m venv .venv
.venv/bin/pip install -e . --no-build-isolation
bash scripts/bootstrap_sources.sh
bash scripts/build_images.sh
.venv/bin/python -m hami_tail_pilot.cli validate --config configs/pilot.yaml
```

측정 장치가 있는 실행 환경과 없는 실행 환경을 각각 만든다. 둘을 번갈아 세 쌍 실행해 측정 장치 자체가 응답시간을 5% 넘게 바꾸면 본 실험을 막는다.

### 3. 환경 사전 확인

```bash
.venv/bin/python -m hami_tail_pilot.cli run \
  --preflight-only \
  --config configs/pilot.yaml \
  --output runs/bert-pilot \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
```

### 4. 공통 요청량 선정

```bash
.venv/bin/python -m hami_tail_pilot.cli calibrate \
  --config configs/pilot.yaml \
  --output runs/bert-pilot \
  --candidate-qps 1 2 4 8 \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
```

`--candidate-qps`는 시험할 “초당 요청 수”다. 실제 장비 성능을 보고 범위를 넓히거나 좁힌다.

### 5. 짧은 조건 확인

```bash
.venv/bin/python -m hami_tail_pilot.cli smoke \
  --config runs/bert-pilot/pilot.resolved.yaml \
  --output runs/bert-pilot \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt
```

예상되는 제한 대기는 다음과 같다.

| 조건 | 측정 대상 대기 | 이웃 작업 대기 |
|---|---|---|
| C1 | 없어야 함 | 해당 없음 |
| C2 | 있어야 함 | 해당 없음 |
| C3 | 없어야 함 | 없어야 함 |
| C4 | 있어야 함 | 없어야 함 |
| C5 | 있어야 함 | 있어야 함 |

### 6. 본 실험과 분석

```bash
.venv/bin/python -m hami_tail_pilot.cli run \
  --config runs/bert-pilot/pilot.resolved.yaml \
  --output runs/bert-pilot \
  --model-file /absolute/path/model.pytorch \
  --dataset-file /absolute/path/dev-v1.1.json \
  --vocab-file /absolute/path/vocab.txt

.venv/bin/python -m hami_tail_pilot.cli analyze --input runs/bert-pilot
```

본 실험은 6조건 × 5묶음으로 총 30회다. 같은 명령을 다시 실행하면 완료된 실험은 건너뛴다. 실패한 실험을 다시 시도할 때만 `--rerun-failed`를 추가한다.

## 파일 역할

| 파일 | 쉬운 역할 |
|---|---|
| `configs/pilot.yaml` | 실험 조건표 |
| `config.py` | 조건표가 승인된 설계와 같은지 검사 |
| `schedule.py` | 30회의 순서를 섞어 작성 |
| `runner.py` | 이웃 작업과 측정 대상 작업을 실제로 실행하고 장비 상태를 기록 |
| `calibration.py` | 모든 조건에 공통으로 보낼 요청량을 선정 |
| `smoke.py` | 짧게 돌려 제한 대기와 처리 능력을 확인 |
| `execution.py` | 본 실험 실행, 중단 후 이어서 실행, 실패 보관 |
| `experiment.py` | 실험 결과를 한곳에 모음 |
| `analysis.py` | 묶음별 비율 계산과 계속 여부 판정 |
| `preflight.py` | Docker, CUDA, 입력 파일, 빈 GPU 등 실행 전 환경 확인 |
| `cli.py` | 위 기능을 명령 한 줄로 부르는 입구 |

## 아직 남은 실제 장비 확인

- 장비가 지원하는 GPU 동작 속도를 정하고 고정 절차를 검증해야 한다.
- 각 실험 시작 전 허용 온도와 냉각 대기 절차를 정해야 한다.
- MLPerf가 남기는 상세 기록만으로 요청이 쌓이지 않았음을 충분히 확인할 수 있는지 실제 출력에서 점검해야 한다.
- HAMi 제한 대기 기록과 개별 요청의 느려진 시점을 직접 연결할 수 있는지 확인해야 한다.

이 네 항목이 해결되기 전에는 실제 GPU 본 실험 준비가 완전히 끝났다고 보지 않는다.
