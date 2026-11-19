"""Persistência local das conversas usadas pelo MyHub Bridge."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any


class ChatStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = {"version": 1, "chats": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("chats"), dict):
                    self.data = loaded
            except (OSError, json.JSONDecodeError):
                # Não sobrescreve silenciosamente um arquivo inválido.
                raise RuntimeError(f"Registro de conversas inválido: {self.path}")

    @staticmethod
    def normalize_key(value: str) -> str:
        key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value).strip()).strip("_.-")
        if not key:
            raise ValueError("chat_key vazio ou inválido.")
        return key[:100]

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "myhub.ia.br" or host.endswith(".myhub.ia.br")):
            raise ValueError("URL de conversa recusada: o endereço precisa pertencer a myhub.ia.br.")

    def get(self, key: str) -> dict[str, Any] | None:
        value = self.data["chats"].get(self.normalize_key(key))
        if not isinstance(value, dict):
            return None
        record = dict(value)
        url = str(record.get("url", ""))
        self._validate_url(url)
        return record

    def save(self, key: str, url: str, *, model: str | None = None,
             project: str | None = None, purpose: str | None = None) -> None:
        normalized = self.normalize_key(key)
        self._validate_url(url)
        current = self.data["chats"].get(normalized, {})
        record = dict(current) if isinstance(current, dict) else {}
        record.update({"url": url})
        if model:
            record["model"] = model
        if project:
            record["project"] = project
        if purpose:
            record["purpose"] = purpose
        self.data["chats"][normalized] = record
        self._write()

    def _write(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
