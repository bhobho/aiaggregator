"""Curated all-time-best AI talks by field visionaries, for the My Page sidebar.

A static, hand-picked list (video IDs verified against YouTube's oEmbed
endpoint) rather than a live API — "all-time best" is an editorial judgment,
not something a feed can rank.

Each entry carries a distinct pastel-tint color (row background + label/title
text) so the sidebar reads as a colorful "trading card" rail rather than a
monochrome list — one hue per person, never reused.
"""
from __future__ import annotations

import random

VISIONARY_VIDEOS: list[dict] = [
    {
        "person": "Geoffrey Hinton",
        "video_id": "qrvK_KuIeJk",
        "title": "“Godfather of AI” Geoffrey Hinton: The 60 Minutes Interview",
        "channel": "60 Minutes",
        "bg": "#EEEDFE", "label": "#3C3489", "title_color": "#26215C",
    },
    {
        "person": "Demis Hassabis",
        "video_id": "Gfr50f6ZBvo",
        "title": "Demis Hassabis: DeepMind — AI, Superintelligence & the Future of Humanity",
        "channel": "Lex Fridman Podcast #299",
        "bg": "#E1F5EE", "label": "#085041", "title_color": "#04342C",
    },
    {
        "person": "Sam Altman",
        "video_id": "L_Guz73e6fw",
        "title": "Sam Altman: OpenAI CEO on GPT-4, ChatGPT, and the Future of AI",
        "channel": "Lex Fridman Podcast #367",
        "bg": "#FAECE7", "label": "#712B13", "title_color": "#4A1B0C",
    },
    {
        "person": "Ilya Sutskever",
        "video_id": "aR20FWCCjAs",
        "title": "Ilya Sutskever — We're Moving from the Age of Scaling to the Age of Research",
        "channel": "Dwarkesh Podcast",
        "bg": "#FBEAF0", "label": "#72243E", "title_color": "#4B1528",
    },
    {
        "person": "Andrej Karpathy",
        "video_id": "kCc8FmEb1nY",
        "title": "Let's Build GPT: From Scratch, in Code, Spelled Out",
        "channel": "Andrej Karpathy",
        "bg": "#E6F1FB", "label": "#0C447C", "title_color": "#042C53",
    },
    {
        "person": "Jensen Huang",
        "video_id": "DiGB5uAYKAg",
        "title": "GTC 2023 Keynote with NVIDIA CEO Jensen Huang",
        "channel": "NVIDIA",
        "bg": "#EAF3DE", "label": "#27500A", "title_color": "#173404",
    },
    {
        "person": "Fei-Fei Li",
        "video_id": "40riCqvRoMs",
        "title": "How We're Teaching Computers to Understand Pictures",
        "channel": "TED",
        "bg": "#FAEEDA", "label": "#633806", "title_color": "#412402",
    },
    {
        "person": "Andrew Ng",
        "video_id": "0jspaMLxBig",
        "title": "Andrew Ng: Deep Learning, Education, and Real-World AI",
        "channel": "Lex Fridman Podcast #73",
        "bg": "#FCEBEB", "label": "#791F1F", "title_color": "#501313",
    },
]


def shuffled() -> list[dict]:
    """A freshly-shuffled copy, so the sidebar order rotates on every page load."""
    videos = VISIONARY_VIDEOS.copy()
    random.shuffle(videos)
    return videos
