"""Turn raw Whisper output into readable subtitles.

Whisper segments can last up to 30 seconds and contain several sentences. This module rebuilds cues
from word timestamps so each subtitle respects line-length, line-count and duration limits, breaks
at natural points (sentence ends, commas, pauses), and never overlaps its neighbours. It also removes
common hallucinations and redistributes translated text across the cues of each source segment.
"""

import math
import re
from dataclasses import dataclass, field

SENTENCE_END = re.compile(r"[.!?…。！？؟]['\"”’»)\]]*$")
CLAUSE_END = re.compile(r"[,;:，、؛،—–]['\"”’»)\]]*$")
_NORMALIZE = re.compile(r"[\W_]+", re.UNICODE)

# Text Whisper commonly invents over silence, music or credits (matched after normalisation).
ALWAYS_HALLUCINATED = [
    "thanks for watching", "thank you for watching", "thank you so much for watching",
    "please subscribe", "like and subscribe", "dont forget to subscribe", "subscribe to my channel",
    "subtitles by the amara org community", "amara org", "www mooji org",
    "nancy qanqar", "نانسي قنقر", "اشتركوا في القناة", "ترجمة نانسي",
    "ご視聴ありがとうございました", "チャンネル登録", "字幕由amara org社区提供", "请不吝点赞",
    "明镜与点点", "시청해주셔서 감사합니다", "구독과 좋아요", "untertitel im auftrag des zdf",
    "untertitel der amara org community", "sous titres réalisés par la communauté d amara org",
    "subtítulos realizados por la comunidad de amara org", "продолжение следует",
    "редактор субтитров",
]
# Credit lines: only hallucinations when the segment starts with them and is short.
CREDIT_PREFIXES = ("subtitled by", "subtitles by", "captions by", "captioned by", "transcribed by",
                   "transcription by", "translated by", "translation by", "sync by", "synced by")
# Short phrases that are only hallucinations when Whisper itself was unsure.
SUSPICIOUS_WHEN_UNSURE = {"you", "thank you", "thanks", "bye", "okay", "so", "oh", "uh", "um", "hmm", "شكرا"}


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 1.0


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str
    segment: int = 0


@dataclass
class SegmentationOptions:
    max_line_chars: int = 42
    max_lines: int = 2
    max_cue_ms: int = 6000
    min_cue_ms: int = 1000
    min_gap_ms: int = 80
    linger_ms: int = 250
    pause_split_ms: int = 700
    unspaced: bool = False

    @property
    def max_chars(self) -> int:
        return self.max_line_chars * self.max_lines


def _normalized(text: str) -> str:
    return _NORMALIZE.sub(" ", text.lower()).strip()


def is_hallucination(segment: Segment) -> bool:
    text = _normalized(segment.text)
    if not text:
        return True
    if any(phrase in text for phrase in ALWAYS_HALLUCINATED):
        return True
    if text.startswith(CREDIT_PREFIXES) and len(text.split()) <= 8:
        return True
    unsure = segment.no_speech_prob > 0.4 or segment.avg_logprob < -0.9
    if text in SUSPICIOUS_WHEN_UNSURE and unsure:
        return True
    # Whisper's own "probably silence" rule, applied again because VAD-less runs keep these.
    if segment.no_speech_prob > 0.75 and segment.avg_logprob < -1.0:
        return True
    return segment.compression_ratio > 2.8 and len(text) > 30


def clean_segments(segments: list[Segment], filter_hallucinations: bool = True) -> list[Segment]:
    """Drop empty/hallucinated segments and collapse runaway repetitions (3+ identical in a row)."""
    result: list[Segment] = []
    run_text, run_length = "", 0
    for segment in segments:
        segment.text = segment.text.strip()
        if not segment.text or segment.end <= segment.start:
            continue
        if filter_hallucinations and is_hallucination(segment):
            continue
        key = _normalized(segment.text)
        run_length = run_length + 1 if key == run_text else 1
        run_text = key
        if filter_hallucinations and run_length >= 3:
            continue
        result.append(segment)
    return result


def _join(words: list[Word]) -> str:
    # faster-whisper words carry their own leading space in spaced languages, none in CJK/Thai.
    return re.sub(r"\s+", " ", "".join(w.text for w in words)).strip()


