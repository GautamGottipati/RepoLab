"""Surfaces a full-stack JavaScript repository really has, and none it does not.

An Angular frontend with an Express backend used to come out as "an API, and a
CLI": the browser application went undetected, and the CLI was assembled from
Express's ``bin/www`` server launcher, npm's build and lint scripts, Angular's
``*.service.ts`` files and a minified bundle checked into the repository. Each
of those is ordinary in a web repository, so each is tested here generically.
"""

from __future__ import annotations

import asyncio

from repolab.classify import classify
from repolab.experiences.catalog import experiences_for, readable_signals
from repolab.ingest.detectors.surfaces import is_generated_bundle
from repolab.ingest.languages import detect_languages
from repolab.ingest.tree import build_file_tree
from repolab.manifest.builder import build_manifest
from repolab.manifest.models import EntryPointKind, SurfaceType
from tests.test_detectors import run_detection

FULL_STACK: dict[str, str] = {
    "package.json": """{
      "name": "board",
      "scripts": {"ng": "ng", "start": "ng serve", "build": "ng build",
                  "test": "ng test", "lint": "ng lint", "e2e": "ng e2e"},
      "dependencies": {"@angular/core": "~9.0.0", "rxjs": "~6.5.0"}
    }""",
    "src/main.ts": (
        "import { platformBrowserDynamic } from '@angular/platform-browser-dynamic';\n"
        "import { AppModule } from './app/app.module';\n"
        "platformBrowserDynamic().bootstrapModule(AppModule);\n"
    ),
    "src/app/auth.service.ts": (
        "export class AuthService {\n"
        "  login() { return this.http.post(url, body).subscribe(this.done.bind(this)); }\n"
        "}\n"
    ),
    "karma.conf.js": "module.exports = config => config.set({ port: 9876 });\n",
    "backend/package.json": """{
      "name": "backend",
      "scripts": {"dev": "nodemon ./bin/www"},
      "dependencies": {"express": "^4.17.1", "mongoose": "^5.9.0"}
    }""",
    "backend/app.js": (
        "var express = require('express');\n"
        "var app = express();\n"
        "app.use('/api/posts', require('./routes/posts'));\n"
        "module.exports = app;\n"
    ),
    "backend/routes/posts.js": "const router = require('express').Router();\n",
    "backend/bin/www": (
        "#!/usr/bin/env node\n"
        "var app = require('../app');\n"
        "var server = require('http').createServer(app);\n"
        "server.listen(port);\n"
    ),
    # A production build checked in, as some repositories do.
    "backend/angular/main-es2015.33497bada58a5d3774a0.js": (
        "(window.webpackJsonp=window.webpackJsonp||[]).push([[1],{0:function(e,t,n){"
        + "var s=n(1);s.listen(8080);" * 80
        + "}}]);\n"
    ),
}


def manifest_for(root):
    outcome = run_detection(root)
    classification = asyncio.run(
        classify(outcome.run, entrypoints=outcome.entrypoints, services=outcome.services)
    )
    tree = build_file_tree(root)
    return outcome, build_manifest(
        repo_url="https://github.com/example/board",
        commit_sha="abc123",
        outcome=outcome,
        languages=detect_languages(tree),
        classification=classification,
    )


def surfaces(manifest) -> dict[SurfaceType, float]:
    return {s.type: s.confidence for s in manifest.interaction_surfaces}


def test_a_full_stack_repository_is_a_web_app_and_an_api_and_nothing_else(make_repo) -> None:
    root, _ = make_repo(FULL_STACK)
    _, manifest = manifest_for(root)
    found = surfaces(manifest)
    assert set(found) == {SurfaceType.WEB, SurfaceType.API}
    assert found[SurfaceType.WEB] >= 0.6 and found[SurfaceType.API] >= 0.6
    assert manifest.classification is not None and manifest.classification.hybrid
    assert "Angular" in manifest.frameworks


def test_npm_scripts_are_servers_tests_or_tooling_not_a_command_line(make_repo) -> None:
    root, _ = make_repo(FULL_STACK)
    outcome, _ = manifest_for(root)
    kinds = {e.name: e.kind for e in outcome.entrypoints}
    assert kinds["npm run start"] is EntryPointKind.WEB_SERVER
    assert kinds["npm run dev"] is EntryPointKind.WEB_SERVER
    assert kinds["npm run test"] is EntryPointKind.TEST_SUITE
    assert kinds["npm run e2e"] is EntryPointKind.TEST_SUITE
    assert kinds["npm run build"] is EntryPointKind.TOOLING
    assert kinds["npm run lint"] is EntryPointKind.TOOLING


def test_generated_bundles_and_function_binding_are_not_evidence(make_repo) -> None:
    root, _ = make_repo(FULL_STACK)
    outcome, manifest = manifest_for(root)
    cited = {ref.path for s in manifest.interaction_surfaces for ref in s.evidence}
    assert not any("angular/main-es2015" in path for path in cited)
    assert "karma.conf.js" not in cited
    daemons = [f for f in outcome.run.findings if f.kind == "server_daemon"]
    assert daemons == []


def test_a_real_shipped_command_still_counts(make_repo) -> None:
    files = {
        "bin/tool": '#!/bin/sh\nexec python -m tool "$@"\n',
        "tool/__main__.py": "import argparse\nargparse.ArgumentParser()\n",
    }
    root, _ = make_repo(files)
    _, manifest = manifest_for(root)
    assert SurfaceType.CLI in surfaces(manifest)


def test_a_daemon_class_still_counts(make_repo) -> None:
    root, _ = make_repo({"src/main/java/org/db/StoreDaemon.java": "class StoreDaemon {}\n"})
    outcome, _ = manifest_for(root)
    assert any(f.kind == "server_daemon" for f in outcome.run.findings)


def test_bundle_names_and_minified_text() -> None:
    assert is_generated_bundle("dist/vendor.min.js")
    assert is_generated_bundle("build/main-es2015.33497bada58a5d3774a0.js")
    assert not is_generated_bundle("src/main.ts", "const a = 1;\n")
    assert is_generated_bundle("src/vendor.js", "x" * 5000)


def test_the_catalog_speaks_plainly(make_repo) -> None:
    root, _ = make_repo(FULL_STACK)
    _, manifest = manifest_for(root)
    catalog = experiences_for(manifest)
    web = catalog.of(SurfaceType.WEB)
    assert web is not None
    assert web.meaning == "A user interface that runs in the browser."
    assert "bootstraps a browser application" in web.signals
    assert "depends on Angular" in web.signals
    assert web.strength in {"strong", "moderate"}
    assert readable_signals(
        "detected from express_app, framework:Express; corroborated by web_server"
    ) == ["creates an Express app", "depends on Express", "a script starts a server"]
    # An unknown signal is still shown, never dropped.
    assert readable_signals("detected from brand_new_kind") == ["brand new kind"]
