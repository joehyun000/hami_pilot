from hami_tail_pilot.cli import main


def test_cli_without_command_prints_help(capsys):
    assert main([]) == 2
    assert "validate" in capsys.readouterr().out


def test_schedule_command_writes_the_approved_twenty_run_schedule(tmp_path):
    output = tmp_path / "schedule.json"

    assert main(["schedule", "--config", "configs/pilot.yaml", "--output", str(output)]) == 0
    assert output.is_file()
    assert output.read_text(encoding="utf-8").count('"run_id"') == 20
