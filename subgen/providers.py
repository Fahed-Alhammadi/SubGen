import json
from abc import ABC, abstractmethod
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NLLB_CODES = {
    "ar": "arb_Arab", "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn",
    "de": "deu_Latn", "it": "ita_Latn", "pt": "por_Latn", "ru": "rus_Cyrl",
    "tr": "tur_Latn", "zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang",
    "hi": "hin_Deva", "fa": "pes_Arab", "ur": "urd_Arab", "nl": "nld_Latn",
}


def request_json(url: str, payload: dict | None = None, timeout: int = 30) -> object:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - administrator configured URL
        return json.loads(response.read())


class Translator(ABC):
    @abstractmethod
    def translate(self, texts: list[str], source: str, target: str) -> list[str]: ...


class HuggingFaceTranslator(Translator):
    def __init__(self, model_path: str, device: str) -> None:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_path, local_files_only=True).to(device)

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if source == target:
            return texts
        if source not in NLLB_CODES or target not in NLLB_CODES:
            raise ValueError(f"Unsupported local translation pair: {source} -> {target}")
        self.tokenizer.src_lang = NLLB_CODES[source]
        result: list[str] = []
        for offset in range(0, len(texts), 16):
            batch = texts[offset : offset + 16]
            inputs = self.tokenizer(batch, return_tensors="pt", padding=True, truncation=True).to(self.device)
            generated = self.model.generate(
                **inputs,
                forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(NLLB_CODES[target]),
            )
            result.extend(self.tokenizer.batch_decode(generated, skip_special_tokens=True))
        return result


class OllamaTranslator(Translator):
    def __init__(self, url: str, model: str) -> None:
        self.url, self.model = url.rstrip("/"), model

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if source == target:
            return texts
        result: list[str] = []
        for offset in range(0, len(texts), 40):
            batch = texts[offset : offset + 40]
            entries = [{"id": index, "text": text} for index, text in enumerate(batch)]
            prompt = (
                f"Translate subtitle entries from {source} to {target}. Preserve meaning, tone, names, "
                "and entry count. Return only a JSON array with the same integer id and translated text. "
                f"Input: {json.dumps(entries, ensure_ascii=False)}"
            )
            response = request_json(
                f"{self.url}/api/chat",
                {"model": self.model, "stream": False, "format": "json", "messages": [{"role": "user", "content": prompt}]},
                timeout=600,
            )
            content = json.loads(response["message"]["content"])  # type: ignore[index]
            if isinstance(content, dict):
                content = content.get("translations", content.get("items", []))
            mapped = {int(item["id"]): str(item["text"]) for item in content}
            if set(mapped) != set(range(len(batch))):
                raise ValueError(f"Ollama returned an incomplete translation near segment {offset}")
            result.extend(mapped[index] for index in range(len(batch)))
        return result


class LibreTranslateTranslator(Translator):
    def __init__(self, url: str, api_key: str = "") -> None:
        self.url, self.api_key = url.rstrip("/"), api_key

    def translate(self, texts: list[str], source: str, target: str) -> list[str]:
        if source == target:
            return texts
        results: list[str] = []
        for text in texts:
            payload = {"q": text, "source": source, "target": target, "format": "text"}
            if self.api_key:
                payload["api_key"] = self.api_key
            response = request_json(f"{self.url}/translate", payload)
            results.append(str(response["translatedText"]))  # type: ignore[index]
        return results


def test_provider(kind: str, values: dict[str, str]) -> dict[str, object]:
    try:
        if kind == "ollama":
            response = request_json(f"{values['ollama_url'].rstrip('/')}/api/tags", timeout=10)
            models = [item["name"] for item in response.get("models", [])]  # type: ignore[union-attr]
            return {"ok": True, "message": f"Connected · {len(models)} model(s)", "models": models}
        if kind == "libretranslate":
            response = request_json(f"{values['libretranslate_url'].rstrip('/')}/languages", timeout=10)
            return {"ok": True, "message": f"Connected · {len(response)} language(s)"}  # type: ignore[arg-type]
        if kind == "huggingface":
            return {"ok": True, "message": "Local model provider is available"}
        raise ValueError("Unknown provider")
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
