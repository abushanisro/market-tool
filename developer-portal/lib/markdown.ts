import { Marked } from "marked";
import { highlight } from "./highlight";

export const headingId = (t: string) => t.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export function renderMarkdown(md: string): { html: string; headings: { id: string; text: string; depth: number }[] } {
  const headings: { id: string; text: string; depth: number }[] = [];
  const m = new Marked({
    gfm: true,
    renderer: {
      heading({ tokens, depth }) {
        const text = this.parser.parseInline(tokens);
        const id = headingId(text);
        if (depth <= 3) headings.push({ id, text: text.replace(/<[^>]+>/g, ""), depth });
        return `<h${depth} id="${id}">${text}</h${depth}>`;
      },
      code({ text, lang }) {
        return `<pre><code>${highlight(text, lang || "bash")}</code></pre>`;
      },
    },
  });
  return { html: m.parse(md, { async: false }) as string, headings };
}
