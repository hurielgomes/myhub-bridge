from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from artifacts import save_result
from browser import MyHubBrowser
from chat_store import ChatStore
from jobs import load_job

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def timestamp_now() -> str:
    return datetime.now().astimezone().isoformat()


def service_url(path: str = "") -> str:
    host = str(CFG.get("service", {}).get("host", "127.0.0.1"))
    port = int(CFG.get("service", {}).get("port", 8766))
    return f"http://{host}:{port}{path}"


def output_path(job_id: str) -> Path:
    safe = "".join(ch for ch in str(job_id) if ch.isalnum() or ch in "_.-")[:90] or "job"
    base = ROOT / CFG["paths"].get("results", "results") / safe
    if base.exists():
        suffix = datetime.now().strftime("_%Y%m%d_%H%M%S_%f")
        base = base.with_name(base.name + suffix)
    return base


def _process_job_with_client(job: dict[str, Any], client: MyHubBrowser) -> Path | None:
    prompt = job["prompt"]
    chat_key = str(job["chat_key"])
    model = job.get("model")
    chat_store = ChatStore(ROOT / CFG["paths"].get("chat_registry", "data/chat_registry.json"))
    chat_record = chat_store.get(chat_key)
    chat_url = chat_record.get("url") if chat_record else None
    saved_model = chat_record.get("model") if chat_record else None
    selected_model = str(model) if model else saved_model

    print("\n========== NOVA SOLICITAÇÃO ==========", flush=True)
    print(f"Projeto: {job.get('project')}", flush=True)
    print(f"Finalidade: {job.get('purpose')}", flush=True)
    print(f"Conversa: {chat_key} ({'existente' if chat_url else 'nova'})", flush=True)
    print(f"Modelo: {model or (saved_model or 'padrão atual do MyHub')}", flush=True)

    # Reaproveita Chromium/Playwright. Apenas navega entre os chats.
    client.navigate(chat_url or CFG["url"])
    client.ensure_login(interactive=False)

    if model:
        # O modelo selecionado no site pode persistir entre chats/sessões e não é
        # necessariamente o mesmo registrado para esta conversa. Confirme em todo job;
        # select_model retorna imediatamente quando o cabeçalho já mostra a opção correta.
        client.select_model(str(model))
        print(f"Modelo MyHub confirmado: {model}", flush=True)
    elif job.get("manual_model_selection") and not saved_model:
        raise RuntimeError("Seleção manual de modelo não é suportada no modo persistente. Escolha um modelo na interface da Fábrica.")

    job_id = str(job.get("_job_id") or job.get("id") or datetime.now().strftime("job_%Y%m%d_%H%M%S_%f"))
    folder = output_path(job_id)
    artifacts_dir = folder / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    outputs = job.get("expected_outputs") or []
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    force_all_downloads = bool(metadata.get("capture_all_downloads"))

    capture = client.send_and_collect_capture(
        prompt,
        expected_outputs=list(outputs),
        download_dir=artifacts_dir if outputs else None,
        force_all_downloads=force_all_downloads,
    )
    final_url = client.current_url
    if not final_url:
        raise RuntimeError("O navegador não informou a URL da conversa.")
    chat_store.save(
        chat_key,
        final_url,
        model=selected_model,
        project=str(job.get("project", "")),
        purpose=str(job.get("purpose", "")),
    )

    save_result(
        folder,
        {k: v for k, v in job.items() if not k.startswith("_")},
        capture,
        provider="MyHubIA via Playwright",
        timestamp=timestamp_now(),
        chat_key=chat_key,
        chat_url=final_url,
        selected_model=selected_model,
    )
    print(f"Resposta recebida: {len(str(capture.get('text', '')))} caracteres", flush=True)
    print(f"Blocos de código: {len(capture.get('code_blocks', []))}; downloads: {len(capture.get('attachments', []))}", flush=True)
    print("Resultado salvo em:", folder, flush=True)
    return folder


def process_job(job: dict[str, Any], *, confirm: bool = True, client: MyHubBrowser | None = None) -> Path | None:
    prompt = job["prompt"]
    if confirm and input(f"\nEnviar ao MyHub? [S/n] (projeto {job.get('project')}, {job.get('purpose')}): ").strip().lower() not in ("", "s", "sim"):
        print("Solicitação cancelada; nenhum prompt foi enviado.")
        return None

    if client is not None:
        return _process_job_with_client(job, client)

    # Compatibilidade com CLI manual. Na Fábrica, --serve mantém uma única sessão.
    chat_store = ChatStore(ROOT / CFG["paths"].get("chat_registry", "data/chat_registry.json"))
    record = chat_store.get(str(job["chat_key"]))
    url = record.get("url") if record else None
    with MyHubBrowser(CFG) as browser:
        browser.open(url or CFG["url"])
        browser.ensure_login(interactive=True)
        return _process_job_with_client(job, browser)


