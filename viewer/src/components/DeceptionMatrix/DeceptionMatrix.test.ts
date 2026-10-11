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

  it("with Jev labels the source toggle is there and starts on the chosen source", () => {
    const f = structuredClone(fixture) as typeof fixture;
    (f.pairs as Record<string, unknown>).jev = structuredClone(f.pairs.self_tag);
    const m = parseDeceptionMatrix(f);
    const withJev = (props = {}) => render({ matrix: m, ...props });
    expect(withJev()).toContain("deception-sources");
    expect(withJev()).not.toContain("Jev labels haven");
    expect(withJev({ initialSource: "jev" })).toContain('aria-pressed="true">Jev');
    expect(withJev()).toContain('aria-pressed="true">Self-tagged');
  });

  it("with no Jev labels it hides the toggle and says why, even if Jev was asked for", () => {
    for (const html of [render(), render({ initialSource: "jev" })]) {
      expect(html).not.toContain("deception-sources");
      expect(html).not.toContain("No lies heard");
      expect(html).toContain("Lie labels in this batch are self-tags. Jev labels haven&#x27;t been run on it yet.");
      expect(html).toContain("80% <small>(n=10)</small>");
    }
  });

  it("headers show short model names, not catalog ids", () => {
    const f = structuredClone(fixture) as typeof fixture;
    f.pairs.self_tag = { "llama4-maverick": { "gpt-5-mini": f.pairs.self_tag.a.b } } as never;
    const html = renderToStaticMarkup(
      React.createElement(DeceptionMatrix, { matrix: parseDeceptionMatrix(f) }),
    );
    expect(html).toContain('<span class="deception-name">Llama 4 Maverick</span>');
    expect(html).toContain('<span class="deception-name">GPT-5 mini</span>');
    expect(html).not.toContain(">llama4-maverick<");
  });
});