def _best_break(words: list[Word]) -> int | None:
    """Index after which to break ``words`` (keeping at least a third of it), or None."""
    minimum = max(1, len(words) // 3)
    for pattern in (SENTENCE_END, CLAUSE_END):
        for index in range(len(words) - 2, minimum - 2, -1):
            if pattern.search(words[index].text.strip()):
                return index
    return None


def _segment_words(segment: Segment) -> list[Word]:
    words = [w for w in segment.words if w.text.strip()]
    if words:
        return words
    # No word timestamps: approximate them by spreading the segment duration over characters.
    tokens = segment.text.split() if " " in segment.text.strip() else list(segment.text.strip())
    if not tokens:
        return []
    spaced = " " in segment.text.strip()
    total = sum(len(t) for t in tokens) or 1
    duration, cursor, result = segment.end - segment.start, segment.start, []
    for token in tokens:
        span = duration * len(token) / total
        result.append(Word(cursor, cursor + span, (" " + token) if spaced else token))
        cursor += span
    return result


def build_cues(segments: list[Segment], options: SegmentationOptions) -> list[Cue]:
    """Split segments into cues by length, duration, pauses and punctuation (no timing fixes yet)."""
    cues: list[Cue] = []
    pause = options.pause_split_ms / 1000
    max_duration = options.max_cue_ms / 1000
    sentence_min = max(12, int(options.max_chars * 0.3))
    clause_min = int(options.max_chars * 0.6)

    for index, segment in enumerate(segments):
        current: list[Word] = []

        def flush(upto: int | None = None, seg: int = index) -> None:
            nonlocal current
            take = current if upto is None else current[: upto + 1]
            rest = [] if upto is None else current[upto + 1 :]
            text = _join(take)
            if text:
                cues.append(Cue(int(take[0].start * 1000), int(take[-1].end * 1000), text, seg))
            current = rest

        for word in _segment_words(segment):
            if current:
                candidate = _join([*current, word])
                too_long = len(candidate) > options.max_chars
                too_slow = word.end - current[0].start > max_duration
                paused = word.start - current[-1].end > pause
                if paused:
                    flush()
                elif too_long or too_slow:
                    flush(_best_break(current))
                    # The carried-over words may still be too long together with this word.
                    while current and (
                        len(_join([*current, word])) > options.max_chars
                        or word.end - current[0].start > max_duration
                    ):
                        flush()
                else:
                    previous = current[-1].text.strip()
                    length = len(_join(current))
                    if (SENTENCE_END.search(previous) and length >= sentence_min) or (
                        CLAUSE_END.search(previous) and length >= clause_min
                    ):
                        flush()
            current.append(word)
        if current:
            flush()
    return cues


def wrap_text(text: str, max_line_chars: int, max_lines: int, unspaced: bool = False) -> str:
    """Balance ``text`` over as few lines as possible, preferring breaks at punctuation."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_line_chars:
        return text
    lines_needed = max(2, math.ceil(len(text) / max_line_chars))
    if lines_needed > max_lines >= 2 and len(text) <= max_line_chars * max_lines * 1.35:
        lines_needed = max_lines  # slightly long lines read better than an extra line
    if unspaced or " " not in text:
        size = math.ceil(len(text) / lines_needed)
        return "\n".join(text[i : i + size] for i in range(0, len(text), size))
    words = text.split(" ")
    if lines_needed == 2:
        best, best_score = 1, math.inf
        for split in range(1, len(words)):
            left, right = " ".join(words[:split]), " ".join(words[split:])
            score = max(len(left), len(right))
            if CLAUSE_END.search(words[split - 1]) or SENTENCE_END.search(words[split - 1]):
                score -= 4  # small preference for breaking after punctuation
            if score < best_score:
                best, best_score = split, score
        return " ".join(words[:best]) + "\n" + " ".join(words[best:])
    target = len(text) / lines_needed
    lines, line = [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if line and len(candidate) > target and len(lines) < lines_needed - 1:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    return "\n".join(lines)


def _merge_short(cues: list[Cue], options: SegmentationOptions) -> list[Cue]:
    """Merge cues too brief to read into a close neighbour when the combined text still fits."""
    joiner = "" if options.unspaced else " "
    limit = int(options.max_chars * 1.1)
    result = list(cues)
    index = 0
    while index < len(result):
        cue = result[index]
        if cue.end_ms - cue.start_ms >= options.min_cue_ms:
            index += 1
            continue
        nxt = result[index + 1] if index + 1 < len(result) else None
        prev = result[index - 1] if index > 0 else None
        room_after = nxt.start_ms - cue.end_ms if nxt else options.min_cue_ms
        if room_after >= options.min_cue_ms - (cue.end_ms - cue.start_ms) + options.min_gap_ms:
            index += 1  # finalize can simply extend it
            continue
        if nxt and nxt.start_ms - cue.end_ms <= options.pause_split_ms and len(cue.text) + len(nxt.text) < limit:
            result[index] = Cue(cue.start_ms, nxt.end_ms, f"{cue.text}{joiner}{nxt.text}", cue.segment)
            del result[index + 1]
            continue
        if prev and cue.start_ms - prev.end_ms <= options.pause_split_ms and len(prev.text) + len(cue.text) < limit:
            result[index - 1] = Cue(prev.start_ms, cue.end_ms, f"{prev.text}{joiner}{cue.text}", prev.segment)
            del result[index]
            continue
        index += 1
    return result


def finalize(cues: list[Cue], options: SegmentationOptions) -> list[Cue]:
    """Fix ordering, overlaps, gaps and minimum durations, add linger time, and wrap lines."""
    ordered = sorted((c for c in cues if c.text.strip()), key=lambda c: (c.start_ms, c.end_ms))
    ordered = _merge_short(ordered, options)
    longest = max(options.max_cue_ms + options.linger_ms, options.min_cue_ms)
    result: list[Cue] = []
    for index, cue in enumerate(ordered):
        start = max(0, cue.start_ms)
        if result:
            start = max(start, result[-1].end_ms + options.min_gap_ms)
        natural_end = max(cue.end_ms, start + 1)
        end = max(natural_end + options.linger_ms, start + options.min_cue_ms)
        if index + 1 < len(ordered):
            limit = ordered[index + 1].start_ms - options.min_gap_ms
            if limit > start:
                end = min(end, limit)  # never run into the next subtitle
        end = max(start + 1, min(end, start + longest))
        text = wrap_text(cue.text, options.max_line_chars, options.max_lines, options.unspaced)
        result.append(Cue(start, end, text, cue.segment))
    return result


def _split_tokens(text: str, unspaced: bool) -> tuple[list[str], str]:
    text = text.strip()
    if unspaced:
        return list(text.replace(" ", "")), ""
    return text.split(), " "


def distribute(text: str, weights: list[int], unspaced: bool = False) -> list[str]:
    """Split translated ``text`` into ``len(weights)`` parts proportional to the source cue lengths.

    Returns fewer parts when the translation has fewer tokens than cues; callers merge those cues.
    Breaks prefer punctuation near each proportional boundary.
    """
    tokens, joiner = _split_tokens(text, unspaced)
    parts = len(weights)
    if parts <= 1 or len(tokens) <= 1:
        return [text.strip()] if text.strip() else []
    parts = min(parts, len(tokens))
    weights = weights[:parts] if parts < len(weights) else weights
    total_weight = sum(max(1, w) for w in weights)
    offsets, position = [], 0
    for token in tokens:
        position += len(token) + len(joiner)
        offsets.append(position)
    total_chars = offsets[-1]
    boundaries: list[int] = []
    cumulative = 0
    for weight in weights[:-1]:
        cumulative += max(1, weight)
        target = total_chars * cumulative / total_weight
        window = total_chars * 0.12
        low = boundaries[-1] + 1 if boundaries else 0
        high = len(tokens) - (parts - len(boundaries) - 1)
        candidates = range(low, max(low + 1, high))
        best = min(candidates, key=lambda i: abs(offsets[i] - target))
        punctuated = [
            i for i in candidates
            if abs(offsets[i] - target) <= window and (SENTENCE_END.search(tokens[i]) or CLAUSE_END.search(tokens[i]))
        ]
        if punctuated:
            best = min(punctuated, key=lambda i: abs(offsets[i] - target))
        boundaries.append(best)
    pieces, start = [], 0
    for boundary in [*boundaries, len(tokens) - 1]:
        pieces.append(joiner.join(tokens[start : boundary + 1]).strip())
        start = boundary + 1
    return [p for p in pieces if p]


def apply_translation(cues: list[Cue], segment_texts: list[str], unspaced: bool) -> list[Cue]:
    """Replace each segment's cues with the translated segment text spread across them."""
    by_segment: dict[int, list[Cue]] = {}
    for cue in cues:
        by_segment.setdefault(cue.segment, []).append(cue)
    result: list[Cue] = []
    for segment_index, group in sorted(by_segment.items()):
        translated = segment_texts[segment_index].strip() if segment_index < len(segment_texts) else ""
        if not translated:
            continue
        pieces = distribute(translated, [len(c.text) for c in group], unspaced)
        if len(pieces) < len(group):
            # Fewer translated chunks than cues: merge the trailing cues so timing still matches.
            merged = group[: len(pieces) - 1] + [
                Cue(group[len(pieces) - 1].start_ms, group[-1].end_ms, "", segment_index)
            ] if pieces else []
            group = merged
        for cue, piece in zip(group, pieces, strict=False):
            result.append(Cue(cue.start_ms, cue.end_ms, piece, segment_index))
    return result


def segment_texts(cues: list[Cue], count: int, unspaced: bool = False) -> list[str]:
    """Source text per segment, rebuilt from cues so it reflects any cleaning."""
    joiner = "" if unspaced else " "
    texts = [""] * count
    for cue in cues:
        texts[cue.segment] = f"{texts[cue.segment]}{joiner}{cue.text}" if texts[cue.segment] else cue.text
    return texts


def assign_translation_units(cues: list[Cue], max_chars: int = 160) -> int:
    """Group cues into sentence-sized translation units, stored in ``cue.segment``; returns the count.

    Whole Whisper segments can hold several sentences (or, without punctuation, 30 seconds of speech);
    sentence-level MT models silently drop content from such long inputs. Units never cross a Whisper
    segment, end after sentence punctuation and are capped at ``max_chars`` characters.
    """
    unit, length, previous_segment, previous_text = -1, 0, None, ""
    for cue in cues:
        if (
            cue.segment != previous_segment or SENTENCE_END.search(previous_text)
            or length + len(cue.text) > max_chars
        ):
            unit, length = unit + 1, 0
        previous_segment, previous_text = cue.segment, cue.text.strip()
        length += len(cue.text) + 1
        cue.segment = unit
    return unit + 1


_ARABIC_MARKS = re.compile("[ً-ْٰۖ-ۭ]")  # harakat, tanween, sukun, Quranic marks
ARABIC_SCRIPT = {"ar", "fa", "ur", "ps", "sd", "ckb"}
NOUN_CAPITALISING = {"de", "lb"}


def match_source_case(source: str, translated: str, language: str = "") -> str:
    """Lower-case a translation's capitalised first letter when the source unit continues a sentence.

    MT models capitalise every input as if it started a sentence ("and about The people...").
    Only an ordinary capitalised word is changed: "I", acronyms and uncased scripts are left alone, and
    so is German, which capitalises every noun.
    """
    source, translated = source.lstrip(), translated.lstrip()
    if language in NOUN_CAPITALISING or len(translated) < 2 or not source[:1].islower():
        return translated
    first, second = translated[0], translated[1]
    if first.isupper() and second.islower():
        return first.lower() + translated[1:]
    return translated


def normalize_script(text: str, language: str) -> str:
    """Subtitle conventions per script: Arabic-script subtitles are written without vowel marks."""
    if language in ARABIC_SCRIPT:
        text = _ARABIC_MARKS.sub("", text)
    # MT output often puts a space before punctuation ("الجسر ، قهوة"). French keeps it before ;:!?
    marks = ",،.؟…" if language == "fr" else ",،.؟…!?;:؛"
    return re.sub(rf"[ \t]+([{re.escape(marks)}])", r"\1", text)
