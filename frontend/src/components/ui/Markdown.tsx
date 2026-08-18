import React from "react";

// A small, dependency-free markdown renderer for chat/answer text. Handles the
// subset the LLM actually produces — **bold**, *italic*, `code`, headings,
// unordered/ordered lists, --- rules, and blank-line-separated paragraphs.
//
// Optionally, names in `entities` are turned into tappable grey "pills"
// (onEntityClick) even when they appear inside bold — used to make supplier /
// customer names in a chat answer open their transaction history.

const HR = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/;
const H = /^\s*(#{1,6})\s+(.*)$/;
const UL = /^\s*[-*+]\s+/;
const OL = /^\s*\d+\.\s+/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;                 // | a | b |
const TABLE_SEP = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/; // |---|---| separator
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;

// Split a markdown table row into trimmed cells, dropping the outer pipes.
function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

type Ctx = { re: RegExp | null; onClick?: (name: string) => void };

function EntityPill({ name, onClick }: { name: string; onClick?: (n: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onClick?.(name)}
      className="mx-[1px] inline-flex items-center gap-1 rounded-md px-1.5 py-[1px] align-baseline text-[0.9em] font-medium transition hover:opacity-80"
      style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-1)" }}
    >
      <span className="material-symbols-outlined text-[13px]" style={{ color: "var(--text-3)" }} aria-hidden="true">
        storefront
      </span>
      {name}
    </button>
  );
}

// Split a plain-text run on known entity names, wrapping matches in pills.
function renderText(text: string, k: string, ctx: Ctx): React.ReactNode[] {
  if (!ctx.re) return [text];
  const out: React.ReactNode[] = [];
  let last = 0, i = 0, m: RegExpExecArray | null;
  ctx.re.lastIndex = 0;
  while ((m = ctx.re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<EntityPill key={`${k}e${i}`} name={m[0]} onClick={ctx.onClick} />);
    last = m.index + m[0].length;
    i++;
    if (m.index === ctx.re.lastIndex) ctx.re.lastIndex++; // guard against zero-length loop
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderInline(text: string, k: string, ctx: Ctx): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0, i = 0, m: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(...renderText(text.slice(last, m.index), `${k}t${i}`, ctx));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${k}b${i}`}>{renderText(tok.slice(2, -2), `${k}bb${i}`, ctx)}</strong>);
    else if (tok.startsWith("`"))
      out.push(
        <code key={`${k}c${i}`} className="rounded px-1 py-0.5 text-[0.86em]"
          style={{ background: "var(--surface-2)", fontFamily: "ui-monospace, monospace" }}>
          {tok.slice(1, -1)}
        </code>,
      );
    else out.push(<em key={`${k}i${i}`}>{renderText(tok.slice(1, -1), `${k}ii${i}`, ctx)}</em>);
    last = m.index + tok.length;
    i++;
  }
  if (last < text.length) out.push(...renderText(text.slice(last), `${k}tz`, ctx));
  return out;
}

export default function Markdown({
  text, entities, onEntityClick,
}: {
  text: string;
  entities?: string[];
  onEntityClick?: (name: string) => void;
}) {
  // Build the entity matcher (longest names first so "Silva Traders" wins over
  // "Silva"). Skip very short names to avoid noise. Rebuilt per render — cheap.
  const names = (entities || []).filter((n) => n && n.trim().length >= 3);
  const re = names.length
    ? new RegExp(
        "(" + names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length).join("|") + ")",
        "g",
      )
    : null;
  const ctx: Ctx = { re, onClick: onEntityClick };

  const lines = (text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0, key = 0;

  const isSpecial = (ln: string) => HR.test(ln) || H.test(ln) || UL.test(ln) || OL.test(ln) || TABLE_ROW.test(ln);

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (HR.test(line)) {
      blocks.push(<hr key={key++} className="my-3" style={{ border: 0, borderTop: "1px solid var(--border)" }} />);
      i++; continue;
    }

    const h = line.match(H);
    if (h) {
      blocks.push(
        <p key={key++} className="mt-1 font-bold" style={{ fontSize: h[1].length <= 2 ? "1.05em" : "1em" }}>
          {renderInline(h[2], `h${key}`, ctx)}
        </p>,
      );
      i++; continue;
    }

    // Table: a row line immediately followed by a |---|---| separator.
    if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const header = splitRow(line);
      i += 2; // consume header + separator
      const rows: string[][] = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      const tKey = key++;
      blocks.push(
        <div key={tKey} className="overflow-x-auto">
          <table className="w-full border-collapse text-[0.95em]" style={{ border: "1px solid var(--border)" }}>
            <thead>
              <tr>
                {header.map((c, ci) => (
                  <th key={ci} className="px-3 py-1.5 text-left font-bold"
                    style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)", borderRight: ci < header.length - 1 ? "1px solid var(--border)" : undefined }}>
                    {renderInline(c, `${tKey}th${ci}`, ctx)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {header.map((_, ci) => (
                    <td key={ci} className="px-3 py-1.5 align-top"
                      style={{ borderTop: "1px solid var(--border)", borderRight: ci < header.length - 1 ? "1px solid var(--border)" : undefined }}>
                      {renderInline(r[ci] ?? "", `${tKey}r${ri}c${ci}`, ctx)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (UL.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && UL.test(lines[i])) {
        items.push(<li key={`${key}li${i}`}>{renderInline(lines[i].replace(UL, ""), `${key}l${i}`, ctx)}</li>);
        i++;
      }
      blocks.push(<ul key={key++} className="list-disc space-y-1 pl-5">{items}</ul>);
      continue;
    }

    if (OL.test(line)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && OL.test(lines[i])) {
        items.push(<li key={`${key}li${i}`}>{renderInline(lines[i].replace(OL, ""), `${key}o${i}`, ctx)}</li>);
        i++;
      }
      blocks.push(<ol key={key++} className="list-decimal space-y-1 pl-5">{items}</ol>);
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !isSpecial(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    const nodes: React.ReactNode[] = [];
    para.forEach((p, idx) => {
      if (idx > 0) nodes.push(<br key={`${key}br${idx}`} />);
      nodes.push(...renderInline(p, `${key}p${idx}`, ctx));
    });
    blocks.push(<p key={key++}>{nodes}</p>);
  }

  return <div className="space-y-2">{blocks}</div>;
}
