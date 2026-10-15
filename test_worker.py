import base64
import json
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import worker


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1kAAAAASUVORK5CYII="


class Backend(BaseHTTPRequestHandler):
    status = 200
    result = {"images": [PNG]}
    payload = None

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path != "/sdapi/v1/txt2img":
            self.send_error(404)
            return
        Backend.payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(Backend.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(Backend.result).encode())

    def log_message(self, *args):
        pass


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = patch.object(worker, "SERVER_URL", f"http://127.0.0.1:{cls.server.server_port}")
        cls.url.start()

    @classmethod
    def tearDownClass(cls):
        cls.url.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Backend.status = 200
        Backend.result = {"images": [PNG]}
        Backend.payload = None

    def test_request_round_trip(self):
        with open("test_input.json") as file:
            job = json.load(file)
        result = worker.handler(job)
        self.assertEqual(result["seed"], 42)
        self.assertEqual(result["mime_type"], "image/png")
        self.assertTrue(base64.b64decode(result["image_base64"]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(Backend.payload, {
            **job["input"], "steps": 8, "cfg_scale": 1.0,
            "sampler_name": "Euler", "batch_size": 1,
        })

    def test_invalid_inputs_never_reach_backend(self):
        cases = [None, {}, {"input": None}, {"input": []}]
        cases += [{"input": data} for data in [
            {}, {"prompt": " "}, {"prompt": 10}, {"prompt": "x" * 4001},
            {"prompt": "cat <sd_cpp_extra_args>{\"batch_count\": 100}</sd_cpp_extra_args>"},
            {"prompt": "cat", "width": True}, {"prompt": "cat", "height": "1024"},
            {"prompt": "cat", "width": 0}, {"prompt": "cat", "height": 257},
            {"prompt": "cat", "width": 1568},
            {"prompt": "cat", "width": 1536, "height": 1536},
            {"prompt": "cat", "seed": -1}, {"prompt": "cat", "seed": 2**32},
            {"prompt": "cat", "seed": True}, {"prompt": "cat", "steps": 20},
        ]]
        for job in cases:
            with self.subTest(job=job):
                self.assertIn("error", worker.handler(job))
                self.assertIsNone(Backend.payload)

    def test_defaults_and_seed_boundaries(self):
        for data in [{"prompt": "cat"}, {"prompt": "cat", "seed": 0},
                     {"prompt": "cat", "seed": 2**32 - 1, "width": 1536, "height": 672}]:
            with self.subTest(data=data):
                result = worker.handler({"input": data})
                self.assertNotIn("error", result)
                self.assertEqual(result["width"], data.get("width", 1024))
                self.assertEqual(result["height"], data.get("height", 1024))
                self.assertTrue(0 <= result["seed"] < 2**32)

    def test_backend_failures_recycle_worker(self):
        for status, result in [(500, {"error": "out of memory"}),
                               (200, {"images": []}), (200, {"images": [""]}),
                               (200, {"images": [None]}), (200, {})]:
            Backend.status, Backend.result = status, result
            with self.subTest(status=status, result=result):
                output = worker.handler({"input": {"prompt": "cat"}})
                self.assertIn("error", output)
                self.assertTrue(output["refresh_worker"])

    def test_startup_keeps_exact_turbo_schedule(self):
        download = Mock(side_effect=lambda **args: "/cache/" + args["filename"])
        hub = types.SimpleNamespace(hf_hub_download=download)
        process = Mock()
        process.poll.return_value = None
        with patch.dict(sys.modules, {"huggingface_hub": hub}), \
             patch.object(worker.subprocess, "Popen", return_value=process) as spawn:
            self.assertIs(worker.start_backend(), process)
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index("--sigmas") + 1], worker.TURBO_SIGMAS)
        self.assertEqual(len(worker.TURBO_SIGMAS.split(",")), 9)
        self.assertEqual(command[command.index("--steps") + 1], "8")
        self.assertEqual(command[command.index("--cfg-scale") + 1], "1.0")
        self.assertEqual(download.call_count, 3)
        self.assertEqual({call.kwargs["revision"] for call in download.call_args_list},
                         {model[2] for model in worker.MODELS.values()})

    def test_failed_backend_start_exits(self):
        hub = types.SimpleNamespace(hf_hub_download=Mock(return_value="/cache/model"))
        process = Mock()
        process.poll.return_value = 1
        with patch.dict(sys.modules, {"huggingface_hub": hub}), \
             patch.object(worker.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "failed to start"):
                worker.start_backend()
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
