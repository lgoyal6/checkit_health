import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api.js";
import NarrativePage from "./NarrativePage.jsx";

vi.mock("../api.js");

const NARRATIVE = {
  narrative: {
    narrative_id: "n1",
    label: "Bleach cures autism",
    canonical_claim: "Bleach cures autism",
    topic: "treatment",
    lifecycle_state: "peaking",
    status: "watching",
    first_seen_at: "2026-09-09T06:00:00+00:00",
    last_seen_at: "2026-09-09T14:00:00+00:00",
  },
  members: [
    {
      post_id: "a1",
      claim: "Bleach cures autism",
      username: "someone",
      source: "bluesky",
      like_count: 900,
      retweet_count: 260,
      review_status: "unreviewed",
      evidence_state: "contradicted",
    },
  ],
  evidence: [
    {
      id: "pubmed:1",
      title: "Chlorine dioxide ingestion",
      url: "https://example.org/1",
      publisher: "PubMed",
      published_at: "2021",
      relevance_score: 0.9,
      passage: "No therapeutic benefit was found.",
    },
  ],
  responses: [],
  trajectory: {
    points: [
      { at: "2026-09-09T08:00:00+00:00", reach: 170 },
      { at: "2026-09-09T14:00:00+00:00", reach: 1160 },
    ],
    growth_per_hour: 165,
    observations: 2,
    span_hours: 6,
  },
  snapshots: [],
  response_mode: "debunk",
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/narratives/n1"]}>
      <Routes>
        <Route path="/narratives/:narrativeId" element={<NarrativePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  api.getNarrative.mockResolvedValue(structuredClone(NARRATIVE));
  api.narrativeReportUrl.mockReturnValue("http://api/report");
});

describe("NarrativePage", () => {
  it("shows the rumor with its lifecycle and aggregate reach", async () => {
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Bleach cures autism" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Peaking")).toBeInTheDocument();
    expect(screen.getByText("1,160")).toBeInTheDocument();
  });

  it("links to the printable situation report", async () => {
    renderPage();
    expect(
      await screen.findByRole("link", { name: /situation report/i }),
    ).toHaveAttribute("href", "http://api/report");
  });

  it("lists every cited source with its publisher", async () => {
    renderPage();
    expect(
      await screen.findByRole("link", { name: /Chlorine dioxide ingestion/i }),
    ).toHaveAttribute("href", "https://example.org/1");
    expect(screen.getByText(/PubMed/)).toBeInTheDocument();
  });

  it("records an analyst decision against a member post", async () => {
    api.updateReview.mockResolvedValue({ review_status: "accepted" });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));

    await waitFor(() =>
      expect(api.updateReview).toHaveBeenCalledWith(
        "a1",
        expect.objectContaining({ status: "accepted" }),
      ),
    );
  });

  it("explains a refusal instead of showing an error when evidence is thin", async () => {
    const refusal = new Error(
      "Evidence state is 'insufficient'. A response can only be drafted when retrieved evidence actually contradicts the claim.",
    );
    refusal.reason = "insufficient_evidence";
    api.draftResponse.mockRejectedValue(refusal);
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /draft a response/i }),
    );

    expect(await screen.findByText(/No draft was generated/i)).toBeInTheDocument();
    expect(
      screen.getByText(/can only be drafted when retrieved evidence/i),
    ).toBeInTheDocument();
  });

  it("tells the analyst up front that a draft needs evidence and is never posted", async () => {
    renderPage();
    expect(
      await screen.findByText(/Checkit never posts it/i),
    ).toBeInTheDocument();
  });

  it("shows a generated draft slot by slot with its protocol", async () => {
    api.draftResponse.mockResolvedValue({
      response_id: "r1",
      status: "draft",
      mode: "debunk",
      protocol: "debunking-handbook-2020/fact-myth-fallacy",
      citations: ["pubmed:1"],
      draft: {
        fact: "Chlorine dioxide is not a treatment for autism.",
        myth: "Bleach cures autism.",
        fallacy_name: "anecdote as evidence",
        suggested_post: "Chlorine dioxide is not a treatment. [pubmed:1]",
        tone_notes: ["Lead with the fact; never open on the myth."],
      },
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /draft a response/i }),
    );

    expect(
      await screen.findByText("Chlorine dioxide is not a treatment for autism."),
    ).toBeInTheDocument();
    expect(screen.getByText("Fact (lead with this)")).toBeInTheDocument();
    expect(screen.getByText("Myth (stated once)")).toBeInTheDocument();
    expect(
      screen.getByText("debunking-handbook-2020/fact-myth-fallacy"),
    ).toBeInTheDocument();
  });

  it("offers no copy control until a draft is approved", async () => {
    api.draftResponse.mockResolvedValue({
      response_id: "r1",
      status: "draft",
      mode: "debunk",
      protocol: "p",
      citations: ["pubmed:1"],
      draft: { fact: "A fact." },
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /draft a response/i }),
    );
    await screen.findByText("A fact.");

    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /copy for sending/i }),
    ).not.toBeInTheDocument();
  });

  it("allows copying only after a human approves, and says a person sends it", async () => {
    api.draftResponse.mockResolvedValue({
      response_id: "r1",
      status: "draft",
      mode: "debunk",
      protocol: "p",
      citations: ["pubmed:1"],
      draft: { fact: "A fact." },
    });
    api.decideResponse.mockResolvedValue({
      response_id: "r1",
      status: "approved",
      approved_by: "dr-lee",
      mode: "debunk",
      protocol: "p",
      citations: ["pubmed:1"],
      draft: { fact: "A fact." },
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /draft a response/i }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(
      await screen.findByRole("button", { name: /copy for sending/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/approved by dr-lee/i)).toBeInTheDocument();
  });

  it("explains why a pre-bunk does not restate the rumor", async () => {
    api.draftResponse.mockResolvedValue({
      response_id: "r1",
      status: "draft",
      mode: "prebunk",
      protocol: "inoculation/tactic-first",
      citations: ["pubmed:1"],
      draft: { fact: "A fact.", myth: "" },
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /draft a response/i }),
    );

    expect(
      await screen.findByText(/without restating the rumor/i),
    ).toBeInTheDocument();
  });

  it("flags citations the model invented and the system dropped", async () => {
    api.draftResponse.mockResolvedValue({
      response_id: "r1",
      status: "draft",
      mode: "debunk",
      protocol: "p",
      citations: ["pubmed:1"],
      draft: { fact: "A fact.", dropped_citations: ["made-up:1", "made-up:2"] },
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /draft a response/i }),
    );

    expect(
      await screen.findByText(/2 invented citations dropped/i),
    ).toBeInTheDocument();
  });
});
