"""The phase 0 harness with the scripted fake model: no real call, no `.env` (task 05)."""

import io
import json
from typing import Any

import pytest

import phase0.run
from core.instruction import NOTHING_PENDING, pending_summary
from core.interpretation import TurnInterpretation
from core.turn import handle_turn
from phase0.run import format_report, run_scenarios, shape_failure, strip_code_fence
from phase0.scenarios import CLARIFICATION_QUESTION, SCENARIOS
from tests.fake_llm import ScriptedLlm, contents_text, quota_error, system_text
from tests.interpretations import FIXED

FIRST_MESSAGES = {"A1", "C1", "D1", "E1", "X1", "Q1", "W1", "N1", "G1", "T1"}
SECOND_MESSAGES = {"B2", "F2", "L1", "L2", "L3", "L4", "G2", "K1", "K2", "K3", "S1", "S2", "S3"}
BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def _core_record(key: str) -> dict:
    return handle_turn(
        None, TurnInterpretation.model_validate(FIXED[key].interpretation)
    ).state_delta["pending"]


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {text for item in value.values() for text in _string_values(item)}
    if isinstance(value, list):
        return {text for item in value for text in _string_values(item)}
    return set()


def _expected_json(key: str) -> str:
    return json.dumps(FIXED[key].interpretation, ensure_ascii=False)


