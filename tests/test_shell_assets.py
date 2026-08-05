import json
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


def test_shell_scripts_are_valid_bash_programs():
    for relative_path in ("scripts/bootstrap_sources.sh", "scripts/build_images.sh"):
        subprocess.run(["bash", "-n", str(ROOT / relative_path)], check=True)
