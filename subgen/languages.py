"""Language metadata shared by transcription, translation, scanning and the UI.

Codes are ISO 639-1 where one exists (``zh-hant`` and ``yue`` are the exceptions). Each entry
records the English name, ISO 639-2 aliases used in media container tags, the NLLB-200 code used by
local translation, and the code Whisper expects (``None`` when Whisper cannot transcribe it).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    aliases: tuple[str, ...]
    nllb: str | None
    whisper: str | None


_TABLE: list[tuple[str, str, str, str | None, str | None]] = [
    # code, name, ISO 639-2 aliases (space separated), NLLB code, Whisper code
    ("af", "Afrikaans", "afr", "afr_Latn", "af"),
    ("am", "Amharic", "amh", "amh_Ethi", "am"),
    ("ar", "Arabic", "ara", "arb_Arab", "ar"),
    ("as", "Assamese", "asm", "asm_Beng", "as"),
    ("az", "Azerbaijani", "aze", "azj_Latn", "az"),
    ("ba", "Bashkir", "bak", "bak_Cyrl", "ba"),
    ("be", "Belarusian", "bel", "bel_Cyrl", "be"),
    ("bg", "Bulgarian", "bul", "bul_Cyrl", "bg"),
    ("bn", "Bengali", "ben", "ben_Beng", "bn"),
    ("bo", "Tibetan", "bod tib", "bod_Tibt", "bo"),
    ("br", "Breton", "bre", None, "br"),
    ("bs", "Bosnian", "bos", "bos_Latn", "bs"),
    ("ca", "Catalan", "cat", "cat_Latn", "ca"),
    ("ckb", "Kurdish (Sorani)", "ckb", "ckb_Arab", None),
    ("cs", "Czech", "ces cze", "ces_Latn", "cs"),
    ("cy", "Welsh", "cym wel", "cym_Latn", "cy"),
    ("da", "Danish", "dan", "dan_Latn", "da"),
    ("de", "German", "deu ger", "deu_Latn", "de"),
    ("el", "Greek", "ell gre", "ell_Grek", "el"),
    ("en", "English", "eng", "eng_Latn", "en"),
    ("es", "Spanish", "spa", "spa_Latn", "es"),
    ("et", "Estonian", "est", "est_Latn", "et"),
    ("eu", "Basque", "eus baq", "eus_Latn", "eu"),
    ("fa", "Persian", "fas per", "pes_Arab", "fa"),
    ("fi", "Finnish", "fin", "fin_Latn", "fi"),
    ("fil", "Filipino", "fil", "tgl_Latn", "tl"),
    ("fo", "Faroese", "fao", "fao_Latn", "fo"),
    ("fr", "French", "fra fre", "fra_Latn", "fr"),
    ("ga", "Irish", "gle", "gle_Latn", None),
    ("gl", "Galician", "glg", "glg_Latn", "gl"),
    ("gu", "Gujarati", "guj", "guj_Gujr", "gu"),
    ("ha", "Hausa", "hau", "hau_Latn", "ha"),
    ("haw", "Hawaiian", "haw", None, "haw"),
    ("he", "Hebrew", "heb iw", "heb_Hebr", "he"),
    ("hi", "Hindi", "hin", "hin_Deva", "hi"),
    ("hr", "Croatian", "hrv scr", "hrv_Latn", "hr"),
    ("ht", "Haitian Creole", "hat", "hat_Latn", "ht"),
    ("hu", "Hungarian", "hun", "hun_Latn", "hu"),
    ("hy", "Armenian", "hye arm", "hye_Armn", "hy"),
    ("id", "Indonesian", "ind in", "ind_Latn", "id"),
    ("ig", "Igbo", "ibo", "ibo_Latn", None),
    ("is", "Icelandic", "isl ice", "isl_Latn", "is"),
    ("it", "Italian", "ita", "ita_Latn", "it"),
    ("ja", "Japanese", "jpn", "jpn_Jpan", "ja"),
    ("jv", "Javanese", "jav jw", "jav_Latn", "jw"),
    ("ka", "Georgian", "kat geo", "kat_Geor", "ka"),
    ("kk", "Kazakh", "kaz", "kaz_Cyrl", "kk"),
    ("km", "Khmer", "khm", "khm_Khmr", "km"),
    ("kn", "Kannada", "kan", "kan_Knda", "kn"),
    ("ko", "Korean", "kor", "kor_Hang", "ko"),
    ("ku", "Kurdish (Kurmanji)", "kur kmr", "kmr_Latn", None),
    ("ky", "Kyrgyz", "kir", "kir_Cyrl", None),
    ("la", "Latin", "lat", None, "la"),
    ("lb", "Luxembourgish", "ltz", "ltz_Latn", "lb"),
    ("ln", "Lingala", "lin", "lin_Latn", "ln"),
    ("lo", "Lao", "lao", "lao_Laoo", "lo"),
    ("lt", "Lithuanian", "lit", "lit_Latn", "lt"),
    ("lv", "Latvian", "lav", "lvs_Latn", "lv"),
    ("mg", "Malagasy", "mlg", "plt_Latn", "mg"),
    ("mi", "Maori", "mri mao", "mri_Latn", "mi"),
    ("mk", "Macedonian", "mkd mac", "mkd_Cyrl", "mk"),
    ("ml", "Malayalam", "mal", "mal_Mlym", "ml"),
    ("mn", "Mongolian", "mon", "khk_Cyrl", "mn"),
    ("mr", "Marathi", "mar", "mar_Deva", "mr"),
    ("ms", "Malay", "msa may", "zsm_Latn", "ms"),
    ("mt", "Maltese", "mlt", "mlt_Latn", "mt"),
    ("my", "Burmese", "mya bur", "mya_Mymr", "my"),
    ("ne", "Nepali", "nep", "npi_Deva", "ne"),
    ("nl", "Dutch", "nld dut", "nld_Latn", "nl"),
    ("nn", "Norwegian Nynorsk", "nno", "nno_Latn", "nn"),
    ("no", "Norwegian", "nor nob nb", "nob_Latn", "no"),
    ("oc", "Occitan", "oci", "oci_Latn", "oc"),
    ("pa", "Punjabi", "pan", "pan_Guru", "pa"),
    ("pl", "Polish", "pol", "pol_Latn", "pl"),
    ("ps", "Pashto", "pus", "pbt_Arab", "ps"),
    ("pt", "Portuguese", "por", "por_Latn", "pt"),
    ("ro", "Romanian", "ron rum mol", "ron_Latn", "ro"),
    ("ru", "Russian", "rus", "rus_Cyrl", "ru"),
    ("rw", "Kinyarwanda", "kin", "kin_Latn", None),
    ("sa", "Sanskrit", "san", "san_Deva", "sa"),
    ("sd", "Sindhi", "snd", "snd_Arab", "sd"),
    ("si", "Sinhala", "sin", "sin_Sinh", "si"),
    ("sk", "Slovak", "slk slo", "slk_Latn", "sk"),
    ("sl", "Slovenian", "slv", "slv_Latn", "sl"),
    ("sn", "Shona", "sna", "sna_Latn", "sn"),
    ("so", "Somali", "som", "som_Latn", "so"),
    ("sq", "Albanian", "sqi alb", "als_Latn", "sq"),
    ("sr", "Serbian", "srp scc", "srp_Cyrl", "sr"),
    ("su", "Sundanese", "sun", "sun_Latn", "su"),
    ("sv", "Swedish", "swe", "swe_Latn", "sv"),
    ("sw", "Swahili", "swa", "swh_Latn", "sw"),
    ("ta", "Tamil", "tam", "tam_Taml", "ta"),
    ("te", "Telugu", "tel", "tel_Telu", "te"),
    ("tg", "Tajik", "tgk", "tgk_Cyrl", "tg"),
    ("th", "Thai", "tha", "tha_Thai", "th"),
    ("tk", "Turkmen", "tuk", "tuk_Latn", "tk"),
    ("tl", "Tagalog", "tgl", "tgl_Latn", "tl"),
    ("tr", "Turkish", "tur", "tur_Latn", "tr"),
    ("tt", "Tatar", "tat", "tat_Cyrl", "tt"),
    ("uk", "Ukrainian", "ukr", "ukr_Cyrl", "uk"),
    ("ur", "Urdu", "urd", "urd_Arab", "ur"),
    ("uz", "Uzbek", "uzb", "uzn_Latn", "uz"),
    ("vi", "Vietnamese", "vie", "vie_Latn", "vi"),
    ("xh", "Xhosa", "xho", "xho_Latn", None),
    ("yi", "Yiddish", "yid ji", "ydd_Hebr", "yi"),
    ("yo", "Yoruba", "yor", "yor_Latn", "yo"),
    ("yue", "Cantonese", "yue", "yue_Hant", "yue"),
    ("zh", "Chinese (Simplified)", "zho chi zh-hans chs", "zho_Hans", "zh"),
    ("zh-hant", "Chinese (Traditional)", "zh-tw cht", "zho_Hant", "zh"),
    ("zu", "Zulu", "zul", "zul_Latn", None),
]

LANGUAGES: dict[str, Language] = {
    code: Language(code, name, tuple(aliases.split()), nllb, whisper)
    for code, name, aliases, nllb, whisper in _TABLE
}

_LOOKUP: dict[str, str] = {}
for _language in LANGUAGES.values():
    for _key in (_language.code, _language.name.lower(), *_language.aliases):
        _LOOKUP.setdefault(_key.lower(), _language.code)
for _language in LANGUAGES.values():
    if _language.whisper:
        _LOOKUP.setdefault(_language.whisper, _language.code)
# Common English names used in subtitle file names ("Movie.English.srt").
_LOOKUP.update({"chinese": "zh", "portuguese (brazil)": "pt", "brazilian": "pt", "pt-br": "pt",
                "es-419": "es", "latino": "es", "farsi": "fa", "norwegian bokmal": "no"})

# Languages whose text is written without spaces between words.
UNSPACED = {"zh", "zh-hant", "yue", "ja", "th", "lo", "km", "my", "bo"}


def normalize(tag: str | None) -> str | None:
    """Map a container tag, ISO code, Whisper code or English name to a SubGen code."""
    if not tag:
        return None
    key = tag.strip().lower().replace("_", "-")
    if key in _LOOKUP:
        return _LOOKUP[key]
    base = key.split("-")[0]
    return _LOOKUP.get(base)


def name(code: str) -> str:
    language = LANGUAGES.get(code)
    return language.name if language else code


def nllb_code(code: str) -> str | None:
    language = LANGUAGES.get(code)
    return language.nllb if language else None


def whisper_code(code: str | None) -> str | None:
    if not code:
        return None
    language = LANGUAGES.get(code)
    return language.whisper if language else None


def is_valid_code(code: str) -> bool:
    return 0 < len(code) <= 20 and code.replace("-", "").isalnum() and code == code.lower()


def same_language(left: str | None, right: str | None) -> bool:
    """Compare languages, treating regional/script variants as the same spoken language."""
    if not left or not right:
        return False
    a, b = normalize(left) or left, normalize(right) or right
    if a == b:
        return True
    return a.split("-")[0] == b.split("-")[0] and {a, b} != {"zh", "zh-hant"}


def catalog() -> list[dict[str, object]]:
    return sorted(
        (
            {"code": x.code, "name": x.name, "translation": x.nllb is not None,
             "whisper": x.whisper is not None}
            for x in LANGUAGES.values()
        ),
        key=lambda item: str(item["name"]),
    )
