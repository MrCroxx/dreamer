import { test, expect } from "@playwright/test";
import type { Job } from "../src/types";

test("preview colors preserve the canvas, save as versions, and survive refresh and mobile navigation", async ({
  page,
}) => {
  const root: Job = {
    id: "a".repeat(32),
    name: "Small tray",
    prompt: "A small tray",
    parent_id: null,
    demo: false,
    state: "preview",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    error: null,
    events: [],
    slice_sha256: null,
    model_sha256: "c".repeat(64),
    geometry: {
      dimensions: [40, 30, 10],
      volume_mm3: 12000,
      solids: 1,
      fits_build_volume: true,
    },
    plan: {
      name: "Small tray",
      description: "A simple tray.",
      assumptions: [],
      operations: [],
      color: "#dd7652",
    },
  };
  const jobs: Job[] = [root];
  let saves = 0;
  const mesh =
    "solid part\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 30 0 0\nvertex 0 30 10\nendloop\nendfacet\nendsolid part";
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/status")
      return route.fulfill({
        json: {
          auth_required: false,
          llm_ready: true,
          model: "test-model",
          slicer_ready: true,
          slicing_presets: [],
          print_ready: false,
          printer: "Test printer",
          build_volume: [180, 180, 180],
          filament_slot: "A1",
          bed_type: "Textured PEI Plate",
        },
      });
    if (path === "/api/jobs")
      return route.fulfill({ json: [...jobs].reverse() });
    const id = path.split("/")[3];
    const job = jobs.find((j) => j.id === id);
    if (!job) return route.fulfill({ status: 404 });
    if (path.endsWith("/appearance")) {
      saves++;
      const color = route.request().postDataJSON().color;
      const child: Job = {
        ...root,
        id: "b".repeat(32),
        parent_id: root.id,
        prompt: `Change the preview color to ${color}.`,
        plan: {
          ...root.plan!,
          color,
          description: "Updated the preview color. Geometry is unchanged.",
        },
      };
      jobs.push(child);
      return route.fulfill({ status: 202, json: child });
    }
    if (path.endsWith("/conversation"))
      return route.fulfill({
        json: jobs.slice(0, jobs.indexOf(job) + 1).map((j) => ({
          ...j,
          summary: j.plan?.description,
          color: j.plan?.color,
        })),
      });
    if (path.endsWith("model.stl"))
      return route.fulfill({ contentType: "model/stl", body: mesh });
    return route.fulfill({ json: job });
  });
  await page.goto(`/?job=${root.id}`);
  await expect(page.getByText("Solid model", { exact: true })).toBeVisible();
  const canvas = page.locator("canvas");
  await canvas.evaluate((element) => (element.dataset.identity = "stable"));
  await page.getByRole("button", { name: "Toggle wireframe" }).click();
  await page.getByRole("button", { name: "Preview in Blue" }).click();
  await expect(page.locator(".viewer-canvas")).toHaveAttribute(
    "data-color",
    "#2563eb",
  );
  await expect(canvas).toHaveAttribute("data-identity", "stable");
  await expect(
    page.getByRole("button", { name: "Toggle wireframe" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(saves).toBe(0);
  await page.getByRole("button", { name: "Discard color change" }).click();
  await expect(page.locator(".viewer-canvas")).toHaveAttribute(
    "data-color",
    "#dd7652",
  );
  const input = page.getByRole("textbox", { name: "Describe your model" });
  await input.fill("Keep this draft while comparing");
  await page.getByRole("button", { name: "Preview in Blue" }).click();
  await page.getByRole("button", { name: "Save color" }).click();
  await expect(
    page.getByRole("button", { name: "View version 2" }),
  ).toBeVisible();
  expect(saves).toBe(1);
  await expect(input).toHaveValue("Keep this draft while comparing");
  await page.reload();
  await expect(page.locator(".viewer-canvas")).toHaveAttribute(
    "data-color",
    "#2563eb",
  );
  await expect(input).toHaveValue("Keep this draft while comparing");
  await page.getByRole("button", { name: "View version 1" }).click();
  await expect(page.locator(".viewer-canvas")).toHaveAttribute(
    "data-color",
    "#dd7652",
  );
  await expect(page.getByText("Editing an earlier version")).toBeVisible();
  await page.getByRole("button", { name: "Back to latest" }).click();
  await expect(page.locator(".viewer-canvas")).toHaveAttribute(
    "data-color",
    "#2563eb",
  );
  const bounds = await input.boundingBox();
  expect(bounds!.y + bounds!.height).toBeLessThan(1100);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(input).toBeVisible();
  await page.getByRole("tab", { name: "3D preview" }).click();
  await expect(input).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Preview in Blue" }),
  ).toHaveAttribute("aria-pressed", "true");
  const recent = page.getByRole("combobox", {
    name: "Select a recent creation",
  });
  expect((await recent.boundingBox())!.width).toBeGreaterThan(150);
  await page.getByRole("button", { name: "Ask for a change" }).click();
  await expect(input).toBeVisible();
  await expect(input).toHaveValue("Keep this draft while comparing");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/studio-mobile-chat.png" });
  await page.setViewportSize({ width: 390, height: 568 });
  const sendBounds = await page
    .getByRole("button", { name: "Send changes" })
    .boundingBox();
  expect(sendBounds!.y + sendBounds!.height).toBeLessThanOrEqual(568);
  await expect(input).toBeVisible();
});
