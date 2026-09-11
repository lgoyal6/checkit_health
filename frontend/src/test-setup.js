import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without `globals: true`, so Testing Library's automatic
// afterEach cleanup never registers and rendered trees pile up across tests
// inside a file. Register it explicitly.
afterEach(cleanup);
