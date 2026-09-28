"""Strict source mapping for verbatim claims; no judgments about truth."""
import re

# Only explicit clause separators may fall between claims. Meaning-bearing
# operators (because, not, unless, if, or, etc.) cannot be discarded.
SEPARATOR = re.compile(r'[\s,;:.!?]*(?:(?:and|so|therefore)[\s,;:.!?]+)?', re.IGNORECASE)


def map_input(text, extraction):
    if extraction.intent != 'FACTUAL' or extraction.omitted_claims or not extraction.claims:
        return None
    spans = []
    cursor = 0
    for claim in extraction.claims:
        value = claim.text.strip()
        start = text.find(value, cursor)
        if start < 0 or not SEPARATOR.fullmatch(text[cursor:start]):
            return None
        end = start + len(value)
        # A split clause must retain the entire submission as context, so
        # pronouns, conditions, and causal connectors remain available.
        if len(extraction.claims) > 1:
            if claim.context.strip() != text.strip():
                return None
        elif claim.context.strip() and claim.context.strip() not in text:
            return None
        spans.append({'start': start, 'end': end, 'text': text[start:end]})
        cursor = end
    # A trailing connector is not a completed assertion.
    if text[cursor:].strip(' \t\r\n,;:.!?'):
        return None
    return spans
