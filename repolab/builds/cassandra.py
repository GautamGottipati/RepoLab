"""Build plans for Apache Cassandra, at every rung of the ladder.

Four ways to get a running Cassandra, in descending fidelity:

    exact source build   ant jar on the requested commit, in a JDK container
    official release     the official container, version difference disclosed
    partial build        a cut-down single node, limitations declared
    Mini Lab             no environment at all (repolab/builds/minilab.py)

All three real candidates share **one smoke test**, because CLAUDE.md already
fixes what it must be: *cqlsh connects and a SELECT executes.* Cassandra takes
thirty to sixty seconds before it will accept a connection, so the check polls
rather than assuming, and looks for a marker in the output -- a zero exit code
from a shell pipeline is not proof that a query ran.
"""

from __future__ import annotations

import os

from repolab.builds.plan import BuildPlan, BuildStep, SmokeTest
from repolab.manifest.models import RepoManifest

#: The released version RepoLab substitutes when it cannot build the commit.
#:
#: Pinned rather than floating: an environment whose version changes underneath a
#: recorded experiment would make that experiment unreproducible.
OFFICIAL_VERSION = "5.0"
OFFICIAL_IMAGE = os.environ.get("REPOLAB_CASSANDRA_IMAGE", f"cassandra:{OFFICIAL_VERSION}")

#: Java for the source build. `build.xml` declares `java.supported` as 11, 17 and
#: 21, so 17 is a supported toolchain rather than a guess.
#:
#: The **jammy** variant specifically. Cassandra's `cqlsh` requires Python
#: 3.6-3.13 and refuses to start outside that range; the default Temurin tag is
#: Ubuntu 26.04, which ships Python 3.14, so a source build compiled perfectly
#: and then had no usable client to prove it worked with. Ubuntu 22.04 ships
#: Python 3.10.
SOURCE_IMAGE = os.environ.get("REPOLAB_CASSANDRA_JDK_IMAGE", "eclipse-temurin:17-jdk-jammy")

#: Printed by the smoke query. Checked for, so a zero exit code alone is never
#: taken as evidence that Cassandra answered.
SMOKE_MARKER = "REPOLAB_CQL_OK"

#: Where the official image keeps its binaries.
#:
#: Referenced absolutely rather than through ``PATH``. The image sets a PATH that
#: includes this directory, but RepoLab runs commands through a *login* shell,
#: which resets PATH from ``/etc/profile`` -- so ``cassandra`` is not found. An
#: absolute path does not depend on which shell happens to run it.
OFFICIAL_BIN = "/opt/cassandra/bin"
OFFICIAL_ENTRYPOINT = "/usr/local/bin/docker-entrypoint.sh"
OFFICIAL_CONF = "/opt/cassandra/conf/cassandra.yaml"

#: Container memory for a full node. Measured: 2 GB kills the JVM before it logs
#: anything at all; 4 GB starts and accepts connections.
OFFICIAL_MEMORY = os.environ.get("REPOLAB_CASSANDRA_MEMORY", "4g")

#: How long to wait for a fresh node to accept connections. A first start
#: initialises the system keyspaces and is genuinely slow.
STARTUP_TIMEOUT = 300.0

#: Where a source build puts its tree inside the container.
SOURCE_DIR = "/work/repo"

#: Poll until cqlsh answers, then run a real query against a real table.
#:
#: `system.local` is the one table guaranteed to exist on a fresh single node, so
#: the query needs no schema of RepoLab's own making.
_WAIT_AND_QUERY = f"""
for i in $(seq 1 {int(STARTUP_TIMEOUT // 5)}); do
  if {{cqlsh}} -e 'SELECT release_version FROM system.local' >/tmp/cql.out 2>/tmp/cql.err; then
    echo "{SMOKE_MARKER}"
    cat /tmp/cql.out
    exit 0
  fi
  sleep 5
done
echo "cassandra did not accept a connection within {int(STARTUP_TIMEOUT)}s" >&2
tail -40 /tmp/cql.err 2>/dev/null
exit 1
"""


def smoke_test(cqlsh: str = "cqlsh") -> SmokeTest:
    """The check that decides whether a Cassandra build is usable.

    Identical across the ladder on purpose: a partial build that passes this has
    demonstrated the same thing an exact one did -- that the server starts and
    answers CQL -- and the fidelity difference is recorded separately, in the
    artifact's limitations.
    """
    return SmokeTest(
        name="cqlsh connects and a SELECT executes",
        command=_WAIT_AND_QUERY.format(cqlsh=cqlsh).strip(),
        expect_stdout_contains=SMOKE_MARKER,
        timeout_seconds=STARTUP_TIMEOUT + 60.0,
    )


