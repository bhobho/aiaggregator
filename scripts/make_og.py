"""Generate the Open Graph preview image (1200x630) used for link unfurls.

Run:  uv run python scripts/make_og.py
Output: aiaggregator/static/og.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "aiaggregator" / "static" / "og.png"

W, H = 1200, 630
GREEN = (0, 165, 98)
INK = (17, 24, 39)
GRAY = (107, 114, 128)
SUB = (55, 65, 81)
CHIP_BG = (243, 244, 246)

REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def main() -> None:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # accent bars
    d.rectangle([0, 0, W, 12], fill=GREEN)
    d.rectangle([0, H - 12, W, H], fill=INK)

    # logo: "AI" + "Aggregator"
    f_logo = font(BOLD, 88)
    x, y = 80, 70
    d.text((x, y), "AI", font=f_logo, fill=INK)
    w_ai = d.textlength("AI", font=f_logo)
    d.text((x + w_ai, y), "Aggregator", font=f_logo, fill=GREEN)

    # subtitle
    d.text((82, 185), "AI & Agentic-AI news — aggregated and summarized locally",
           font=font(REG, 34), fill=SUB)

    # source chips
    chips = ["OpenAI · Anthropic", "DeepMind · Meta · MSFT", "Nvidia · AWS · HF"]
    f_chip = font(BOLD, 28)
    cx, cy, ch = 80, 270, 60
    for label in chips:
        tw = d.textlength(label, font=f_chip)
        cw = tw + 44
        d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=10, fill=CHIP_BG)
        d.text((cx + 22, cy + 14), label, font=f_chip, fill=INK)
        cx += cw + 20

    # feature bullets
    f_feat = font(REG, 28)
    feats = [
        "•  Local LLM summaries, tags & importance scoring",
        "•  Cross-source clustering & daily digest",
        "•  Self-hosted · no paid APIs",
    ]
    fy = 380
    for line in feats:
        d.text((82, fy), line, font=f_feat, fill=GRAY)
        fy += 46

    # footer url
    d.text((82, 552), "github.com/bhobho/aiaggregator", font=font(BOLD, 26), fill=GREEN)

    img.save(OUT, "PNG")
    print(f"wrote {OUT} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