def _jsonl(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


# (a)
def test_the_23_scenarios_of_d2():
    ids = [scenario.id for scenario in SCENARIOS]
    assert len(ids) == len(set(ids)) == 23
    assert set(ids) == FIRST_MESSAGES | SECOND_MESSAGES
    assert all(scenario.fixture in FIXED for scenario in SCENARIOS)
    assert BY_ID["S3"].fixture == "C1"
    assert all(s.fixture == s.id for s in SCENARIOS if s.id != "S3")

    assert {s.id for s in SCENARIOS if s.pending is None} == FIRST_MESSAGES
    assert {s.id for s in SCENARIOS if s.pending is not None} == SECOND_MESSAGES

    # From the core: the record `handle_turn` creates for the first message.
    assert BY_ID["B2"].pending == _core_record("B1")
    for key in ("F2", "L1", "L2", "L3", "L4"):
        assert BY_ID[key].pending == _core_record("F1"), key
        assert BY_ID[key].pending["status"] == "collecting"

    # Literals, with no collected value.
    assert BY_ID["G2"].pending == {
        "tools": ["send_notification"],
        "status": "awaiting_confirmation",
        "missing": [],
        "last_question": "¿Confirmas que envíe la notificación?",
    }
    assert CLARIFICATION_QUESTION == (
        "¿Quieres enviar una notificación, programar una reunión, o ambas?"
    )
    for key in ("K1", "K2", "K3", "S1", "S2", "S3"):
        assert BY_ID[key].pending == {
            "tools": ["send_notification", "schedule_meeting"],
            "status": "awaiting_clarification",
            "missing": [],
            "last_question": CLARIFICATION_QUESTION,
        }, key

    # No summary carries a value said earlier in its conversation (D2, D14).
    for scenario in SCENARIOS:
        if scenario.pending is None:
            continue
        summary = pending_summary(scenario.pending)
        said = _string_values(
            [op["args"] for op in FIXED[scenario.previous].interpretation["operations"]]
        )
        said |= _string_values([step["args"] for step in scenario.pending.get("steps", [])])
        assert said, scenario.id
        for value in said:
            assert value not in summary, (scenario.id, value)


# (b)
async def test_happy_path():
    fake = ScriptedLlm(script=[_expected_json(s.fixture) for s in SCENARIOS])
    out = io.StringIO()

    result = await run_scenarios(fake, SCENARIOS, out, delay=0)

    assert fake.calls == 23
    for request, scenario in zip(fake.requests, SCENARIOS, strict=True):
        assert contents_text(request) == [FIXED[scenario.fixture].message], scenario.id
        expected = pending_summary(scenario.pending) if scenario.pending else NOTHING_PENDING
        # ADK appends its own identity line after the instruction, so this is `in`, not endswith.
        assert f"Pending:\n{expected}\n" in system_text(request), scenario.id

    assert result.stopped_at is None
    assert [r.shape_failure for r in result.records] == [None] * 23
    assert all(r.matches_expected for r in result.records)
    lines = _jsonl(out)
    assert [line["id"] for line in lines] == [s.id for s in SCENARIOS]
    for line in lines:
        assert {"id", "message", "pending_summary", "raw", "error", "shape_failure"} <= set(line)
        assert line["error"] is None

    report = format_report(result)
    assert "Fallos de forma: 0" in report
    assert "0/23 → se sigue con la opción B" in report
    assert "`depends_on` de E1: presente" in report
    assert "Difieren de lo esperado (act y tools): ninguno" in report
    # UTF-8 without escapes in the file.
    assert "está caído" in out.getvalue()


# (c), plus the informative comparison and E1's `depends_on` (F7)
async def test_shape_failures_are_counted_and_the_run_goes_on():
    a1_empty_args = {
        "act": "request_action",
        "operations": [{"tool": "create_ticket", "certainty": "explicit", "args": {}}],
    }
    d1_empty_date = json.loads(_expected_json("D1"))
    d1_empty_date["operations"][0]["args"]["date"] = {}
    fenced_t1 = f"```json\n{_expected_json('T1')}\n```"
    n1_plausible = json.loads(_expected_json("N1"))
    n1_plausible["operations"][0]["certainty"] = "plausible"
    e1_no_dependency = json.loads(_expected_json("E1"))
    del e1_no_dependency["operations"][1]["depends_on"]
    script = {
        "C1": "",
        "G1": "   ",
        "X1": "{",
        "A1": json.dumps(a1_empty_args),
        "D1": json.dumps(d1_empty_date),
        "T1": fenced_t1,  # option (a): valid JSON in a fence is not a shape failure
        "N1": json.dumps(n1_plausible, ensure_ascii=False),  # valid, but differs
        "E1": json.dumps(e1_no_dependency, ensure_ascii=False),  # valid, no `depends_on`
    }
    scenarios = [BY_ID[key] for key in script]
    fake = ScriptedLlm(script=list(script.values()))
    out = io.StringIO()

    result = await run_scenarios(fake, scenarios, out, delay=0)

    assert fake.calls == 8
    assert result.stopped_at is None
    causes = {r.id: r.shape_failure for r in result.records}
    assert causes == {
        "C1": "empty",
        "G1": "empty",
        "X1": "validation",
        "A1": "empty_args",
        "D1": "empty_date",
        "T1": None,
        "N1": None,
        "E1": None,
    }
    assert {line["id"]: line["raw"] for line in _jsonl(out)} == script
    report = format_report(result)
    for key in ("C1", "G1", "X1", "A1", "D1"):
        assert f"  - {key}: {causes[key]}" in report
    assert "Veredicto" not in report

    # Same act, other certainty: only N1 differs (the failed ones have no output to compare).
    assert {r.id: r.matches_expected for r in result.records} == {
        "C1": None,
        "G1": None,
        "X1": None,
        "A1": True,
        "D1": True,
        "T1": True,
        "N1": False,
        "E1": True,
    }
    assert "Difieren de lo esperado (act y tools): N1" in report
    assert "`depends_on` de E1: ausente" in report


def test_empty_nested_object_needs_content_in_the_expected_output():
    # K1 expects operations without args: `{}` there is what was asked for.
    assert shape_failure(_expected_json("K1"), FIXED["K1"].interpretation) == (
        None,
        FIXED["K1"].interpretation,
    )
    assert strip_code_fence("```\n{}\n```") == "{}"
    assert strip_code_fence('{"act": "other"}') == '{"act": "other"}'


async def test_a_shape_failure_in_a_full_run_stops_for_the_author():
    script = [_expected_json(s.fixture) for s in SCENARIOS]
    script[0] = ""

    report = format_report(await run_scenarios(ScriptedLlm(script=script), SCENARIOS, delay=0))

    assert "1/23 → parar y mostrar estos casos al autor" in report
    assert "  - A1: empty" in report


# (d)
async def test_model_error_stops_the_run():
    scenarios = SCENARIOS[:3]
    fake = ScriptedLlm(script=[_expected_json(scenarios[0].fixture), quota_error()])
    out = io.StringIO()

    result = await run_scenarios(fake, scenarios, out, delay=0)

    assert fake.calls == 2
    assert result.stopped_at == scenarios[1].id
    stopped = result.records[-1]
    assert stopped.error.startswith("ClientError")
    assert stopped.shape_failure is None
    assert len(_jsonl(out)) == 2
    report = format_report(result)
    assert f"Corrida detenida en {scenarios[1].id}: ClientError" in report
    assert "2 de 3; sin veredicto" in report
    assert "Veredicto" not in report


def test_listing_calls_nothing(monkeypatch, tmp_path, capsys):
    runs = tmp_path / "runs"
    monkeypatch.setattr(phase0.run, "RUNS_DIR", runs)
    monkeypatch.setattr(phase0.run, "load_dotenv", lambda: pytest.fail("read .env"))
    monkeypatch.setattr(phase0.run, "run_scenarios", lambda *a, **k: pytest.fail("ran"))

    assert phase0.run.main([]) == 0

    printed = capsys.readouterr().out
    assert all(scenario.id in printed for scenario in SCENARIOS)
    assert "23 llamadas a gemini-3.5-flash-lite" in printed
    assert not runs.exists()


def test_run_writes_the_jsonl_and_prints_the_report(monkeypatch, tmp_path, capsys):
    # `main(["--run"])` with the fake model in place of Gemini: no `.env`, no real call.
    fake = ScriptedLlm(script=[_expected_json(s.fixture) for s in SCENARIOS])
    real_run = phase0.run.run_scenarios

    async def fake_run(model, scenarios, out):
        assert model == phase0.run.MODEL
        return await real_run(fake, scenarios, out, delay=0)

    monkeypatch.setattr(phase0.run, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(phase0.run, "load_dotenv", lambda: None)
    monkeypatch.setattr(phase0.run, "run_scenarios", fake_run)

    assert phase0.run.main(["--run"]) == 0

    [path] = (tmp_path / "runs").iterdir()
    assert path.name.endswith(f"-{phase0.run.MODEL}.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["id"] for line in lines] == [s.id for s in SCENARIOS]
    printed = capsys.readouterr().out
    assert "0/23 → se sigue con la opción B" in printed
    assert f"Salida cruda: {path}" in printed
