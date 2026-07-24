"""Regression tests for #6491: foreign (CLI/TUI/Desktop) sessions bypassed the
``msg_limit`` display window on ``GET /api/session``.

The synthesis branch that serves sessions without a WebUI sidecar built its own
response dict with ``"messages": msgs`` (the full array) and never called
``_message_window_for_display``. It also omitted ``_messages_truncated`` and
``_messages_offset``, so the frontend's ``hasServerOlder`` check was always
false: the whole transcript was rendered with no "Load earlier messages" button.

These tests pin the fix statically (the handler now windows the stub and emits
both keys) and functionally (the shared window helper honors ``msg_limit`` and
``msg_before`` for the same shapes the stub feeds it).
"""
from __future__ import annotations

import re
from pathlib import Path

from api.routes import _message_window_for_display


ROOT = Path(__file__).resolve().parents[1]
ROUTES_SRC = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")


def _foreign_stub_block() -> str:
    """Return the source of the foreign-session synthesis response block."""
    anchor = ROUTES_SRC.index('"session_id": synth.session_id,')
    start = ROUTES_SRC.rindex("msgs = list(synth.messages or [])", 0, anchor)
    end = ROUTES_SRC.index("_merge_cli_sidebar_metadata", anchor)
    return ROUTES_SRC[start:end]


# ---------------------------------------------------------------------------
# Static checks: the windowing actually reached the stub branch
# ---------------------------------------------------------------------------


def test_foreign_stub_windows_messages_instead_of_shipping_all():
    """The stub must serve the paginated window, not the raw full array."""
    block = _foreign_stub_block()
    assert "_message_window_for_display(" in block
    assert '"messages": _foreign_window,' in block
    assert '"messages": msgs,' not in block


def test_foreign_stub_emits_truncation_keys():
    """Both keys must be present; the frontend derives hasServerOlder from them."""
    block = _foreign_stub_block()
    assert '"_messages_truncated"' in block
    assert '"_messages_offset": _foreign_offset,' in block


def test_foreign_stub_passes_msg_limit_and_msg_before_through():
    """Paging params must be forwarded, otherwise msg_before stays a no-op."""
    block = _foreign_stub_block()
    assert "msg_limit=msg_limit," in block
    assert "msg_before=msg_before," in block


def test_foreign_stub_keeps_full_count_and_full_history_todo_state():
    """``message_count`` stays the full count (the frontend's external-refresh
    comparison keys off it) and todo state stays derived from the full list,
    matching the native path."""
    block = _foreign_stub_block()
    assert '"message_count": len(msgs),' in block
    assert "attach_todo_state(sess, msgs)" in block


def test_foreign_stub_skips_window_work_for_metadata_only_polls():
    """``messages=0`` must not carry the transcript."""
    block = _foreign_stub_block()
    assert "if load_messages:" in block
    assert "_foreign_window, _foreign_offset = [], 0" in block


def test_truncated_flag_is_gated_on_msg_limit_being_present():
    """The bare no-msg_limit path is the documented full-transcript escape
    hatch (branch/undo/jump-to-start, outline.js), so it must report
    ``_messages_truncated`` false even though the offset math still runs."""
    block = _foreign_stub_block()
    flag = re.search(r'"_messages_truncated": bool\((.*?)\),', block, re.S)
    assert flag, "truncated flag expression not found"
    expr = flag.group(1)
    assert "msg_limit is not None" in expr
    assert "_foreign_offset > 0" in expr
    assert "load_messages" in expr


# ---------------------------------------------------------------------------
# Functional checks: the window helper on foreign-shaped transcripts
# ---------------------------------------------------------------------------


def _transcript(n: int) -> list[dict]:
    """A plain alternating user/assistant transcript, the CLI stub shape."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(n)
    ]


def test_window_returns_tail_and_nonzero_offset_for_long_foreign_session():
    """A 92-message CLI session with msg_limit=30 returns a 30-row tail and a
    positive offset, the exact case reported in #6491."""
    msgs = _transcript(92)
    window, offset = _message_window_for_display(msgs, msg_limit=30)
    assert len(window) == 30
    assert offset == 62
    assert window[-1]["content"] == "m91"


def test_window_without_msg_limit_returns_everything_at_offset_zero():
    """The escape hatch: no msg_limit still yields the untruncated transcript."""
    msgs = _transcript(92)
    window, offset = _message_window_for_display(msgs, msg_limit=None)
    assert len(window) == 92
    assert offset == 0


def test_msg_before_paging_walks_back_to_the_head():
    """Repeated msg_before paging must terminate at offset 0 so the frontend's
    "Load earlier messages" button disappears at the head."""
    msgs = _transcript(92)
    _, offset = _message_window_for_display(msgs, msg_limit=30)
    seen = [offset]
    while offset > 0:
        _, offset = _message_window_for_display(
            msgs, msg_limit=30, msg_before=offset
        )
        seen.append(offset)
        assert len(seen) < 20, f"paging did not converge: {seen}"
    assert seen[-1] == 0
    assert seen == sorted(seen, reverse=True), seen


def test_window_shorter_than_limit_is_not_truncated():
    """A short foreign session must not advertise older history."""
    msgs = _transcript(8)
    window, offset = _message_window_for_display(msgs, msg_limit=30)
    assert len(window) == 8
    assert offset == 0


def test_empty_foreign_transcript_is_safe():
    """A synthesized stub with no recovered messages must not blow up."""
    window, offset = _message_window_for_display([], msg_limit=30)
    assert window == []
    assert offset == 0
