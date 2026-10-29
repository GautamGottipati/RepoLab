"""Runtime plans, from a repository's own files. Offline; nothing runs.

A plan is how RepoLab would start a web app or API: which directory, which
image, which install, which of the project's own scripts, which port and why.
Every value must come from evidence, or be labelled as inferred.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from repolab.ingest.detectors.base import DetectionContext
from repolab.ingest.tree import build_file_tree
from repolab.manifest.models import (
    BuildKind,
    BuildStrategy,
    Fidelity,
    InteractionSurface,
    RepoManifest,
    SurfaceType,
)
from repolab.runtime import node
from repolab.runtime.models import DependencyState, EnvKind
from repolab.runtime.plan import plan_runtime


def manifest(*surfaces: SurfaceType) -> RepoManifest:
    return RepoManifest(
        repo_url="https://github.com/example/app",
        commit_sha="a" * 40,
        interaction_surfaces=[
            InteractionSurface(type=s, name=s.value, confidence=0.9, reason="test", primary=i == 0)
            for i, s in enumerate(surfaces)
        ],
        build_strategy=BuildStrategy(kind=BuildKind.SOURCE_BUILD, fidelity=Fidelity.EXACT),
        confidence=0.9,
    )


def plan_for(tmp_path: Path, files: dict[str, str], *surfaces: SurfaceType):
    root = tmp_path / "repo"
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    context = DetectionContext(root=root, tree=build_file_tree(root), commit_sha="a" * 40)
    return plan_runtime(context, manifest(*(surfaces or (SurfaceType.API,))))


def pkg(name: str, scripts: dict[str, str], deps: dict[str, str], **extra) -> str:
    return json.dumps({"name": name, "scripts": scripts, "dependencies": deps, **extra}, indent=2)


EXPRESS = {
    "package.json": pkg("api", {"start": "node ./bin/www"}, {"express": "^4"}),
    "package-lock.json": '{"lockfileVersion": 3}',
    "bin/www": "var port = process.env.PORT || '3001';\nserver.listen(port);\n",
    "app.js": (
        "const express = require('express');\nconst app = express();\n"
        "const users = require('./routes/users');\n"
        "app.use('/users', users);\napp.get('/health', (q, s) => s.send('ok'));\n"
    ),
    "routes/users.js": (
        "const router = require('express').Router();\n"
        "router.get('/', list);\nrouter.post('/api/signup', signup);\n"
    ),
}


class TestAnExpressApi:
    def test_the_projects_own_script_directory_and_port(self, tmp_path: Path) -> None:
        plan = plan_for(tmp_path, EXPRESS)
        (service,) = plan.services
        assert service.surfaces == ["api"]
        assert service.working_directory == ""
        assert service.install == "npm ci --no-audit --no-fund"
        assert service.start == "npm run start"
        assert service.port == 3001
        assert service.port_source == "PORT || 3001 in bin/www:1"
        assert "port" not in service.inferred_fields

    def test_node_version_from_the_lockfile_is_labelled_inferred(self, tmp_path: Path) -> None:
        (service,) = plan_for(tmp_path, EXPRESS).services
        assert service.image == "node:20"
        assert "lockfileVersion" in service.image_reason or "v3" in service.image_reason
        assert "image" in service.inferred_fields

    def test_routes_carry_their_mount_prefix_and_line(self, tmp_path: Path) -> None:
        (service,) = plan_for(tmp_path, EXPRESS).services
        found = {(r.method, r.path): r for r in service.routes}
        assert ("GET", "/users") in found
        assert ("POST", "/users/api/signup") in found
        assert found[("POST", "/users/api/signup")].file == "routes/users.js"
        assert found[("POST", "/users/api/signup")].line == 3
        assert service.health_path == "/health"

    def test_safe_variables_are_generated(self, tmp_path: Path) -> None:
        (service,) = plan_for(tmp_path, EXPRESS).services
        env = {v.name: v for v in service.env}
        assert env["PORT"].kind is EnvKind.GENERATED and env["PORT"].value == "3001"
        assert env["HOST"].value == "0.0.0.0"


class TestPackageManagersAndVersions:
    @pytest.mark.parametrize(
        ("lockfile", "install"),
        [
            ("package-lock.json", "npm ci --no-audit --no-fund"),
            ("yarn.lock", "yarn install --frozen-lockfile --non-interactive"),
            ("pnpm-lock.yaml", "corepack enable && pnpm install --frozen-lockfile"),
            (None, "npm install --no-audit --no-fund"),
        ],
    )
    def test_install_follows_the_lockfile(self, tmp_path, lockfile, install) -> None:
        files = {"package.json": pkg("a", {"start": "node index.js"}, {"express": "4"})}
        files["index.js"] = "app.listen(process.env.PORT || 5000)"
        if lockfile:
            files[lockfile] = "{}"
        (service,) = plan_for(tmp_path, files).services
        assert service.install == install
        assert node.allowed(service.install)

    def test_engines_beats_everything(self, tmp_path: Path) -> None:
        files = {
            "package.json": pkg(
                "a", {"start": "node i.js"}, {"express": "4"}, engines={"node": ">=18"}
            ),
            "package-lock.json": '{"lockfileVersion": 1}',
            "i.js": "app.listen(4000)",
        }
        (service,) = plan_for(tmp_path, files).services
        assert service.image == "node:18"
        assert "image" not in service.inferred_fields


class TestFrontends:
    @pytest.mark.parametrize(
        ("deps", "script", "start", "port"),
        [
            (
                {"@angular/core": "9"},
                "ng serve",
                "npm run start -- --host 0.0.0.0 --port 4200 --disable-host-check",
                4200,
            ),
            (
                {"vite": "5", "react": "18"},
                "vite",
                "npm run start -- --host 0.0.0.0 --port 5173",
                5173,
            ),
            ({"next": "14"}, "next dev", "npm run start -- -H 0.0.0.0 -p 3000", 3000),
            ({"react-scripts": "5"}, "react-scripts start", "npm run start", 3000),
        ],
    )
    def test_dev_servers_are_told_to_listen_everywhere(
        self, tmp_path, deps, script, start, port
    ) -> None:
        files = {"package.json": pkg("ui", {"start": script}, deps)}
        (service,) = plan_for(tmp_path, files, SurfaceType.WEB).services
        assert service.surfaces == ["web"]
        assert service.start == start
        assert service.port == port
        assert "port" in service.inferred_fields  # a framework default, not evidence
        assert node.allowed(service.start)

    def test_a_script_port_flag_is_evidence(self, tmp_path: Path) -> None:
        files = {"package.json": pkg("ui", {"dev": "vite --port 8081"}, {"vite": "5"})}
        (service,) = plan_for(tmp_path, files, SurfaceType.WEB).services
        assert service.port == 8081
        assert "port" not in service.inferred_fields

    def test_next_start_builds_first(self, tmp_path: Path) -> None:
        files = {
            "package.json": pkg(
                "ui", {"start": "next start", "build": "next build"}, {"next": "14"}
            )
        }
        (service,) = plan_for(tmp_path, files, SurfaceType.WEB).services
        assert service.build == "npm run build"


class TestMonorepos:
    FILES: ClassVar[dict[str, str]] = {
        "frontend/package.json": pkg("frontend", {"dev": "vite"}, {"vite": "5", "react": "18"}),
        "frontend/src/api.ts": "export const base = import.meta.env.VITE_API_URL;\n",
        "backend/package.json": pkg(
            "backend", {"start": "node server.js"}, {"express": "4", "mongoose": "8"}
        ),
        "backend/server.js": (
            "mongoose.connect(process.env.MONGODB_URI);\napp.listen(process.env.PORT || 8080);\n"
        ),
        "package.json": pkg("root", {"lint": "eslint ."}, {}),
    }

    def test_each_package_is_its_own_service(self, tmp_path: Path) -> None:
        plan = plan_for(tmp_path, self.FILES, SurfaceType.WEB, SurfaceType.API)
        by_name = {s.name: s for s in plan.services}
        assert set(by_name) == {"frontend", "backend"}
        assert by_name["frontend"].working_directory == "frontend"
        assert by_name["backend"].working_directory == "backend"
        assert by_name["backend"].port == 8080
        assert plan.unplanned == {}

    def test_a_database_read_from_the_environment_is_provisionable(self, tmp_path: Path) -> None:
        plan = plan_for(tmp_path, self.FILES, SurfaceType.WEB, SurfaceType.API)
        (dependency,) = plan.dependencies
        assert dependency.kind.value == "mongodb"
        assert dependency.state is DependencyState.PROVISIONABLE
        assert dependency.env_var == "MONGODB_URI"
        assert dependency.services == ["backend"]
        backend = plan.service("backend")
        assert {v.name: v.kind for v in backend.env}["MONGODB_URI"] is EnvKind.DEPENDENCY

    def test_tooling_only_packages_are_not_services(self, tmp_path: Path) -> None:
        plan = plan_for(tmp_path, self.FILES, SurfaceType.WEB, SurfaceType.API)
        assert "root" not in {s.name for s in plan.services}

    def test_packages_sharing_a_name_are_each_planned_with_their_own_files(
        self, tmp_path: Path
    ) -> None:
        """Airflow has two `ui` packages. Renaming the second must not lose it."""
        files = {
            "a/ui/package.json": pkg("ui", {"dev": "vite"}, {"vite": "5", "react": "18"}),
            "a/ui/src/api.ts": "export const base = import.meta.env.VITE_API_URL;\n",
            "b/ui/package.json": pkg(
                "ui", {"start": "node server.js"}, {"express": "4", "mongoose": "8"}
            ),
            "b/ui/server.js": (
                "mongoose.connect(process.env.MONGODB_URI);\n"
                "app.listen(process.env.PORT || 8080);\n"
            ),
        }
        plan = plan_for(tmp_path, files, SurfaceType.WEB, SurfaceType.API)
        by_dir = {s.working_directory: s.name for s in plan.services}
        assert by_dir == {"a/ui": "ui", "b/ui": "ui-2"}
        (dependency,) = plan.dependencies
        assert dependency.services == ["ui-2"]


class TestEvidencedLimits:
    def test_a_hard_coded_external_database_is_external_and_redacted(self, tmp_path: Path) -> None:
        files = {
            "package.json": pkg("api", {"start": "node app.js"}, {"express": "4", "mongoose": "5"}),
            "app.js": "mongoose.connect('mongodb+srv://admin:hunter2@cluster0.abc.mongodb.net/db');\n"
            "app.listen(3000);\n",
        }
        plan = plan_for(tmp_path, files)
        (dependency,) = plan.dependencies
        assert dependency.state is DependencyState.EXTERNAL_REQUIRED
        text = dependency.model_dump_json() + plan.model_dump_json()
        assert "hunter2" not in text and "admin:" not in text
        assert all(ref.excerpt is None for ref in dependency.evidence)
        assert "app.js:1" in dependency.reason
        assert any("external host" in w.message for w in plan.services[0].warnings)

    def test_a_localhost_database_cannot_be_redirected(self, tmp_path: Path) -> None:
        files = {
            "package.json": pkg("api", {"start": "node app.js"}, {"express": "4", "pg": "8"}),
            "app.js": "new Pool({connectionString: 'postgres://localhost:5432/app'});\napp.listen(3000);\n",
        }
        (dependency,) = plan_for(tmp_path, files).dependencies
        assert dependency.state is DependencyState.UNSUPPORTED
        assert "localhost" in dependency.reason

    def test_a_frontend_calling_a_fixed_localhost_is_warned(self, tmp_path: Path) -> None:
        files = {
            "package.json": pkg("ui", {"start": "ng serve"}, {"@angular/core": "9"}),
            "src/app/api.service.ts": "this.http.get('http://localhost:3000/posts');\n",
            "backend/package.json": pkg("backend", {"start": "node app.js"}, {"express": "4"}),
            "backend/app.js": "app.listen(process.env.PORT || 3000);\n",
        }
        plan = plan_for(tmp_path, files, SurfaceType.WEB, SurfaceType.API)
        web = next(s for s in plan.services if "web" in s.surfaces)
        warning = next(w for w in web.warnings if "localhost:3000" in w.message)
        assert "backend service's port" in warning.message
        assert warning.evidence[0].path == "src/app/api.service.ts"

    def test_secrets_are_classified_never_valued(self, tmp_path: Path) -> None:
        files = {
            "package.json": pkg("api", {"start": "node app.js"}, {"express": "4"}),
            "app.js": "jwt.sign(x, process.env.JWT_SECRET);\nconst k = process.env.STRIPE_KEY;\n"
            "const r = process.env.REGION;\napp.listen(3000);\n",
            ".env": "JWT_SECRET=do-not-read\n",
        }
        (service,) = plan_for(tmp_path, files).services
        env = {v.name: v for v in service.env}
        assert env["JWT_SECRET"].kind is EnvKind.SECRET and env["JWT_SECRET"].value is None
        assert env["STRIPE_KEY"].kind is EnvKind.SECRET
        assert env["REGION"].kind is EnvKind.REQUIRED
        assert "do-not-read" not in service.model_dump_json()
        assert any("REGION" in w.message for w in service.warnings)

    def test_no_server_script_means_no_service_and_a_reason(self, tmp_path: Path) -> None:
        files = {"package.json": pkg("lib", {"build": "tsc"}, {"express": "4"})}
        plan = plan_for(tmp_path, files)
        assert plan.services == []
        assert "start or dev script" in plan.unplanned["api"]

    def test_no_package_json_says_what_this_build_runs(self, tmp_path: Path) -> None:
        plan = plan_for(tmp_path, {"main.py": "app = FastAPI()"})
        assert "Node.js" in plan.unplanned["api"]

    def test_only_detected_surfaces_are_planned(self, tmp_path: Path) -> None:
        plan = plan_runtime(
            DetectionContext(root=tmp_path, tree=build_file_tree(tmp_path)),
            manifest(SurfaceType.CLI),
        )
        assert plan.services == [] and plan.unplanned == {}


class TestTheCommandAllowlist:
    @pytest.mark.parametrize(
        "command",
        ["npm run start; curl evil", "rm -rf /", "npm run start -- $(id)", "npx something"],
    )
    def test_anything_else_is_refused(self, command: str) -> None:
        assert not node.allowed(command)