def _declared_version(manifest: RepoManifest) -> str | None:
    """The version the repository itself declares, when it says so."""
    for dependency in manifest.dependencies:
        if dependency.name.lower() == "cassandra" and dependency.version_spec:
            return dependency.version_spec.lstrip("=<>~!").strip()
    return None


# ---------------------------------------------------------------------------
# Exact source build
# ---------------------------------------------------------------------------


def cassandra_source_plan(manifest: RepoManifest, *, base_image: str | None = None) -> BuildPlan:
    """Build the requested commit with the repository's own build system.

    `ant jar` is what Cassandra's `build.xml` defines, and it is genuinely
    expensive: Ivy resolves dependencies over the network from inside the
    container and the compile takes minutes. That cost is the reason the ladder
    exists, not a reason to avoid doing it.

    One accommodation is needed. The archive uploaded to a build deliberately
    excludes ``.git`` -- history is hundreds of megabytes and no build needs it --
    but Cassandra's ``build.xml`` execs a git script with ``failonerror="true"``.
    So the extracted tree is re-initialised as an empty repository: enough for
    that step to succeed, and honest about being a workaround rather than the
    repository's own history.
    """
    image = base_image or SOURCE_IMAGE

    return BuildPlan(
        base_image=image,
        workdir=SOURCE_DIR,
        setup=[
            BuildStep(
                name="prepare the workspace",
                command="mkdir -p /work",
                timeout_seconds=60.0,
            ),
            BuildStep(
                name="install the build toolchain",
                command=(
                    # `git` is not optional: build.xml execs
                    # `.build/git/install-git-defaults.sh` with
                    # failonerror="true", so the build stops without it.
                    "(apt-get update -qq && apt-get install -y -qq ant git python3 procps "
                    "> /dev/null 2>&1) || true; ant -version && git --version"
                ),
                timeout_seconds=900.0,
            ),
        ],
        steps=[
            BuildStep(
                name="check the toolchain",
                command=f"cd {SOURCE_DIR} && java -version 2>&1 | head -1 && ant -version",
                timeout_seconds=120.0,
            ),
            BuildStep(
                name="initialise a repository for the build",
                command=(
                    f"cd {SOURCE_DIR} && git init -q 2>/dev/null; "
                    "git config user.email repolab@invalid; "
                    "git config user.name RepoLab; echo ok"
                ),
                timeout_seconds=120.0,
                optional=True,
            ),
            BuildStep(
                name="build the jars",
                # The repository's own target. Nothing is substituted, which is
                # what makes this candidate exact.
                command=f"cd {SOURCE_DIR} && ant jar -Duse.jdk17=true",
                timeout_seconds=3600.0,
            ),
            BuildStep(
                name="record versions",
                command=(
                    f"cd {SOURCE_DIR} && "
                    "echo cassandra $(cat build/classes/main/META-INF/* 2>/dev/null | head -1 "
                    "|| echo from-source) && java -version 2>&1 | head -1"
                ),
                timeout_seconds=120.0,
                optional=True,
            ),
        ],
        service_command=f"cd {SOURCE_DIR} && bin/cassandra -R -f",
        cli_command=f"{SOURCE_DIR}/bin/cqlsh",
        needs_submodules=True,
        memory=OFFICIAL_MEMORY,
        smoke=smoke_test(cqlsh=f"{SOURCE_DIR}/bin/cqlsh"),
        capabilities=[
            "run the Cassandra server built from the requested commit",
            "execute CQL through the repository's own cqlsh",
            "inspect server configuration and logs",
        ],
        limitations=[
            "a single node, so replication and multi-node behaviour cannot be observed",
        ],
    )


# ---------------------------------------------------------------------------
# Official release
# ---------------------------------------------------------------------------


