import { test, expect } from "@playwright/test";

test("build a real demo, preview, download, inspect, and prepare a revision", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "New creation", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Try demo", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "Build demo model" }).click();
  await expect(page.getByText("Solid model", { exact: true })).toBeVisible({
    timeout: 40_000,
  });
  await expect(
    page.locator(".spec-grid").getByText("100 × 65 × 30", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Toggle wireframe" }).click();
  await expect(
    page.getByRole("button", { name: "Toggle wireframe" }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Toggle wireframe" }).click();
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download STEP", exact: true })
    .click();
  expect((await download).suggestedFilename()).toBe("model.step");
  await page.locator(".model-inspector > summary").click();
  await page.getByRole("button", { name: "CAD plan", exact: true }).click();
  await expect(page.locator("pre")).toContainText("operations");
  await page.getByRole("button", { name: "Design notes", exact: true }).click();
  await page.screenshot({
    path: "test-results/workbench-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Ask for a change" }).click();
  await expect(
    page.getByRole("textbox", { name: "Describe your model" }),
  ).toBeFocused();
  await expect(
    page.getByRole("textbox", { name: "Describe your model" }),
  ).toHaveValue("");
  await page.getByRole("button", { name: "Connections & settings" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  expect(errors).toEqual([]);
});

test("mobile workbench builds a curved vase and keeps the primary flow usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator(".template-card")
    .filter({ hasText: "Contoured vase" })
    .getByRole("button", { name: "Try demo", exact: true })
    .click();
  await page.getByRole("button", { name: "Build demo model" }).click();
  await page.getByRole("tab", { name: "3D preview" }).click();
  await expect(page.getByText("Solid model", { exact: true })).toBeVisible({
    timeout: 40_000,
  });
  await page.locator(".model-inspector > summary").click();
  await page.getByRole("button", { name: "CAD plan", exact: true }).click();
  await expect(page.locator("pre")).toContainText('"op": "loft"');
  await expect(page.locator("pre")).toContainText('"op": "shell"');
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/workbench-mobile.png",
    fullPage: true,
  });
});
