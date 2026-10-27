/** Tiny, dependency-free highlighter for json / bash / python / ts snippets (escapes first, then wraps tokens). */
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function highlight(code: string, lang = "json"): string {
  const l = lang.toLowerCase();
  if (l === "json") {
    return esc(code).replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)/gi,
      (m, str, colon, kw, n) => str ? (colon ? `<span class="tk-k">${str}</span>${colon}` : `<span class="tk-s">${str}</span>`)
        : kw ? `<span class="tk-w">${kw}</span>` : `<span class="tk-n">${n}</span>`);
  }
  const kws = l === "python" || l === "py" ? "import|from|as|def|return|while|if|else|for|in|not|None|True|False|print|with|break"
    : l === "bash" || l === "sh" || l === "curl" ? "curl|export|echo"
    : "const|let|await|async|return|new|throw|if|else|function|import|from|process";
  return esc(code).replace(new RegExp(`(#[^\\n]*$)|(//[^\\n]*$)|("(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|\`[^\`]*\`)|\\b(${kws})\\b|(\\s-{1,2}[A-Za-z][\\w-]*)`, "gm"),
    (m, hc, sc, str, kw, flag) => hc && (l === "python" || l === "bash" || l === "sh" || l === "curl") ? `<span class="tk-c">${hc}</span>`
      : sc && !(l === "python" || l === "bash") ? `<span class="tk-c">${sc}</span>`
      : str ? `<span class="tk-s">${str}</span>` : kw ? `<span class="tk-w">${kw}</span>` : flag ? `<span class="tk-k">${flag}</span>` : m);
}
