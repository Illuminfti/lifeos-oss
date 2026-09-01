from __future__ import annotations

import json
from pathlib import Path

from lifeos.cli import main


def read_json(capsys):
    return json.loads(capsys.readouterr().out)


def test_cli_initializes_connects_captures_and_promotes(tmp_path: Path, capsys):
    brain = tmp_path / "brain"
    assert main(["--brain", str(brain), "init"]) == 0
    initialized = read_json(capsys)
    assert initialized["initialized"] is True

    assert main(["--brain", str(brain), "connector", "connect", "example"]) == 0
    connection = read_json(capsys)
    assert "secret" not in json.dumps(connection).lower().replace("secret_custody", "custody")

    assert main([
        "--brain",
        str(brain),
        "connector",
        "backfill",
        connection["connection_id"],
    ]) == 0
    captured = read_json(capsys)
    assert captured["ingest"]["accepted"] == 1

    assert main(["--brain", str(brain), "staging", "list"]) == 0
    proposals = read_json(capsys)
    assert len(proposals) == 1

    assert main([
        "--brain",
        str(brain),
        "staging",
        "promote",
        proposals[0]["proposal_id"],
        "--reviewer",
        "test-owner",
    ]) == 0
    promoted = read_json(capsys)
    assert promoted["receipt"]["reviewer"] == "test-owner"
    assert (brain / promoted["receipt"]["target_path"]).is_file()


def test_secret_file_permissions_are_enforced(tmp_path: Path, capsys):
    brain = tmp_path / "brain"
    main(["--brain", str(brain), "init"])
    capsys.readouterr()
    secret = tmp_path / "secret.json"
    secret.write_text('{"token":"bad"}')
    secret.chmod(0o644)
    code = main([
        "--brain",
        str(brain),
        "connector",
        "connect",
        "example",
        "--secret-file",
        str(secret),
    ])
    assert code == 2
    assert "mode 0600" in capsys.readouterr().err


def test_generated_secret_file_is_private(tmp_path: Path, capsys):
    output = tmp_path / "webhook.json"
    assert main(["secret", "generate", str(output)]) == 0
    read_json(capsys)
    assert output.stat().st_mode & 0o777 == 0o600
    assert len(json.loads(output.read_text())["ingest_token"]) >= 32
