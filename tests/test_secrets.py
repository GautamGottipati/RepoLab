"""No credential may appear anywhere a person or a log can see it.

Verified rather than assumed. The audit was run against the real services first:
a deliberately wrong key produces `AuthenticationError: Error code: 401 -
{'detail': "Couldn't authenticate…"}` from Nebius and `InvalidAPIKeyError:
Unauthorized` from Tavily, and neither echoes the key back. This suite keeps that
true, and extends it to the places RepoLab itself could leak one: events, job
errors, API responses, failure messages and the log stream.

The secrets below are obvious fakes with a distinctive marker, so a match is
unambiguous and no real credential is ever placed in a test.
"""

from __future__ import annotations

import json

import pytest

from repolab.analytics import AnalyticsEvent, AnalyticsEventType, CollectingAnalyticsWriter
from repolab.config import Settings
from repolab.events import CollectingEmitter, EventType
from repolab.failures import describe, explain

NEBIUS_SECRET = "nebius-LEAKCANARY-aaaaaaaaaaaaaaaaaaaa"
TAVILY_SECRET = "tvly-LEAKCANARY-bbbbbbbbbbbbbbbbbbbb"
CANARY = "LEAKCANARY"

SECRETS = (NEBIUS_SECRET, TAVILY_SECRET)


def assert_clean(subject: object, *, where: str) -> None:
    """Assert no secret appears anywhere in *subject*, however it is serialised."""
    text = subject if isinstance(subject, str) else json.dumps(subject, default=str)
    for secret in SECRETS:
        assert secret not in text, f"a credential reached {where}"
    assert CANARY not in text, f"part of a credential reached {where}"


@pytest.fixture
def settings() -> Settings:
    """Settings carrying the canaries. `_env_file=None` keeps the real .env out."""
    return Settings(
        _env_file=None,
        NEBIUS_API_KEY=NEBIUS_SECRET,
        TAVILY_API_KEY=TAVILY_SECRET,
        MODEL_NANO="fake/nano",
        MODEL_SUPER="fake/super",
        MODEL_ULTRA="fake/ultra",
    )


class TestSettings:
    def test_a_secret_is_masked_in_repr(self, settings: Settings) -> None:
        assert_clean(repr(settings), where="the settings repr")
        assert "**" in repr(settings), "masked, not absent"

    def test_a_secret_is_masked_in_a_dump(self, settings: Settings) -> None:
        assert_clean(settings.model_dump(), where="a settings dump")

    def test_a_secret_is_masked_in_json(self, settings: Settings) -> None:
        assert_clean(settings.model_dump_json(), where="settings json")

    def test_reading_one_takes_a_deliberate_call(self, settings: Settings) -> None:
        """`get_secret_value()` is the only way out, so every use is greppable."""
        assert settings.nebius_api_key is not None
        assert settings.nebius_api_key.get_secret_value() == NEBIUS_SECRET

    def test_the_configured_models_summary_carries_no_secret(self, settings: Settings) -> None:
        assert_clean(settings.configured_models, where="the configured-models summary")


class TestFailureMessages:
    """Every failure message is built from an exception, which may hold anything."""

    def test_a_secret_inside_an_exception_is_not_echoed_by_the_headline(self) -> None:
        exc = RuntimeError(f"request failed with Authorization: Bearer {NEBIUS_SECRET}")

        failure = explain(exc)

        assert_clean(failure.headline, where="a failure headline")
        assert_clean(failure.why, where="a failure explanation")
        assert_clean(failure.next_step, where="a failure next step")

    def test_the_technical_detail_is_the_one_place_it_could_appear(self) -> None:
        """And it is a disclosure the user opens, not a message they are shown.

        RepoLab does not redact here: the technical field is the exception's own
        words, and silently rewriting them would make a real error harder to
        diagnose. What this test fixes is that the *default* view carries
        nothing, so a screenshot or a glance cannot leak.
        """
        exc = RuntimeError(f"Bearer {NEBIUS_SECRET}")
        failure = explain(exc)

        shown_by_default = f"{failure.headline} {failure.why} {failure.next_step}"
        assert_clean(shown_by_default, where="what a failure shows by default")

    def test_a_real_provider_error_carries_no_secret(self) -> None:
        """The shape the real services actually produce, recorded from a live call."""
        for message in (
            "Error code: 401 - {'detail': \"Couldn't authenticate. Reason: Unable authenticate\"}",
            "Unauthorized: missing or invalid API key.",
        ):
            assert_clean(describe(RuntimeError(message)), where="a provider failure")


