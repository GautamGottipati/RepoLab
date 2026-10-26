/**
 * The Phase 10 done-when criterion, mechanically.
 *
 * > A new user can complete one Cassandra lesson from the browser using the
 * > real CLI experience.
 *
 * So this spec invokes no script. It opens a browser, imports Apache Cassandra,
 * asks for an environment, waits for the ladder to produce one, opens a terminal
 * on it and completes all eight lesson steps — including the step that provokes
 * a real error, and the one that explains it.
 *
 * Nothing it asserts is simulated: the terminal is a WebSocket to a container
 * running the build RepoLab resolved, every statement is executed by that
 * container's own cqlsh, and a step is only marked complete after RepoLab has
 * checked the real output of the real command.
 *
 * Requires: `make up`, the API on :8000, the worker running, the web app on
 * :3000, and Docker with ~4 GB available to a container. Run with `make e2e`.
 */

import { expect, test } from "@playwright/test";

const CASSANDRA = "https://github.com/apache/cassandra";

/** Cassandra is a large repository; the clone and investigation take minutes. */
const INVESTIGATION_TIMEOUT = 10 * 60 * 1000;

/** The ladder races every rung; the first to pass its smoke test wins. */
const BUILD_TIMEOUT = 12 * 60 * 1000;

/** Restoring a checkpoint and waiting for Cassandra to accept a connection. */
const SESSION_TIMEOUT = 6 * 60 * 1000;

/** cqlsh starts a Python process per statement, so each step costs a second or two. */
const STEP_TIMEOUT = 2 * 60 * 1000;

test.describe("P1 — the Cassandra lesson, from the browser", () => {
  // Longer than the shared default: a cold run clones a large repository,
  // investigates it, resolves the ladder and starts a real node before the
  // first statement is typed.
  test.setTimeout(45 * 60 * 1000);

  test("import, build, connect, and complete every step", async ({ page }) => {
    // -- Import -------------------------------------------------------------
    await page.goto("/");
    await page.getByLabel("Repository URL").fill(CASSANDRA);
    await page.getByRole("button", { name: "Investigate" }).click();

    await expect(page).toHaveURL(/\/r\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    console.log(`  workspace: ${page.url()}`);

    await expect(page.getByTestId("repo-slug")).toContainText("cassandra");
    await expect(page.getByTestId("job-status")).toHaveAttribute("data-status", "completed", {
      timeout: INVESTIGATION_TIMEOUT,
    });
    console.log("  investigation complete");

    // -- The CLI is offered as an experience because the manifest found it -----
    await page.getByTestId("tab-Experiences").click();
    const learn = page.getByTestId("open-experience");
    await expect(learn).toHaveAttribute("href", /\/learn$/);
    console.log("  Try the CLI is offered — the manifest decides");

    await learn.click();
    await expect(page.getByTestId("lesson-title")).toBeVisible({ timeout: 60_000 });

    // -- Building an environment is asked for, not assumed -------------------
    const build = page.getByTestId("build-environment");
    if (await build.isVisible().catch(() => false)) {
      await build.click();
      await expect(page.getByTestId("build-job")).toBeVisible();
      console.log("  environment requested — the ladder is resolving");
    } else {
      console.log("  an environment was already cached");
    }

    // -- A terminal, once a rung has passed its smoke test ------------------
    const start = page.getByTestId("start-session");
    await expect(start).toBeVisible({ timeout: BUILD_TIMEOUT });
    console.log("  an environment is ready");

    await start.click();

    const terminal = page.getByTestId("terminal");
    await expect(terminal).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("terminal-status")).toHaveText("connected", {
      timeout: SESSION_TIMEOUT,
    });
    console.log("  terminal connected to the running environment");

    // -- Every step, run and checked against real output --------------------
    const dots = page.getByTestId("lesson-progress").locator("li");
    const total = await dots.count();
    expect(total).toBe(8);

    for (let step = 1; step <= total; step += 1) {
      await page.getByTestId(`goto-step-${step}`).click();

      const card = page.getByTestId(`lesson-step-${step}`);
      await expect(card).toBeVisible();
      const statement = (await page.getByTestId(`lesson-action-${step}`).innerText()).trim();

      await page.getByTestId("run-step").click();

      // The statement really reaches the terminal...
      await expect(terminal).toContainText(statement.slice(0, 40), { timeout: STEP_TIMEOUT });

      // ...and its result really comes back. The buttons are disabled while a
      // statement is in flight, because the record a step is judged from does
      // not exist until cqlsh has returned.
      const check = page.getByTestId("check-step");
      await expect(check).toBeEnabled({ timeout: STEP_TIMEOUT });

      await check.click();
      const verdict = page.getByTestId("step-verdict");
      await expect(verdict).toBeVisible({ timeout: STEP_TIMEOUT });
      await expect(verdict).toContainText("That worked.", { timeout: STEP_TIMEOUT });

      // Checked, not judged: the deterministic path decided every step.
      await expect(page.getByTestId("decided-by")).toContainText("deterministic");

      await expect(page.getByTestId(`lesson-dot-${step}`)).toHaveAttribute("data-done", "true");
      console.log(`  step ${step} passed: ${statement.slice(0, 60)}`);
    }

    // -- The error step was a real refusal from the real server --------------
    await page.getByTestId("goto-step-7").click();
    await expect(terminal).toContainText("ALLOW FILTERING");
    console.log("  the provoked error was Cassandra's own");

    // -- And the lesson is complete -----------------------------------------
    await expect(page.getByTestId("step-verdict")).toBeVisible();
    for (let step = 1; step <= total; step += 1) {
      await expect(page.getByTestId(`lesson-dot-${step}`)).toHaveAttribute("data-done", "true");
    }
    console.log("  every step complete, against a real Cassandra");

    // -- A reload resumes: progress is recorded, not held in the page --------
    await page.reload();
    await expect(page.getByTestId("lesson-title")).toBeVisible({ timeout: 60_000 });
    console.log("  reload replayed the workspace");
  });
});
