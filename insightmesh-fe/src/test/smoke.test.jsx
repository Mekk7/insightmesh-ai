// src/test/smoke.test.jsx
// Minimal smoke test to verify the Vitest + React setup is wired up.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

function Hello({ name = "World" }) {
  return <h1>Hello, {name}!</h1>;
}

describe("Vitest + React Testing Library smoke", () => {
  it("renders a basic React component", () => {
    render(<Hello name="InsightMesh" />);
    expect(screen.getByText("Hello, InsightMesh!")).toBeInTheDocument();
  });

  it("can access jest-dom matchers", () => {
    render(<Hello />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toBeVisible();
    expect(heading).toHaveTextContent("Hello, World!");
  });
});
