"""What the agents work with. Each module here does one outside-world job; none of them decides anything.

    providers     the language model (structured output, image input) and web search
    fetcher       safe fetching of public pages
    excerpts      numbered excerpts of a page, with character offsets
    credibility   a rule-based rating of where a page comes from, and how strong a verdict's sources are
    media         ffmpeg validation, audio and keyframe extraction
    transcribe    local Whisper transcription

The agents that choose when to use these are in `agents/`. Application plumbing (spending cap, saved
reports, sign-in, article and video entry points) is in `services/`.
"""
