"""Repository URL validation.

Ingestion clones whatever it is handed, so validation is a security boundary:
it must refuse anything that could reach the local network or invoke a transport
helper.
"""

from __future__ import annotations

import pytest

from repolab.ingest import InvalidRepoUrlError, validate_repo_url


class TestAcceptedUrls:
    def test_plain_github_url(self) -> None:
        url = validate_repo_url("https://github.com/apache/spark")
        assert (url.host, url.owner, url.name) == ("github.com", "apache", "spark")
        assert url.slug == "apache/spark"

    @pytest.mark.parametrize(
        "raw",
        [
            "https://github.com/apache/spark.git",
            "https://github.com/apache/spark/",
            "  https://github.com/apache/spark  ",
            "https://GitHub.com/apache/spark",
            "https://github.com/apache/spark/tree/master",
        ],
    )
    def test_all_normalise_to_the_same_url(self, raw: str) -> None:
        assert validate_repo_url(raw).normalized == "https://github.com/apache/spark"

    def test_other_hosts_are_allowed(self) -> None:
        assert validate_repo_url("https://gitlab.com/g/p").host == "gitlab.com"

    def test_dots_and_dashes_in_names(self) -> None:
        url = validate_repo_url("https://github.com/my-org/my.repo-name")
        assert url.name == "my.repo-name"


class TestRejectedSchemes:
    @pytest.mark.parametrize(
        "raw",
        [
            "http://github.com/a/b",
            "git://github.com/a/b",
            "ssh://git@github.com/a/b",
            "file:///etc/passwd",
            "ext::sh -c touch$IFSpwned",
        ],
    )
    def test_non_https_is_refused(self, raw: str) -> None:
        with pytest.raises(InvalidRepoUrlError):
            validate_repo_url(raw)

    def test_scp_style_is_refused(self) -> None:
        with pytest.raises(InvalidRepoUrlError, match="ssh and scp-style"):
            validate_repo_url("git@github.com:apache/spark.git")


class TestRejectedHosts:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.10",
            "172.16.0.1",
            "169.254.169.254",
            "0.0.0.0",
            "[::1]",
            "evil.localhost",
        ],
    )
    def test_internal_hosts_are_refused(self, host: str) -> None:
        """Cloning from these would turn ingestion into an internal-network probe."""
        with pytest.raises(InvalidRepoUrlError):
            validate_repo_url(f"https://{host}/a/b")

    def test_cloud_metadata_address_is_refused(self) -> None:
        with pytest.raises(InvalidRepoUrlError, match="non-public"):
            validate_repo_url("https://169.254.169.254/latest/meta-data")


class TestRejectedShapes:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "https://github.com/",
            "https://github.com/onlyowner",
            "https://github.com/a/b?token=x",
            "https://github.com/a/b#frag",
            "https://github.com/../etc",
            "not-a-url",
        ],
    )
    def test_malformed_urls_are_refused(self, raw: str) -> None:
        with pytest.raises(InvalidRepoUrlError):
            validate_repo_url(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            "https://user:pass@github.com/a/b",
            "https://token@github.com/a/b",
        ],
    )
    def test_credentials_are_refused(self, raw: str) -> None:
        """A URL carrying a credential would put a secret in logs and the DB."""
        with pytest.raises(InvalidRepoUrlError, match="credentials"):
            validate_repo_url(raw)
