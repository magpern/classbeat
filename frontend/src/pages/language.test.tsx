import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SIGNED_IN, fakeApi } from "../test/fakeApi";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";
import { I18nProvider } from "../i18n/I18n";

function renderAt(url: string) {
  window.history.replaceState(null, "", url);
  return render(
    <I18nProvider>
      <App />
    </I18nProvider>,
  );
}

// The manifest lists only English until the catalogues ship; these tests need three languages.
vi.mock("../i18n/locales.json", () => ({ default: ["en", "sv", "da"] }));

const OFFERED = ["en", "sv", "da"];
const ANON = {
  authenticated: false,
  csrf_token: "csrf-anon",
  locales: OFFERED,
};
const signedIn = (locale: string | null) => ({
  ...SIGNED_IN,
  user: { ...SIGNED_IN.user, locale },
  locales: OFFERED,
});

beforeEach(() => {
  window.localStorage.clear();
});

describe("the logged-out language picker", () => {
  it("lists the offered languages and keeps the choice in this browser", async () => {
    fakeApi({ "GET /api/auth/session": ANON });
    renderAt("/signin");
    const picker = await screen.findByRole("combobox", { name: "Language" });
    expect(
      Array.from(picker.querySelectorAll("option")).map((o) => o.textContent),
    ).toEqual(["English", "Svenska", "Dansk"]);
    fireEvent.change(picker, { target: { value: "sv" } });
    expect(window.localStorage.getItem("cb-locale")).toBe("sv");
  });

  it("is hidden when the server offers only English", async () => {
    fakeApi({ "GET /api/auth/session": { ...ANON, locales: ["en"] } });
    renderAt("/signin");
    await screen.findByRole("link", { name: "Sign up" });
    expect(screen.queryByRole("combobox", { name: "Language" })).toBeNull();
  });

  it("sends the chosen language with the sign-up", async () => {
    window.localStorage.setItem("cb-locale", "da");
    const api = fakeApi({
      "GET /api/auth/session": ANON,
      "POST /api/auth/signup": { status: "ok" },
    });
    renderAt("/signup");
    fireEvent.change(await screen.findByLabelText(/^Email/), {
      target: { value: "a@example.test" },
    });
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: "a long enough password" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign up|create/i }));
    await waitFor(() => {
      expect(api.to("POST", "/api/auth/signup")).toHaveLength(1);
    });
    expect(api.to("POST", "/api/auth/signup")[0]?.body).toMatchObject({
      locale: "da",
    });
  });
});

describe("the Account language", () => {
  async function openAppearance() {
    renderAt("/account");
    await userEvent.click(
      await screen.findByRole("tab", { name: "Appearance" }),
    );
    return screen.findByRole("combobox", { name: "Language" });
  }

  it("shows Follow this browser when nothing is saved and saves a choice", async () => {
    let saved: string | null = null;
    const api = fakeApi({
      "GET /api/auth/session": () => signedIn(saved),
      "PUT /api/account/locale": (body) => {
        saved = (body as { locale: string | null }).locale;
        return { locale: saved };
      },
    });
    const select = await openAppearance();
    expect(select).toHaveValue("");
    expect(
      screen.getByRole("option", { name: "Follow this browser" }),
    ).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "sv" } });
    await waitFor(() => {
      expect(api.to("PUT", "/api/account/locale")[0]?.body).toEqual({
        locale: "sv",
      });
    });
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Language" })).toHaveValue(
        "sv",
      );
    });
  });

  it("saves null for Follow this browser and forgets the browser choice", async () => {
    window.localStorage.setItem("cb-locale", "sv");
    const api = fakeApi({
      "GET /api/auth/session": signedIn("sv"),
      "PUT /api/account/locale": { locale: null },
    });
    const select = await openAppearance();
    expect(select).toHaveValue("sv");
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => {
      expect(api.to("PUT", "/api/account/locale")[0]?.body).toEqual({
        locale: null,
      });
    });
    await waitFor(() => {
      expect(window.localStorage.getItem("cb-locale")).toBeNull();
    });
  });

  it("shows a refused choice as an error", async () => {
    fakeApi({
      "GET /api/auth/session": signedIn(null),
      "PUT /api/account/locale": () =>
        new Response(
          JSON.stringify({ detail: { reason: "locale_not_offered" } }),
          {
            status: 400,
            headers: { "content-type": "application/json" },
          },
        ),
    });
    const select = await openAppearance();
    fireEvent.change(select, { target: { value: "da" } });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
