"""Fixed interpretations of the user messages of TASK.md §4 (data, not tests).

Each entry pairs a user message with the JSON the interpreter is expected to return for it
under D2/D3. Tasks 03, 04, 08 and 15 use them as the output of a fake model. Every value is
written in its message: names as whole words, text as a substring (D4: nothing is invented).

`L1`-`L4` are the list scenarios of D2 and assume a pending meeting with Juan. `X1` is the
scenario X of D2 (a word outside the allowed values). `H1` is our own wording for case H.
"""

from typing import Any, NamedTuple


class FixedTurn(NamedTuple):
    message: str
    interpretation: dict[str, Any]


def _op(tool: str, certainty: str = "explicit", **args: Any) -> dict[str, Any]:
    return {"tool": tool, "certainty": certainty, "args": args}


FIXED: dict[str, FixedTurn] = {
    # Case A, TASK.md line 296.
    "A1": FixedTurn(
        "Necesito crear un ticket porque el servidor está caído",
        {
            "act": "request_action",
            "operations": [_op("create_ticket", description="el servidor está caído")],
        },
    ),
    # Case B, TASK.md lines 315 and 323.
    "B1": FixedTurn(
        "Crea un ticket para revisar el problema de autenticación",
        {
            "act": "request_action",
            "operations": [
                _op("create_ticket", description="revisar el problema de autenticación")
            ],
        },
    ),
    "B2": FixedTurn(
        "Alta, asígnalo a Carlos",
        {
            "act": "provide_info",
            "operations": [_op("create_ticket", priority="Alta", assignee="Carlos")],
        },
    ),
    # Case B, third message (own wording): the title, which the agent asks for because it is
    # never derived from the description (D2, D17; amendment F4).
    "B3": FixedTurn(
        "El título es Falla de autenticación",
        {
            "act": "provide_info",
            "operations": [_op("create_ticket", title="Falla de autenticación")],
        },
    ),
    # Case C, TASK.md line 342: one explicit reading and one plausible one.
    "C1": FixedTurn(
        "Avísale a Carlos que mañana revisamos el servidor",
        {
            "act": "request_action",
            "operations": [
                _op(
                    "send_notification",
                    recipient="Carlos",
                    message="mañana revisamos el servidor",
                ),
                _op(
                    "schedule_meeting",
                    "plausible",
                    attendees=["Carlos"],
                    date={"relative_days": 1},
                ),
            ],
            "ambiguity_reason": "It may also mean a meeting with Carlos tomorrow.",
        },
    ),
    # Case D, TASK.md line 361: the values arrive uncorrected.
    "D1": FixedTurn(
        "Agenda una reunión el 40 de noviembre a las 25:90",
        {
            "act": "request_action",
            "operations": [
                _op(
                    "schedule_meeting",
                    date={"day": 40, "month": 11},
                    time={"hour": 25, "minute": 90},
                )
            ],
        },
    ),
    # Case E, TASK.md line 378.
    "E1": FixedTurn(
        "Crea un ticket de prioridad alta para Carlos y mándale una notificación por email "
        "cuando esté creado",
        {
            "act": "request_action",
            "operations": [
                _op("create_ticket", priority="alta", assignee="Carlos"),
                {
                    **_op("send_notification", recipient="Carlos", channel="email"),
                    "depends_on": "create_ticket",
                },
            ],
        },
    ),
    # Case F, TASK.md lines 399 and 407.
    "F1": FixedTurn(
        "Agenda una reunión con Juan",
        {
            "act": "request_action",
            "operations": [_op("schedule_meeting", attendees=["Juan"])],
        },
    ),
    "F2": FixedTurn(
        "Mejor crea un ticket para revisar el servidor",
        {
            "act": "request_action",
            "operations": [_op("create_ticket", description="revisar el servidor")],
        },
    ),
    # Case G, TASK.md lines 424 and 430.
    "G1": FixedTurn(
        "Envía un correo a Carlos para informarle que el ticket quedó resuelto",
        {
            "act": "request_action",
            "operations": [
                _op(
                    "send_notification",
                    recipient="Carlos",
                    channel="correo",
                    message="el ticket quedó resuelto",
                )
            ],
        },
    ),
    "G2": FixedTurn("Sí, hazlo", {"act": "confirm", "operations": []}),
    # Case H, TASK.md line 443 (own wording): complete, to be sent twice.
    "H1": FixedTurn(
        "Crea un ticket titulado Servidor caído: el servidor de producción no responde, "
        "prioridad alta, asígnalo a Carlos",
        {
            "act": "request_action",
            "operations": [
                _op(
                    "create_ticket",
                    title="Servidor caído",
                    description="el servidor de producción no responde",
                    priority="alta",
                    assignee="Carlos",
                )
            ],
        },
    ),
    # Scenario X of D2: a priority word that is not allowed.
    "X1": FixedTurn(
        "Crea un ticket de prioridad urgente porque el servidor está caído",
        {
            "act": "request_action",
            "operations": [
                _op("create_ticket", priority="urgente", description="el servidor está caído")
            ],
        },
    ),
    # List scenarios of D2, with a meeting with Juan pending.
    "L1": FixedTurn(
        "Agrega a Ana",
        {
            "act": "provide_info",
            "operations": [_op("schedule_meeting", attendees_add=["Ana"])],
        },
    ),
    "L2": FixedTurn(
        "Quita a Juan",
        {
            "act": "provide_info",
            "operations": [_op("schedule_meeting", attendees_remove=["Juan"])],
        },
    ),
    "L3": FixedTurn(
        "Con Ana",
        {
            "act": "provide_info",
            "operations": [_op("schedule_meeting", attendees=["Ana"])],
        },
    ),
    "L4": FixedTurn(
        "Quita a Juan y agrega a Ana",
        {
            "act": "provide_info",
            "operations": [
                _op("schedule_meeting", attendees_remove=["Juan"], attendees_add=["Ana"])
            ],
        },
    ),
    # Phase 0 of D2 (task 05): the scenarios that are not a case of TASK.md §4.
    # Informative question (TASK.md line 554: informative vs execution intent).
    "Q1": FixedTurn(
        "¿Puedes enviar notificaciones por SMS?", {"act": "question", "operations": []}
    ),
    # Weekday scenario of D2: the weekday word arrives as said.
    "W1": FixedTurn(
        "Agenda una reunión con Ana el viernes a las 10",
        {
            "act": "request_action",
            "operations": [
                _op(
                    "schedule_meeting",
                    attendees=["Ana"],
                    date={"weekday": "viernes"},
                    time={"hour": 10},
                )
            ],
        },
    ),
    # Contrast of case C (TASK.md line 117): a notice with no meeting reading.
    "N1": FixedTurn(
        "Avísale a Carlos que el servidor ya está disponible",
        {
            "act": "request_action",
            "operations": [
                _op(
                    "send_notification",
                    recipient="Carlos",
                    message="el servidor ya está disponible",
                )
            ],
        },
    ),
    # TASK.md line 113.
    "T1": FixedTurn(
        "Necesito reportar que el servidor está caído",
        {
            "act": "request_action",
            "operations": [_op("create_ticket", description="el servidor está caído")],
        },
    ),
    # Answers to the clarification of case C (notification, meeting or both) and probes on it.
    # The expected value of K1-K3 and S1-S2 is one possible reading: D2 judges by what the code
    # would do, not by the exact JSON. The probe S3 repeats the message of C1 and is compared
    # with the expected value of C1.
    "K1": FixedTurn(
        "Las dos",
        {
            "act": "provide_info",
            "operations": [_op("send_notification"), _op("schedule_meeting")],
        },
    ),
    "K2": FixedTurn(
        "Solo avísale",
        {"act": "provide_info", "operations": [_op("send_notification")]},
    ),
    "K3": FixedTurn("No sé, lo que sea", {"act": "other", "operations": []}),
    "S1": FixedTurn("Sí", {"act": "confirm", "operations": []}),
    "S2": FixedTurn("No, la reunión no", {"act": "reject", "operations": []}),
}
