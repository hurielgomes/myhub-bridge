"""Grava respostas e extrai blocos de código sem executar conteúdo gerado."""
from __future__ import annotations

import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

EXTENSIONS = {
    "json": ".json", "svg": ".svg", "xml": ".xml", "html": ".html",
    "css": ".css", "javascript": ".js", "js": ".js", "typescript": ".ts",
    "ts": ".ts", "python": ".py", "py": ".py", "bash": ".sh", "sh": ".sh",
    "bat": ".bat", "powershell": ".ps1", "ps1": ".ps1", "markdown": ".md",
    "md": ".md", "yaml": ".yaml", "yml": ".yml", "text": ".txt", "txt": ".txt",
}


def safe_filename(name: str, fallback: str) -> str:
    name = Path(str(name).strip().strip('"\'' )).name
    name = re.sub(r"[^\w. -]+", "_", name, flags=re.UNICODE).strip(" .")
    return name[:120] or fallback


def _filename_from_block(block: dict[str, Any], index: int, language: str) -> str:
    explicit = block.get("filename")
    content = str(block.get("content", ""))
    if not explicit:
        match = re.search(r"(?im)^\s*(?:#|//|<!--)\s*(?:filename|file|arquivo)\s*:\s*([^\r\n>]+)", content[:500])
        if match:
            explicit = match.group(1).strip()
    ext = EXTENSIONS.get(language.lower(), ".txt")
    return safe_filename(explicit or f"artefato_{index:02d}{ext}", f"artefato_{index:02d}.txt")


def validate_artifact(path: Path, language: str) -> dict[str, Any]:
    result: dict[str, Any] = {"valid": None, "validation": "não aplicável"}
    try:
        if language.lower() == "json" or path.suffix.lower() == ".json":
            json.loads(path.read_text(encoding="utf-8-sig"))
            result.update(valid=True, validation="JSON válido")
        elif language.lower() == "svg" or path.suffix.lower() == ".svg":
            root = ET.parse(path).getroot()
            if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                raise ValueError("A raiz XML não é <svg>.")
            result.update(valid=True, validation="XML/SVG bem-formado; renderização não testada")
        elif language.lower() in {"python", "py"} or path.suffix.lower() == ".py":
            result.update(valid=None, validation="código Python salvo, não executado por segurança")
    except Exception as exc:
        result.update(valid=False, validation=f"Falha na validação: {exc}")
    return result


def save_result(output_dir: Path, request: dict[str, Any], capture: dict[str, Any],
                *, provider: str, timestamp: str, chat_key: str,
                chat_url: str, selected_model: str | None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = output_dir / "artifacts"
    artifact_dir.mkdir(exist_ok=True)

    response_text = str(capture.get("text", "")).strip()
    (output_dir / "response.txt").write_text(response_text + "\n", encoding="utf-8")

    artifacts: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, original in enumerate(capture.get("code_blocks", []), start=1):
        block = dict(original)
        language = str(block.get("language") or "text").lower()
        filename = _filename_from_block(block, index, language)
        stem, suffix = Path(filename).stem, Path(filename).suffix
        candidate = filename
        counter = 2
        while candidate.lower() in used_names:
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        used_names.add(candidate.lower())
        path = artifact_dir / candidate
        path.write_text(str(block.get("content", "")), encoding="utf-8")
        metadata = {
            "filename": candidate,
            "language": language,
            "path": str(Path("artifacts") / candidate),
            **validate_artifact(path, language),
        }
        artifacts.append(metadata)

    # Anexos baixados diretamente pelo Playwright. Mantemos os arquivos na pasta
    # artifacts para que a Fábrica leia o SVG/JSON original, não apenas o texto da mensagem.
    for attachment in capture.get("attachments", []):
        source_path = Path(str(attachment.get("path") or ""))
        if not source_path.is_file():
            continue
        filename = safe_filename(attachment.get("filename") or source_path.name, source_path.name)
        candidate = filename
        stem, suffix = Path(filename).stem, Path(filename).suffix
        counter = 2
        while candidate.lower() in used_names:
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        used_names.add(candidate.lower())
        destination = artifact_dir / candidate
        try:
            if source_path.resolve() != destination.resolve():
                shutil.copy2(source_path, destination)
            language = destination.suffix.lower().lstrip(".") or "text"
            validation = validate_artifact(destination, language)
            artifacts.append({
                "filename": candidate,
                "language": language,
                "path": str(Path("artifacts") / candidate),
                "source": "download",
                "valid": validation.get("valid"),
                "validation": validation.get("validation"),
                "download_url": attachment.get("url", ""),
            })
        except Exception as exc:
            artifacts.append({
                "filename": candidate,
                "language": language if 'language' in locals() else "unknown",
                "path": str(Path("artifacts") / candidate),
                "source": "download",
                "valid": False,
                "validation": f"Falha ao copiar anexo: {exc}",
            })

    result = {
        "success": True,
        "provider": provider,
        "timestamp": timestamp,
        "request": request,
        "chat": {"key": chat_key, "url": chat_url, "model": selected_model},
        "response": response_text,
        "code_blocks": capture.get("code_blocks", []),
        "artifacts": artifacts,
        "attachment_diagnostics": capture.get("attachment_diagnostics", []),
        "notes": [
            "Arquivos gerados pela IA são tratados como dados; nenhum código foi executado.",
            "A validação de SVG verifica apenas a estrutura XML, não a aparência visual.",
        ],
    }
    (output_dir / "response.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
