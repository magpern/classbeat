import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeApi } from "../test/fakeApi";
import {
  JOB_ID,
  SESSION_ROUTES,
  jobBody,
  openStream,
  sse,
} from "../test/jobFixtures";
import {
  MOWL_ID,
  LIST_PATH,
  connectionsBody,
  listBody,
  waitingJob,
} from "../test/songFixtures";
import { renderAt } from "../test/render";

// Afterburn on the focused flows (issue #1245, ADR 0013 Afterburn A2-A4): the header sits in the
// content container, the pages open with the eyebrow and display heading, and motion is opt-in.

const JOB = `/api/jobs/${JOB_ID}`;

describe("the focused header", () => {
  it("holds the logo and the way out in the same max-w-5xl container as the content", async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody("running"),
      [`GET ${JOB}/events`]: openStream,
    });
    renderAt(`/jobs/${JOB_ID}`);
    const back = await screen.findByRole("link", { name: "Back to home" });
    const logo = screen.getByRole("link", { name: "classbeat, home" });
    const container = back.parentElement!;
    expect(container.className).toContain("max-w-5xl");
    expect(container.className).toContain("mx-auto");
    expect(logo.parentElement).toBe(container);
    expect(container.parentElement!.tagName).toBe("HEADER");
    expect(container.parentElement!.className).toContain("border-b");
  });
});

describe("the Designing page", () => {
  it("opens with the Designing eyebrow and a 24 px card, and animates only under motion-safe", async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: jobBody("running"),
      [`GET ${JOB}/events`]: () =>
        sse([1, "queued"], [3, "stage_started", { stage: "music" }]),
    });
    renderAt(`/jobs/${JOB_ID}`);
    const title = await screen.findByRole("heading", {
      level: 1,
      name: "Designing your class…",
    });
    expect(title.className).toContain("font-display");
    expect(
      screen.getByText("Designing", { selector: "p" }),
    ).toBeInTheDocument();
    const card = screen.getByRole("region", { name: "Designing your class…" });
    expect(card.className).toContain("rounded-3xl");
    expect(card.className).toContain("shadow-card");
    const ghost = card.querySelector("svg.ghost-bars")!;
    expect(ghost.getAttribute("class")).toContain("motion-safe:animate-pulse");
    expect(ghost.getAttribute("class")).not.toMatch(/(^|\s)animate-/);
    for (const rect of ghost.querySelectorAll("rect"))
      expect(rect.getAttribute("class")).toMatch(/fill-brand/);
    // The current step carries the heat dot, pulsing only under motion-safe (never the text).
    await waitFor(() => {
      expect(card.querySelector('[aria-current="step"]')).not.toBeNull();
    });
    const current = card.querySelector('[aria-current="step"]');
    expect(current?.querySelector(".bg-heat")?.className).toContain(
      "motion-safe:animate-pulse",
    );
    expect(current?.querySelector(".bg-heat")?.className).not.toMatch(
      /(^|\s)animate-/,
    );
  });
});

describe("the song approval page", () => {
  it("opens with the Your songs eyebrow over the heading, in 24 px cards", async () => {
    fakeApi({
      ...SESSION_ROUTES,
      [`GET ${JOB}`]: waitingJob(),
      [`GET ${JOB}/events`]: openStream,
      [`GET ${LIST_PATH}`]: listBody(),
    });
    renderAt(`/jobs/${JOB_ID}`);
    const title = await screen.findByRole("heading", {
      level: 1,
      name: "Songs for your class",
    });
    expect(
      screen.getByText("Your songs", { selector: "p" }),
    ).toBeInTheDocument();
    expect(title.closest("header")!.className).toContain("rounded-3xl");
    expect(screen.getByRole("list", { name: /songs/i }).className).toContain(
      "rounded-3xl",
    );
    expect(
      screen.getByRole("region", { name: "Approval" }).className,
    ).toContain("lg:rounded-3xl");
  });
});

describe("the Advanced page", () => {
  it("opens with the Advanced eyebrow, the display title and FieldCard sections", async () => {
    fakeApi({
      ...SESSION_ROUTES,
      "GET /api/connections": connectionsBody(),
      "GET /api/music/mowl-playlists": {
        playlists: [{ spotify_playlist_id: MOWL_ID, name: "Hills" }],
      },
    });
    renderAt("/classes/new/advanced");
    const title = await screen.findByRole("heading", {
      level: 1,
      name: "Design a class",
    });
    expect(title.className).toContain("font-display");
    expect(screen.getByText("Advanced", { selector: "p" })).toBeInTheDocument();
    const basics = await screen.findByRole("heading", { name: "The basics" });
    expect(basics.closest("section")!.className).toContain("rounded-3xl");
  });
});
