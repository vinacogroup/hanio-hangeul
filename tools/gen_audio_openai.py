#!/usr/bin/env python3
"""Regenerate the course audio in audio/ using the OpenAI TTS API.

The API key is read from the OPENAI_API_KEY environment variable and is never
written to disk or logged. Do not pass it as a command-line argument: arguments
are visible to other processes via `ps` and land in your shell history.

    export OPENAI_API_KEY=sk-...
    python3 tools/gen_audio_openai.py --dry-run     # list what would be made
    python3 tools/gen_audio_openai.py               # generate into audio/

Filenames are derived from the Korean text (가 -> ac00.m4a), so hangun.html
needs no changes: the page looks each item up by text in its inline CLIP_INDEX.

Requires ffmpeg for the AAC conversion and loudness pass. Install with
`brew install ffmpeg`, or use --format mp3 to skip the conversion entirely.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_URL = "https://api.openai.com/v1/audio/speech"
MODEL = "gpt-4o-mini-tts"
# OpenAI recommends marin and cedar as its best-quality voices; marin is female.
VOICE = "marin"

# The API returns 24 kHz audio. Keep that rate and give AAC enough bits to hold
# the high frequencies: at 16 kHz / 32 kbps the top of the spectrum is discarded
# and speech comes back sounding hoarse and gravelly.
SAMPLE_RATE = 24000
BITRATE_FFMPEG = "96k"
# afconvert rejects anything above 64 kbps at 24 kHz mono, so it gets the
# highest rate it will actually accept.
BITRATE_AFCONVERT = 64000

# The course teaches decoding, so each clip must be slow and distinctly
# articulated. gpt-4o-mini-tts takes free-text delivery instructions; this is
# what makes it usable for pronunciation teaching rather than narration.
VOICE_QUALITY = (
    "Use a bright, clear, smooth female voice with clean resonance. "
    "No breathiness, no rasp, no vocal fry, no creaky or husky quality. "
    "Speak as a professional language-textbook narrator recording studio audio. "
)
INSTRUCTIONS_DRILL = (
    VOICE_QUALITY
    + "This is textbook-standard Seoul Korean for an absolute beginner learning "
    "to read Hangul. Pronounce this single syllable very slowly and crisply, "
    "drawing the vowel out, with a neutral tone and no emotion. Articulate the "
    "consonant precisely: keep plain, aspirated and tense consonants clearly "
    "distinct. Do not add any extra sound, word or filler."
)
INSTRUCTIONS_WORD = (
    VOICE_QUALITY
    + "This is textbook-standard Seoul Korean for a beginner learner. Read this "
    "word slowly and clearly, noticeably slower than conversation but still "
    "connected, applying standard Korean pronunciation rules. Neutral tone. "
    "Do not add any extra sound, word or filler."
)
INSTRUCTIONS_SENTENCE = (
    VOICE_QUALITY
    + "This is textbook-standard Seoul Korean for a beginner learner. Read this "
    "sentence slowly and clearly, noticeably slower than normal conversation, "
    "with natural phrasing and a calm, neutral tone."
)

# Drill items are isolated sounds; words and sentences keep natural connected
# speech, because the liaison and sound-change lessons depend on it.
DRILL_KINDS = {"cons", "vowel", "pseudo"}
SENTENCE_KINDS = {"sentence"}

RETRIES = 4
TIMEOUT_S = 120


@dataclass(frozen=True)
class Item:
    text: str
    kind: str

    @property
    def stem(self) -> str:
        return "".join(f"{ord(c):04x}" for c in self.text if not c.isspace())

    @property
    def instructions(self) -> str:
        if self.kind in DRILL_KINDS:
            return INSTRUCTIONS_DRILL
        if self.kind in SENTENCE_KINDS:
            return INSTRUCTIONS_SENTENCE
        return INSTRUCTIONS_WORD

    @property
    def speed(self) -> float:
        # Stay at 1.0. The speed parameter time-stretches the rendered audio,
        # which smears consonants and adds a gravelly artefact; asking the model
        # to speak slowly in `instructions` gives real slow speech instead.
        return 1.0


def load_items(manifest_path: Path) -> list[Item]:
    """Read the existing manifest so regenerated files keep their filenames."""
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"Manifest not found: {manifest_path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"Manifest is not valid JSON ({manifest_path}): {exc}")

    items = [Item(text=text, kind=entry.get("kind", "word")) for text, entry in raw.items()]
    if not items:
        sys.exit(f"Manifest is empty: {manifest_path}")
    return items


def read_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit(
            "OPENAI_API_KEY is not set.\n"
            "  export OPENAI_API_KEY=sk-...    then re-run.\n"
            "Do not pass the key as an argument; it would leak via ps and shell history."
        )
    return key


def synthesize(item: Item, key: str, audio_format: str, voice: str) -> bytes:
    """Call the TTS API, retrying on rate limits and transient server errors."""
    payload = json.dumps(
        {
            "model": MODEL,
            "voice": voice,
            "input": item.text,
            "instructions": item.instructions,
            "speed": item.speed,
            "response_format": audio_format,
        }
    ).encode("utf-8")

    last_error = ""
    for attempt in range(RETRIES):
        request = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            # 401/400 will not fix themselves; fail fast with the API's own message.
            if exc.code in (400, 401, 403):
                sys.exit(f"API rejected the request ({exc.code}): {body}")
            last_error = f"HTTP {exc.code}: {body}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = f"network error: {exc}"

        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)

    raise RuntimeError(f"failed after {RETRIES} attempts ({last_error})")


def converter() -> str | None:
    """ffmpeg if present (it can also normalise loudness), else macOS afconvert."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    if shutil.which("afconvert"):
        return "afconvert"
    return None


