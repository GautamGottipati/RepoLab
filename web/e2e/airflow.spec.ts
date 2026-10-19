/**
 * The Phase 11 done-when criterion, mechanically.
 *
 * > Demonstrate that RepoLab can identify multiple interaction surfaces.
 * > Each surface must include evidence explaining why it was detected.
 * > The user should be able to select a surface.
 * > Unsupported experiences should clearly say: Detected, but not yet runnable
 * > in this RepoLab build. Never pretend an unsupported experience works.
 *
 * So this spec imports `apache/airflow` — a genuinely hybrid repository: a CLI,
 * a web UI, an HTTP API and a library — and checks the screen on all four
 * counts, including the last one. The strongest assertion here is a *negative*
 * one: RepoLab cannot build Airflow, so no tab, link or button may offer to run
 * any of its surfaces.
 *
 * Nothing is built. Investigation is a clone and a bounded read, which is why
 * this is the cheapest of the three browser specs despite the repository being
 * the largest.
 *
 * Requires: `make up`, the API on :8000, the worker running, the web app on
 * :3000. Run with `make e2e` or `make e2e-airflow`.
 */

import { expect, test } from "@playwright/test";

const AIRFLOW = "https://github.com/apache/airflow";

/** The exact sentence the specification requires. */
const NOT_RUNNABLE = "Detected, but not yet runnable in this RepoLab build.";

/** Airflow is ~14,000 files; the clone and the bounded scan take minutes. */
const INVESTIGATION_TIMEOUT = 12 * 60 * 1000;

test.describe("P3 — hybrid detection, from the browser", () => {
  test.setTimeout(20 * 60 * 1000);

  test("four surfaces, each with evidence, and nothing pretending to run", async ({ page }) => {
    // -- Import -------------------------------------------------------------
    await page.goto("/");
    await page.getByLabel("Repository URL").fill(AIRFLOW);
    await page.getByRole("button", { name: "Investigate" }).click();

    await expect(page).toHaveURL(/\/r\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    console.log(`  workspace: ${page.url()}`);

    await expect(page.getByTestId("repo-slug")).toContainText("airflow");
    await expect(page.getByTestId("job-status")).toHaveAttribute("data-status", "completed", {
      timeout: INVESTIGATION_TIMEOUT,
    });
    console.log("  investigation complete");

    // -- Available Experiences ----------------------------------------------
    await page.getByTestId("tab-Experiences").click();
    await expect(page.getByRole("heading", { name: "Available Experiences" })).toBeVisible();

    // The four surfaces the specification names, each with CLAUDE.md's icon.
    for (const [surface, icon, label] of [
      ["cli", ">_", "CLI"],
      ["web", "🌐", "Web"],
      ["api", "🔌", "API"],
      ["notebook", "📓", "Code / Notebook"],
    ] as const) {
      const card = page.getByTestId(`experience-${surface}`);
      await expect(card).toBeVisible();
      await expect(card).toContainText(icon);
      await expect(card).toContainText(label);
      console.log(`  surface: ${icon} ${label}`);
    }
    await expect(page.getByTestId("experience-list").locator("> li")).toHaveCount(4);
    await expect(page.getByTestId("hybrid-badge")).toBeVisible();

    // -- Selecting a surface shows the evidence behind it -------------------
    for (const surface of ["cli", "web", "api", "notebook"]) {
      await page.getByTestId(`select-${surface}`).click();

      const evidence = page.getByTestId(`evidence-${surface}`);
      await expect(evidence).toBeVisible();
      await expect(evidence).toContainText("detected from");

      // At least one real repository path, which is what makes it evidence
      // rather than an assertion.
      await expect(evidence.locator("li").first()).toContainText(/[\w./-]+/);
      console.log(`  ${surface}: evidence shown`);
    }

    // -- Nothing pretends to work -------------------------------------------
    for (const surface of ["cli", "web", "api", "notebook"]) {
      const card = page.getByTestId(`experience-${surface}`);
      await expect(card).toHaveAttribute("data-status", "detected_only");
      await expect(page.getByTestId(`unavailable-${surface}`)).toContainText(NOT_RUNNABLE);
      await expect(page.getByTestId(`open-${surface}`)).toHaveCount(0);
      await expect(page.getByTestId(`runnable-${surface}`)).toHaveCount(0);
    }
    console.log("  every surface states it is not runnable here");

    // The CLI is Airflow's *primary* surface and still offers nothing to open.
    await expect(page.getByTestId("experience-cli")).toContainText("primary");

    // And no tab offers an experience RepoLab cannot run.
    await expect(page.getByTestId("tab-Learn")).toHaveCount(0);
    await expect(page.getByTestId("tab-Playground")).toHaveCount(0);
    console.log("  no Learn or Playground tab is offered");

    // -- The Investigation screen agrees ------------------------------------
    await page.getByTestId("tab-Investigation").click();
    await expect(page.getByTestId("see-experiences")).toContainText("4 experiences");
    await expect(page.getByTestId("open-runnable")).toHaveCount(0);

    // The surface count links to the screen, so the route in is discoverable.
    await page.getByTestId("surfaces-link").click();
    await expect(page.getByRole("heading", { name: "Available Experiences" })).toBeVisible();
    console.log("  the investigation screen routes to the experiences");
  });
});