def process_inbox(*, confirm: bool = True, watch: bool = False) -> int:
    inbox = ROOT / CFG["paths"].get("inbox", "inbox")
    processing = ROOT / CFG["paths"].get("processing", "processing")
    processed = ROOT / CFG["paths"].get("processed", "processed")
    failed = ROOT / CFG["paths"].get("failed", "failed")
    for directory in (inbox, processing, processed, failed):
        directory.mkdir(parents=True, exist_ok=True)

    total = 0
    failures = 0
    print(f"Aguardando solicitações JSON/TXT em: {inbox}")
    while True:
        jobs = sorted([*inbox.glob("*.json"), *inbox.glob("*.txt")])
        if not jobs:
            if not watch:
                break
            time.sleep(2)
            continue
        for source in jobs:
            working = processing / source.name
            try:
                shutil.move(str(source), str(working))
                job = load_job(working)
                result_folder = process_job(job, confirm=confirm)
                if result_folder is None:
                    shutil.move(str(working), str(inbox / source.name))
                    continue
                destination = processed / source.name
                if destination.exists():
                    destination = processed / f"{source.stem}_{datetime.now():%Y%m%d_%H%M%S}{source.suffix}"
                shutil.move(str(working), str(destination))
                total += 1
            except Exception as exc:
                logging.exception("Falha ao processar solicitação %s", source.name)
                print(f"\nFALHA em {source.name}: {exc}")
                failures += 1
                if working.exists():
                    destination = failed / source.name
                    if destination.exists():
                        destination = failed / f"{source.stem}_{datetime.now():%Y%m%d_%H%M%S}{source.suffix}"
                    shutil.move(str(working), str(destination))
            if watch:
                time.sleep(0.5)
        if not watch:
            break
    print(f"Solicitações concluídas nesta execução: {total}; falhas: {failures}")
    return 1 if failures else 0


def run_service() -> int:
    """Servidor local serializado. Um único Chromium atende todas as chamadas da Fábrica."""
    host = str(CFG.get("service", {}).get("host", "127.0.0.1"))
    port = int(CFG.get("service", {}).get("port", 8766))
    (ROOT / CFG["paths"].get("logs", "logs")).mkdir(parents=True, exist_ok=True)
    logging.info("Iniciando sessão persistente MyHub Bridge em %s:%s", host, port)

    browser = MyHubBrowser(CFG)
    browser.open(CFG["url"])
    browser.ensure_login(interactive=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info("HTTP %s - %s", self.address_string(), fmt % args)

        def _json(self, status: int, payload: dict[str, Any]):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") == "/health":
                return self._json(200, {"ok": True, "service": "myhub-bridge", "persistent_browser": True})
            return self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            if self.path.rstrip("/") == "/shutdown":
                self._json(200, {"ok": True, "message": "Encerrando Bridge."})
                Thread(target=server.shutdown, daemon=True).start()
                return
            if self.path.rstrip("/") != "/job":
                return self._json(404, {"ok": False, "error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 8 * 1024 * 1024:
                    raise ValueError("Tamanho de solicitação inválido.")
                job = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(job, dict) or not isinstance(job.get("prompt"), str) or not job["prompt"].strip():
                    raise ValueError("Solicitação precisa ter prompt textual não vazio.")
                job.setdefault("chat_key", "fabrica__default")
                result = process_job(job, confirm=False, client=browser)
                self._json(200, {"ok": True, "result_dir": str(result) if result else None})
            except Exception as exc:
                logging.exception("Falha em job HTTP MyHub")
                self._json(500, {"ok": False, "error": str(exc)[:1800]})

    server = HTTPServer((host, port), Handler)
    pid_path = ROOT / CFG["paths"].get("service_pid", "data/service.pid")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    print(f"MyHub Bridge persistente ativo em http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        browser.close()
        pid_path.unlink(missing_ok=True)
    return 0


def stop_service() -> int:
    try:
        request = urllib.request.Request(service_url("/shutdown"), data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=3) as response:
            print(response.read().decode("utf-8", errors="replace"))
        return 0
    except Exception as exc:
        print(f"Bridge não parece estar ativo: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Ponte local entre a Fábrica e MyHubIA.")
    parser.add_argument("--login", action="store_true", help="Abre o MyHub para login manual.")
    parser.add_argument("--prompt", help="Envia um prompt rápido (sem arquivo JSON).")
    parser.add_argument("--job", help="Caminho para um arquivo de solicitação JSON ou TXT.")
    parser.add_argument("--inbox", action="store_true", help="Processa uma vez os arquivos JSON/TXT da pasta inbox.")
    parser.add_argument("--watch", action="store_true", help="Monitora inbox continuamente.")
    parser.add_argument("--serve", action="store_true", help="Inicia o navegador persistente e atende a Fábrica via HTTP local.")
    parser.add_argument("--shutdown", action="store_true", help="Encerra a sessão persistente do Bridge.")
    parser.add_argument("--yes", action="store_true", help="Não solicita confirmação antes do envio.")
    args = parser.parse_args()

    (ROOT / CFG["paths"].get("logs", "logs")).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=ROOT / CFG["paths"].get("logs", "logs") / "myhub-bridge.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        if args.shutdown:
            return stop_service()
        if args.serve:
            return run_service()
        if args.login:
            with MyHubBrowser(CFG) as client:
                client.open()
                input("Faça login no MyHub. Quando o chat estiver pronto, pressione ENTER aqui...")
                print("Perfil local salvo em:", ROOT / CFG["paths"]["profile"])
            return 0
        if args.job:
            job = load_job(Path(args.job))
            process_job(job, confirm=not args.yes)
            return 0
        if args.inbox or args.watch:
            return process_inbox(confirm=not args.yes, watch=args.watch)

        prompt = args.prompt or CFG["test_prompt"]
        job = {
            "id": "teste_rapido_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
            "project": "myhub_bridge",
            "purpose": "teste_rapido",
            "chat_key": "myhub_bridge__teste_rapido",
            "model": None,
            "prompt": prompt,
            "expected_outputs": [],
        }
        process_job(job, confirm=not args.yes)
        return 0
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130
    except Exception as exc:
        logging.exception("Falha na execução")
        print("\nERRO:", exc)
        print("Consulte README.md para instruções de configuração.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
