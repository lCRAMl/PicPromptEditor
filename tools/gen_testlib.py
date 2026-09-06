"""Erzeugt eine synthetische Bild-/Prompt-Bibliothek fuer Tests und Lastmessung.

Die Prompts folgen dem realen Format des Projekts: Prosa-Absaetze, beschriftete
Bloecke, eine CAPS-Sektion mit Bullet-Parametern, eine Style-Tag-Liste und ein
Negativblock. Varianten entstehen wie im Echtbetrieb durch Austausch einzelner
Saetze, damit Aehnlichkeit, Cluster und Evolution etwas zu finden haben.

Aufruf:
    python tools/gen_testlib.py ZIEL --pairs 5000 --folders 20
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

BASE_SENTENCES = [
    "A extreme low angle hyper-realistic, cinematic editorial portrait of a young woman photographed indoors against a plain wall.",
    "The subject must never face the camera directly and must never be photographed front-on.",
    "Favor three-quarter, candid, seated, leaning, and dynamic angled poses.",
    "Every image should feel naturally captured from an angle rather than straight-on.",
    "Skin texture is realistic and detailed, with natural pores and soft highlights.",
    "No glam retouching, no plastic skin.",
    "Preserve all existing structure and design elements.",
]

LIGHTING = [
    "warm golden sunlight dominates the scene.",
    "cold blue moonlight dominates the scene.",
    "soft diffused window light dominates the scene.",
    "hard theatrical spotlight dominates the scene.",
]

EXPRESSION = [
    "She looks directly into the camera with a confident, dominant, arrogant expression.",
    "She glances away with a calm, thoughtful, distant expression.",
    "She smiles faintly with a warm, approachable, relaxed expression.",
]

MAKEUP = [
    "Maintain the makeup from the first image and add smokey eyes.",
    "Maintain the makeup from the first image and add natural eyes.",
    "Maintain the makeup from the first image and add bold red lips.",
]

APERTURES = ["f/1.4", "f/1.8", "f/2.0", "f/2.8", "f/4.0"]
LENSES = ["35mm prime", "50mm prime", "85mm prime", "135mm prime"]

STYLE_TAGS = (
    "hyper-realistic, cinematic lighting, fine-art portrait photography, "
    "editorial fashion portrait, natural skin texture, high dynamic range, "
    "moody color grading, shallow depth of field, photorealism"
)

NEGATIVE_TAGS = (
    "cartoon, illustration, anime, CGI, plastic skin, beauty filter, "
    "over-retouched face, exaggerated makeup, glossy skin, distorted anatomy, "
    "extra limbs, warped shoulders, unnatural pose, harsh flash, flat lighting, "
    "washed-out colors, oversharpening, blur, noise artifacts, watermark, logo, "
    "text, low resolution, grainy output"
)


def build_prompt(rng: random.Random) -> str:
    sentences = list(BASE_SENTENCES)
    rng.shuffle(sentences)
    body = " ".join(sentences[: rng.randint(4, len(sentences))])
    return "\n".join(
        [
            body,
            "",
            "Pose variation: " + rng.choice(BASE_SENTENCES[1:4]),
            "",
            rng.choice(EXPRESSION),
            rng.choice(MAKEUP),
            "",
            rng.choice(LIGHTING),
            "",
            "CAMERA & LENS SETTINGS",
            "•Camera: Full-frame mirrorless (cinema-grade look)",
            "•Lens: " + rng.choice(LENSES),
            "•Aperture: " + rng.choice(APERTURES) + " (soft background separation)",
            "•ISO: " + str(rng.choice([200, 400, 640, 800])),
            "•Aspect Ratio: 3:4 vertical",
            "",
            "STYLE & QUALITY TAGS",
            STYLE_TAGS,
            "",
            "NEGATIVE PROMPT",
            NEGATIVE_TAGS,
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Zielverzeichnis")
    parser.add_argument("--pairs", type=int, default=500, help="Anzahl Bild-/Prompt-Paare")
    parser.add_argument("--folders", type=int, default=5, help="Anzahl Unterordner")
    parser.add_argument("--size", type=int, default=64, help="Kantenlaenge der Bilder in Pixel")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--orphans", type=int, default=0, help="zusaetzliche Dateien ohne Gegenstueck"
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    root = Path(args.target)
    root.mkdir(parents=True, exist_ok=True)

    folders = [root / ("shoot_" + str(i + 1).zfill(3)) for i in range(max(1, args.folders))]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)

    for index in range(args.pairs):
        folder = folders[index % len(folders)]
        stem = "2026-09-01_" + str(index).zfill(6)
        extension = ".png" if index % 7 == 0 else ".jpg"

        colour = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        image = Image.new("RGB", (args.size, args.size), colour)
        image.save(folder / (stem + extension))
        (folder / (stem + ".txt")).write_text(build_prompt(rng), encoding="utf-8")

    for index in range(args.orphans):
        folder = folders[index % len(folders)]
        if index % 2 == 0:
            Image.new("RGB", (args.size, args.size), (10, 10, 10)).save(
                folder / ("orphan_image_" + str(index) + ".jpg")
            )
        else:
            (folder / ("orphan_prompt_" + str(index) + ".txt")).write_text(
                build_prompt(rng), encoding="utf-8"
            )

    print("erzeugt: " + str(args.pairs) + " Paare in " + str(len(folders)) + " Ordnern unter " + str(root))
    if args.orphans:
        print("dazu " + str(args.orphans) + " Dateien ohne Gegenstueck")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
