import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { renderAt } from "../test/render";

// Afterburn (ADR 0013, A2/A3): the signed-out header's buttons and the auth card's heading.

describe("the signed-out header", () => {
  it("has Sign up as the heat primary and Sign in as the ghost button", async () => {
    fakeApi({});
    renderAt("/signin");
    const account = await screen.findByRole("navigation", { name: "Account" });
    const signUp = within(account).getByRole("link", { name: "Sign up" });
    expect(signUp).toHaveClass("bg-heat", "text-on-heat");
    expect(signUp).not.toHaveClass("bg-brand");
    // Takeover gate: the prerender placeholder cannot carry the glow, so the header button has none.
    expect(signUp).not.toHaveClass("shadow-glow")
    const signIn = within(account).getByRole("link", { name: "Sign in" });
    expect(signIn).toHaveClass("bg-transparent");
    expect(signIn).not.toHaveClass("bg-heat");
  });
});

describe("the auth card", () => {
  it("shows the section heading with its eyebrow, and a heat submit button", async () => {
    fakeApi({});
    renderAt("/signin");
    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "Sign in",
    });
    expect(heading).toHaveClass("font-display", "text-2xl");
    expect(screen.getByText("Welcome back")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toHaveClass(
      "bg-heat",
    );
  });
});