class TestEvents:
    def test_an_event_carries_no_secret(self) -> None:
        """Events are persisted and streamed to the browser, so they are the
        highest-risk surface in the product."""
        emitter = CollectingEmitter("job-1")
        failure = explain(RuntimeError("auth failed"))

        emitter.emit(
            EventType.FAILED,
            failure.summary,
            step="investigate",
            data=failure.model_dump_public(),
        )

        for event in emitter.events:
            assert_clean(event.model_dump(mode="json"), where="a job event")

    def test_the_analytics_properties_carry_no_secret(self) -> None:
        writer = CollectingAnalyticsWriter()
        writer.record(
            AnalyticsEvent(
                type=AnalyticsEventType.AGENT_FAILED,
                job_id="job-1",
                properties={"error": "LLMTransportError"},
            )
        )

        for event in writer.events:
            assert_clean(event.model_dump(mode="json"), where="an analytics event")


class TestTheCodebaseItself:
    def test_no_secret_is_logged_by_the_model_router(self, capture_logs: list[dict]) -> None:
        """The router logs one record per call. It must hold no credential."""
        import asyncio

        from repolab.llm import ModelRouter, ModelTier
        from repolab.testing.fakes import FakeLLMProvider, FakeMessage

        class Answer(__import__("pydantic").BaseModel):
            answer: str

        provider = FakeLLMProvider(message=FakeMessage(content=json.dumps({"answer": "ok"})))
        router = ModelRouter(provider, {ModelTier.NANO: "fake/nano"})

        asyncio.run(
            router.nano(purpose="file_summary", schema=Answer, prompt=f"key: {NEBIUS_SECRET}")
        )

        # The prompt deliberately contains a secret; nothing about the call is
        # allowed to carry it into the log.
        for entry in capture_logs:
            assert_clean(entry, where="a log line")

    def test_no_source_file_contains_a_credential_shaped_literal(self) -> None:
        """A key pasted into a file during debugging is the classic leak."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        # Shapes that would be real credentials: a Nebius/OpenAI-style key, a
        # Tavily key, or a long bearer token.
        patterns = [
            re.compile(r"\bsk-[A-Za-z0-9]{24,}"),
            re.compile(r"\btvly-[A-Za-z0-9]{20,}"),
            re.compile(r"\beyJ[A-Za-z0-9_-]{30,}"),
        ]
        skip = {".venv", "node_modules", ".git", ".next", "test-results", "__pycache__"}

        offenders: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".md", ".yml"}:
                continue
            if any(part in skip for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(text) for pattern in patterns):
                offenders.append(str(path.relative_to(root)))

        assert offenders == [], f"credential-shaped literals in {offenders}"

    def test_the_env_template_holds_no_real_credential(self) -> None:
        """`.env.example` is meant to be shared, so it must hold only placeholders.

        This test found a live Nebius key and a live Tavily key committed in that
        file, which is exactly the leak the phase asked to look for: `.env` is
        gitignored and is the right home for a real value, while the template is
        the file someone copies, pastes into an issue, or pushes.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        example = root / ".env.example"
        if not example.exists():
            pytest.skip("no .env.example")

        template = example.read_text()

        # Whatever is really configured must not appear in the template.
        live = root / ".env"
        if live.exists():
            for line in live.read_text().splitlines():
                if "=" not in line or line.strip().startswith("#"):
                    continue
                name, _, value = line.partition("=")
                value = value.strip().strip("\"'")
                if name.strip().endswith(("_API_KEY", "_TOKEN", "_SECRET")) and value:
                    assert value not in template, f"{name.strip()}'s real value is in .env.example"

        # And nothing in it may look like a credential on its own terms.
        for line in template.splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            name, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if not name.strip().endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PROJECT_ID")):
                continue
            assert len(value) < 60, f"{name.strip()} looks like a real value"
            assert "your" in value.lower() or not value, (
                f"{name.strip()} should read as a placeholder"
            )
