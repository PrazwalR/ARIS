import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/** The measured data the site must render, read from the file it serves. */
const metrics = JSON.parse(
  readFileSync(new URL("../public/data/metrics.json", import.meta.url), "utf8"),
);
const fixed = (v: number) => v.toFixed(3);
const PAGES = ["/", "/live", "/model", "/security", "/evidence"] as const;

function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().includes("fonts.g")) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

test.beforeEach(async ({ page }) => {
  expect((await page.request.post("/demo/reset")).ok()).toBeTruthy();
});

test.describe("navigation", () => {
  test("every page is reachable from the nav, with the current page marked", async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Main" });
    for (const [label, path] of [
      ["Live demo", "/live"],
      ["Model", "/model"],
      ["Security", "/security"],
      ["Evidence", "/evidence"],
      ["Overview", "/"],
    ] as const) {
      await nav.getByRole("link", { name: label }).click();
      await expect(page).toHaveURL(new RegExp(`${path === "/" ? "/$" : path}$`));
      await expect(nav.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
      await expect(page.locator("main h1")).toBeFocused();
    }
    expect(errors).toEqual([]);
  });

  test("deep links load directly and back/forward work", async ({ page }) => {
    await page.goto("/security");
    await expect(page.getByRole("heading", { level: 1, name: "Security" })).toBeVisible();
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Model" }).click();
    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: "Security" })).toBeVisible();
  });

  test("an unknown address shows a not-found page", async ({ page }) => {
    await page.goto("/no-such-page");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
  });

  test("the logo is the project wordmark and links home", async ({ page }) => {
    await page.goto("/model");
    const brand = page.locator(".brand");
    await expect(brand.getByRole("img", { name: "ARIS" })).toBeVisible();
    const mask = await brand.locator(".logo").evaluate((el) => getComputedStyle(el).maskImage);
    expect(mask).toContain("favicon.svg");
    await brand.click();
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe("live demo", () => {
  test("the scenario: flag, block, forged retraction rejected", async ({ page }) => {
    const errors = trackErrors(page);
    await page.goto("/live");
    await expect(page.getByTestId("server-status")).toContainText("online");
    await page.getByRole("button", { name: "Run scenario" }).click();
    await expect(page.getByTestId("forge-result")).toContainText("Rejected: signature invalid", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("story-decision")).toContainText("Blocked");
    await expect(page.getByTestId("flag-result")).toContainText("BANK-B, score 92");
    await expect(page.getByTestId("forge-result")).toContainText("remains 92");
    // The customer-facing message never carries the score.
    await expect(page.getByTestId("story-decision")).not.toContainText("92");
    expect(errors).toEqual([]);
  });

  test("before Bank B flags it, the same transfer is allowed", async ({ page }) => {
    await page.goto("/live");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByTestId("story-decision")).toContainText("Allowed");
    await page.getByRole("button", { name: "Publish" }).click();
    await expect(page.getByTestId("flag-result")).toContainText("BANK-B");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByTestId("story-decision")).toContainText("Blocked");
  });

  test("network view replaces the account number with the server-derived ID", async ({ page }) => {
    await page.goto("/live");
    await expect(page.getByTestId("account-plain")).toContainText("ACC-999");
    await page.getByTestId("network-view").check();
    const chip = page.getByRole("button", { name: /^risk_id/ }).first();
    await expect(chip).toBeVisible();
    const { risk_id } = await (
      await page.request.post("/demo/risk-id", {
        data: { receiver_ifsc: "HDFC0001234", receiver_account: "ACC-999" },
      })
    ).json();
    await chip.click();
    await expect(chip).toContainText(risk_id);
  });

  test("sandbox validates input, then allows a clean account and blocks a flagged one", async ({
    page,
  }) => {
    await page.goto("/live");
    const box = page.getByTestId("sandbox");
    await box.getByLabel("Receiver IFSC").fill("HDFC1001234");
    await expect(box.getByText(/Format: four letters/)).toBeVisible();
    await expect(box.getByRole("button", { name: "Send transfer" })).toBeDisabled();
    await box.getByLabel("Receiver IFSC").fill("HDFC0001234");

    const account = `ACC-E2E-${Date.now()}`;
    await box.getByLabel("Receiver account").fill(account);
    await box.getByRole("button", { name: "Send transfer" }).click();
    await expect(box.getByTestId("console-result")).toContainText("Allow");
    await box.getByRole("button", { name: "Flag as Bank B" }).click();
    await expect(box.getByTestId("console-result")).toContainText("Flagged by BANK-B");
    await box.getByRole("button", { name: "Send transfer" }).click();
    await expect(box.getByTestId("console-result")).toContainText("Block");

    const log = page.getByTestId("feed");
    await expect(log.locator("tbody tr")).toHaveCount(2);
    await expect(log).not.toContainText(account);
  });

  test("investigator access: wrong key refused, right key shows evidence", async ({ page }) => {
    await page.goto("/live");
    await page.getByRole("button", { name: "Publish" }).click();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByTestId("story-decision")).toContainText("Blocked");

    await expect(page.getByLabel("Investigator")).toHaveValue("reviewer");
    await expect(page.getByLabel("Audit reference")).not.toHaveValue("");
    await page.getByLabel("Key").fill("wrong-key");
    await page.getByRole("button", { name: "Open record" }).click();
    await expect(page.getByTestId("audit-result")).toContainText("Credentials rejected (401)");
    await page.getByLabel("Key").fill("e2e-key");
    await page.getByRole("button", { name: "Open record" }).click();
    await expect(page.getByTestId("audit-score")).toHaveText("92");
    await expect(page.getByTestId("audit-policy")).toHaveText("step-up ≥ 50 · block ≥ 85");
  });

  test("with the demo server unreachable, nothing is faked", async ({ page }) => {
    await page.route(/\/(api|demo)\//, (route) => route.abort());
    await page.goto("/live");
    await expect(page.getByTestId("offline-notice")).toBeVisible();
    await expect(page.getByTestId("server-status")).toContainText("offline");
    await expect(page.getByRole("button", { name: "Run scenario" })).toBeDisabled();
    await expect(page.getByTestId("story-decision")).toContainText("Not sent");
  });
});

