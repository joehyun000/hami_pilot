import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def run_script(path: str, argument: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / path), argument],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
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


def test_bert_image_uses_blackwell_compatible_pytorch_and_cuda():
    dockerfile = (ROOT / "docker/Dockerfile.bert").read_text(encoding="utf-8")

    assert "nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04" in dockerfile
    assert "pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime" in dockerfile


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
        "scripts/run_gpu_pilot.sh",
    ):
        subprocess.run(["bash", "-n", str(ROOT / relative_path)], check=True)