def cassandra_official_plan(manifest: RepoManifest, *, image: str | None = None) -> BuildPlan:
    """Use the official released container, and disclose the version difference.

    The deviation is recorded unconditionally, because it always exists: a
    release is never the commit that was asked for. That disclosure is what
    drops this candidate's fidelity to ``HIGH``, and it travels with the
    artifact into anything that cites it.
    """
    resolved = image or OFFICIAL_IMAGE
    declared = _declared_version(manifest)
    requested = manifest.commit_sha[:12] if manifest.commit_sha else "the requested commit"

    deviations = [
        f"official Cassandra {OFFICIAL_VERSION} substituted for {requested}; "
        "this is a released version, not a build of that commit"
    ]
    if declared and not declared.startswith(OFFICIAL_VERSION):
        deviations.append(f"the repository declares cassandra {declared}")

    return BuildPlan(
        base_image=resolved,
        workdir="/work",
        setup=[BuildStep(name="prepare the workspace", command="mkdir -p /work")],
        steps=[
            BuildStep(
                name="record versions",
                command=(
                    f"echo cassandra $({OFFICIAL_BIN}/cassandra -v) && java -version 2>&1 | head -1"
                ),
                timeout_seconds=120.0,
            ),
        ],
        # The server is the container, not a background job inside it.
        service_command=f"{OFFICIAL_ENTRYPOINT} cassandra -R -f",
        cli_command=f"{OFFICIAL_BIN}/cqlsh",
        memory=OFFICIAL_MEMORY,
        smoke=smoke_test(f"{OFFICIAL_BIN}/cqlsh"),
        deviations=deviations,
        capabilities=[
            f"run a real Cassandra {OFFICIAL_VERSION} server",
            "execute CQL through the official cqlsh",
            "create keyspaces and tables, insert and query data",
        ],
        limitations=[
            f"Cassandra {OFFICIAL_VERSION}, not the requested commit; "
            "behaviour specific to that commit cannot be observed here",
            "a single node, so replication and multi-node behaviour cannot be observed",
        ],
    )


# ---------------------------------------------------------------------------
# Partial build
# ---------------------------------------------------------------------------

#: A cut-down configuration: one node, a small heap, and the optional
#: subsystems a lesson does not need turned off. This is what makes the partial
#: build viable where the full one will not fit.
PARTIAL_HEAP = os.environ.get("REPOLAB_CASSANDRA_PARTIAL_HEAP", "512M")

#: Container memory for the reduced node. Lower than the full server, which is
#: the point: this rung exists for machines where 4 GB is not available.
PARTIAL_MEMORY = os.environ.get("REPOLAB_CASSANDRA_PARTIAL_MEMORY", "2g")


def cassandra_partial_plan(manifest: RepoManifest, *, image: str | None = None) -> BuildPlan:
    """A simplified single node, sufficient for the lessons RepoLab supports.

    Not a lesser copy of the official release for its own sake: it exists for
    machines where the full server will not start. It runs on a small heap with
    virtual nodes reduced to one and the optional subsystems off, and it
    **declares what it therefore cannot do** rather than implying a full server.
    """
    resolved = image or OFFICIAL_IMAGE

    return BuildPlan(
        base_image=resolved,
        workdir="/work",
        setup=[BuildStep(name="prepare the workspace", command="mkdir -p /work")],
        steps=[
            BuildStep(
                name="reduce the configuration",
                command=(
                    f"CONF={OFFICIAL_CONF}; "
                    "sed -i 's/^num_tokens:.*/num_tokens: 1/' $CONF && "
                    "sed -i 's/^materialized_views_enabled:.*/materialized_views_enabled: false/' "
                    "$CONF 2>/dev/null; "
                    "grep -E '^(num_tokens|cluster_name)' $CONF"
                ),
                timeout_seconds=120.0,
            ),
            BuildStep(
                name="record versions",
                command=f"echo cassandra $({OFFICIAL_BIN}/cassandra -v)",
                timeout_seconds=120.0,
            ),
        ],
        service_command=(
            f"MAX_HEAP_SIZE={PARTIAL_HEAP} HEAP_NEWSIZE=128M CASSANDRA_NUM_TOKENS=1 "
            f"{OFFICIAL_ENTRYPOINT} cassandra -R -f"
        ),
        cli_command=f"{OFFICIAL_BIN}/cqlsh",
        memory=PARTIAL_MEMORY,
        smoke=smoke_test(f"{OFFICIAL_BIN}/cqlsh"),
        capabilities=[
            "execute CQL against a single node",
            "create keyspaces and tables, insert and query data",
        ],
        limitations=[
            f"a {PARTIAL_HEAP} heap, so anything beyond small lesson datasets will not fit",
            "one virtual node, so token distribution and ring behaviour are not representative",
            "materialized views and other optional subsystems are disabled",
            "not the requested commit, and not a full default configuration",
            "performance measurements taken here describe a deliberately reduced node",
        ],
    )
