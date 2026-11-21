import { type Page } from "@playwright/test";
import {
  expect,
  signInThroughUi,
  signUpThroughUi,
  test,
  uniqueEmail,
} from "./helpers";
import { useModelScript } from "./model-script";
import {
  listedTitles,
  readyToPick,
  sql,
  startPick,
  untilState,
} from "./song-helpers";
import { resetSpotify } from "./spotify-helpers";
import { resetSpotdown, spotdownCount } from "./spotdown-helpers";

// The Spotify song pick on spotdown (ADR 0040 amendment "spotdown is the only analysis path";
// SD-P6, #1303, #1354; this spec, #1337) against the fake spotdown, the fake Spotify, the fake MOWL
// and the recorded fake model: every pick runs on spotdown (no allowlist, no fallback); the list
// says "Timing approximate" on every song; Change songs analyses the new song only and the kept
// songs keep the label. Public test values only: no real spotdown, YouTube, Spotify or MOWL.

test.describe.configure({ mode: "serial" });
test.setTimeout(180_000); // a pick, an approval and a change, polling the fake

const LABEL = "song-timing-approximate";

test.beforeAll(() => {
  sql(
    `INSERT INTO app_settings (key, value) VALUES ('song_picking', '"on"') ` +
      `ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
  );
});

test.beforeEach(() => {
  resetSpotify();
  resetSpotdown();
});

test.afterAll(() => {
  resetSpotify();
  sql(`DELETE FROM app_settings WHERE key = 'song_picking'`);
});

async function newUser(page: Page, tag: string): Promise<void> {
  const email = uniqueEmail(tag);
  await signUpThroughUi(page, email);
  await signInThroughUi(page, email);
}

test("a pick labels the songs spotdown times; Change songs keeps the label on the kept songs", async ({
  page,
}) => {
  await newUser(page, "sd-pick");
  await readyToPick(page);

  // Pick songs: the pool is matched with spotdown, the eight chosen songs analysed, and the list
  // labels each of them.
  const id = await startPick(page, "E2E spotdown ride");
  await untilState(page, id, "waiting_for_approval");
  expect(await listedTitles(page)).toHaveLength(8);
  await expect(page.getByTestId(LABEL)).toHaveCount(8);
  await expect(page.getByTestId(LABEL).first()).toHaveText(
    "Timing approximate",
  );
  expect(spotdownCount("POST", "/v2/match")).toBeGreaterThan(0);
  expect(spotdownCount("POST", "/v2/analyses")).toBe(8); // the chosen songs only, never the pool

  // Approve and design.
  useModelScript("good_first_draft");
  await page.getByRole("button", { name: "Approve songs" }).click();
  await untilState(page, id, "completed");
  await expect(
    page.getByRole("heading", { name: "Your class is ready" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open the class" }).click();
  await expect(
    page.getByRole("region", { name: "Change songs" }),
  ).toBeVisible();

  // Change songs: revise one song. Only the new song is analysed; the kept songs keep the label.
  const before = spotdownCount("POST", "/v2/analyses");
  useModelScript("e2e_change");
  const change = page.getByRole("region", { name: "Change songs" });
  await change
    .getByRole("button", { name: "Replace Tempo Cascade 24" })
    .click();
  await change
    .getByLabel("What should be different?")
    .fill("A harder song for the climb.");
  await change.getByRole("button", { name: "Change marked songs" }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/);
  const revise = /\/jobs\/([0-9a-f-]{36})$/.exec(page.url())![1]!;
  await untilState(page, revise, "waiting_for_approval");

  expect(await listedTitles(page)).toHaveLength(8);
  await expect(page.getByTestId("song").nth(2)).toContainText("New");
  for (const position of [0, 1, 3, 4, 5, 6, 7]) {
    await expect(
      page.getByTestId("song").nth(position).getByTestId(LABEL),
    ).toHaveText("Timing approximate");
  }
  expect(spotdownCount("POST", "/v2/analyses") - before).toBe(1);
});
