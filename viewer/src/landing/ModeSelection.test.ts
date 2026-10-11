import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import ModeSelection from './ModeSelection';
it('links to all three start modes and keeps the batch override on each destination', () => {
 const html = renderToStaticMarkup(createElement(ModeSelection, {href: extra => '?' + new URLSearchParams({base:'https://batch.test/',...extra})}));
 expect(html).toContain('aria-label="Game modes"');
 for(const mode of ['basic','watch','advanced']) expect(html).toContain(`page=play&amp;mode=${mode}`);
 expect(html.match(/base=https%3A%2F%2Fbatch.test%2F/g)).toHaveLength(4);
 expect(html).toContain('aria-label="Back to home"');
});
