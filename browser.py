from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import unquote, unquote_to_bytes, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class MyHubBrowser:
    def __init__(self, config: dict[str, Any]):
        self.cfg = config
        root = Path(__file__).resolve().parent
        self.profile = root / config["paths"]["profile"]
        self.profile.mkdir(parents=True, exist_ok=True)
        self.pw = self.context = self.page = None
        self.last_capture: dict[str, Any] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def open(self, chat_url: str | None = None) -> None:
        self.pw = sync_playwright().start()
        self.context = self.pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile),
            headless=False,
            viewport=self.cfg["browser"]["viewport"],
            accept_downloads=True,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(chat_url or self.cfg["url"], wait_until="domcontentloaded")
        self.page.wait_for_timeout(1200)
        logging.info("Página aberta: %s", self.page.url)

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self.pw:
                self.pw.stop()
            self.context = self.pw = self.page = None

    def visible(self, selectors: list[str]):
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    def ensure_login(self, *, interactive: bool = True) -> None:
        indicators = self.cfg["selectors"].get("login_indicators", [])
        if self.visible(indicators):
            if not interactive:
                raise RuntimeError("O MyHub solicita login. Execute login.bat, autentique-se e tente novamente.")
            input("O MyHub parece solicitar login. Entre na conta na janela e pressione ENTER aqui...")
        if self.visible(indicators):
            raise RuntimeError("A página ainda parece estar na tela de login.")

    def navigate(self, chat_url: str | None = None) -> None:
        """Navega para outro chat sem reiniciar Playwright/Chromium."""
        target = chat_url or self.cfg["url"]
        if not self.page:
            raise RuntimeError("Navegador MyHub ainda não foi iniciado.")
        if self.page.url.rstrip("/") != target.rstrip("/"):
            self.page.goto(target, wait_until="domcontentloaded")
            self.page.wait_for_timeout(500)

    def composer(self):
        candidates = self.cfg["selectors"].get("composer", []) + [
            'textarea[placeholder]', "textarea",
            '[contenteditable="true"][role="textbox"]',
            '[contenteditable="true"]', '[role="textbox"]',
        ]
        locator = self.visible(candidates)
        if not locator:
            raise RuntimeError("Campo de mensagem não localizado. Ajuste selectors.composer em config.json.")
        return locator

    def _assistant_messages(self):
        custom = self.cfg["selectors"].get("assistant_messages", [])
        if custom:
            for selector in custom:
                try:
                    loc = self.page.locator(selector)
                    if loc.count():
                        return loc
                except Exception:
                    continue
        selector = (
            "//div[contains(concat(' ', normalize-space(@class), ' '), ' max-w-[812px] ')]"
            "/div[contains(concat(' ', normalize-space(@class), ' '), ' space-y-4 ')]"
            "/div[contains(concat(' ', normalize-space(@class), ' '), ' flex-col ') "
            "and not(contains(concat(' ', normalize-space(@class), ' '), ' max-w-[600px] '))]"
        )
        return self.page.locator(f"xpath={selector}")

    def assistant_capture(self) -> dict[str, Any]:
        """Captura texto e blocos de código da última mensagem do assistente."""
        try:
            messages = self._assistant_messages()
            for index in range(messages.count() - 1, -1, -1):
                message = messages.nth(index)
                content = message.locator(
                    "xpath=.//div[contains(concat(' ', normalize-space(@class), ' '), ' flex-1 ') "
                    "and contains(concat(' ', normalize-space(@class), ' '), ' min-w-0 ') "
                    "and contains(concat(' ', normalize-space(@class), ' '), ' break-words ')]"
                    "/div[contains(concat(' ', normalize-space(@class), ' '), ' space-y-4 ')]"
                )
                if not content.count():
                    content = message
                target = content.last if content.count() else message
                text = target.inner_text().strip()
                code_blocks: list[dict[str, str]] = []
                code_nodes = target.locator("pre")
                for code_index in range(code_nodes.count()):
                    pre = code_nodes.nth(code_index)
                    code = pre.locator("code")
                    source = code.first if code.count() else pre
                    code_text = (source.text_content() or "").strip("\r\n")
                    classes = " ".join(filter(None, [
                        source.get_attribute("class"), pre.get_attribute("class")
                    ]))
                    language_match = re.search(r"language-([\w+#.-]+)", classes, re.I)
                    language = language_match.group(1) if language_match else "text"
                    filename = (
                        source.get_attribute("data-filename")
                        or pre.get_attribute("data-filename")
                        or source.get_attribute("title")
                        or pre.get_attribute("title")
                    )
                    code_blocks.append({
                        "index": str(code_index + 1),
                        "language": language,
                        "filename": filename or "",
                        "content": code_text,
                    })
                has_attachment = self._message_has_download(message)
                if text or code_blocks or has_attachment:
                    return {"text": text, "code_blocks": code_blocks, "has_attachment": has_attachment}
        except Exception:
            logging.exception("Erro ao capturar mensagem do assistente")
        return {"text": "", "code_blocks": []}

    def assistant_text(self) -> str:
        return str(self.assistant_capture().get("text", ""))

    def _message_has_download(self, message) -> bool:
        selectors = [
            "a[download]", "a[href*='.svg' i]", "a[href*='.json' i]",
            "button[aria-label*='download' i]", "button[aria-label*='baixar' i]",
            "[role='button'][aria-label*='download' i]", "[role='button'][aria-label*='baixar' i]",
            "[data-testid*='download' i]", "[data-testid*='attachment' i]", "[data-testid*='file' i]",
            "button:has-text('Baixar')", "button:has-text('Download')",
            "[role='button']:has-text('Baixar')", "[role='button']:has-text('Download')",
        ]
        for selector in selectors:
            try:
                if message.locator(selector).count():
                    return True
            except Exception:
                continue
        return False

    def generating(self) -> bool:
        selectors = self.cfg["selectors"].get("stop_generating", []) + [
            'button[aria-label*="Parar" i]', 'button[aria-label*="Stop" i]',
            'button:has-text("Parar geração")', 'button:has-text("Stop generating")',
        ]
        return self.visible(selectors) is not None

    MODEL_LABELS = {
        "GPT-6 Luna": ["GPT-6 Luna"],
        "Claude Haiku 5.5": ["Claude Haiku 5.5"],
        "Gemini 2.5 Flash": ["Gemini 2.5 Flash"],
        "Gemini 3.8 Flash": ["Gemini 3.8 Flash"],
        "DeepSeek v4 Flash": ["DeepSeek v4 Flash", "Deepseak v4 Flash", "DeepSeek V4 Flash"],
        "Grok 4.3": ["Grok 4.3"],
        "MyHubNitro": ["MyHubNitro", "MyHub Nitro", "MyHub Nitro AI"],
    }

    @staticmethod
    def _model_text_variants(model_name: str) -> list[str]:
        known = MyHubBrowser.MODEL_LABELS
        if model_name in known:
            return known[model_name]
        folded = str(model_name).strip().casefold()
        for canonical, variants in known.items():
            if folded == canonical.casefold() or any(folded == v.casefold() for v in variants):
                return variants
        return [str(model_name).strip()]

    def _current_model_trigger(self):
        """Localiza o botão do modelo atualmente exibido no cabeçalho do chat."""
        names = [variant for variants in self.MODEL_LABELS.values() for variant in variants]
        # O seletor fica no cabeçalho; restringimos por texto conhecido para não clicar
        # em menções ao nome do modelo dentro do histórico da conversa.
        for name in names:
            for selector in (f'button:has-text("{name}")', f'[role="button"]:has-text("{name}")'):
                try:
                    loc = self.page.locator(selector).first
                    if loc.count() and loc.is_visible():
                        box = loc.bounding_box()
                        if box and box.get("y", 9999) < 180:
                            return loc
                except Exception:
                    continue
        # Algumas versões do site usam um elemento textual dentro do botão.
        for name in names:
            try:
                text_loc = self.page.get_by_text(name, exact=True).first
                if not text_loc.count() or not text_loc.is_visible():
                    continue
                for xpath in (
                    "xpath=ancestor-or-self::button[1]",
                    "xpath=ancestor-or-self::*[@role='button'][1]",
                ):
                    parent = text_loc.locator(xpath)
                    if parent.count() and parent.is_visible():
                        box = parent.bounding_box()
                        if box and box.get("y", 9999) < 180:
                            return parent
            except Exception:
                continue
        return None

    def select_model(self, model_name: str | None) -> None:
        """Seleciona e confirma o modelo no menu real do MyHub; não envia silenciosamente no modelo errado."""
        if not model_name:
            return
        variants = self._model_text_variants(str(model_name))
        trigger = self._current_model_trigger()
        if trigger:
            try:
                current = (trigger.inner_text() or "").strip()
                if any(v.casefold() in current.casefold() for v in variants):
                    logging.info("Modelo MyHub já selecionado: %s", model_name)
                    return
            except Exception:
                pass
        # Configuração explícita, quando o usuário já tiver descoberto os seletores.
        model_cfg = self.cfg.get("model_selection", {})
        configured_trigger = self.visible(model_cfg.get("trigger_selectors", [])) if model_cfg.get("trigger_selectors") else None
        trigger = configured_trigger or trigger
        if not trigger:
            raise RuntimeError(
                "Não localizei o seletor de modelo no cabeçalho do MyHub. "
                "Nenhuma mensagem foi enviada; confira se o chat está aberto e logado."
            )
        trigger.click()
        self.page.wait_for_timeout(350)

        option = None
        for variant in variants:
            # Primeiro tenta os papéis semânticos; depois, texto exato no menu.
            for role in ("option", "menuitem"):
                try:
                    loc = self.page.get_by_role(role, name=variant, exact=True).last
                    if loc.count() and loc.is_visible():
                        option = loc
                        break
                except Exception:
                    pass
            if option:
                break
            try:
                loc = self.page.get_by_text(variant, exact=True).last
                if loc.count() and loc.is_visible():
                    option = loc
                    break
            except Exception:
                pass
            templates = model_cfg.get("option_selectors", [])
            if templates:
                option = self.visible([x.replace("{model}", variant) for x in templates])
                if option:
                    break
        if not option:
            raise RuntimeError(
                f"O menu do MyHub abriu, mas não encontrei o modelo '{model_name}'. "
                "Nenhuma mensagem foi enviada. Confira o nome exibido no seletor."
            )
        option.click()
        self.page.wait_for_timeout(500)

        # Confirmação pelo texto do cabeçalho; o menu deve ter fechado e o modelo deve estar visível.
        selected = self._current_model_trigger()
        try:
            selected_text = (selected.inner_text() or "") if selected else ""
        except Exception:
            selected_text = ""
        if not selected or not any(v.casefold() in selected_text.casefold() for v in variants):
            # Em layouts com cabeçalho não clicável, confirma presença no topo da página.
            confirmed = False
            for variant in variants:
                try:
                    loc = self.page.get_by_text(variant, exact=True).first
                    box = loc.bounding_box() if loc.count() and loc.is_visible() else None
                    if box and box.get("y", 9999) < 180:
                        confirmed = True
                        break
                except Exception:
                    continue
            if not confirmed:
                raise RuntimeError(
                    f"Não consegui confirmar a seleção do modelo '{model_name}'. A mensagem não foi enviada."
                )
        logging.info("Modelo solicitado selecionado: %s", model_name)

    @staticmethod
    def _expected_extensions(expected_outputs: list[str] | None) -> set[str]:
        if not expected_outputs:
            return set()
        extensions = set()
        for item in expected_outputs:
            key = str(item).lower().strip().lstrip(".")
            if key in {"svg", "image/svg+xml"}:
                extensions.add(".svg")
            elif key in {"json", "json_sidecar", "sidecar"}:
                extensions.add(".json")
            elif key in {"xml"}:
                extensions.add(".xml")
        return extensions

    def _attachment_candidates(self):
        messages = self._assistant_messages()
        if not messages.count():
            return [], []
        message = messages.nth(messages.count() - 1)
        configured = self.cfg.get("selectors", {}).get("attachments", [])
        selectors = list(configured) + [
            "a[download]",
            "a[href*='.svg' i]", "a[href*='.json' i]",
            "a[href*='download' i]", "a[href*='attachment' i]", "a[href*='file' i]",
            "button[aria-label*='download' i]", "button[aria-label*='baixar' i]",
            "button[title*='download' i]", "button[title*='baixar' i]",
            "[role='button'][aria-label*='download' i]", "[role='button'][aria-label*='baixar' i]",
            "[data-testid*='download' i]", "[data-testid*='attachment' i]", "[data-testid*='file' i]",
            "button:has-text('Baixar')", "button:has-text('Download')",
            "[role='button']:has-text('Baixar')", "[role='button']:has-text('Download')",
            "a:has-text('.svg')", "button:has-text('.svg')", "[role='button']:has-text('.svg')",
            "a:has-text('.json')", "button:has-text('.json')", "[role='button']:has-text('.json')",
            "a:has-text('SVG')", "button:has-text('SVG')", "[role='button']:has-text('SVG')",
            "a:has-text('JSON')", "button:has-text('JSON')", "[role='button']:has-text('JSON')",
        ]
        candidates, diagnostics = [], []
        # Um seletor CSS combinado retorna cada elemento uma vez, mesmo que ele
        # corresponda a mais de um seletor. Não deduplicar pelo texto/aria: dois
        # botões de download distintos (SVG e JSON) podem ter rótulos idênticos.
        try:
            locs = message.locator(", ".join(selectors))
            for i in range(min(locs.count(), 24)):
                loc = locs.nth(i)
                if not loc.is_visible():
                    continue
                info = loc.evaluate("""el => ({
                    tag: el.tagName.toLowerCase(), href: el.getAttribute('href') || '',
                    download: el.getAttribute('download') || '',
                    aria: el.getAttribute('aria-label') || '', title: el.getAttribute('title') || '',
                    text: (el.innerText || el.textContent || '').trim().slice(0,180),
                    parentText: (el.parentElement?.innerText || '').trim().slice(0,180),
                    testid: el.getAttribute('data-testid') || '',
                    cls: (typeof el.className === 'string' ? el.className : '').slice(0,120)
                })""")
                safe_info = dict(info)
                raw_href = str(info.get("href") or "")
                if raw_href:
                    parsed_href = urlparse(raw_href)
                    safe_info["href"] = (
                        f"{parsed_href.scheme}://{parsed_href.netloc}{parsed_href.path}"
                        if parsed_href.scheme else parsed_href.path
                    )
                diagnostics.append(safe_info)
                label = " ".join(str(info.get(k, "")) for k in ("href", "download", "aria", "title", "text", "parentText", "testid", "cls")).lower()
                looks_like_file = any(x in label for x in (
                    ".svg", ".json", "download", "baixar", "arquivo", "anexo", "attachment", "file"
                ))
                if looks_like_file:
                    candidates.append((loc, info))
        except Exception:
            logging.exception("Falha ao enumerar elementos de anexo da última mensagem")
        if not diagnostics:
            try:
                messages = self._assistant_messages()
                if messages.count():
                    excerpt = messages.nth(messages.count() - 1).inner_html()[:12000]
                    diagnostics.append({"kind": "assistant_message_dom_excerpt", "html_excerpt": excerpt})
            except Exception:
                pass
        return candidates, diagnostics

    @staticmethod
    def _sniff_extension(path: Path, fallback: str) -> str:
        """Identifica SVG/JSON pelo conteúdo, não apenas pelo nome sugerido no download."""
        try:
            raw = path.read_bytes()[:2 * 1024 * 1024]
            sample = raw.decode("utf-8-sig", errors="replace").lstrip("\ufeff \r\n\t")
            # Um SVG válido pode começar por declaração XML ou comentário.
            if re.search(r"<svg\b", sample[:20000], re.I):
                try:
                    root = ET.fromstring(raw.decode("utf-8-sig", errors="strict"))
                    if root.tag.rsplit("}", 1)[-1].lower() == "svg":
                        return ".svg"
                except Exception:
                    return ".invalid-svg"
            try:
                value = json.loads(raw.decode("utf-8-sig", errors="strict"))
                if isinstance(value, (dict, list)):
                    return ".json"
            except Exception:
                pass
        except Exception:
            pass
        return fallback

    @staticmethod
    def _validate_download(path: Path, extension: str) -> tuple[bool, str]:
        """Não deixa arquivo HTML/erro do servidor passar por SVG/JSON."""
        try:
            if extension == ".svg":
                root = ET.parse(path).getroot()
                if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                    return False, "raiz XML não é <svg>"
                return True, "SVG/XML bem-formado"
            if extension == ".json":
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(value, (dict, list)):
                    return False, "JSON não contém objeto ou lista"
                return True, "JSON válido"
        except Exception as exc:
            return False, f"arquivo incompleto/inválido: {exc}"
        return False, "extensão não reconhecida"

    def _save_href_content(self, href: str, destination: Path) -> bool:
        if not href:
            return False
        try:
            if href.startswith("data:"):
                header, payload = href.split(",", 1)
                data = base64.b64decode(payload) if ";base64" in header else unquote_to_bytes(payload)
                destination.write_bytes(data)
                return destination.is_file() and destination.stat().st_size > 0
            if href.startswith("blob:"):
                text = self.page.evaluate("async url => await (await fetch(url)).text()", href)
                if text:
                    destination.write_text(text, encoding="utf-8")
                    return True
            parsed = urlparse(href)
            if not parsed.scheme and href.startswith("/"):
                base = urlparse(self.page.url)
                href = f"{base.scheme}://{base.netloc}{href}"
                parsed = urlparse(href)
            if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.hostname.endswith("myhub.ia.br"):
                response = self.page.request.get(href, timeout=10000)
                if response.ok:
                    destination.write_bytes(response.body())
                    return destination.stat().st_size > 0
        except Exception:
            logging.exception("Não foi possível ler o link de anexo diretamente")
        return False

    @staticmethod
    def _complete_svg_candidates(text: str) -> list[str]:
        """Extrai apenas SVGs completos e XML bem-formados de um texto."""
        found: list[str] = []
        for match in re.finditer(r"<svg\b[\s\S]*?</svg\s*>", text or "", re.I):
            candidate = match.group(0).strip()
            try:
                root = ET.fromstring(candidate)
                if root.tag.rsplit("}", 1)[-1].lower() == "svg":
                    found.append(candidate)
            except ET.ParseError:
                continue
        return found

    @classmethod
    def _has_valid_inline_svg(cls, capture: dict[str, Any]) -> bool:
        sources = [str(capture.get("text", ""))]
        sources.extend(str(block.get("content", "")) for block in capture.get("code_blocks", []))
        return any(cls._complete_svg_candidates(source) for source in sources)

    @staticmethod
    def _has_valid_inline_json(capture: dict[str, Any]) -> bool:
        candidates = [str(block.get("content", "")) for block in capture.get("code_blocks", [])]
        text = str(capture.get("text", ""))
        if text:
            candidates.append(text)
        decoder = json.JSONDecoder()
        for candidate in candidates:
            candidate = candidate.strip().strip("`").strip()
            try:
                value = json.loads(candidate)
                if isinstance(value, (dict, list)):
                    return True
            except Exception:
                pass
            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                    if isinstance(value, (dict, list)):
                        return True
                except Exception:
                    continue
        return False

    def _collect_downloads(self, expected_outputs: list[str] | None, download_dir: Path,
                           capture: dict[str, Any], *, force_all: bool = False) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        expected = self._expected_extensions(expected_outputs)
        if not expected:
            return [], []
        has_valid_svg = self._has_valid_inline_svg(capture)
        has_valid_json = self._has_valid_inline_json(capture)
        missing = {ext for ext in expected if not (
            (ext == ".svg" and has_valid_svg)
            or (ext == ".json" and has_valid_json)
        )}
        if not missing and not force_all:
            return [], []
        if force_all:
            # Em lotes pode haver vários SVGs do mesmo tipo. Precisamos baixar todos,
            # mesmo quando a mensagem também contém um bloco inline válido.
            missing = set(expected)

        download_dir.mkdir(parents=True, exist_ok=True)
        diagnostics: list[dict[str, Any]] = []
        candidates: list[tuple[Any, dict[str, Any]]] = []
        deadline = time.monotonic() + min(18, float(self.cfg.get("timeouts", {}).get("attachment_seconds", 14)))
        while time.monotonic() < deadline:
            candidates, diagnostics = self._attachment_candidates()
            if candidates:
                break
            self.page.wait_for_timeout(500)

        saved: list[dict[str, str]] = []
        seen_files: set[str] = set()
        attempts: list[dict[str, Any]] = []
        for index, (locator, info) in enumerate(candidates, start=1):
            label = " ".join(str(info.get(k, "")) for k in ("href", "download", "aria", "title", "text", "parentText")).lower()
            identified_ext = next((ext for ext in sorted(expected) if ext[1:] in label), None)
            if identified_ext and identified_ext not in missing and not force_all:
                continue

            # Não descarte um botão genérico quando mais de um formato foi solicitado.
            # O nome/extensão real será obtido pelo evento Download e pelo conteúdo do arquivo.
            hint_ext = identified_ext or (next(iter(missing)) if len(missing) == 1 else ".bin")
            href = str(info.get("href") or "")
            file_name = str(info.get("download") or "")
            if not file_name:
                file_name = Path(urlparse(href).path).name if href else ""
            if not file_name or Path(file_name).suffix.lower() not in {".svg", ".json", ".xml", ".bin"}:
                file_name = f"anexo_{index:02d}{hint_ext}"
            file_name = re.sub(r"[^\w. -]+", "_", Path(file_name).name, flags=re.UNICODE).strip(" .") or f"anexo_{index:02d}{hint_ext}"

            destination = download_dir / file_name
            counter = 2
            while destination.exists():
                destination = download_dir / f"{Path(file_name).stem}_{counter}{Path(file_name).suffix}"
                counter += 1

            downloaded = False
            download_error = ""
            try:
                timeout_ms = int(self.cfg.get("timeouts", {}).get("download_event_ms", 10000))
                with self.page.expect_download(timeout=timeout_ms) as download_info:
                    locator.click(timeout=5000)
                download = download_info.value
                suggested = download.suggested_filename or file_name
                suggested = re.sub(r"[^\w. -]+", "_", Path(suggested).name, flags=re.UNICODE).strip(" .")
                if not suggested or Path(suggested).suffix.lower() not in {".svg", ".json", ".xml", ".bin"}:
                    suggested = f"anexo_{index:02d}{hint_ext}"
                destination = download_dir / suggested
                counter = 2
                while destination.exists():
                    destination = download_dir / f"{Path(suggested).stem}_{counter}{Path(suggested).suffix}"
                    counter += 1
                download.save_as(str(destination))
                downloaded = destination.is_file() and destination.stat().st_size > 0
            except PlaywrightTimeoutError as exc:
                download_error = f"evento Download não ocorreu: {exc}"
                # Alguns cartões expõem data/blob/href em vez de um evento de download.
                downloaded = self._save_href_content(href, destination)
            except Exception as exc:
                download_error = f"falha ao clicar/baixar: {exc}"
                logging.exception("Falha ao clicar/baixar anexo MyHub")
                downloaded = self._save_href_content(href, destination)

            if not downloaded or not destination.exists():
                attempts.append({
                    "candidate": index,
                    "result": "download_failed",
                    "label": label[:180],
                    "error": download_error or "nenhum arquivo foi salvo",
                })
                continue

            hinted_suffix = destination.suffix.lower()
            actual_ext = self._sniff_extension(destination, hinted_suffix or hint_ext)
            if actual_ext not in {".svg", ".json"}:
                attempts.append({
                    "candidate": index,
                    "result": "unsupported_or_invalid_file",
                    "filename": destination.name,
                    "detected_extension": actual_ext,
                    "error": "o conteúdo baixado não pôde ser identificado como SVG ou JSON válido",
                })
                destination.unlink(missing_ok=True)
                continue

            valid, validation_message = self._validate_download(destination, actual_ext)
            if not valid:
                attempts.append({
                    "candidate": index,
                    "result": "invalid_file",
                    "filename": destination.name,
                    "detected_extension": actual_ext,
                    "error": validation_message,
                })
                destination.unlink(missing_ok=True)
                continue

            if destination.suffix.lower() != actual_ext:
                renamed = destination.with_suffix(actual_ext)
                if renamed.exists():
                    renamed = download_dir / f"{renamed.stem}_{counter}{renamed.suffix}"
                destination.replace(renamed)
                destination = renamed

            dedupe_key = hashlib.sha256(destination.read_bytes()).hexdigest()
            if dedupe_key in seen_files:
                attempts.append({"candidate": index, "result": "duplicate", "filename": destination.name})
                continue
            seen_files.add(dedupe_key)
            saved.append({
                "filename": destination.name,
                "path": str(destination),
                "source": "myhub_download",
                "url": href,
            })
            missing.discard(actual_ext)
            attempts.append({
                "candidate": index,
                "result": "downloaded",
                "filename": destination.name,
                "detected_extension": actual_ext,
                "validation": validation_message,
            })
            logging.info("Anexo MyHub baixado e validado: %s (%s)", destination.name, actual_ext)
            if not missing and not force_all:
                break

        diagnostics.extend({"download_attempt": item} for item in attempts)
        return saved, diagnostics

    def send_and_collect_capture(self, prompt: str, *, expected_outputs: list[str] | None = None,
                                 download_dir: Path | None = None, force_all_downloads: bool = False) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt vazio.")
        box = self.composer()
        before_count = self._assistant_messages().count()
        previous = self.assistant_text()
        try:
            box.fill(prompt)
        except Exception:
            box.click()
            self.page.keyboard.insert_text(prompt)

        send_candidates = self.cfg["selectors"].get("send_button", []) + [
            'button[aria-label*="Enviar" i]', 'button[title*="Enviar" i]',
            'button[aria-label*="Send" i]', 'button[type="submit"]',
            'button:has-text("Enviar")', 'button:has-text("Send")',
        ]
        button = self.visible(send_candidates)
        if button and button.is_enabled():
            button.click()
        elif self.cfg["send"].get("keyboard", "Enter") == "Control+Enter":
            box.press("Control+Enter")
        else:
            box.press("Enter")

        logging.info("Prompt enviado; aguardando resposta.")
        deadline = time.monotonic() + int(self.cfg["timeouts"]["response_seconds"])
        stable_seconds = float(self.cfg["timeouts"]["stable_seconds"])
        last_text = ""
        stable_at: float | None = None
        seen_new_message = False
        capture: dict[str, Any] = {"text": "", "code_blocks": []}
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(800)
            capture = self.assistant_capture()
            current = str(capture.get("text", "")).strip()
            current_count = self._assistant_messages().count()
            if current_count > before_count or (current and current != previous):
                seen_new_message = True
            if seen_new_message and (current or capture.get("code_blocks") or capture.get("has_attachment")):
                signature = "\n".join([
                    current,
                    *[str(x.get("content", "")) for x in capture.get("code_blocks", [])],
                    "[anexo]" if capture.get("has_attachment") else "",
                ]).strip() or "[anexo sem texto]"
                if signature == last_text:
                    if stable_at is None:
                        stable_at = time.monotonic()
                else:
                    last_text = signature
                    stable_at = None
                if (not self.generating() and stable_at is not None
                        and time.monotonic() - stable_at >= stable_seconds):
                    break
        else:
            if not last_text:
                raise PlaywrightTimeoutError(
                    "Tempo esgotado aguardando a resposta. Confira a janela do MyHub e os seletores."
                )

        capture = self.assistant_capture() or capture
        if download_dir is not None and expected_outputs:
            attachments, diagnostics = self._collect_downloads(
                expected_outputs, Path(download_dir), capture, force_all=force_all_downloads
            )
            capture["attachments"] = attachments
            capture["attachment_diagnostics"] = diagnostics
        else:
            capture["attachments"] = []
            capture["attachment_diagnostics"] = []
        self.last_capture = capture
        return capture

    def send_and_collect(self, prompt: str) -> str:
        return str(self.send_and_collect_capture(prompt).get("text", ""))

    @property
    def current_url(self) -> str:
        return self.page.url if self.page else ""
