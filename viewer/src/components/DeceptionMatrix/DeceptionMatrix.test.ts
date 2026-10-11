import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { parseDeceptionMatrix } from "../../data/deception";
import { DeceptionMatrix } from "./DeceptionMatrix";
import { fixture } from "./fixture";

const matrix = parseDeceptionMatrix(structuredClone(fixture));
const render = (props: Partial<React.ComponentProps<typeof DeceptionMatrix>> = {}) =>
  renderToStaticMarkup(React.createElement(DeceptionMatrix, { matrix, ...props }));

describe("DeceptionMatrix", () => {
  it("shows rate and n, with CI and baseline in the title", () => {
    const html = render();
    expect(html).toContain("80% <small>(n=10)</small>");
    expect(html).toContain("--rate:0.8");
    expect(html).toContain('title="95% CI 10% to 90%; baseline 25%"');
  });

  it("greys cells under the n threshold only", () => {
    const html = render({ minN: 5 });
    expect(html).toContain("100% <small>(n=4)</small>");
    const cells = html.match(/<td[^>]*>.*?<\/td>/g) ?? [];
    const grey = cells.filter((c) => c.includes('data-below-threshold="true"'));
    expect(grey).toHaveLength(1);
    expect(grey[0]).toContain("(n=4)");
    expect(grey[0]).not.toContain("--rate"); // greyed cells are hatched, not shaded
    expect(render({ minN: 1 })).not.toContain('data-below-threshold="true"');
  });

  it("starts on the chosen source; an empty source says so", () => {
    expect(render({ initialSource: "jev" })).toContain("No lies heard");
    expect(render({ initialSource: "jev" })).toContain('aria-pressed="true">Jev');
    expect(render()).toContain('aria-pressed="true">Self-tagged');
  });
});
