/**
 * The P0 done-when criterion, mechanically.
 *
 * > The entire P0 Spark experience works from the browser without manually
 * > invoking backend scripts.
 *
 * So this spec invokes no script. It opens a browser, types a repository URL,
 * and clicks through the whole product: investigate → architecture → run the
 * pipeline → ask the shuffle-partitions question → approve → watch variants
 * arrive → read the explanation and its evidence.
 *
 * Everything it asserts is real: a real clone, real Spark in a container, real
 * measurements from Spark's event log. That is why it is slow, and why it is
 * kept out of the default test run.
 *
 * Requires: `make up`, the API on :8000, the worker running, the web app on
 * :3000, and Docker. Run with `make e2e`.
 */

import { expect, test, type Page } from "@playwright/test";

const DEMO_REPO = "https://github.com/AlexIoannides/pyspark-example-project";
const QUESTION = "Why does this job use 200 shuffle partitions?";

/** Investigation clones and may pull a 600 MB image on a cold machine. */
const INVESTIGATION_TIMEOUT = 6 * 60 * 1000;

/** One Spark run: container start, submit, event log, parse. */
const RUN_TIMEOUT = 3 * 60 * 1000;

/** A full approved experiment: several variants, each a real Spark run. */
const EXPERIMENT_TIMEOUT = 10 * 60 * 1000;

test.describe("P0 — the Spark experience, from the browser", () => {
  test("import, investigate, run, ask, approve, measure, explain", async ({ page }) => {
    // -- Import -------------------------------------------------------------
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "RepoLab" })).toBeVisible();

    await page.getByLabel("Repository URL").fill(DEMO_REPO);
    await page.getByRole("button", { name: "Investigate" }).click();

    // The job id is in the URL, so a refresh resumes the same repository.
    await expect(page).toHaveURL(/\/r\/[0-9a-f-]{36}$/, { timeout: 30_000 });
    const jobUrl = page.url();
    console.log(`  workspace: ${jobUrl}`);

    // -- Investigation ------------------------------------------------------
    await expect(page.getByTestId("repo-slug")).toContainText("pyspark-example-project");

    // The four phases the specification names, from the backend's own
    // vocabulary. Each must actually be announced; none is cosmetic.
    for (const label of [
      "Inspecting repository",
      "Finding Spark entry points",
      "Understanding data flow",
    ]) {
      await expect(page.getByRole("listitem").filter({ hasText: label })).toHaveAttribute(
        "data-state",
        /active|done/,
        { timeout: INVESTIGATION_TIMEOUT },
      );
      console.log(`  phase: ${label}`);
    }

    // Agent activity is visible throughout, and carries real statuses.
    await expect(page.getByTestId("activity-feed")).toContainText("cloning");

    // Refreshing mid-run must replay history rather than lose it.
    await page.reload();
    await expect(page.getByTestId("activity-feed")).toContainText("cloning", {
      timeout: 60_000,
    });
    console.log("  refresh replayed the stream");

    await expect(page.getByTestId("job-status")).toHaveAttribute("data-status", "completed", {
      timeout: INVESTIGATION_TIMEOUT,
    });
    console.log("  investigation complete");

    // -- Architecture -------------------------------------------------------
    await page.getByTestId("tab-Architecture").click();
    await expect(page).toHaveURL(/\/architecture$/);

    await expect(page.getByTestId("components")).toBeVisible();
    await expect(page.getByTestId("architecture-graph")).toBeVisible();
    await expect(page.getByTestId("important-files")).toContainText("etl_job.py");
    await expect(page.getByTestId("concepts")).toBeVisible();
    console.log("  architecture rendered with components, flow, files and concepts");

    // -- Spark Playground ---------------------------------------------------
    await page.getByTestId("tab-Playground").click();
    await expect(page).toHaveURL(/\/playground$/);

    // Input data: the real dataset, with the hash the run will record.
    await expect(page.getByText("Dataset sha256")).toBeVisible();
    await expect(page.getByTestId("dataset-sample")).toContainText("engineering");

    // Pipeline: RepoLab's additions are disclosed, not hidden.
    await expect(page.getByText("the repository's own step")).toBeVisible();
    await expect(page.getByText("added by RepoLab").first()).toBeVisible();

    // Configuration, then execution.
    await page.getByTestId("partitions-200").click();
    await page.getByTestId("run-pipeline").click();

    await expect(page.getByTestId("metrics")).toBeVisible({ timeout: RUN_TIMEOUT });
    console.log("  pipeline ran and produced metrics");

    // Metrics: the finding the whole demo turns on, measured from the event log.
    await expect(page.getByTestId("proportion-bar")).toContainText("/ 200");
    await expect(page.getByText("Event log sha256")).toBeVisible();
    await expect(page.getByText("Structural · repeats exactly across identical runs")).toBeVisible();
    await expect(page.getByText("Timing · varies between runs")).toBeVisible();

    // -- Ask RepoLab --------------------------------------------------------
    await page.getByTestId("tab-Ask RepoLab").click();
    await expect(page).toHaveURL(/\/ask$/);

    await page.getByTestId("question").fill(QUESTION);
    await page.getByTestId("ask").click();

    const approval = page.getByTestId("approval-card");
    await expect(approval).toBeVisible({ timeout: INVESTIGATION_TIMEOUT });
    console.log("  experiment proposed and awaiting approval");

    // The plan and its cost, before the decision.
    await expect(approval).toContainText("RepoLab wants to run an experiment");
    await expect(page.getByTestId("plan-values")).toContainText("baseline");
    await expect(page.getByTestId("plan-cost")).toContainText("Spark jobs");
    await expect(page.getByTestId("plan-cost")).toContainText("min");
    await expect(approval).toContainText("Nothing runs until you choose.");

    // Both choices are offered.
    await expect(page.getByTestId("reject-experiment")).toBeEnabled();

    await page.getByTestId("approve-experiment").click();

    // -- Experiment Results -------------------------------------------------
    await expect(page).toHaveURL(/\/ask\/[0-9a-f-]{36}$/, { timeout: 60_000 });
    console.log(`  results: ${page.url()}`);

    // Charts must fill in *as variants complete*, not only at the end.
    await expect(page.getByTestId("live-charts").or(page.getByTestId("result-charts"))).toBeVisible(
      { timeout: EXPERIMENT_TIMEOUT },
    );
    console.log("  charts began filling in from partial results");

    await expect(page.getByTestId("experiment-status")).toHaveAttribute(
      "data-status",
      "completed",
      { timeout: EXPERIMENT_TIMEOUT },
    );
    console.log("  experiment completed");

    // The computed statistics replace the live view.
    await expect(page.getByTestId("result-charts")).toBeVisible();
    await expect(page.getByText("empty partitions").first()).toBeVisible();

    // The explanation, with every claim labelled by its kind.
    await expect(page.getByTestId("answer")).toBeVisible({ timeout: 3 * 60 * 1000 });
    await expect(page.getByTestId("claims-measured")).toBeVisible();
    console.log("  explanation rendered with labelled claims");

    // Evidence: measurements, code, logs, official docs.
    const evidence = page.getByTestId("evidence-panel");
    await expect(evidence).toBeVisible();
    for (const tab of ["measurements", "code", "logs", "docs"] as const) {
      await page.getByTestId(`evidence-tab-${tab}`).click();
      await expect(evidence).toBeVisible();
    }
    console.log("  evidence panel shows measurements, code, logs and docs");

    // Reproducibility: the raw source every figure came from.
    await expect(page.getByText("Added by RepoLab, not present in the repository")).toBeVisible();
    await expect(page.getByText("Dataset hash")).toBeVisible();
  });
});

