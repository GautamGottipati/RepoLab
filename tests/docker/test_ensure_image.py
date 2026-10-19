"""Image readiness, against a real Docker daemon.

Marked ``docker``: run with ``make test-docker``.

This is what makes the "Preparing environment" phase real rather than cosmetic.
The behaviour that matters is that an image already present is reported without
contacting a registry -- otherwise every import would pay for a network round
trip to learn something it already knew.
"""

from __future__ import annotations

import pytest

from repolab.sandbox.docker import docker_available, ensure_image, image_present

pytestmark = pytest.mark.docker

#: Pinned: the image the playground runs in, pulled by earlier phases.
IMAGE = "apache/spark:3.5.1-python3"


@pytest.fixture(autouse=True)
def requires_docker() -> None:
    if not docker_available():
        pytest.skip("docker is not available")


class TestAnImageThatIsPresent:
    def test_it_is_reported_without_pulling(self) -> None:
        if not image_present(IMAGE):
            pytest.skip(f"{IMAGE} is not present; run the Spark demo once first")

        status = ensure_image(IMAGE)

        assert status.ready is True
        assert status.pulled is False, "an already-present image must not be re-pulled"
        assert status.error is None
        assert "already present" in status.describe()

    def test_the_check_is_fast(self) -> None:
        """An inspect, not a registry round trip."""
        if not image_present(IMAGE):
            pytest.skip(f"{IMAGE} is not present")
        assert ensure_image(IMAGE).seconds < 5.0


class TestAnImageThatCannotBeFetched:
    def test_the_failure_is_returned_rather_than_raised(self) -> None:
        """An unavailable image must not take down an otherwise good investigation."""
        status = ensure_image("repolab/deliberately-nonexistent:v0")

        assert status.ready is False
        assert status.present is False
        assert status.error
        assert "unavailable" in status.describe()

    def test_image_present_is_false_for_something_that_does_not_exist(self) -> None:
        assert image_present("repolab/deliberately-nonexistent:v0") is False
