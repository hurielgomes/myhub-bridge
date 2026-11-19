"""Leitura e validação simples de solicitações JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_job(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    suffix = path.suffix.lower()
    if suffix == ".txt":
        prompt = path.read_text(encoding="utf-8-sig")
        data: dict[str, Any] = {
            "id": path.stem,
            "project": "default",
            "purpose": "general",
            "chat_key": "default__general",
            "model": None,
            "prompt": prompt,
            "expected_outputs": [],
            "metadata": {"source_format": "txt"},
        }
    elif suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON da solicitação inválido ({exc}).") from exc
    else:
        raise ValueError("Formato de solicitação não suportado. Use .json ou .txt.")
    if not isinstance(data, dict):
        raise ValueError("A solicitação deve ser um objeto JSON.")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("O campo 'prompt' é obrigatório e não pode estar vazio.")
    data.setdefault("project", "default")
    data.setdefault("purpose", "general")
    data.setdefault("chat_key", f"{data['project']}__{data['purpose']}")
    data.setdefault("model", None)
    data.setdefault("expected_outputs", [])
    data.setdefault("metadata", {})
    data["_source_path"] = str(path)
    data["_job_id"] = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(data.get("id") or path.stem))[:80]
    return data
