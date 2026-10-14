import { test, expect } from "@playwright/test";
import type { Job, Status } from "../src/types";

test("drafts update in place, survive orbit controls, and become a reviewable model", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const id = "a".repeat(32);
  const status: Status = {
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
  };
  const job: Job = {
    id,
    name: "Streamed part",
    prompt: "A small part",
    parent_id: null,
    demo: false,
    state: "generating",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    error: null,
    events: [],
    plan: null,
    geometry: null,
    model_sha256: null,
    slice_sha256: null,
  };
  let step = 1;
  let attempt = "b".repeat(32);
  let finalRequested = false;
  let releaseFinal: (() => void) | undefined;
  const finalReady = new Promise<void>((resolve) => {
    releaseFinal = resolve;
  });
  const mesh = `solid test
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 30 0 0
vertex 0 30 10
endloop
endfacet
endsolid test`;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/status") return route.fulfill({ json: status });
    if (path === "/api/jobs") return route.fulfill({ json: [job] });
    if (path.endsWith("/conversation"))
      return route.fulfill({ json: [{ ...job, summary: null }] });
    if (path === `/api/jobs/${id}/preview`)
      return route.fulfill({
        json: {
          active: job.state === "generating",
          preview: step
            ? { revision: `${attempt}-${step}`, operations: step }
            : null,
        },
      });
    if (path === `/api/jobs/${id}/files/model.stl`) {
      finalRequested = true;
      await finalReady;
      return route.fulfill({ contentType: "model/stl", body: mesh });
    }
    if (path.startsWith(`/api/jobs/${id}/preview/`)) {
      return route.fulfill({ contentType: "model/stl", body: mesh });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto(`/?job=${id}`);
  await expect(
    page.getByText("Live draft · Step 1", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve & slice" }),
  ).toBeDisabled();
  const canvas = page.locator("canvas");
  await canvas.evaluate((element) => {
    element.dataset.identity = "original";
  });
  await page.getByRole("button", { name: "Toggle wireframe" }).click();
  const bounds = await canvas.boundingBox();
  if (bounds) {
    await page.mouse.move(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      bounds.x + bounds.width / 2 + 50,
      bounds.y + bounds.height / 2 + 30,
    );
    await page.mouse.up();
  }
  step = 3;
  await expect(
    page.getByText("Live draft · Step 3", { exact: true }),
  ).toBeVisible();
  await expect(canvas).toHaveAttribute("data-identity", "original");
  await expect(
    page.getByRole("button", { name: "Toggle wireframe" }),
  ).toHaveAttribute("aria-pressed", "true");

  // A failed attempt must discard its draft before the repair attempt starts.
  step = 0;
  await expect(
    page.getByText("Creating your model", { exact: true }),
  ).toBeVisible();
  attempt = "c".repeat(32);
  step = 1;
  await expect(
    page.getByText("Live draft · Step 1", { exact: true }),
  ).toBeVisible();
  job.state = "preview";
  job.model_sha256 = "d".repeat(64);
  job.geometry = {
    dimensions: [30, 30, 10],
    volume_mm3: 900,
    solids: 1,
    fits_build_volume: true,
  };
  await expect.poll(() => finalRequested).toBe(true);
  await expect(
    page.getByRole("button", { name: "Approve & slice" }),
  ).toBeDisabled();
  await expect(
    page.getByText("Live draft · Step 1", { exact: true }),
  ).toBeVisible();
  releaseFinal!();
  await expect(page.getByText("Solid model", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve & slice" }),
  ).toBeEnabled();
  await expect(
    page.getByText("Live draft · Final geometry is still being validated"),
  ).toHaveCount(0);
  await expect(canvas).toHaveAttribute("data-identity", "original");
  await expect(
    page.getByRole("button", { name: "Toggle wireframe" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(errors).toEqual([]);
});
