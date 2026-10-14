import { test, expect } from "@playwright/test";
import type { Job } from "../src/types";

test("continue revisions, restore a conversation, and branch from an earlier model", async ({
  page,
}) => {
  const jobs = new Map<string, Job>();
  const requests: { prompt: string; parent_id: string | null }[] = [];
  function create(id: string, prompt: string, parent: string | null): Job {
    const job: Job = {
      id,
      name: "Test part",
      prompt,
      parent_id: parent,
      demo: false,
      state: "queued",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      error: null,
      events: [],
      plan: null,
      geometry: null,
      model_sha256: null,
      slice_sha256: null,
    };
    jobs.set(id, job);
    return job;
  }
  function complete(job: Job, description: string) {
    Object.assign(job, {
      state: "preview",
      updated_at: new Date().toISOString(),
      model_sha256: job.id.repeat(2),
      geometry: {
        dimensions: [40, 30, 10],
        volume_mm3: 12000,
        solids: 1,
        fits_build_volume: true,
      },
      plan: { name: job.name, description, assumptions: [], operations: [] },
    });
  }
  const root = create("1".repeat(32), "Make a small tray", null);
  complete(root, "A small tray with thin walls.");
  const mesh =
    "solid part\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 30 0 0\nvertex 0 30 10\nendloop\nendfacet\nendsolid part";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/status")
      return route.fulfill({
        json: {
          auth_required: false,
          llm_ready: true,
          model: "test-model",
          slicer_ready: false,
          slicing_presets: [],
          print_ready: false,
          printer: "Test printer",
          build_volume: [180, 180, 180],
          filament_slot: "A1",
          bed_type: "Textured PEI Plate",
        },
      });
    if (path === "/api/jobs" && route.request().method() === "POST") {
      const request = route.request().postDataJSON();
      requests.push(request);
      const id = String(requests.length + 1).repeat(32);
      return route.fulfill({
        status: 202,
        json: create(id, request.prompt, request.parent_id),
      });
    }
    // Older versions are deliberately absent from the recent jobs list.
    if (path === "/api/jobs")
      return route.fulfill({ json: [Array.from(jobs.values()).at(-1)] });
    const id = path.split("/")[3];
    const job = jobs.get(id);
    if (!job) return route.fulfill({ status: 404 });
    if (path.endsWith("/conversation")) {
      const turns = [];
      let current: Job | undefined = job;
      while (current) {
        turns.unshift({
          ...current,
          summary: current.plan?.description || null,
        });
        current = current.parent_id ? jobs.get(current.parent_id) : undefined;
      }
      return route.fulfill({ json: turns });
    }
    if (path.endsWith("/preview"))
      return route.fulfill({ json: { active: true, preview: null } });
    if (path.endsWith("/model.stl"))
      return route.fulfill({ contentType: "model/stl", body: mesh });
    return route.fulfill({ json: job });
  });
  await page.goto(`/?job=${root.id}`);
  const conversation = page.getByRole("region", {
    name: "Design conversation",
  });
  const input = page.getByRole("textbox", { name: "Describe your model" });
  const send = page.getByRole("button", { name: "Send changes" });
  await expect(conversation).toContainText("Make a small tray");
  await input.fill("Make the walls thicker");
  await send.click();
  await expect(input).toHaveValue("");
  expect(requests[0].parent_id).toBe(root.id);
  await input.fill("Add a handle");
  await expect(send).toBeDisabled();
  await input.press("Control+Enter");
  expect(requests).toHaveLength(1);
  const first = jobs.get("2".repeat(32))!;
  complete(first, "The walls are now thicker.");
  await expect(send).toBeEnabled();
  await expect(conversation).toContainText("The walls are now thicker.");
  await send.click();
  await expect(input).toHaveValue("");
  expect(requests[1].parent_id).toBe(first.id);
  const second = jobs.get("3".repeat(32))!;
  complete(second, "A handle has been added.");
  await expect(
    conversation.getByRole("button", { name: "View version 3" }),
  ).toBeVisible();
  await input.fill("Draft while comparing versions");
  await conversation.getByRole("button", { name: "View version 1" }).click();
  await expect(input).toHaveValue("Draft while comparing versions");
  await expect(
    conversation.getByRole("button", { name: "View version 1" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    conversation.getByRole("button", { name: "View version 3" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    conversation.getByRole("button", { name: "View version 1" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(conversation).toContainText("A handle has been added.");
  await input.fill("Make the original tray shorter instead");
  await send.click();
  await expect(input).toHaveValue("");
  expect(requests[2].parent_id).toBe(root.id);
  await expect(conversation).toContainText(
    "Make the original tray shorter instead",
  );
  await expect(conversation).not.toContainText("Add a handle");
  await page.getByRole("button", { name: "New creation" }).click();
  await expect(conversation).toHaveCount(0);
  await input.fill("Make a separate bracket");
  await page
    .getByRole("button", { name: "Generate model", exact: true })
    .click();
  await expect(input).toHaveValue("");
  expect(requests[3].parent_id).toBeNull();
  expect(errors).toEqual([]);
});