test.describe("measured results", () => {
  test("model page shows exactly what metrics.json holds", async ({ page }) => {
    await page.goto("/model");
    const ulb = metrics.datasets.ulb;
    const figs = page.getByTestId("metric-tiles");
    await expect(figs).toContainText(fixed(ulb.global.metrics.auc));
    await expect(figs).toContainText(fixed(ulb.global.metrics.pr_auc));
    await expect(figs).toContainText(fixed(ulb.mean_local.auc));
    const table = page.getByTestId("bank-table");
    await expect(table.locator("tbody tr")).toHaveCount(ulb.local.length + 2);
    for (const bank of ulb.local) {
      await expect(table.locator("tbody tr", { hasText: bank.bank_id })).toContainText(
        fixed(bank.metrics.auc),
      );
    }
    await expect(page.locator(".ar-chart-line")).toHaveCount(ulb.local.length + 1);
    await expect(page.getByTestId("rounds-table").locator("tbody tr")).toHaveCount(
      metrics.datasets.synthetic.rounds.length,
    );
    await expect(page.getByTestId("robustness")).toContainText(fixed(metrics.robustness.krum_auc));

    await page.getByRole("tab", { name: "Synthetic" }).click();
    await expect(figs).toContainText(fixed(metrics.datasets.synthetic.global.metrics.auc));
  });

  test("overview and evidence report the measured run", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("headline-figures")).toContainText(
      fixed(metrics.datasets.ulb.global.metrics.auc),
    );
    await page.goto("/evidence");
    await expect(page.getByTestId("badge-tests")).toContainText(`${metrics.tests.passed} passed`);
    await expect(page.getByTestId("provenance")).toContainText(metrics.commit);
  });
});

test.describe("quality", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`no serious accessibility violations on any page (${theme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      for (const path of PAGES) {
        await page.goto(path);
        await expect(page.locator("main h1")).toBeVisible();
        if (path === "/model") await expect(page.getByTestId("metric-tiles")).toBeVisible();
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
        const serious = results.violations.filter((v) =>
          ["serious", "critical"].includes(v.impact ?? ""),
        );
        expect(
          serious.map((v) => `${path} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
        ).toEqual([]);
      }
    });
  }

  test("375px: no horizontal scroll on any page; the menu navigates", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const path of PAGES) {
      await page.goto(path);
      await expect(page.locator("main h1")).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
    await page.getByRole("button", { name: "Menu" }).click();
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Security" }).click();
    await expect(page).toHaveURL(/\/security$/);
    await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
  });

  test("theme choice is applied and survives a reload", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await page.getByTestId("theme-toggle").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("keyboard: the skip link moves focus to the content", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#main$/);
  });
});
