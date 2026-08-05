# HAMi Tail-Latency Pilot

MLPerf BERT Server 워크로드에서 HAMi compute quota의 자기 대기와 공동배치 경합을 P0~P3 조건으로 분리하는 최소 파일럿 하네스다.

이 결과는 공식 MLPerf 제출 결과가 아니다. 첫 파일럿은 현상의 존재와 반복 가능성을 판정하며, CUPTI·TGS·정책 구현은 범위에 포함하지 않는다.

## 현재 구현 상태

- Mac에서 설정·schedule·parser·probe·GO 판정·프로세스 lifecycle·보정·preflight를 테스트한다.
- Docker image 빌드와 CUDA smoke test는 NVIDIA GPU가 있는 Linux 서버에서만 통과 판정한다.
- HAMi `v2.9.0`은 HAMi-core tag가 아니라 submodule commit `5091a2fbe1816df1265490f771346730f29e2c8d`를 사용한다.

## GPU 서버에서의 첫 실행 순서

```bash
python3 -m venv .venv
.venv/bin/pip install -e . --no-build-isolation
bash scripts/bootstrap_sources.sh
bash scripts/build_images.sh
.venv/bin/python -m hami_tail_pilot.cli validate --config configs/pilot.yaml
```

그다음 preflight가 Docker server, image digest, CUDA 접근, 빈 GPU, model·dataset·vocab, source commit을 모두 확인해야 한다. smoke에서는 P0 wait=0, P1/P3 wait>0을 확인하기 전까지 20-run 본 실험을 시작하지 않는다.
