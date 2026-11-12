"""Translation providers. All run locally or against administrator-configured private services."""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import languages

logger = logging.getLogger(__name__)
Progress = Callable[[int, int], None]

# Kept for backwards compatibility with integrations importing it.
NLLB_CODES = {code: item.nllb for code, item in languages.LANGUAGES.items() if item.nllb}


def request_json(url: str, payload: dict | None = None, timeout: int = 30, headers: dict | None = None) -> Any:
    if not url.startswith(("http://", "https://")):
        raise ValueError("Provider URL must start with http:// or https://")
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - administrator configured URL
        return json.loads(response.read())


class Translator(ABC):
    @abstractmethod
    def translate(
        self, texts: list[str], source: str, target: str, progress: Progress | None = None
    ) -> list[str]: ...


def _report(progress: Progress | None, done: int, total: int) -> None:
    if progress:
        progress(done, total)


class HuggingFaceTranslator(Translator):
    """Local seq2seq translation with NLLB-200, M2M100 or Marian (OPUS-MT) checkpoints."""

    def __init__(self, model_path: str, device: str, batch_size: int = 16) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.device, self.batch_size, self.path = device, batch_size, model_path
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        dtype = torch.float16 if device == "cuda" else torch.float32
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            model_path, local_files_only=True, dtype=dtype
        ).to(device)
        self.model.eval()
        tokenizer_name = type(self.tokenizer).__name__.lower()
        model_type = getattr(self.model.config, "model_type", "")
        if "nllb" in tokenizer_name:
            self.family = "nllb"
        elif model_type == "m2m_100":
            self.family = "m2m100"
        elif model_type == "marian":
            self.family = "marian"
        else:
            self.family = "generic"
        logger.info("Loaded %s translation model from %s on %s", self.family, model_path, device)

    def _marian_pair(self) -> tuple[str | None, str | None]:
        match = re.search(r"opus-mt-([a-z]{2,3}(?:_[a-z]+)?)-([a-z]{2,3}(?:_[a-z]+)?)$", Path(self.path).name, re.I)
        if not match:
            return None, None
        return languages.normalize(match.group(1)), languages.normalize(match.group(2))

    def _prepare(self, source: str, target: str) -> dict[str, Any]:
        if self.family == "nllb":
            src, tgt = languages.nllb_code(source), languages.nllb_code(target)
            if not src or not tgt:
                missing = source if not src else target
                raise ValueError(f"The local NLLB model does not support '{languages.name(missing)}'")
            self.tokenizer.src_lang = src
            return {"forced_bos_token_id": self.tokenizer.convert_tokens_to_ids(tgt)}
        if self.family == "m2m100":
            src, tgt = source.split("-")[0], target.split("-")[0]
            lang_ids = getattr(self.tokenizer, "lang_code_to_id", {}) or {}
            if lang_ids and (f"__{src}__" not in lang_ids and src not in lang_ids):
                raise ValueError(f"The local M2M100 model does not support '{languages.name(source)}'")
            self.tokenizer.src_lang = src
            return {"forced_bos_token_id": self.tokenizer.get_lang_id(tgt)}
        if self.family == "marian":
            model_source, model_target = self._marian_pair()
            if model_target and model_target != languages.normalize(target):
                raise ValueError(
                    f"The Marian model {Path(self.path).name} translates into "
                    f"{languages.name(model_target)}, not {languages.name(target)}"
                )
            if model_source and model_source != languages.normalize(source):
                raise ValueError(
                    f"The Marian model {Path(self.path).name} expects "
                    f"{languages.name(model_source)} speech, but {languages.name(source)} was detected"
                )
            # Multi-target OPUS-MT models need a ">>xxx<<" target token at the start of each input.
            codes = set(getattr(self.tokenizer, "supported_language_codes", None) or [])
            if codes:
                language = languages.LANGUAGES.get(target)
                candidates = [target, *(language.aliases if language else ())]
                token = next((f">>{c}<<" for c in candidates if f">>{c}<<" in codes), None)
                if token is None:
                    raise ValueError(f"The Marian model {Path(self.path).name} cannot produce {languages.name(target)}")
                return {"_prefix": f"{token} "}
        return {}

    def translate(self, texts: list[str], source: str, target: str, progress: Progress | None = None) -> list[str]:
        import torch

        if languages.same_language(source, target) or not texts:
            return texts
        extra = self._prepare(source, target)
        prefix = extra.pop("_prefix", "")
        result: list[str] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = [prefix + text for text in texts[offset : offset + self.batch_size]]
            inputs = self.tokenizer(
                batch, return_tensors="pt", padding=True, truncation=True, max_length=512
            ).to(self.device)
            longest = int(inputs["input_ids"].shape[1])
            with torch.inference_mode():
                generated = self.model.generate(
                    **inputs, **extra, num_beams=4, max_new_tokens=min(512, longest * 2 + 16)
                )
            result.extend(t.strip() for t in self.tokenizer.batch_decode(generated, skip_special_tokens=True))
            _report(progress, len(result), len(texts))
        return result