test.describe("the approval gate", () => {
  test("declining runs nothing", async ({ page }) => {
    // Reuses the repository the first test already investigated, so this does
    // not pay for a second clone.
    const jobId = await latestJobId(page);
    test.skip(!jobId, "no investigated repository yet; run the main spec first");

    await page.goto(`/r/${jobId}/ask`);
    await page.getByTestId("question").fill("Does the partition count affect spill?");
    await page.getByTestId("ask").click();

    const approval = page.getByTestId("approval-card");
    await expect(approval).toBeVisible({ timeout: INVESTIGATION_TIMEOUT });

    await page.getByTestId("reject-experiment").click();

    await expect(page.getByText(/Declined — nothing ran/)).toBeVisible();
    // And the gate stays closed: no approval control remains.
    await expect(page.getByTestId("approve-experiment")).toHaveCount(0);
    console.log("  declining ran nothing, and the gate stayed closed");
  });
});

/** The most recent completed investigation, read through the API the UI uses. */
async function latestJobId(page: Page): Promise<string | null> {
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const response = await page.request.get(
    `${api}/experiments?repo_url=${encodeURIComponent(DEMO_REPO)}`,
  );
  if (!response.ok()) return null;
  const experiments = (await response.json()) as { job_id: string | null }[];
  return experiments.find((entry) => entry.job_id)?.job_id ?? null;
}
