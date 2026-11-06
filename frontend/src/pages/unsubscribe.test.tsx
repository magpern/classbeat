import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SIGNED_IN, fakeApi, reason } from "../test/fakeApi";
import { renderAt } from "../test/render";

describe("unsubscribe page", () => {
  it("sends nothing on load, strips the fragment, and posts the chosen token on a click", async () => {
    const api = fakeApi({
      "POST /api/mail/unsubscribe/confirm": { status: "ok" },
    });
    renderAt("/unsubscribe#token=cat-token&all=all-token");
    expect(window.location.hash).toBe("");
    expect(api.to("POST", "/api/mail/unsubscribe/confirm")).toHaveLength(0);
    fireEvent.click(
      screen.getByRole("button", { name: "Stop all optional email" }),
    );
    expect(
      await screen.findByText(/no longer send you optional email/),
    ).toBeInTheDocument();
    expect(api.to("POST", "/api/mail/unsubscribe/confirm")[0]?.body).toEqual({
      token: "all-token",
    });
    // Security notices are optional mail: the page must not promise they still arrive.
    expect(
      screen.getByText(
        /Sign-in, password reset and confirmation links are always sent/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/security notices, is still sent/)).toBeNull();
  });

  it("posts the category token for the first button", async () => {
    const api = fakeApi({
      "POST /api/mail/unsubscribe/confirm": { status: "ok" },
    });
    renderAt("/unsubscribe#token=cat-token&all=all-token");
    fireEvent.click(
      screen.getByRole("button", { name: "Stop this kind of email" }),
    );
    expect(
      await screen.findByText(/send you this kind of email/),
    ).toBeInTheDocument();
    expect(api.to("POST", "/api/mail/unsubscribe/confirm")[0]?.body).toEqual({
      token: "cat-token",
    });
  });

  it("says so when the link is no longer valid", async () => {
    fakeApi({
      "POST /api/mail/unsubscribe/confirm": reason(400, "link_invalid"),
    });
    renderAt("/unsubscribe#token=bad");
    fireEvent.click(
      screen.getByRole("button", { name: "Stop this kind of email" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /no longer valid/,
    );
  });

  it("asks for the link when there is no token", () => {
    fakeApi({});
    renderAt("/unsubscribe");
    expect(
      screen.getByText(/needs the link from the mail/),
    ).toBeInTheDocument();
  });
});

describe("account email section", () => {
  const ACCOUNT = {
    "GET /api/auth/session": SIGNED_IN,
    "GET /api/auth/identities": {
      identities: [],
      has_password: true,
      available: [],
    },
    "GET /api/auth/mfa": { totp_enabled: false, recovery_codes_left: 0 },
  };

  it("shows the state and saves a switched-off kind", async () => {
    const api = fakeApi({
      ...ACCOUNT,
      "GET /api/mail/preferences": { unsubscribed_all: false, opted_out: [] },
      "PUT /api/mail/preferences": {
        unsubscribed_all: false,
        opted_out: ["welcome"],
      },
    });
    renderAt("/account#data");
    const welcome = await screen.findByRole("switch", { name: "Welcome mail" });
    expect(welcome).toBeChecked();
    fireEvent.click(welcome);
    await waitFor(() => {
      expect(
        screen.getByRole("switch", { name: "Welcome mail" }),
      ).not.toBeChecked();
    });
    expect(api.to("PUT", "/api/mail/preferences")[0]?.body).toEqual({
      unsubscribed_all: false,
      opted_out: ["welcome"],
    });
    const copy = screen.getByText(/always sent/);
    expect(copy).toHaveTextContent(
      "Sign-in, password reset and confirmation links are always sent.",
    );
    expect(copy).toHaveTextContent(
      /Account activity notices include security notices/,
    );
    expect(copy).not.toHaveTextContent(/security alerts/);
  });
});
