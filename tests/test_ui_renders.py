"""Render the whole app headlessly and assert nothing raises.

WHY THIS EXISTS
---------------
Three UI crashes reached the browser during this project, each invisible to the
rest of the suite because Streamlit code only executes when a human opens a tab:

    AttributeError: 'Rubric' object has no attribute 'max_fk_grade'
    AttributeError: 'MemoryStore' object has no attribute 'signature_run_counts'
    TypeError: metric() got multiple values for argument 'delta'

The static guard in test_no_stale_references.py catches the first two — a name
that no longer exists. It cannot catch the third, which is a valid name used
wrongly.

Streamlit's AppTest actually executes the script, so it catches anything that
raises at render time. It needs no browser, no API key, and no network: the
pipeline only runs when a button is pressed, and these tests never press one.
"""

from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest

# Relative paths resolve against this file, not the project root.
APP = str(Path(__file__).parent.parent / "app.py")
TIMEOUT = 60


@pytest.fixture(scope="module")
def app():
    """Run app.py once and share the rendered result across tests."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    return at


def test_app_renders_without_exception(app):
    """The default tab must render cleanly on a cold load."""
    assert not app.exception, (
        "app.py raised on load:\n"
        + "\n".join(str(e.value) for e in app.exception)
    )


def test_every_tab_renders(app):
    """Streamlit executes every tab's body on each run, so a crash in any of
    them surfaces here — which is exactly how the Memory tab bugs escaped."""
    assert not app.exception
    labels = [t.label for t in app.tabs] if app.tabs else []
    assert labels, "no tabs rendered"


def test_sidebar_renders(app):
    """The sidebar reads RUBRIC thresholds directly; a renamed field crashed it."""
    assert not app.exception
    assert app.sidebar, "sidebar did not render"


def test_no_pipeline_runs_without_a_button_press(app):
    """Guard on this test suite itself: rendering must not call an LLM.

    If a future change made the app run the pipeline on load, these tests would
    silently start costing money and needing an API key.
    """
    assert not app.exception
    from src.memory.store import MemoryStore

    before = len(MemoryStore().get_run_history())
    AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert len(MemoryStore().get_run_history()) == before, (
        "rendering the app started a pipeline run — it must only run on demand"
    )
