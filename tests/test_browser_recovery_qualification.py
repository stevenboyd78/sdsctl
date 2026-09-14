"""The real-browser fixture must not accept the obsolete ordinary pause notice."""
from __future__ import annotations

import pytest

from scripts.experimental.qualify_browser_recovery import released_pause_text

NOTICE = (
    "Automatic sign-in is paused after completed recovery. A separate administrator "
    "continuation is required before this display can sign in again. Keep the saved "
    "profile and recovery evidence; do not repeat setup or remove the guard."
)


@pytest.mark.parametrize("text", [NOTICE, "Heading\n" + NOTICE + "\nFooter",
                                 NOTICE.replace(" ", "\n")])
def test_actual_completed_recovery_notice_is_required(text):
    assert released_pause_text(text)


@pytest.mark.parametrize("text", [
    "Automatic sign-in is paused.",
    "Automatic sign-in is paused. Ask your administrator to review before resuming.",
    "Managed startup could not be confirmed.",
    NOTICE.replace("do not repeat setup or remove the guard.", ""),
    NOTICE.replace("A separate administrator continuation is required",
                   "Continuation is available"),
    NOTICE + "\nReview automatic sign-in resume",
])
def test_ordinary_pause_or_missing_boundary_is_not_recovery_acceptance(text):
    assert not released_pause_text(text)
