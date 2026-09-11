import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  ClusteringNotice,
  EvidenceBadge,
  LifecycleBadge,
  PriorityMeter,
  ReviewButtons,
  Sparkline,
} from "./Signals.jsx";

const PRIORITY = {
  score: 72,
  components: { reach: 0.9, potential_harm: 1.0, uncertainty: 0.1 },
  weights: { reach: 0.45, potential_harm: 0.35, uncertainty: 0.2 },
  weights_version: "heuristic-2026-07-23",
};

describe("PriorityMeter", () => {
  it("shows the score and every component that produced it", () => {
    render(<PriorityMeter priority={PRIORITY} />);

    expect(screen.getByText("72")).toBeInTheDocument();
    expect(screen.getByText("Reach")).toBeInTheDocument();
    expect(screen.getByText("Harm")).toBeInTheDocument();
    expect(screen.getByText("Uncertainty")).toBeInTheDocument();
  });

  it("says the score is a review priority, not a truth verdict", () => {
    render(<PriorityMeter priority={PRIORITY} />);
    expect(
      screen.getByText(/not a judgment that the claim is false/i),
    ).toBeInTheDocument();
  });

  it("names the weights version so a retune stays attributable", () => {
    render(<PriorityMeter priority={PRIORITY} />);
    expect(screen.getByText("heuristic-2026-07-23")).toBeInTheDocument();
  });

  it("degrades to a placeholder when a row carries no score", () => {
    const { container } = render(<PriorityMeter priority={null} />);
    expect(container.textContent).toBe("-");
  });
});

describe("LifecycleBadge", () => {
  it("labels an emerging narrative and explains why it matters", () => {
    render(<LifecycleBadge state="emerging" />);
    const badge = screen.getByText("Emerging");
    expect(badge).toHaveAttribute("title", expect.stringContaining("pre-bunk"));
  });

  it("warns that responding to a declining rumor revives it", () => {
    render(<LifecycleBadge state="declining" />);
    expect(screen.getByText("Declining")).toHaveAttribute(
      "title",
      expect.stringContaining("revive"),
    );
  });

  it("falls back to watching for an unknown state", () => {
    render(<LifecycleBadge state="something-else" />);
    expect(screen.getByText("Watching")).toBeInTheDocument();
  });
});

describe("EvidenceBadge", () => {
  it("renders the state in plain words", () => {
    render(<EvidenceBadge state="contradicted" />);
    expect(screen.getByText("contradicted")).toBeInTheDocument();
  });

  it("treats a missing state as insufficient rather than blank", () => {
    render(<EvidenceBadge state={undefined} />);
    expect(screen.getByText("insufficient")).toBeInTheDocument();
  });
});

describe("ReviewButtons", () => {
  it("offers every decision an analyst can record", () => {
    render(<ReviewButtons value="unreviewed" onChoose={() => {}} />);
    for (const label of ["Accept", "Reject", "Needs evidence", "In review"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("reports the current decision to assistive tech", () => {
    render(<ReviewButtons value="accepted" onChoose={() => {}} />);
    expect(screen.getByRole("button", { name: "Accept" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Reject" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("passes the chosen status up", async () => {
    const onChoose = vi.fn();
    render(<ReviewButtons value="unreviewed" onChoose={onChoose} />);

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onChoose).toHaveBeenCalledWith("rejected");
  });

  it("locks while a decision is in flight so it cannot double-submit", () => {
    render(<ReviewButtons value="unreviewed" onChoose={() => {}} busy />);
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
  });
});

describe("Sparkline", () => {
  it("says so rather than drawing a line from a single observation", () => {
    render(<Sparkline points={[{ at: "x", reach: 10 }]} />);
    expect(screen.getByText("not enough data")).toBeInTheDocument();
  });

  it("plots once there are two observations", () => {
    const { container } = render(
      <Sparkline
        points={[
          { at: "a", reach: 10 },
          { at: "b", reach: 90 },
        ]}
      />,
    );
    expect(container.querySelector("polyline")).toBeTruthy();
  });

  it("survives an all-zero series without dividing by zero", () => {
    const { container } = render(
      <Sparkline
        points={[
          { at: "a", reach: 0 },
          { at: "b", reach: 0 },
        ]}
      />,
    );
    const points = container.querySelector("polyline").getAttribute("points");
    expect(points).not.toContain("NaN");
  });
});

describe("ClusteringNotice", () => {
  it("warns when grouping is running on the offline embedding", () => {
    render(
      <ClusteringNotice
        clustering={{ semantic: false, note: "Offline fallback: near-duplicate wording only." }}
      />,
    );
    expect(screen.getByText(/Grouping is limited/i)).toBeInTheDocument();
    expect(screen.getByText(/may appear more than once/i)).toBeInTheDocument();
  });

  it("stays out of the way when semantic grouping is active", () => {
    const { container } = render(
      <ClusteringNotice clustering={{ semantic: true, note: "Managed." }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing before settings have loaded", () => {
    const { container } = render(<ClusteringNotice clustering={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