class OllamaTranslator(Translator):
    def __init__(self, url: str, model: str, temperature: float = 0.2, batch_size: int = 30) -> None:
        self.url, self.model, self.temperature, self.batch_size = url.rstrip("/"), model, temperature, batch_size

    def _chat(self, batch: list[str], source: str, target: str, context: list[str]) -> list[str]:
        entries = [{"id": index, "text": text} for index, text in enumerate(batch)]
        system = (
            "You are a professional subtitle translator. Translate each subtitle entry from "
            f"{languages.name(source)} to {languages.name(target)}. Keep the meaning, tone, register, names "
            "and line-level brevity of spoken dialogue. Do not merge, split, add or omit entries. Respond with "
            'JSON only: {"translations": [{"id": <int>, "text": "<translation>"}]}.'
        )
        prompt = ""
        if context:
            prompt += "Previous lines for context (do not translate): " + json.dumps(context, ensure_ascii=False) + "\n"
        prompt += "Entries: " + json.dumps(entries, ensure_ascii=False)
        response = request_json(
            f"{self.url}/api/chat",
            {"model": self.model, "stream": False, "format": "json",
             "options": {"temperature": self.temperature},
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
            timeout=900,
        )
        content = json.loads(response["message"]["content"])
        if isinstance(content, dict):
            content = content.get("translations", content.get("items", []))
        mapped = {int(item["id"]): str(item["text"]).strip() for item in content if isinstance(item, dict)}
        if set(mapped) != set(range(len(batch))):
            raise ValueError("incomplete response")
        return [mapped[index] for index in range(len(batch))]

    def _translate_batch(self, batch: list[str], source: str, target: str, context: list[str]) -> list[str]:
        try:
            return self._chat(batch, source, target, context)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            if len(batch) == 1:
                raise ValueError("Ollama returned an invalid translation for a subtitle entry") from None
            # Smaller batches are far more reliable with small local models.
            middle = len(batch) // 2
            first = self._translate_batch(batch[:middle], source, target, context)
            return first + self._translate_batch(batch[middle:], source, target, batch[:middle][-3:])

    def translate(self, texts: list[str], source: str, target: str, progress: Progress | None = None) -> list[str]:
        if languages.same_language(source, target) or not texts:
            return texts
        result: list[str] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            result.extend(self._translate_batch(batch, source, target, texts[max(0, offset - 3) : offset]))
            _report(progress, len(result), len(texts))
        return result


class LibreTranslateTranslator(Translator):
    def __init__(self, url: str, api_key: str = "", batch_size: int = 25) -> None:
        self.url, self.api_key, self.batch_size = url.rstrip("/"), api_key, batch_size

    def translate(self, texts: list[str], source: str, target: str, progress: Progress | None = None) -> list[str]:
        if languages.same_language(source, target) or not texts:
            return texts
        results: list[str] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            payload: dict[str, Any] = {"q": batch, "source": source, "target": target, "format": "text"}
            if self.api_key:
                payload["api_key"] = self.api_key
            response = request_json(f"{self.url}/translate", payload, timeout=300)
            translated = response.get("translatedText") if isinstance(response, dict) else None
            if isinstance(translated, str) and len(batch) == 1:
                translated = [translated]
            if not isinstance(translated, list) or len(translated) != len(batch):
                raise ValueError("LibreTranslate returned an unexpected response")
            results.extend(str(item) for item in translated)
            _report(progress, len(results), len(texts))
        return results


def translation_model_dir(repo_id: str) -> Path:
    from .models import model_path

    return model_path("translation", repo_id)


def test_provider(kind: str, values: dict[str, Any]) -> dict[str, object]:
    try:
        if kind == "ollama":
            response = request_json(f"{values['ollama_url'].rstrip('/')}/api/tags", timeout=10)
            models = [item["name"] for item in response.get("models", [])]
            message = f"Connected · {len(models)} model(s)"
            if values.get("ollama_model") and values["ollama_model"] not in models:
                message += f" · '{values['ollama_model']}' is not pulled yet"
            return {"ok": True, "message": message, "models": models}
        if kind == "libretranslate":
            response = request_json(f"{values['libretranslate_url'].rstrip('/')}/languages", timeout=10)
            return {"ok": True, "message": f"Connected · {len(response)} language(s)"}
        if kind == "huggingface":
            location = translation_model_dir(values["translation_model"])  # raises ValueError if invalid
            if not (location / "config.json").is_file():
                raise ValueError(f"Translation model is not installed: {values['translation_model']}")
            return {"ok": True, "message": f"{values['translation_model']} is installed and ready"}
        raise ValueError("Unknown provider")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
        reason = getattr(exc, "reason", None) or exc
        return {"ok": False, "message": f"Connection failed: {reason}"}
