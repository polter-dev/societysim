import json
import logging

from societysim.analysis.labeler import read_log
from societysim.logging.textio import read_text_lenient

TEXT = "it’s p3 — “the wolf”"


def test_utf8_file_reads_as_utf8(tmp_path, caplog):
    p = tmp_path / "a.jsonl"
    p.write_bytes((TEXT + " ‑ \U0001f43a\n").encode("utf-8"))
    with caplog.at_level(logging.WARNING):
        assert read_text_lenient(p) == TEXT + " ‑ \U0001f43a\n"
    assert not caplog.text


def test_cp1252_file_falls_back_and_warns_once(tmp_path, caplog):
    p = tmp_path / "old.jsonl"
    p.write_bytes(TEXT.encode("cp1252"))
    with caplog.at_level(logging.WARNING):
        assert read_text_lenient(p) == TEXT
        assert read_text_lenient(p) == TEXT
    assert caplog.text.count("not UTF-8") == 1


def test_read_log_reads_a_pre_t230_windows_log(tmp_path):
    ev = {
        "game_id": "g",
        "seq": 0,
        "t": 1,
        "day": 1,
        "phase": "day",
        "type": "statement",
        "visibility": "public",
        "actor": "p3",
        "public_text": TEXT,
    }
    p = tmp_path / "g.jsonl"
    p.write_bytes((json.dumps(ev, ensure_ascii=False) + "\r\n").encode("cp1252"))
    [e] = read_log(p)
    assert e.public_text == TEXT
