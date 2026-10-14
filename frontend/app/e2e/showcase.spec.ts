import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/** The measured data the page must render, read from the same file it serves. */
const metrics = JSON.parse(
  readFileSync(new URL("../public/data/metrics.json", import.meta.url), "utf8"),
);
const fixed = (v: number) => v.toFixed(3);

async function reset(page: Page) {
  const resp = await page.request.post("/demo/reset");
  expect(resp.ok()).toBeTruthy();
}

function trackConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    // Google Fonts may be unreachable in a sandboxed run; that is not an app error.
    if (m.type() === "error" && !m.text().includes("fonts.g")) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

test.beforeEach(async ({ page }) => {
  await reset(page);
});

test.describe("hero story, live", () => {
  test("play the whole story: flag, block, forged retraction rejected", async ({ page }) => {
    const errors = trackConsoleErrors(page);
    await page.goto("/");
    const card = page.locator(".story-card");
    await card.getByRole("button", { name: "Play the whole story" }).click();

    await expect(card.getByTestId("forge-result")).toContainText("Rejected: signature invalid", {
      timeout: 15_000,
    });
    await expect(card.getByTestId("story-decision")).toContainText("Blocked");
    await expect(card.getByTestId("story-decision")).toContainText("This transfer looks risky");
    await expect(card.getByTestId("flag-result")).toContainText("Signed by BANK-B, score 92");
    await expect(card.getByTestId("forge-result")).toContainText("Network score still 92");
    // The customer-facing message never carries the score.
    await expect(card.getByTestId("story-decision")).not.toContainText("92");
    expect(errors).toEqual([]);
  });

  test("before Bank B flags it, the same transfer is allowed", async ({ page }) => {
    await page.goto("/");
    const card = page.locator(".story-card");
    await card.getByRole("button", { name: "Send transfer" }).click();
    await expect(card.getByTestId("story-decision")).toContainText("Allowed");
    await card.getByRole("button", { name: "Publish flag" }).click();
    await expect(card.getByTestId("flag-result")).toBeVisible();
    await card.getByRole("button", { name: "Send transfer" }).click();
    await expect(card.getByTestId("story-decision")).toContainText("Blocked");
  });

  test("privacy lens swaps the account number for the risk_id the network sees", async ({
    page,
  }) => {
    await page.goto("/");
    const card = page.locator(".story-card");
    await expect(card.getByTestId("account-plain")).toContainText("ACC-999");
    await card.getByRole("button", { name: /Privacy lens/ }).click();
    await expect(card.getByRole("button", { name: /^risk_id/ })).toBeVisible();
    await expect(card.locator(".story-to")).not.toContainText("ACC-999");
    // The id shown is the one the server derives.
    const derived = await page.request.post("/demo/risk-id", {
      data: { receiver_ifsc: "HDFC0001234", receiver_account: "ACC-999" },
    });
    const { risk_id } = await derived.json();
    await card.getByRole("button", { name: /^risk_id/ }).click();
    await expect(card.locator(".story-to")).toContainText(risk_id);
  });
});

test.describe("try it yourself", () => {
  test("invalid input is caught before it reaches the server", async ({ page }) => {
    await page.goto("/#console");
    const form = page.locator("#console .console-card").first();
    await form.getByLabel("Receiver IFSC").fill("HDFC1001234");
    await expect(form.getByText(/Four letters, a zero/)).toBeVisible();
    await expect(form.getByRole("button", { name: /^Send/ })).toBeDisabled();
    await form.getByLabel("Receiver IFSC").fill("HDFC0001234");
    await form.getByLabel("Amount (₹)").fill("12.345");
    await expect(form.getByText(/at most two decimal places/)).toBeVisible();
    await expect(form.getByRole("button", { name: /^Send/ })).toBeDisabled();
  });

  test("a clean account is allowed; after Bank B flags it, it is blocked", async ({ page }) => {
    await page.goto("/#console");
    const form = page.locator("#console .console-card").first();
    const account = `ACC-E2E-${Date.now()}`;
    await form.getByLabel("Receiver account").fill(account);
    await form.getByLabel("Amount (₹)").fill("2,500");
    await form.getByRole("button", { name: "Send ₹2,500" }).click();
    await expect(form.getByTestId("console-result")).toContainText("Allow");

    await form.getByRole("button", { name: "Flag as Bank B" }).click();
    await expect(form.getByTestId("console-result")).toContainText("Bank B published a flag");
    await form.getByRole("button", { name: "Send ₹2,500" }).click();
    await expect(form.getByTestId("console-result")).toContainText("Block");
  });

  test("the decision log never shows an account number", async ({ page }) => {
    await page.goto("/#console");
    const form = page.locator("#console .console-card").first();
    const account = `ACC-LOG-${Date.now()}`;
    await form.getByLabel("Receiver account").fill(account);
    await form.getByRole("button", { name: /^Send/ }).click();
    const feed = page.getByTestId("feed");
    await expect(feed.locator(".feed-row")).toHaveCount(1);
    await expect(feed).not.toContainText(account);
    await expect(feed).toContainText("risk_id");
  });

  test("analyst view: wrong key refused, right key shows evidence and thresholds", async ({
    page,
  }) => {
    await page.goto("/");
    const card = page.locator(".story-card");
    await card.getByRole("button", { name: "Publish flag" }).click();
    await card.getByRole("button", { name: "Send transfer" }).click();
    await expect(card.getByTestId("story-decision")).toContainText("Blocked");

    const analyst = page.locator(".analyst-card");
    await expect(analyst.getByLabel("Investigator")).toHaveValue("reviewer");
    await expect(analyst.getByLabel("Audit reference")).not.toHaveValue("");
    await analyst.getByLabel("Key").fill("wrong-key");
    await analyst.getByRole("button", { name: "Open record" }).click();
    await expect(analyst.getByRole("alert")).toContainText("credentials rejected (401)");

    await analyst.getByLabel("Key").fill("e2e-key");
    await analyst.getByRole("button", { name: "Open record" }).click();
    await expect(analyst.getByTestId("audit-score")).toHaveText("92");
    // The thresholds the page explains are the ones the server applied.
    await expect(analyst.getByTestId("audit-policy")).toHaveText("step-up ≥ 50 · block ≥ 85");
  });
});

