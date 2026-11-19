import json
import tempfile
import unittest
from pathlib import Path

from artifacts import save_result
from chat_store import ChatStore
from jobs import load_job
from browser import MyHubBrowser


class BridgeUnitTests(unittest.TestCase):
    def test_chat_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chats.json"
            store = ChatStore(path)
            store.save("Projeto / M1", "https://myhub.ia.br/chat/abc", model="GPT-6")
            self.assertEqual(store.get("Projeto / M1")["url"], "https://myhub.ia.br/chat/abc")
            self.assertEqual(store.get("Projeto / M1")["model"], "GPT-6")
            self.assertTrue(path.exists())

    def test_chat_store_rejects_external_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ChatStore(Path(temp) / "chats.json")
            with self.assertRaises(ValueError):
                store.save("unsafe", "https://example.com/chat")

    def test_load_job_validates_and_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pedido.json"
            path.write_text(json.dumps({"prompt": "Crie um SVG"}), encoding="utf-8")
            job = load_job(path)
            self.assertEqual(job["chat_key"], "default__general")
            self.assertEqual(job["prompt"], "Crie um SVG")

    def test_plain_text_prompt_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pedido.txt"
            path.write_text("Crie um personagem SVG", encoding="utf-8")
            job = load_job(path)
            self.assertEqual(job["prompt"], "Crie um personagem SVG")
            self.assertEqual(job["chat_key"], "default__general")

    def test_artifacts_are_saved_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "result"
            capture = {"text": "Arquivos criados.", "code_blocks": [
                {"language": "json", "filename": "objeto.json", "content": '{"ok": true}'},
                {"language": "svg", "filename": "objeto.svg", "content": '<svg xmlns="http://www.w3.org/2000/svg"/>'},
                {"language": "python", "filename": "script.py", "content": 'print("não executar")'},
            ]}
            result = save_result(folder, {"prompt": "teste"}, capture,
                                 provider="teste", timestamp="2026-01-01T00:00:00",
                                 chat_key="test", chat_url="https://myhub.ia.br/chat/test",
                                 selected_model=None)
            self.assertEqual(len(result["artifacts"]), 3)
            self.assertTrue(result["artifacts"][0]["valid"])
            self.assertTrue(result["artifacts"][1]["valid"])
            self.assertIsNone(result["artifacts"][2]["valid"])
            self.assertTrue((folder / "response.json").exists())
            self.assertTrue((folder / "artifacts" / "script.py").exists())

    def test_modelos_myhub_disponiveis_incluem_nitro_e_opcoes_rapidas(self):
        expected = {
            "GPT-6 Luna", "Claude Haiku 5.5", "Gemini 2.5 Flash",
            "Gemini 3.8 Flash", "DeepSeek v4 Flash", "Grok 4.3", "MyHubNitro",
        }
        self.assertTrue(expected.issubset(set(MyHubBrowser.MODEL_LABELS)))
        self.assertIn("Deepseak v4 Flash", MyHubBrowser.MODEL_LABELS["DeepSeek v4 Flash"])
        self.assertIn("MyHub Nitro", MyHubBrowser.MODEL_LABELS["MyHubNitro"])


if __name__ == "__main__":
    unittest.main()
