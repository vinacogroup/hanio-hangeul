# Chặng 0 – Đọc được tiếng Hàn

A self-contained Hangul reading course for Vietnamese beginners. `hangun.html` is the whole
application: 25 stages from syllable structure through to a timed final reading gate, with no
build step and no dependencies.

```
hangun.html        the course (open via a web server, see below)
audio/             110 pre-generated Korean pronunciation clips + manifest.json
tools/             gen_audio_openai.py — regenerate audio/ with the OpenAI TTS API
feedback.md        reviewer notes that drove the current lesson structure
```

## Regenerating the audio

`tools/gen_audio_openai.py` rebuilds every clip in `audio/` using OpenAI's
`gpt-4o-mini-tts`. It reads the API key from the environment and never stores or
prints it. Do not pass the key as an argument — arguments are visible to other
processes and land in shell history.

```bash
export OPENAI_API_KEY=sk-...
python3 tools/gen_audio_openai.py --dry-run     # show what would be made, calls nothing
python3 tools/gen_audio_openai.py --audition    # sample several voices first (~1 cent)
python3 tools/gen_audio_openai.py               # generate all 110 clips
python3 tools/gen_audio_openai.py --only cons vowel pseudo   # just the drills
python3 tools/gen_audio_openai.py --voice cedar              # use a specific voice
```

`--audition` renders the same few items (카, 까, 의, 한국어, 안녕하세요.) in each
candidate voice into `audio-audition/`, so the choice is made by listening
rather than by guessing from a name.

The whole course is 211 characters, about **$0.003** per full run.

Filenames are derived from the Korean text, so `hangun.html` needs no changes —
the page looks each item up by text in its inline `CLIP_INDEX`.

Delivery is tuned per item kind through the API's `instructions` parameter:
isolated syllables and pseudo-words are spoken slowly and distinctly with the
plain/aspirated/tense contrast called out explicitly, while words and sentences
keep natural connected speech so the liaison and sound-change lessons in stages
16–20 still teach the right thing.

Nothing is replaced until every clip succeeds. On any failure the script reports
what broke and leaves `audio/` exactly as it was, so a half-finished run can
never leave the course in two different voices.

`ffmpeg` is used when present, which also matches loudness across clips and
trims edge silence; otherwise it falls back to macOS `afconvert`, which converts
but cannot normalise. `brew install ffmpeg` is worth it for a final run — it also
allows a higher bitrate (96 kbps against afconvert's 64 kbps ceiling at 24 kHz
mono).

### If the voice sounds hoarse or gravelly

That is usually the encoder, not the voice. The first OpenAI run inherited the
old settings — 16 kHz at 32 kbps — which discards the top of the spectrum and
makes clean speech sound rasping. Current settings keep the API's native 24 kHz
and raise the bitrate, measured at 34 → 56 kbps on real speech.

The other cause is the `speed` parameter: values below 1.0 time-stretch audio
that has already been rendered, smearing consonants. The script now stays at 1.0
and asks for slow delivery through `instructions` instead, which produces genuine
slow speech. The instructions also request a bright, clear voice with no
breathiness or vocal fry.

## Running it

Serve the folder over HTTP — do not open `hangun.html` by double-clicking.

```bash
cd "$(dirname "$0")"
python3 -m http.server 8731
# then open http://localhost:8731/hangun.html
```

Opening the file directly via `file://` still works, but browsers block local media fetches,
so the bundled clips will not play and every sound falls back to the browser's own speech
synthesis. Any static host (Netlify, Vercel, S3, nginx, GitHub Pages) works for deployment.

If a host serves `.m4a` with an unusual MIME type, set it to `audio/mp4`.

## How audio works

Each spoken item is looked up in `CLIP_INDEX`, an inline map from Korean text to filename that
mirrors `audio/manifest.json`. When a clip exists it plays; otherwise the page falls back to
the browser's speech synthesis.

The fallback is structural, not a safety net. The blend practice stage builds a **random**
syllable from 19 leads × 21 vowels = 399 possibilities, and only 39 of those are bundled. The
final-gate passage is likewise a long unbundled string. The page also falls back if a clip
404s, fails to decode, or does not start within 1.5 seconds, so a control is never silent.

The map is inlined rather than fetched because the page's CSP `connect-src` omits `'self'`,
which blocks `fetch()` to its own origin. `media-src` does allow `'self'`, so playing the files
is fine.

Spell mode (`speak(text, true)`) splits a drill into syllables and plays each one separately so
individual letters stay audible. Real words and sentences are never split — that would destroy
the liaison and sound-change lessons in stages 16–20.

## Audio credits and licensing

The 110 clips were generated with Apple's **Yuna** Korean voice via the macOS `say` command:
`-r 120` for drills and pseudo-words, `-r 135` for words and sentences, converted with
`afconvert -f m4af -d aac -b 32000`, then loudness-normalised (RMS spread 1.04x) and
silence-trimmed. Filenames are the Unicode codepoints of the text (가 → `ac00.m4a`).

macOS lists nine `ko-KR` voices, but eight of them (Eddy, Flo, Grandma, Grandpa, Reed, Rocko,
Sandy, Shelley) barely synthesise Korean — they render 가 in 0.012 s against Yuna's 0.35 s.
That was the original cause of the "nuốt chữ" complaint, since the page previously let the
browser pick the first match. `hangun.html` now names Yuna first in `VOICE_PREFERENCE` and
rejects those voices by name for the fallback path too.

Verified on the generated set: the plain/aspirated/tense contrast is acoustically real (tense
syllables show lower noise and shorter duration); sound-change rules are applied correctly
(synthesising 한국어 gives byte-identical audio to 한구거, and 학교 to 학꾜, across all eight
rule pairs tested); and the 12 invented pseudo-words render cleanly.

### Before selling this course

**Apple's terms for redistributing audio generated by its system voices are UNVERIFIED.** The
voice is licensed for use on the Mac it ships with; bundling its output into a distributed or
sold product is a separate question that has not been confirmed. Internal or free use is low
risk. For commercial sale, confirm with Apple or regenerate the clips first.

Regenerating requires replacing only the files in `audio/`, keeping the same filenames —
`hangun.html` needs no changes. Two clean-licence options:

- **MeloTTS** (github.com/myshell-ai/MeloTTS) — MIT licence, official Korean support, runs
  offline on Apple Silicon in CPU mode. Its Korean path needs `python-mecab-ko`, which
  conflicts with the `mecab-python3` its Japanese module imports; expect to resolve that.
- **Google Cloud Text-to-Speech** (ko-KR Neural2 / Chirp3-HD) — its service terms treat
  generated output as customer data. The entire course is ~3,000 characters, far inside the
  free monthly allowance.

Ruled out during research: **Naver Clova Voice** (its policy requires live API calls and
forbids storing generated files), **Meta MMS-TTS Korean** (CC-BY-NC-4.0), **Coqui XTTS-v2**
(non-commercial, vendor defunct), **Forvo** (dropped CC licensing in 2019, forbids
redistribution), and **Piper**/**Kokoro** (no Korean voice exists).

24 native-speaker recordings from Wikimedia Commons (12 CC0, 12 CC BY-SA 4.0) were evaluated
but not used: they cover only ~26% of what the course speaks, and mixing them would change
voice between stages.