test.describe("measured results", () => {
  test("metric tiles and the bank table show exactly what metrics.json holds", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" }); // final values, no count-up
    await page.goto("/#metrics");
    const ulb = metrics.datasets.ulb;
    const tiles = page.getByTestId("metric-tiles");
    await expect(tiles).toContainText(fixed(ulb.global.metrics.auc));
    await expect(tiles).toContainText(fixed(ulb.global.metrics.pr_auc));
    await expect(tiles).toContainText(fixed(ulb.mean_local.auc));

    const table = page.getByTestId("bank-table");
    await expect(table.locator("tbody tr")).toHaveCount(ulb.local.length + 2);
    for (const bank of ulb.local) {
      const row = table.locator("tbody tr", { hasText: bank.bank_id });
      await expect(row).toContainText(fixed(bank.metrics.auc));
    }
    // One ROC line per local bank plus the global model.
    await expect(page.locator("#metrics .ar-chart-line")).toHaveCount(ulb.local.length + 1);

    await page.getByRole("tab", { name: metrics.datasets.synthetic.label }).click();
    await expect(tiles).toContainText(fixed(metrics.datasets.synthetic.global.metrics.auc));
  });

  test("federated toggles reveal the measured privacy and robustness results", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/#federated");
    const dp = page.getByRole("button", { name: /DP-SGD/ });
    await dp.click();
    await expect(dp).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("privacy-table").locator("tbody tr")).toHaveCount(
      metrics.privacy.rows.filter((r: { epsilon: number | null }) => r.epsilon !== null).length,
    );
    await page.getByRole("button", { name: /Krum/ }).click();
    await expect(page.getByTestId("robustness-tiles")).toContainText(
      fixed(metrics.robustness.krum_auc),
    );
    await expect(page.getByTestId("round-counter")).toContainText(
      `Round ${metrics.datasets.synthetic.rounds.length} / ${metrics.datasets.synthetic.rounds.length}`,
    );
  });

  test("proof strip reports the measured test run", async ({ page }) => {
    await page.goto("/#proof");
    await expect(page.getByTestId("badge-tests")).toContainText(
      `${metrics.tests.passed.toLocaleString("en-IN")} tests passed`,
    );
    await expect(page.getByTestId("provenance")).toContainText(metrics.commit);
  });

  test("security: eight cards, each operable from the keyboard", async ({ page }) => {
    await page.goto("/#security");
    const cards = page.getByTestId("security-grid").getByRole("button");
    await expect(cards).toHaveCount(8);
    await cards.first().focus();
    await page.keyboard.press("Enter");
    await expect(cards.first()).toHaveAttribute("aria-pressed", "true");
  });
});

test.describe("quality", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`no serious accessibility violations (${theme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.goto("/");
      await expect(page.getByTestId("metric-tiles")).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      const serious = results.violations.filter((v) =>
        ["serious", "critical"].includes(v.impact ?? ""),
      );
      expect(
        serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
      ).toEqual([]);
    });
  }

  test("375px: no horizontal scroll, menu opens and navigates", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/");
    await expect(page.getByTestId("metric-tiles")).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("navigation", { name: "Sections" }).getByText("Security").click();
    await expect(page).toHaveURL(/#security$/);
  });

  test("theme choice is applied and survives a reload", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await page.getByTestId("theme-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("with the demo server unreachable, nothing is faked", async ({ page }) => {
    await page.route(/\/(api|demo)\//, (route) => route.abort());
    await page.goto("/");
    await expect(page.getByTestId("offline-notice").first()).toBeVisible();
    await expect(
      page.locator(".story-card").getByRole("button", { name: "Play the whole story" }),
    ).toBeDisabled();
    await expect(page.getByTestId("story-decision")).toContainText("No decision yet");
    // Measured results are static data and still load.
    await expect(page.getByTestId("metric-tiles")).toBeVisible();
  });

  test("keyboard: skip link goes straight to the content", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main")).toBeFocused();
  });
});
