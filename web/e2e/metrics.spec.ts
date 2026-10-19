/**
 * The Phase 12 done-when criterion, mechanically.
 *
 * > Done when the demo can show both:
 * > Developer learning activity + RepoLab operational/agent performance
 *
 * And the prohibition that goes with it: do not invent enterprise outcome
 * metrics. So the strongest assertions here are negative — the north-star KPI
 * is labelled and carries no number, and **no metric that was not measured
 * displays a value anywhere on the page**.
 *
 * Fast: the dashboard is one aggregate read over data the rest of the suite
 * has already produced. Nothing is built and nothing is run.
 *
 * Requires: `make up`, the API on :8000, the web app on :3000.
 * Run with `make e2e` or `make e2e-metrics`.
 */

import { expect, test } from "@playwright/test";

test.describe("P4 — the KPI dashboard", () => {
  test("learning activity, operational performance, and nothing invented", async ({ page }) => {
    // Reachable from the product, not just by URL.
    await page.goto("/");
    await page.getByTestId("metrics-link").click();
    await expect(page).toHaveURL(/\/metrics$/);

    await expect(page.getByRole("heading", { name: "Metrics", exact: true })).toBeVisible();

    // -- Whose activity this is, stated before any figure -------------------
    const provenance = page.getByTestId("provenance");
    await expect(provenance).toBeVisible();
    await expect(provenance).toContainText("development traffic");

    // -- Both sections, separately ------------------------------------------
    const learning = page.getByTestId("section-learning");
    const operations = page.getByTestId("section-operations");
    await expect(learning).toBeVisible();
    await expect(operations).toBeVisible();

    // Case-insensitive: the section titles are rendered uppercase by CSS.
    await expect(learning).toContainText(/developer learning activity/i);
    await expect(operations).toContainText(/RepoLab operational & agent performance/i);

    // `exact` matters here. Playwright matches accessible names as substrings,
    // so "Agent performance" also matches the section's own heading, "RepoLab
    // operational & agent performance" — two matches, and a strict-mode failure.
    for (const group of ["Repositories", "Time to value", "Lessons", "Experiments"]) {
      await expect(learning.getByRole("heading", { name: group, exact: true })).toBeVisible();
    }
    for (const group of ["Agent performance", "Tool layer", "Model usage", "Compute and cost"]) {
      await expect(operations.getByRole("heading", { name: group, exact: true })).toBeVisible();
    }
    console.log("  both sections present, with all eight groups");

    // -- The product metrics the phase names --------------------------------
    for (const key of [
      "repositories_analyzed",
      "time_to_repo_ready_median",
      "time_to_first_run",
      "time_to_first_experiment",
      "lessons_started",
      "lessons_completed",
      "experiments_proposed",
      "experiments_approved",
      "experiments_completed",
      "experiment_completion_rate",
      "hints_shown",
      "build_success_rate",
      "challenge_success_rate",
    ]) {
      await expect(page.getByTestId(`metric-${key}`)).toBeVisible();
    }

    // -- The agent and infrastructure metrics, separately -------------------
    for (const key of [
      "agent_steps_mean",
      "tool_success_rate",
      "model_latency_median",
      "model_tokens_prompt",
      "model_cost_total",
      "container_compute_seconds",
      "cost_per_ready_repo",
      "cost_per_experiment",
    ]) {
      await expect(page.getByTestId(`metric-${key}`)).toBeVisible();
    }
    console.log("  every named metric is on the page");

    // -- Measured metrics really do carry numbers ---------------------------
    await expect(page.getByTestId("value-repositories_analyzed")).toContainText(/\d/);
    await expect(page.getByTestId("value-tool_success_rate")).toContainText(/\d/);
    await expect(page.getByTestId("value-agent_steps_mean")).toContainText(/\d/);
    console.log("  learning and operational figures both measured");

    // -- Nothing unmeasured shows a number ----------------------------------
    const unmeasured = page.locator(
      '[data-testid^="metric-"]:not([data-status="measured"])',
    );
    const count = await unmeasured.count();
    for (let index = 0; index < count; index += 1) {
      const cell = unmeasured.nth(index);
      const key = (await cell.getAttribute("data-testid")) ?? "";
      await expect(cell.locator('[data-testid^="value-"]')).toHaveCount(0);
      await expect(cell.locator('[data-testid^="status-"]')).toHaveCount(1);
      await expect(cell).toContainText(/not instrumented|no data yet/);
      console.log(`  ${key}: stated, not numbered`);
    }

    // Challenges do not exist in RepoLab, and the screen says so.
    await expect(page.getByTestId("metric-challenge_success_rate")).toHaveAttribute(
      "data-status",
      "not_instrumented",
    );

    // -- The north star is named, defined, and never numbered ---------------
    const northStar = page.getByTestId("north-star");
    await expect(northStar).toBeVisible();
    await expect(northStar).toContainText("Time to First Meaningful Contribution");
    await expect(northStar).toContainText(/long-term north-star KPI/i);
    await expect(page.getByTestId("north-star-value")).toHaveText("not measured");
    await expect(northStar).toContainText("Requires longitudinal data");

    // No percentage, no trend, no claim of improvement anywhere in that block.
    await expect(northStar).not.toContainText("%");
    await expect(northStar).not.toContainText(/improved|reduction|faster/i);
    console.log("  the north star is labelled and carries no number");
  });
});
