import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("optional output streams text safely, stops polling when closed, and replays after refresh", async ({
  page,
}) => {
  const id = "e".repeat(32);
  const job = {
    id,
    name: "Output test",
    prompt: "A tray",
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
  const events: Record<string, unknown>[] = [
    { kind: "attempt", attempt: 1 },
    { kind: "reasoning", text: "Checking the requested dimensions." },
  ];
  let reads = 0;
  let active = true;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/status")
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
    if (url.pathname === "/api/jobs") return route.fulfill({ json: [job] });
    if (url.pathname.endsWith("/conversation"))
      return route.fulfill({ json: [{ ...job, summary: null }] });
    if (url.pathname.endsWith("/preview"))
      return route.fulfill({ json: { active: true, preview: null } });
    if (url.pathname.endsWith("/output")) {
      reads++;
      const offset = Number(url.searchParams.get("offset") || 0);
      return route.fulfill({
        json: {
          entries: events.slice(offset),
          offset: events.length,
          has_more: false,
          active,
          source: "recorded",
        },
      });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto(`/?job=${id}`);
  const toggle = page.getByRole("button", { name: "Model output Optional" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.waitForTimeout(1100);
  expect(reads).toBe(0);
  await toggle.click();
  const log = page.getByRole("region", { name: "Model output text" });
  await expect(log).toContainText("Checking the requested dimensions.");
  await page.getByRole("checkbox", { name: "Follow output" }).uncheck();
  events.push({
    kind: "content",
    text: '{"description":"<script>window.injected = true</script>"}',
  });
  await expect(log).toContainText("<script>window.injected = true</script>");
  expect(await page.evaluate(() => "injected" in window)).toBe(false);
  await expect(
    page.getByRole("checkbox", { name: "Follow output" }),
  ).not.toBeChecked();
  await toggle.click();
  const closedReads = reads;
  await page.waitForTimeout(1200);
  expect(reads).toBe(closedReads);
  events.push(
    { kind: "end", status: "interrupted" },
    { kind: "attempt", attempt: 2 },
    { kind: "content", text: "Repaired CAD response" },
    { kind: "end", status: "completed" },
  );
  active = false;
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(log).toContainText("Attempt 2");
  await expect(log).toContainText("Repaired CAD response");
  await expect(log).toContainText("Checking the requested dimensions.");
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download model output" }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("model-output.txt");
  const transcript = await readFile((await download.path())!, "utf-8");
  expect(transcript).toContain("Checking the requested dimensions.");
  expect(transcript).toContain("Repaired CAD response");
  const finalReads = reads;
  await page.waitForTimeout(1200);
  expect(reads).toBe(finalReads);
  expect(errors).toEqual([]);
});