def to_m4a(src: Path, dest: Path, tool: str) -> None:
    """Convert to mono 16 kHz AAC.

    With ffmpeg we also match loudness and trim edge silence. Without it some
    letters sound far louder than others and students keep reaching for the
    volume control. afconvert cannot do that, so the clips are left as the API
    returned them, which is acceptable because one voice generates them all.
    """
    if tool == "ffmpeg":
        command = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,silenceremove="
                   "start_periods=1:start_silence=0.05:start_threshold=-50dB:"
                   "stop_periods=1:stop_silence=0.08:stop_threshold=-50dB",
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "aac", "-b:a", BITRATE_FFMPEG,
            str(dest),
        ]
    else:
        command = [
            "afconvert", "-f", "m4af", "-d", "aac",
            "-b", str(BITRATE_AFCONVERT),
            str(src), str(dest),
        ]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{tool} failed: {result.stderr.strip()[:200]}")


def audition(voices: list[str], out_dir: Path, audio_format: str) -> None:
    """Render the same few items in several voices so a human can choose one.

    Picking a voice from its name is guesswork; hearing the actual drill
    syllables and a sentence takes a minute and costs about a cent.
    """
    samples = [
        Item("카", "cons"), Item("까", "cons"), Item("의", "vowel"),
        Item("한국어", "word"), Item("안녕하세요.", "sentence"),
    ]
    tool = converter() if audio_format == "m4a" else None
    if audio_format == "m4a" and tool is None:
        sys.exit("Need ffmpeg or afconvert to write .m4a. Install ffmpeg, or use --format mp3.")

    key = read_api_key()
    out_dir.mkdir(parents=True, exist_ok=True)
    chars = sum(len(i.text) for i in samples) * len(voices)
    print(f"Auditioning {len(voices)} voices x {len(samples)} clips "
          f"(~${chars / 1_000_000 * 15:.4f}) -> {out_dir}")

    for voice in voices:
        for item in samples:
            name = f"{voice}_{item.stem}"
            try:
                raw = synthesize(item, key, "wav" if audio_format == "m4a" else "mp3", voice)
                if audio_format == "mp3":
                    (out_dir / f"{name}.mp3").write_bytes(raw)
                else:
                    tmp = out_dir / f"{name}.wav"
                    tmp.write_bytes(raw)
                    to_m4a(tmp, out_dir / f"{name}.m4a", tool)
                    tmp.unlink()
            except (RuntimeError, OSError) as exc:
                print(f"  {voice} {item.text}: FAILED {exc}", file=sys.stderr)
        print(f"  {voice}  done")

    print(f"\nListen, then re-run with the winner:  --voice <name>")
    print(f"  for f in {out_dir}/*.{audio_format}; do echo \"$f\"; afplay \"$f\"; done")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio-dir", type=Path, default=Path(__file__).resolve().parent.parent / "audio")
    parser.add_argument("--voice", default=VOICE, help=f"OpenAI voice name (default: {VOICE})")
    parser.add_argument("--format", dest="audio_format", default="m4a", choices=["m4a", "mp3"],
                        help="m4a keeps the current filenames; mp3 skips ffmpeg but needs a page change")
    parser.add_argument("--only", nargs="*", metavar="KIND",
                        help="limit to these kinds: cons vowel word pseudo final rule sentence")
    parser.add_argument("--dry-run", action="store_true", help="print what would be generated, call nothing")
    parser.add_argument("--audition", nargs="*", metavar="VOICE",
                        help="generate a short sample per voice into audio-audition/ "
                             "so you can pick one before the full run "
                             "(default: marin cedar shimmer nova sage coral)")
    args = parser.parse_args()

    if args.audition is not None:
        audition(args.audition or ["marin", "cedar", "shimmer", "nova", "sage", "coral"],
                 args.audio_dir.parent / "audio-audition", args.audio_format)
        return

    voice: str = args.voice
    audio_dir: Path = args.audio_dir
    items = load_items(audio_dir / "manifest.json")
    if args.only:
        items = [i for i in items if i.kind in set(args.only)]
        if not items:
            sys.exit(f"No items match --only {' '.join(args.only)}")

    chars = sum(len(i.text) for i in items)
    print(f"{len(items)} clips, {chars} characters, ~${chars / 1_000_000 * 15:.4f} at $15/1M")
    print(f"model={MODEL} voice={voice} format={args.audio_format} -> {audio_dir}")

    if args.dry_run:
        for i in items[:10]:
            print(f"  {i.text}  [{i.kind}]  speed={i.speed}  -> {i.stem}.{args.audio_format}")
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more")
        return

    tool = converter() if args.audio_format == "m4a" else None
    if args.audio_format == "m4a" and tool is None:
        sys.exit("Need ffmpeg or afconvert to write .m4a. Install ffmpeg, or use --format mp3.")
    if tool == "afconvert":
        print("note: using afconvert; install ffmpeg for loudness matching across clips")

    key = read_api_key()
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Write to a staging directory so a mid-run failure never leaves audio/ half
    # replaced, with some clips in the new voice and some in the old.
    staging = Path(tempfile.mkdtemp(prefix="tts-", dir=audio_dir.parent))
    failures: list[tuple[str, str]] = []
    try:
        for index, item in enumerate(items, 1):
            label = f"[{index}/{len(items)}] {item.text}"
            try:
                # Ask for wav when converting, so ffmpeg starts from lossless input.
                raw = synthesize(item, key, "wav" if args.audio_format == "m4a" else "mp3", voice)
                if args.audio_format == "mp3":
                    (staging / f"{item.stem}.mp3").write_bytes(raw)
                else:
                    tmp_wav = staging / f"{item.stem}.wav"
                    tmp_wav.write_bytes(raw)
                    to_m4a(tmp_wav, staging / f"{item.stem}.m4a", tool)
                    tmp_wav.unlink()
                print(f"{label}  ok")
            except (RuntimeError, OSError) as exc:
                print(f"{label}  FAILED: {exc}", file=sys.stderr)
                failures.append((item.text, str(exc)))

        if failures:
            print(f"\n{len(failures)} clip(s) failed, so nothing was replaced:", file=sys.stderr)
            for text, reason in failures[:5]:
                print(f"  {text}: {reason}", file=sys.stderr)
            if len(failures) > 5:
                print(f"  ... and {len(failures) - 5} more", file=sys.stderr)
            print(f"{audio_dir} still holds the previous clips. Fix the cause and re-run.",
                  file=sys.stderr)
            sys.exit(1)

        # Only now, with every clip in hand, replace the live files.
        for produced in staging.iterdir():
            shutil.move(str(produced), str(audio_dir / produced.name))
        print(f"\nDone. {len(items)} clips written to {audio_dir}")
        print("Verify in a browser: python3 -m http.server 8731, then open hangun.html")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
