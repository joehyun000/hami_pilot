from hami_tail_pilot.cli import main


def test_cli_without_command_prints_help(capsys):
    assert main([]) == 2
    assert "validate" in capsys.readouterr().out
