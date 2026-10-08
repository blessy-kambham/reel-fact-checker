// A shareable verdict card: the report's verdict drawn on a canvas in the browser and saved as a PNG.
// Nothing is sent to the server. Colours come from the page's own tokens in style.css.

const WIDTH = 1200, HEIGHT = 630, LEFT = 90, TEXT_WIDTH = 1020;

function token(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// The same colour pairs the report uses for each verdict label.
function verdictColours(verdict) {
  if (verdict === 'TRUE') return [token('--mint', '#cff3df'), token('--mint-ink', '#14573a')];
  if (verdict === 'FALSE') return [token('--cherry', '#d11a3a'), '#ffffff'];
  if (verdict === 'MISLEADING' || verdict === 'PARTIALLY TRUE') return [token('--butter', '#ffe7a3'), token('--butter-ink', '#6a4700')];
  return [token('--lilac', '#e8dff5'), token('--lilac-ink', '#47356b')];
}

// A word wider than the line (a long link, or a language written without spaces) split into pieces that fit.
function pieces(context, word, width) {
  if (context.measureText(word).width <= width) return [word];
  const out = [];
  let piece = '';
  for (const character of word) {
    if (piece && context.measureText(piece + character).width > width) { out.push(piece); piece = character; } else piece += character;
  }
  if (piece) out.push(piece);
  return out;
}

// Break `text` into at most `maxLines` lines that fit `width`. Whenever text is left over, the last line ends with
// …, so a cut-off claim never looks complete.
export function wrap(context, text, width, maxLines) {
  const lines = [];
  let line = '', cut = false;
  outer: for (const word of text.split(/\s+/).filter(Boolean)) {
    const parts = pieces(context, word, width);
    for (let i = 0; i < parts.length; i++) {
      const next = !line ? parts[i] : i === 0 ? `${line} ${parts[i]}` : `${line}${parts[i]}`;
      if (!line || context.measureText(next).width <= width) { line = next; continue; }
      lines.push(line);
      line = parts[i];
      if (lines.length === maxLines) { cut = true; break outer; }
    }
  }
  if (!cut && line) lines.push(line);
  if (cut) {
    let last = lines[lines.length - 1];
    while (last && context.measureText(`${last}…`).width > width) last = last.slice(0, -1);
    lines[lines.length - 1] = `${last.trimEnd()}…`;
  }
  return lines;
}

function roundedRect(context, x, y, w, h, r) {
  context.beginPath();
  context.roundRect(x, y, w, h, r);
}

// What the card says: one claim's verdict, or the overall verdict when several claims were checked.
export function cardContent(report) {
  const claims = report.claims || [];
  if (claims.length === 1) {
    const claim = claims[0];
    const issued = claim.verdict_state === 'issued' && claim.verdict !== 'UNVERIFIABLE';
    const details = issued
      ? [claim.confidence && `${claim.confidence[0].toUpperCase()}${claim.confidence.slice(1)} confidence`,
         claim.verdict_site_count && `${claim.verdict_site_count} ${claim.verdict_site_count === 1 ? 'site' : 'sites'} checked`].filter(Boolean).join(' · ')
      : 'No verdict was given';
    return { verdict: claim.verdict, headline: claim.claim, lines: [], details };
  }
  // Reports saved before overall verdicts existed have none, and the card does not invent one.
  return {
    verdict: report.overall_verdict || null,
    headline: report.overall_summary || `${claims.length} claims checked.`,
    lines: claims.slice(0, 3).map(claim => `${claim.verdict} · ${claim.claim}`),
    details: `${claims.length} claims checked`,
  };
}

export async function drawVerdictCard(report) {
  const display = token('--display', 'Georgia, serif'), body = token('--body', 'sans-serif');
  try {
    await Promise.all([document.fonts.load(`500 44px ${display}`), document.fonts.load(`700 40px ${body}`)]);
  } catch { /* fall back to the system fonts */ }
  const content = cardContent(report);
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const context = canvas.getContext('2d');
  const plum = token('--plum', '#47112c'), mauve = token('--mauve', '#7c4a61'), raspberry = token('--raspberry', '#c2255c');

  context.fillStyle = token('--petal', '#ffeef4');
  context.fillRect(0, 0, WIDTH, HEIGHT);
  roundedRect(context, 40, 40, WIDTH - 80, HEIGHT - 80, 32);
  context.fillStyle = token('--milk', '#fffbfd');
  context.fill();
  context.lineWidth = 3;
  context.strokeStyle = token('--rose', '#f5a9c4');
  context.stroke();

  context.textBaseline = 'alphabetic';
  context.fillStyle = raspberry;
  context.font = `700 40px ${display}`;
  context.fillText('r.', LEFT, 112);
  const markWidth = context.measureText('r. ').width;
  context.fillStyle = plum;
  context.font = `600 28px ${body}`;
  context.fillText('reel fact-checker', LEFT + markWidth, 110);
  context.fillStyle = mauve;
  context.font = `500 22px ${body}`;
  context.textAlign = 'right';
  context.fillText(`Checked ${new Date(report.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`,
                   WIDTH - 90, 108);
  context.textAlign = 'left';

  // The verdict label, tilted like the one on the page.
  if (content.verdict) {
    const [fill, ink] = verdictColours(content.verdict);
    context.font = `700 40px ${body}`;
    const labelWidth = context.measureText(content.verdict).width + 56;
    context.save();
    context.translate(LEFT, 150);
    context.rotate(-2 * Math.PI / 180);
    roundedRect(context, 0, 0, labelWidth, 70, 12);
    context.fillStyle = fill;
    context.fill();
    context.fillStyle = ink;
    context.fillText(content.verdict, 28, 50);
    context.restore();
  }

  let y = 300;
  context.fillStyle = plum;
  context.font = `500 44px ${display}`;
  for (const line of wrap(context, content.headline, TEXT_WIDTH, content.lines.length ? 2 : 4)) {
    context.fillText(line, LEFT, y);
    y += 56;
  }
  context.font = `500 24px ${body}`;
  context.fillStyle = mauve;
  for (const line of content.lines) {
    for (const part of wrap(context, line, TEXT_WIDTH, 1)) context.fillText(part, LEFT, y + 6);
    y += 36;
  }

  context.font = `600 24px ${body}`;
  context.fillStyle = plum;
  context.fillText(content.details, LEFT, 548);
  context.font = `500 22px ${body}`;
  context.fillStyle = raspberry;
  context.textAlign = 'right';
  context.fillText('Read the evidence before sharing.', WIDTH - 90, 548);
  context.textAlign = 'left';
  return canvas;
}

export async function downloadVerdictCard(report) {
  const canvas = await drawVerdictCard(report);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('The card could not be drawn in this browser.');
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `verdict-${report.id}.png`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
