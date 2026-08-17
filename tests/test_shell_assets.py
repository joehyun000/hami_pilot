import base64
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def run_script(
    path: str, argument: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / path), argument],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def write_server_env(tmp_path: Path, *, output_dir: Path | None = None) -> Path:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    model = inputs / "model.pytorch"
    dataset = inputs / "dev-v1.1.json"
    vocab = inputs / "vocab.txt"
    for path in (model, dataset, vocab):
        path.write_text("fixture\n", encoding="utf-8")

    env_file = tmp_path / "server.env"
    env_file.write_text(
        "\n".join(
            (
                f'PILOT_MODEL_FILE="{model}"',
                f'PILOT_DATASET_FILE="{dataset}"',
                f'PILOT_VOCAB_FILE="{vocab}"',
                'PILOT_CANDIDATE_QPS="1 2 4 8"',
                'PILOT_GPU_INDEX="0"',
                f'PILOT_OUTPUT_DIR="{output_dir or tmp_path / "runs"}"',
                'PILOT_GPU_CLOCK_MHZ="1200"',
                'PILOT_MAX_START_TEMP_C="55"',
                'PILOT_TEMPERATURE_WAIT_SECONDS="60"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return env_file


def run_gpu_pilot(
    *arguments: str, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts/run_gpu_pilot.sh"), *arguments],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def test_bootstrap_reports_the_exact_upstream_commits_without_network_access():
    result = run_script("scripts/bootstrap_sources.sh", "--print-pins")

    assert json.loads(result.stdout) == {
        "hami_release": "v2.9.0",
        "hami_commit": "3a006c6ae2f077a2683df7805c43656c07f6dc15",
        "hami_core_commit": "5091a2fbe1816df1265490f771346730f29e2c8d",
        "mlperf_release": "v5.1.1",
        "mlperf_commit": "6776245e99dce0600cfc9a6fb61efd310f87de3d",
    }


def test_build_script_reports_the_versioned_image_tag_without_docker_access():
    result = run_script("scripts/build_images.sh", "--print-tag")

    assert result.stdout.strip() == "hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"


def test_build_script_distinguishes_probe_and_vanilla_images_for_overhead_ablation():
    result = run_script("scripts/build_images.sh", "--print-tags")

    assert json.loads(result.stdout) == {
        "probe": "hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0",
        "vanilla": "hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0-vanilla",
    }


def test_kubernetes_build_script_reports_configured_registry_tags():
    result = run_script(
        "scripts/build_images_k8s.sh",
        "--print-tags",
        env={
            "PILOT_K8S_NAMESPACE": "test-namespace",
            "PILOT_K8S_BUILD_NODE": "test-gpu-node",
            "PILOT_NFS_SERVER": "nfs.example.test",
            "PILOT_NFS_ROOT": str(ROOT),
            "PILOT_REGISTRY": "registry.example.test:5000",
        },
    )

    assert json.loads(result.stdout) == {
        "probe": "registry.example.test:5000/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0",
        "vanilla": "registry.example.test:5000/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0-vanilla",
    }


def test_kubernetes_scripts_support_an_authenticated_https_registry():
    build_script = (ROOT / "scripts/build_images_k8s.sh").read_text(encoding="utf-8")
    check_script = (ROOT / "scripts/check_probe_k8s.sh").read_text(encoding="utf-8")
    example = (ROOT / "configs/k8s.env.example").read_text(encoding="utf-8")

    assert "PILOT_REGISTRY_SECRET" in build_script
    assert "/kaniko/.docker" in build_script
    assert "PILOT_REGISTRY_INSECURE" in build_script
    assert "PILOT_REGISTRY_SECRET" in check_script
    assert "imagePullSecrets:" in check_script
    assert 'PILOT_REGISTRY="ghcr.io/your-account"' in example
    assert 'PILOT_REGISTRY_SECRET="hami-pilot-registry"' in example
    assert 'PILOT_REGISTRY_INSECURE="false"' in example


def test_registry_auth_script_uses_hidden_input_and_stdin_not_password_arguments():
    script = (ROOT / "scripts/configure_registry_auth_k8s.sh").read_text(
        encoding="utf-8"
    )

    assert "read -r -s" in script
    assert ".dockerconfigjson" in script
    assert "kubectl apply -f -" in script
    assert "--docker-password" not in script


def test_registry_auth_script_does_not_echo_the_token(tmp_path):
    captured_manifest = tmp_path / "secret.yaml"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_kubectl = fake_bin / "kubectl"
    fake_kubectl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"apply -f -"* ]]; then
  cat > "$CAPTURED_MANIFEST"
  exit 0
fi
if [[ "$*" == *"get secret"* ]]; then
  printf 'kubernetes.io/dockerconfigjson'
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    fake_kubectl.chmod(0o755)

    env_file = tmp_path / "k8s.env"
    env_file.write_text(
        "\n".join(
            (
                'PILOT_K8S_NAMESPACE="test-namespace"',
                'PILOT_REGISTRY="ghcr.io/test-user"',
                'PILOT_REGISTRY_USERNAME="test-user"',
                'PILOT_REGISTRY_SECRET="registry-auth"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    token = "test-token-must-stay-hidden"

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/configure_registry_auth_k8s.sh")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        input=token + "\n",
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PILOT_K8S_ENV_FILE": str(env_file),
            "CAPTURED_MANIFEST": str(captured_manifest),
        },
    )

    assert token not in result.stdout
    assert token not in result.stderr
    encoded = next(
        line.split(":", 1)[1].strip()
        for line in captured_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(".dockerconfigjson:")
    )
    docker_config = json.loads(base64.b64decode(encoded))
    assert docker_config["auths"]["ghcr.io"]["username"] == "test-user"
    assert docker_config["auths"]["ghcr.io"]["password"] == token


def test_kubernetes_probe_check_uses_one_gpu_and_a_fifty_percent_limit():
    script = (ROOT / "scripts/check_probe_k8s.sh").read_text(encoding="utf-8")

    assert 'CUDA_DEVICE_SM_LIMIT' in script
    assert 'value: "50"' in script
    assert 'nvidia.com/gpu: 1' in script
    assert 'HAMI_PROBE_OUTPUT' in script
    assert 'waited_calls' in script
    assert "check_seconds = 45" in script
    assert "while time.perf_counter() - started < check_seconds:" in script
    assert 'name: LIBCUDA_LOG_LEVEL' in script
    assert 'value: "3"' in script


def test_bert_image_uses_blackwell_compatible_pytorch_and_cuda():
    dockerfile = (ROOT / "docker/Dockerfile.bert").read_text(encoding="utf-8")

    assert "nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04" in dockerfile
    assert "pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime" in dockerfile


def test_bert_input_download_script_reports_pinned_sources_and_checksums():
    result = run_script("scripts/download_bert_inputs.sh", "--print-manifest")

    assert json.loads(result.stdout) == {
        "dev-v1.1.json": {
            "md5": "3e85deb501d4e538b6bc56f786231552",
            "url": "https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v1.1.json",
        },
        "model.pytorch": {
            "md5": "00fbcbfaebfa20d87ac9885120a6e9b4",
            "url": "https://zenodo.org/records/3733896/files/model.pytorch?download=1",
        },
        "vocab.txt": {
            "md5": "64800d5d8528ce344256daf115d4965e",
            "url": "https://zenodo.org/records/3733896/files/vocab.txt?download=1",
        },
    }

    script = (ROOT / "scripts/download_bert_inputs.sh").read_text(encoding="utf-8")
    assert ".part" in script
    assert "md5sum" in script


def test_kubernetes_single_bert_check_uses_a_low_request_rate_and_no_limit_wait():
    result = run_script(
        "scripts/check_bert_k8s.sh",
        "--print-plan",
        env={
            "PILOT_K8S_NAMESPACE": "test-namespace",
            "PILOT_K8S_BUILD_NODE": "test-gpu-node",
            "PILOT_NFS_SERVER": "nfs.example.test",
            "PILOT_NFS_ROOT": "/shared",
            "PILOT_REGISTRY": "ghcr.io/test-user",
        },
    )

    assert json.loads(result.stdout) == {
        "image": "ghcr.io/test-user/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0",
        "measurement_seconds": 30,
        "node": "test-gpu-node",
        "sm_limit": 100,
        "target_qps": 1,
        "warmup_seconds": 60,
    }

    script = (ROOT / "scripts/check_bert_k8s.sh").read_text(encoding="utf-8")
    assert "eval_features.pickle" in script
    assert "mlperf_log_summary.txt" in script
    assert "waited_calls" in script
    assert "nvidia.com/gpu: 1" in script


def test_gpu_pilot_prepare_dry_run_stops_after_the_short_condition_check(tmp_path):
    env_file = write_server_env(tmp_path)

    result = run_gpu_pilot(
        "prepare", "--dry-run", "--env-file", str(env_file), check=True
    )

    assert "[4/5] 공통 요청량 찾기" in result.stdout
    assert "[5/5] 짧은 조건 확인" in result.stdout
    assert "30회 본 실험은 시작하지 않았습니다." in result.stdout
    assert "scripts/run_gpu_pilot.sh run" in result.stdout


def test_gpu_pilot_run_refuses_to_start_without_a_passed_short_check(tmp_path):
    output_dir = tmp_path / "runs"
    output_dir.mkdir()
    (output_dir / "pilot.resolved.yaml").write_text("fixture\n", encoding="utf-8")
    env_file = write_server_env(tmp_path, output_dir=output_dir)

    result = run_gpu_pilot("run", "--dry-run", "--env-file", str(env_file))

    assert result.returncode != 0
    assert "짧은 조건 확인을 통과하지 않았습니다" in result.stderr
    assert "30회 본 실험 시작" not in result.stdout


def test_gpu_pilot_prepare_rejects_a_missing_model_before_setup(tmp_path):
    env_file = write_server_env(tmp_path)
    missing_model = tmp_path / "missing-model.pytorch"
    contents = env_file.read_text(encoding="utf-8")
    env_file.write_text(
        contents.replace(
            next(line for line in contents.splitlines() if line.startswith("PILOT_MODEL_FILE=")),
            f'PILOT_MODEL_FILE="{missing_model}"',
        ),
        encoding="utf-8",
    )

    result = run_gpu_pilot("prepare", "--dry-run", "--env-file", str(env_file))

    assert result.returncode != 0
    assert f"입력 파일이 없습니다: {missing_model}" in result.stderr
    assert "파이썬 실행 환경" not in result.stdout


def test_shell_scripts_are_valid_bash_programs():
    for relative_path in (
        "scripts/bootstrap_sources.sh",
        "scripts/build_images.sh",
        "scripts/build_images_k8s.sh",
        "scripts/check_probe_k8s.sh",
        "scripts/check_bert_k8s.sh",
        "scripts/configure_registry_auth_k8s.sh",
        "scripts/download_bert_inputs.sh",
        "scripts/run_gpu_pilot.sh",
    ):
        subprocess.run(["bash", "-n", str(ROOT / relative_path)], check=True)
